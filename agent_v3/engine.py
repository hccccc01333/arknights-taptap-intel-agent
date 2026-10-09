from .contracts import run_task
from agent_v2.engine import run_agent as bounded_agent, SYSTEM as EVIDENCE_SYSTEM
from agent_v2.store import dump
import time
from .tools import GrowthTools
from .intelligence import native_contract
from .opencode_zen import StructuredDeliveryError

SYSTEM=EVIDENCE_SYSTEM+"""
你运行的是 V3，按 discover_topics → read_topic → 情报提炼 → 素材积累/复用 → 增长创意包完成工作。
优先扫描跨领域候选，选有价值的机会；没有价值时如实保存不跟进判断，不强行凑创意。
读取已有 insight 时复用；原文有新版本时再 record_insight。情绪、需求、传播机制属于推断，不冒充评论统计。
每个判断关联已读的 topic_id 和 fingerprint；同一事件更新原 event_id，避免每次新建。
同标题不证明是同一事件；若是新的发生，省略 event_id 并用 new_event_reason 解释对象、时间或行为差异。
opportunity 解释用户需求、传播吸引点、TapTap 的价值桥梁、行动时机和增长目标。
每个创意包含具体 hook、站外 distribution、进入 TapTap 的 journey 与资源条件。
deliverables 交付可直接编辑的文案(copy)，以及短视频脚本(script)或视觉制作方案(visual_brief)，可附制作清单。
素材库有可复用素材时搜索读取，reuse_material_ids 指向实际读过素材；没有积累时填空列表。
每项素材标明 origin：原文逐字摘录是 source_quote/source，模式分析是 inferred，创作文案和脚本是 original。
原创的 evidence_ids 是创作依据，不代表借用了原视频或取得来源授权；rights_status 说明制作和审核条件。
预算不足时优先完成一个话题的一条具体创意及其素材，不要用大量空泛提案占满字段。
本阶段使用前序独立积累的 intelligence 和 source_assets；复用已有情报，不为填满字段重复分析。
来源素材可 search_source_assets，reuse_source_asset_ids 记录实际阅读并用于本创意的素材引用。
不要将跨领域热点统一套成“推荐几款游戏”。先找到人群愿意参与或传播的具体理由，再设计 TapTap 能承接的增长动作。
"""

TASK="主动扫描跨领域待研究候选，识别用户需求与传播吸引点，提炼可复用情报和素材，交付与 TapTap 相关的具体增长创意包。已有话题跟踪变化，已有素材按需求复用。"


def run_agent(store,run_id,model=None,*,assigned_topic=None,**budgets):
    if assigned_topic:
        from .risk import require_growth
        topic=store.conn.execute('SELECT fingerprint FROM topic WHERE topic_id=?',(assigned_topic,)).fetchone()
        if not topic:raise ValueError('创意话题不存在')
        require_growth(store,assigned_topic,topic[0])
    from .runtime_guard import LeaseLost
    own_lease=store.active_owner()!=run_id
    if own_lease:
        if not store.acquire(run_id,ttl=1200):raise LeaseLost('创意运行租约不可用')
        store._active_job=None
        if assigned_topic:
            from . import work
            work.enqueue(store,topic_ids=[assigned_topic])
            # An explicitly assigned maintenance invocation can resume a deferred
            # draft; the automatic scheduler continues to respect retry_at.
            with store.conn:store.conn.execute("UPDATE work_item SET retry_at=NULL WHERE topic_id=? AND stage='creative' AND status='deferred'",(assigned_topic,))
            store._active_job=work.claim(store,run_id,'creative',topic_id=assigned_topic)
    try:return _run_agent(store,run_id,model,assigned_topic=assigned_topic,**budgets)
    finally:
        if own_lease:store.release(run_id)


