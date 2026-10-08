from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from agent_v2.ingest import ingest
from agent_v2.store import dump, now_iso
from .store import Store, migrate_v2
from .discovery import scan, queue, read_topic
from .connectors import CHANNELS, collect, coverage
from .engine import TASK, run_agent
from .materials import index_recent
from . import work
from .pipeline import process
from .model import gate, import_failure_history, model_status, model_options, zen_quota, set_zen_quota

_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="v3-cycle")
_lock=threading.Lock();_stop=threading.Event();_thread=None


def schedule(store):
    row=store.conn.execute("SELECT value FROM settings WHERE key='schedule'").fetchone()
    return json.loads(row[0]) if row else {"enabled":False,"interval_minutes":60,"research":True,"next_run_at":None}


def automatic_work(store):
    row=store.conn.execute("SELECT value FROM settings WHERE key='automatic_work'").fetchone()
    return json.loads(row[0]) if row else {'interval_minutes':15,'next_run_at':None}


def has_automatic_work(store):
    from agent_v2.store import stable_id
    context=stable_id('context_',dump(store.context()));cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat(timespec='seconds')
    from .followups import due
    if due(store):return True
    return bool(store.conn.execute('''SELECT 1 FROM topic t LEFT JOIN main_decision d ON d.topic_id=t.topic_id
      AND d.fingerprint=t.fingerprint AND d.context_version=? WHERE t.eligible=1 AND t.last_seen_at>=?
      AND (d.topic_id IS NULL OR json_extract(d.payload,'$.action') IN ('delegate_research','analyze','watch')
        AND COALESCE(json_extract(d.payload,'$.next_review_at'),'')<=?
        AND NOT EXISTS (SELECT 1 FROM topic_intelligence i WHERE i.topic_id=t.topic_id AND i.fingerprint=t.fingerprint
          AND json_extract(i.payload,'$.context_version')=? AND json_extract(i.payload,'$.contract_version')=? AND
            ((json_extract(i.payload,'$.opportunity.decision') IN ('watch','archive') AND
              (json_extract(i.payload,'$.next_review_at') IS NULL OR json_extract(i.payload,'$.next_review_at')>?)) OR
             EXISTS (SELECT 1 FROM work_item w WHERE w.topic_id=t.topic_id AND w.fingerprint=t.fingerprint AND w.stage='creative' AND w.status='succeeded'))))
      LIMIT 1''',(context,cutoff,now_iso(),context,work.INTELLIGENCE_VERSION,now_iso())).fetchone())


def unscreened_count(store):
    from agent_v2.store import stable_id
    context=stable_id('context_',dump(store.context()))
    cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat(timespec='seconds')
    return store.conn.execute('''SELECT COUNT(*) FROM topic t LEFT JOIN main_decision d ON d.topic_id=t.topic_id
      AND d.fingerprint=t.fingerprint AND d.context_version=? WHERE t.eligible=1 AND t.last_seen_at>=? AND d.topic_id IS NULL''',
      (context,cutoff)).fetchone()[0]


def start_screening():
    """Cheap screening owns a separate cadence, not a second business agent."""
    with _lock:
        store=Store()
        try:
            if store.active_owner():raise ValueError('已有后台任务')
            run_id=store.create_run('自动初筛全网候选')
            if not store.acquire(run_id,ttl=600):raise ValueError('已有后台任务')
        finally:store.close()
        _pool.submit(execute_screening,run_id)
    return {'run_id':run_id,'status':'queued'}


