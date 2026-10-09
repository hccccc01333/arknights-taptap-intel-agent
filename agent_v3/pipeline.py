"""Collection never depends on model availability; intelligence routes creative jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from agent_v2.store import dump, now_iso
from . import work, intelligence
from .model import gate, GatedModel
from .engine import run_agent


def process(store,run_id,*,topic_id=None,model=None,selected_topics=None):
    from .runtime_guard import LeaseLost
    own_lease=store.active_owner()!=run_id
    if not store.acquire(run_id,ttl=1200):raise LeaseLost('自动处理运行租约不可用')
    try:return _process(store,run_id,topic_id=topic_id,model=model,selected_topics=selected_topics)
    finally:
        if own_lease:store.release(run_id)


def _process(store,run_id,*,topic_id=None,model=None,selected_topics=None):
    enqueued=work.enqueue(store,topic_id=topic_id,topic_ids=selected_topics)
    result={"enqueued":enqueued,"intelligence":[],"creative":[],"usage":{},"status":"intelligence_ready"}
    clause=" AND topic_id=?" if topic_id else ""
    count=store.conn.execute("SELECT COUNT(*) FROM work_item WHERE status IN ('pending','deferred','running')"+clause,
                             [topic_id] if topic_id else []).fetchone()[0]
    if not count:return {**result,"status":"no_pending_work"}
    current_gate=gate(store) if model is None else {"status":"ready"}
    if current_gate["status"]=="deferred":
        work.defer_due(store,current_gate)
        return {**result,"status":"ai_deferred","model_gate":current_gate}
    due=store.conn.execute("SELECT COUNT(*) FROM work_item WHERE (status IN ('pending','deferred') AND (retry_at IS NULL OR retry_at<=?) OR status='running' AND lease_until<=?)"+clause,
                           [now_iso(),now_iso(),*([topic_id] if topic_id else [])]).fetchone()[0]
    if not due:return {**result,"status":"waiting_work"}
    try:
        model=model or GatedModel(store)
        result["model"]=getattr(model,"model",None)
    except Exception as error:
        # Missing credentials is actionable; collection and pending tasks are retained.
        from .model import model_status, failure
        selected=model_status(store.model_setting())["model"]
        failure(store,selected,error)
        work.defer_due(store,gate(store,selected))
        return {**result,"status":"ai_unavailable","model":selected,"error":str(error)[:400] or "模型服务配置不可用，情报任务已保留"}
    if getattr(model,'supports_main_agent',False) and selected_topics is None:
        from .main_agent import plan
        from . import research,research_child
        from .discovery import scan
        preparation_start=store.conn.execute('SELECT COALESCE(MAX(sequence),0) FROM step WHERE run_id=?',(run_id,)).fetchone()[0]
        decisions=plan(store,run_id,model,topic_id)
        research.run(store,model=model,run_id=run_id,delegations=[d for d in decisions if d['action']=='delegate_research'])
        scan(store)
        selected_topics=[]
        for decision in decisions:
            research_child.interpret(store,run_id,model,decision['topic_id'],decision)
            selected_topics.append(decision['topic_id'])
        work.enqueue(store,topic_id=topic_id)
        for row in store.conn.execute("SELECT payload FROM step WHERE run_id=? AND sequence>? AND kind IN ('model_task','research_plan_model')",(run_id,preparation_start)):
            for key,value in (json.loads(row[0]).get('usage') or {}).items():
                if type(value) in (int,float):result['usage'][key]=result['usage'].get(key,0)+value
    if selected_topics==[]:return {**result,'status':'no_selected_work'}
    for _ in range(2):
        job=work.claim(store,run_id,"intelligence",topic_id=topic_id,topic_ids=selected_topics)
        if not job:break
        store.acquire(run_id,ttl=1200)
        first_step=store.conn.execute('SELECT COALESCE(MAX(sequence),0) FROM step WHERE run_id=?',(run_id,)).fetchone()[0]
        try:
            output=intelligence.run(store,run_id,job,model)
            work.finish(store,job,"succeeded",result=output)
            result["intelligence"].append(output)
            result["model"]=output["model"]
            for key,value in output["usage"].items():result["usage"][key]=result["usage"].get(key,0)+value
            store.step(run_id,"intelligence_saved",output)
            work.enqueue(store,topic_id=job["topic_id"],topic_ids=selected_topics)
        except Exception as error:
            from .runtime_guard import LeaseLost,StaleBasis
            if isinstance(error,LeaseLost):
                if isinstance(error,StaleBasis):work.finish(store,job,'superseded',error=str(error))
                store.step(run_id,'stale_delivery_rejected',{'job_id':job['job_id'],'reason':str(error)})
                result.update(status='waiting_work',error=str(error));return result
            # Completed requests still incur usage when business validation fails.
            for row in store.conn.execute("SELECT payload FROM step WHERE run_id=? AND sequence>? AND kind IN ('model_task','opencode_task','intelligence_model')",(run_id,first_step)):
                for key,value in (json.loads(row[0]).get('usage') or {}).items():
                    if type(value) in (int,float):result['usage'][key]=result['usage'].get(key,0)+value
            cooldown=gate(store,getattr(model,"model",None))
            retry=cooldown.get("retry_at") or (datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(timespec="seconds")
            reason=cooldown.get("reason") or ("情报预算用完，等待重试" if isinstance(error,TimeoutError) else "情报处理未完成，等待重试")
            work.finish(store,job,"deferred",error=reason,retry_at=retry)
            store.step(run_id,"intelligence_deferred",{"job_id":job["job_id"],"error":reason,"retry_at":retry,
                                                       "detail":str(error)[:400],"session_id":getattr(model,"last_session",None)})
            result.update(status="ai_deferred",error=reason+"："+str(error)[:400])
            result["model"]=getattr(model,"model",None)
            if cooldown["status"]=="deferred":
                work.defer_due(store,cooldown);return result
    job=work.claim(store,run_id,"creative",topic_id=topic_id,topic_ids=selected_topics)
    if job:
        # Business judgment is cached; no game-signal or material artifact is required.
        store.conn.execute("UPDATE run SET task=task||? WHERE run_id=?",
                           ("\n当前创意任务只处理 topic_id="+job["topic_id"]+"；读取热点与主 Agent 机会判断，情报与素材可为空。",run_id));store.conn.commit()
        output=run_agent(store,run_id,model,assigned_topic=job["topic_id"])
        result["model"]=output["model"]
        for key,value in output["usage"].items():result["usage"][key]=result["usage"].get(key,0)+value
        if output["status"]=="completed":
            work.finish(store,job,"succeeded",result={"event_ids":output["result"]["event_ids"]})
            result["creative"]=output["result"]["event_ids"];result["status"]="completed"
        else:
            cooldown=gate(store,getattr(model,"model",None))
            retry=cooldown.get("retry_at") or (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(timespec="seconds")
            work.finish(store,job,"deferred",error=output["error"],retry_at=retry)
            result.update(status="ai_deferred",error=output["error"])
            if cooldown["status"]=="deferred":work.defer_due(store,cooldown)
    elif result["intelligence"] and result.get("error"):
        result["status"]="intelligence_partial"
    elif not result["intelligence"] and not result.get("error"):
        result["status"]="waiting_work"
    return result