def _run_agent(store,run_id,model=None,*,assigned_topic=None,**budgets):
    store._delivery_basis=None
    if assigned_topic:
        from .risk import require_growth
        topic=store.conn.execute('SELECT fingerprint FROM topic WHERE topic_id=?',(assigned_topic,)).fetchone()
        if not topic:raise ValueError('创意话题不存在')
        require_growth(store,assigned_topic,topic[0])
    if getattr(model,"supports_tasks",False):
        return native_task(store,run_id,model,assigned_topic,timeout_seconds=budgets.get("timeout_seconds",360))
    def tools_factory(database,network_budget):
        tools=GrowthTools(database,network_budget);tools.run_id=run_id;tools.assigned_topic=assigned_topic
        return tools
    from .risk import PROMPT
    return bounded_agent(store,run_id,model,tools_factory=tools_factory,system_prompt=SYSTEM+PROMPT,
                         prompt_version="growth-pack-v3.2",manage_lease=store.active_owner()!=run_id,**budgets)


def native_task(store,run_id,model,topic_id,*,timeout_seconds=360):
    """OpenCode executes the entire creative job; V3 validates and stores it."""
    if not topic_id:raise ValueError("OpenCode 创意任务需要已分配的话题")
    if getattr(model,'compact_tasks',False):
        from .native_growth import run
        return run(store,run_id,model,topic_id,timeout_seconds=timeout_seconds)
    tools=GrowthTools(store,0);tools.run_id=run_id;tools.assigned_topic=topic_id
    topic=tools.call("read_topic",{"topic_id":topic_id})
    from .risk import require_growth
    require_growth(store,topic_id,topic['fingerprint'])
    materials=tools.call("search_materials",{"query":"","kind":""})["materials"]
    packet={"task":store.get_run(run_id)["task"],"topic":topic,"business_context":store.context(),
            "evidence":store.evidence(sorted(tools.read_ids)),"reusable_materials":materials,
            "delivery_context":tools.delivery_context()}
    from .runtime_guard import basis
    store._delivery_basis=basis(store,topic_id,topic['fingerprint'],packet=packet)
    schema=next(d["function"]["parameters"] for d in tools.definitions if d["function"]["name"]=="finish_research")
    grounded,packet["quote_candidates"]=native_contract(topic,topic.get("source_assets") or [])
    assessment=schema["properties"]["assessments"]
    assessment["maxItems"]=1
    fields=assessment["items"]["properties"]
    for key in ("topic_id","topic_fingerprint","facts"):fields[key]=grounded["properties"][key]
    creative=fields["creatives"];creative["maxItems"]=1
    for key,ids in (("reuse_material_ids",sorted(tools.read_materials)),("reuse_source_asset_ids",sorted(tools.read_source_assets))):
        creative["items"]["properties"][key]={"type":"array","items":({"type":"string","enum":ids} if ids else {"type":"string"}),"maxItems":len(ids)}
    started=time.monotonic();usage={}
    try:
        for attempt in range(2):
            store.heartbeat(run_id)
            remaining=timeout_seconds-(time.monotonic()-started)
            if remaining<5:raise TimeoutError("创意任务时间预算用完")
            delivery_error=None
            from .risk import PROMPT
            try:response=run_task(model,"creative",packet,schema,SYSTEM+PROMPT,timeout_seconds=remaining)
            except StructuredDeliveryError as error:
                response=error.response;delivery_error=error
            store.step(run_id,"opencode_task",{k:response[k] for k in ("model","usage","seconds","transport","session_id","input_file")})
            if response.get("reasoning_effort"):store.step(run_id,"reasoning_setting",{"effort":response["reasoning_effort"]})
            for key,value in response["usage"].items():usage[key]=usage.get(key,0)+value
            try:
                if delivery_error:raise delivery_error
                assessments=tools.validate(response["result"])
            except (ValueError,TypeError,KeyError) as error:
                store.step(run_id,"validation_error",{"name":"native_creative_delivery","error":str(error)})
                packet["validation_errors"]=[str(error)]
                if attempt:raise
                continue
            event_ids=store.save_assessments(run_id,assessments)
            store.finish(run_id,"completed",result={"summary":response["result"]["summary"],"event_ids":event_ids,
                "assessments":assessments,"prompt_version":"growth-pack-v3.2","review_status":"unreviewed",
                "transport":"opencode-agent"},model=model.model,usage=usage)
            return store.get_run(run_id)
    except Exception as error:
        store.step(run_id,"failed",{"type":type(error).__name__,"error":str(error)[:600],"session_id":model.last_session})
        store.finish(run_id,"failed",error=str(error)[:600],model=model.model,usage=usage)
        return store.get_run(run_id)