def execute_screening(run_id,*,model=None,store_path=None):
    store=Store(store_path);usage={}
    try:
        with store.conn:store.conn.execute("UPDATE run SET status='running' WHERE run_id=?",(run_id,))
        model=model or __import__('agent_v3.model',fromlist=['GatedModel']).GatedModel(store)
        from .main_agent import plan
        before=unscreened_count(store)
        for _ in range(2):
            if not unscreened_count(store):break
            store.acquire(run_id,ttl=600)
            plan(store,run_id,model,screen_limit=40,screen_only=True)
        after=unscreened_count(store)
        from .retention import compact
        store.step(run_id,'candidate_retention',compact(store))
        store.finish(run_id,'completed',result={'screened':before-after,'remaining':after,'kind':'automatic_screening'},model=model.model)
    except Exception as error:
        store.finish(run_id,'failed',error=type(error).__name__+': '+str(error)[:200])
    finally:
        for step in store.get_run(run_id)['steps']:
            if step['kind']=='model_task':
                for key,value in (step['payload'].get('usage') or {}).items():
                    if type(value) in (int,float):usage[key]=usage.get(key,0)+value
        with store.conn:store.conn.execute('UPDATE run SET usage=? WHERE run_id=?',(dump(usage),run_id))
        store.release(run_id);store.close()


def overview():
    with_store=Store()
    try:
        data=with_store.overview()
        data.update({"channels":coverage(with_store),"topics":queue(with_store),"materials":with_store.usable_materials(limit=100),
                     "source_assets":with_store.source_assets(limit=30),"work":work.overview(with_store),"model_gate":gate(with_store),
                     "context":with_store.context(),"active_run":with_store.active_owner(),"schedule":schedule(with_store),'automatic_work':automatic_work(with_store),
                     "model":model_status(with_store.model_setting()),"model_options":model_options(),
                     "zen_quota":zen_quota(with_store),
                     "reasoning":{"effort":with_store.reasoning_setting()},
                     "output":{"max_tokens":with_store.output_setting()}})
        from . import tracking,research
        data.update({'tracking':tracking.overview(with_store),'research':research.overview(with_store)})
        from .presentation import classify_outputs,material_ready
        data['materials']=[m for m in data['materials'] if material_ready(m)]
        data['counts']['usable_materials']=len(data['materials'])
        data['latest_delivery_at']=with_store.conn.execute('''SELECT MAX(stamp) FROM
          (SELECT created_at AS stamp FROM topic_intelligence UNION ALL SELECT created_at FROM creative)''').fetchone()[0]
        from .library_context import attach
        return attach(with_store,classify_outputs(data))
    finally:with_store.close()


def start_cycle(*, research=True, live=True, task=TASK, topic_id=None):
    if not isinstance(task,str) or not 1<=len(task.strip())<=3000:raise ValueError("任务需为 1 至 3000 字")
    with _lock:
        store=Store()
        try:
            if store.active_owner():raise ValueError("已有 V3 采集或研究正在运行")
            run_id=store.create_run(task.strip())
            if not store.acquire(run_id,ttl=1200):raise ValueError("已有任务正在运行")
            cycle_id="cycle_"+uuid.uuid4().hex
            store.conn.execute("INSERT INTO cycle VALUES(?,?,?,?,?,?,?,?)",(cycle_id,now_iso(),None,"queued",None,None,dump([run_id]),None))
            store.conn.commit()
        finally:store.close()
        try:_pool.submit(execute_cycle,run_id,cycle_id,research,live,topic_id)
        except Exception:
            store=Store()
            try:
                store.finish(run_id,"failed",error="运行器未接受任务");store.release(run_id)
            finally:store.close()
            raise
    return {"run_id":run_id,"cycle_id":cycle_id,"status":"queued"}


def execute_cycle(run_id,cycle_id,research,live,topic_id=None,*,model=None,store_path=None):
    store=Store(store_path)
    try:
        if not store.acquire(run_id,ttl=1200):raise ValueError("运行租约不可用")
        store.conn.execute("UPDATE run SET status='running' WHERE run_id=?",(run_id,))
        store.conn.execute("UPDATE cycle SET status='collecting' WHERE cycle_id=?",(cycle_id,));store.conn.commit()
        marker=store.conn.execute("SELECT value FROM settings WHERE key='v2_migration'").fetchone()
        if not marker:
            migrated=migrate_v2(store);store.step(run_id,"migration",migrated)
            if migrated["status"]=="ok":
                store.conn.execute("INSERT INTO settings VALUES('v2_migration',?)",(dump(migrated),));store.conn.commit()
        # Imported V1/V2 files do not need rereading in every AI-only drain.
        if live or not store.conn.execute("SELECT 1 FROM settings WHERE key='legacy_ingest_done'").fetchone():
            imported=ingest(store);store.step(run_id,"ingest",imported)
            with store.conn:store.conn.execute("INSERT OR REPLACE INTO settings VALUES('legacy_ingest_done',?)",(dump(now_iso()),))
        collected={"status":"not_requested","channels":[]}
        if live:
            for channel in CHANNELS:
                result=collect(store,channel["id"]);collected["channels"].append(result)
                store.step(run_id,"channel_collection",result)
            collected["status"]="ok" if all(r["status"]=="ok" for r in collected["channels"]) else "partial"
        inventory=index_recent(store);store.step(run_id,"source_materials",inventory)
        discovered=scan(store);store.step(run_id,"discovery",discovered)
        from . import followups
        followups.wake_changed(store)
        from . import tracking,research as research_tools
        import_failure_history(store)
        auxiliary=model if getattr(model,'supports_research_tasks',False) or getattr(model,'supports_main_agent',False) else None
        if research and model is None and gate(store)['status']=='ready':
            from .model import GatedModel,failure
            try:auxiliary=GatedModel(store)
            except Exception as error:
                failure(store,store.model_setting(),error)
        main_decisions=None;selected_topics=None
        if getattr(auxiliary,'supports_main_agent',False):
            from .main_agent import plan
            main_decisions=plan(store,run_id,auxiliary,topic_id)
            if not topic_id:
                supplements=followups.missions(store,run_id,limit=1)
                for mission in supplements:
                    main_decisions=[mission,*[d for d in main_decisions if d['topic_id']!=mission['topic_id']]][:2]
        enriched=research_tools.run(store,topic_id=topic_id,model=auxiliary,max_calls=6,run_id=run_id,
            delegations=[d for d in main_decisions if d['action']=='delegate_research'] if main_decisions is not None else None)
        store.step(run_id,"source_enrichment",enriched)
        if enriched['calls']:discovered=scan(store)
        observed=tracking.observe(store);store.step(run_id,'event_tracking',observed)
        recalled=tracking.recall(store);store.step(run_id,'event_recall',recalled)
        if auxiliary is not None and gate(store)['status']=='ready' and (live or main_decisions):
            try:store.step(run_id,'event_relations',tracking.review_model(store,auxiliary,run_id,limit=1))
            except Exception as error:
                from .opencode_zen import StructuredDeliveryError
                if isinstance(error,StructuredDeliveryError):
                    from .model import task_metadata
                    store.step(run_id,'event_relation_model',{**task_metadata(error.response),'delivery_status':'invalid'})
                store.step(run_id,'event_relation_deferred',{'error':type(error).__name__,'note':'待判关系保留，未通过来源校验的不合并'})
        discovered=scan(store)
        if topic_id:topic_id=read_topic(store,topic_id,include_tracking=False)['canonical_topic_id']
        if main_decisions is not None:
            from .research_child import interpret
            selected_topics=[];deferred_topics=[]
            for decision in main_decisions:
                canonical=read_topic(store,decision['topic_id'],include_tracking=False)['canonical_topic_id']
                if canonical in selected_topics:continue
                try:
                    interpret(store,run_id,auxiliary,canonical,decision)
                    selected_topics.append(canonical)
                except Exception as error:
                    followups.settle(store,decision,run_id)
                    deferred_topics.append(canonical)
                    store.step(run_id,'research_child_deferred',{'topic_id':canonical,'error':type(error).__name__,
                        'detail':str(error)[:300],'reason':'解读尚未通过交付校验，保留研究缺口，未计入热点'})
                    retry=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(timespec='seconds')
                    with store.conn:store.conn.execute("UPDATE main_decision SET payload=json_set(payload,'$.next_review_at',?) WHERE topic_id=? AND fingerprint=?",
                        (retry,decision['topic_id'],decision['fingerprint']))
        if main_decisions:
            # New research can update business judgment while preserving all
            # previous deliveries. An existing completed creative is not duplicated.
            with store.conn:
                for d in main_decisions:
                    if d.get('followup_ids') and d['topic_id'] in (selected_topics or []):
                        store.conn.execute("UPDATE work_item SET status='pending',retry_at=NULL WHERE topic_id=? AND stage='intelligence' AND status='succeeded'",
                            (d['topic_id'],))
        pending=work.enqueue(store,topic_id=topic_id,topic_ids=selected_topics);store.step(run_id,"work_enqueued",pending)
        import_failure_history(store)
        store.conn.execute("UPDATE cycle SET collection=?,discovery=?,status='discovered' WHERE cycle_id=?",(dump(collected),dump(discovered),cycle_id));store.conn.commit()
        if not research:
            status="collected" if collected["status"]!="partial" else "partial"
            store.finish(run_id,status,result={"collection":collected,"discovery":discovered,"source_materials":inventory,"source_enrichment":enriched,"work":pending,'event_tracking':observed,'event_recall':recalled})
        else:
            if topic_id:
                topic=read_topic(store,topic_id)
                store.conn.execute("UPDATE run SET task=task||? WHERE run_id=?",("\n优先研究候选 topic_id="+topic["topic_id"],run_id));store.conn.commit()
            store.conn.execute("UPDATE cycle SET status='researching' WHERE cycle_id=?",(cycle_id,));store.conn.commit()
            result=process(store,run_id,topic_id=topic_id,model=model or auxiliary,selected_topics=selected_topics)
            if main_decisions is not None and deferred_topics:
                result['deferred_research_topics']=deferred_topics
                if result['status']=='intelligence_ready':result['status']='intelligence_partial'
            if main_decisions and not selected_topics:
                result.update(status='research_deferred',error='主 Agent 已委派研究，但事件解读尚未通过，等待补证据或重试')
            after=scan(store)
            result["followup_work"]=work.enqueue(store,topic_id=topic_id,topic_ids=selected_topics)
            store.step(run_id,"post_research_discovery",after)
            status=result["status"]
            previous=store.get_run(run_id)
            usage={}
            for step in previous.get('steps',[]):
                if step['kind'] in ('research_plan_model','event_relation_model','model_task','opencode_task','intelligence_model','model'):
                    for key,value in (step['payload'].get('usage') or {}).items():
                        if type(value) in (int,float):usage[key]=usage.get(key,0)+value
            store.finish(run_id,status,result={**(previous.get("result") or {}),"pipeline":result,
                         "collection":collected,"discovery":discovered,"source_materials":inventory,"source_enrichment":enriched,'event_tracking':observed,'event_recall':recalled},
                         error=result.get("error") or result.get("model_gate",{}).get("reason"),
                         model=result.get("model") or previous.get("model") or getattr(model,"model",None),usage=usage)
        store.conn.execute("UPDATE cycle SET status=?,finished_at=?,error=? WHERE cycle_id=?",(status,now_iso(),store.get_run(run_id).get("error"),cycle_id));store.conn.commit()
    except Exception as error:
        store.conn.rollback()
        usage={}
        for step in store.get_run(run_id).get('steps',[]):
            if step['kind'] in ('research_plan_model','event_relation_model','model_task','opencode_task','intelligence_model','model'):
                for key,value in (step['payload'].get('usage') or {}).items():
                    if type(value) in (int,float):usage[key]=usage.get(key,0)+value
        store.finish(run_id,"failed",error=f"{type(error).__name__}: {str(error)[:400]}",usage=usage)
        store.conn.execute("UPDATE cycle SET status='failed',finished_at=?,error=? WHERE cycle_id=?",(now_iso(),str(error)[:400],cycle_id));store.conn.commit()
    finally:
        store.release(run_id);store.close()


def read(kind,identifier=None,query="",days=7,domain=''):
    store=Store()
    try:
        if kind=="run":return store.get_run(identifier)
        if kind=="topic":return read_topic(store,identifier)
        if kind=='tracked_event':
            from .tracking import read_event
            return read_event(store,identifier)
        if kind=='relation':
            from .tracking import read_relation
            return read_relation(store,identifier)
        if kind=='topics':return {'topics':queue(store,domain=domain),'hotspots':store.hotspot_feed(domain=domain)}
        if kind=="event":return store.get_event(identifier)
        if kind=='creative_event':return store.creative_event(identifier)
        if kind=='sources':return {'sources':store.evidence(identifier)}
        if kind=="materials":
            from .presentation import material_ready
            return {"materials":[m for m in store.usable_materials(query,limit=100) if material_ready(m)]}
        if kind=="source_materials":return {"source_assets":store.source_assets(query)}
        if kind=="work":return work.overview(store)
        if kind=="brief":
            from .reports import brief
            return brief(store,days)
        raise ValueError("读取类型无效")
    finally:store.close()


def configure(kind,value):
    store=Store()
    try:
        if kind=="context":return store.set_context(value)
        if kind=="zen_quota":
            if store.active_owner():raise ValueError("当前任务结束后再修改额度暂停状态")
            result=set_zen_quota(store,value.get("hold"))
            cooldown=gate(store)
            if cooldown["status"]=="deferred":work.defer_due(store,cooldown)
            return result
        if kind=="model":
            if store.active_owner():raise ValueError("当前任务结束后再切换模型")
            result=store.set_model(value)
            if gate(store,value)["status"]=="ready":work.wake_provider_work(store)
            return result
        if kind=="reasoning":
            if store.active_owner():raise ValueError("当前任务结束后再调整推理强度")
            return store.set_reasoning(value.get("effort"))
        if kind=="output":
            if store.active_owner():raise ValueError("当前任务结束后再调整输出预算")
            return store.set_output(value.get("max_tokens"))
        if kind=="schedule":
            if not isinstance(value.get("enabled"),bool) or type(value.get("interval_minutes")) is not int or not 15<=value["interval_minutes"]<=1440 or not isinstance(value.get("research"),bool):raise ValueError("需设置启用状态、15 至 1440 分钟间隔和研究开关")
            result={k:value[k] for k in ("enabled","interval_minutes","research")}
            result["next_run_at"]=(datetime.now(timezone.utc)+timedelta(minutes=value["interval_minutes"])).isoformat(timespec="seconds") if value["enabled"] else None
            store.conn.execute("INSERT INTO settings VALUES('schedule',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(result),));store.conn.commit()
            return result
        raise ValueError("配置类型无效")
    finally:store.close()


def feedback(body,actor):
    store=Store()
    try:return store.add_feedback(body["creative_id"],actor,body["decision"],body["reason"],body.get("outcome"))
    finally:store.close()


def relation_decision(pair_id,body,actor,*,withdraw=False):
    store=Store()
    try:
        if store.active_owner():raise ValueError('当前采集或研究结束后再修改事件关系')
        from . import tracking
        value=tracking.withdraw(store,pair_id,body.get('reason'),actor) if withdraw else tracking.decide(
            store,pair_id,body.get('status'),body.get('reason'),actor)
        scan(store);work.enqueue(store)
        return value
    finally:store.close()


def start_scheduler():
    global _thread
    if _thread and _thread.is_alive():return
    store=Store()
    try:
        import_failure_history(store)
        # Existing explicit preferences persist; initialize automatic operation only once.
        if not store.conn.execute("SELECT 1 FROM settings WHERE key='schedule'").fetchone():
            config={"enabled":True,"interval_minutes":60,"research":True,
                    "next_run_at":(datetime.now(timezone.utc)+timedelta(minutes=60)).isoformat(timespec="seconds")}
            store.conn.execute("INSERT INTO settings VALUES('schedule',?)",(dump(config),));store.conn.commit()
        if not store.conn.execute("SELECT 1 FROM settings WHERE key='automatic_work'").fetchone():
            store.conn.execute("INSERT INTO settings VALUES('automatic_work',?)",(dump({'interval_minutes':15,
              'next_run_at':(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(timespec='seconds')}),));store.conn.commit()
        if not store.conn.execute("SELECT 1 FROM settings WHERE key='automation_v13'").fetchone():
            with store.conn:
                store.conn.execute("INSERT OR REPLACE INTO settings VALUES('automatic_work',?)",(dump({'interval_minutes':3,'next_run_at':now_iso()}),))
                store.conn.execute("INSERT OR REPLACE INTO settings VALUES('automatic_screen',?)",(dump({'interval_minutes':1,'next_run_at':now_iso()}),))
                store.conn.execute("INSERT INTO settings VALUES('automation_v13',?)",(dump(now_iso()),))
    finally:store.close()
    _stop.clear()
    def loop():
        while not _stop.wait(30):
            store=Store()
            try:
                from .public_site import tick as publish_tick
                publish_tick(store)
                scheduler_tick(store)
            except Exception as error:
                # Preserve scheduler failures rather than silently swallowing them.
                store.conn.execute("INSERT INTO settings VALUES('scheduler_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump({"at":now_iso(),"type":type(error).__name__}),));store.conn.commit()
            finally:store.close()
    _thread=threading.Thread(target=loop,name="v3-scheduler",daemon=True);_thread.start()


def scheduler_tick(store,*,cycle_dispatch=None,screen_dispatch=None):
    """One testable automatic tick; no browser request is needed to start work."""
    cycle_dispatch=cycle_dispatch or start_cycle;screen_dispatch=screen_dispatch or start_screening
    config=schedule(store)
    if not config['enabled'] or store.active_owner():return 'idle'
    stamp=now_iso()
    if config.get('next_run_at') and config['next_run_at']<=stamp:
        cycle_dispatch(research=config['research'],live=True)
        config['next_run_at']=(datetime.now(timezone.utc)+timedelta(minutes=config['interval_minutes'])).isoformat(timespec='seconds')
        with store.conn:store.conn.execute("UPDATE settings SET value=? WHERE key='schedule'",(dump(config),))
        return 'collection'
    if not config['research'] or gate(store)['status']!='ready':return 'waiting_provider'
    ai=automatic_work(store)
    row=store.conn.execute("SELECT value FROM settings WHERE key='automatic_screen'").fetchone()
    screen=json.loads(row[0]) if row else {'interval_minutes':1,'next_run_at':stamp}
    screen_due=screen['next_run_at']<=stamp and unscreened_count(store)>0
    previous=store.conn.execute("SELECT value FROM settings WHERE key='last_automatic_kind'").fetchone()
    last=json.loads(previous[0]) if previous else None
    # A deep cycle can outlast its interval. Alternate when both are due so
    # the cheap screening queue cannot be starved by continuously overdue AI.
    if ai.get('next_run_at') and ai['next_run_at']<=stamp and has_automatic_work(store) and not (screen_due and last=='research'):
        cycle_dispatch(research=True,live=False)
        ai['next_run_at']=(datetime.now(timezone.utc)+timedelta(minutes=ai['interval_minutes'])).isoformat(timespec='seconds')
        with store.conn:
            store.conn.execute("UPDATE settings SET value=? WHERE key='automatic_work'",(dump(ai),))
            store.conn.execute("INSERT OR REPLACE INTO settings VALUES('last_automatic_kind',?)",(dump('research'),))
        return 'research'
    if screen_due:
        screen_dispatch()
        screen['next_run_at']=(datetime.now(timezone.utc)+timedelta(minutes=screen['interval_minutes'])).isoformat(timespec='seconds')
        with store.conn:
            store.conn.execute("INSERT OR REPLACE INTO settings VALUES('automatic_screen',?)",(dump(screen),))
            store.conn.execute("INSERT OR REPLACE INTO settings VALUES('last_automatic_kind',?)",(dump('screening'),))
        return 'screening'
    return 'idle'


def stop_scheduler():
    _stop.set()
    if _thread:_thread.join(timeout=1)
    from .opencode_zen import _runtime
    _runtime.close()
