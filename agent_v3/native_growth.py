"""Plan then produce; preserve a real AI plan when production is interrupted."""
from .contracts import run_task
import copy
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator,ValidationError
from agent_v2.store import dump,now_iso,stable_id
from .tools import GrowthTools
from .opencode_zen import StructuredDeliveryError
from .model import task_metadata
from .task_packets import topic_packet,PLAN_SCHEMA,PRODUCTION_SCHEMA,PLAN_SYSTEM,PRODUCTION_SYSTEM,references


def run(store,run_id,model,topic_id,*,timeout_seconds=360):
    tools=GrowthTools(store,0);tools.run_id=run_id;tools.assigned_topic=topic_id
    topic=tools.call('read_topic',{'topic_id':topic_id})
    from .risk import require_growth,PROMPT
    safety=require_growth(store,topic_id,topic['fingerprint'])
    record=topic.get('intelligence')
    if not record or record['payload']['opportunity']['decision']!='opportunity':
        raise ValueError('制作增长创意需要主 Agent 的机会判断；情报与素材可为空')
    intel=record['payload'];context=store.context()
    if getattr(model,'supports_main_agent',False):
        from .freshness import delivery_current
        current=delivery_current(store,topic,(topic.get('interpretation') or {}).get('payload',{}))
        if not current['business_eligible']:raise ValueError('创意时机已过期或时间依据不足')
    packet=topic_packet(topic,context)
    from .runtime_guard import basis
    expected=basis(store,topic_id,topic['fingerprint'],packet=packet)
    store._delivery_basis=expected
    packet['intelligence']=intel;packet['business_decision']=intel;packet['hotspot_interpretation']=(topic.get('interpretation') or {}).get('payload');packet['risk_assessment']=safety
    packet['optional_artifacts_note']='游戏情报和已保存素材可为空；根据热点、来源和机会判断形成创意，不依赖这些产物'
    packet['current_time']=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    # Select relevant existing materials, rather than send every prior script.
    ids={e['evidence_id'] for e in topic['evidence']}
    materials=[m for m in store.usable_materials(limit=60) if ids.intersection(m['evidence_ids'])][:8]
    tools.read_materials.update(m['material_id'] for m in materials)
    packet['reusable_materials']=[{'ref':i,**{k:m[k] for k in ('material_id','kind','origin','title','content','rights_status')}} for i,m in enumerate(materials)]
    schema=copy.deepcopy(PLAN_SCHEMA)
    schema['properties']['reuse_material_refs']=references(len(materials),maximum=5)
    schema['properties']['reuse_source_asset_refs']=references(len(packet['source_assets']),maximum=5)
    context_version=stable_id('context_',dump(context))
    draft_id=stable_id('draft_',dump([topic_id,topic['fingerprint'],context_version,model.model,'plan-v3.13.1']))
    started=time.monotonic();usage={}

    def task(stage,input,schema,system):
        for attempt in range(2):
            store.heartbeat(run_id)
            remaining=timeout_seconds-(time.monotonic()-started)
            if remaining<5:raise TimeoutError('创意任务时间预算用完')
            invalid=None
            try:response=run_task(model,stage,input,schema,system,timeout_seconds=remaining)
            except StructuredDeliveryError as error:response=error.response;invalid=error
            store.step(run_id,'opencode_task' if response['transport']=='opencode-agent' else 'model_task',task_metadata(response))
            if response.get('reasoning_effort'):store.step(run_id,'reasoning_setting',{'effort':response['reasoning_effort']})
            for key,value in response['usage'].items():usage[key]=usage.get(key,0)+value
            try:
                if invalid:raise invalid
                Draft202012Validator(schema).validate(response['result'])
                if stage=='creative_production' and any(s in response['result']['copy'] for s in ('上线前配置','经确认的TapTap入口','经确认的入口','待确认入口')):
                    raise ValueError('用户文案不能包含配置入口的制作过程；以 {{TapTap承接链接}} 占位，制作条件写 rights_notes')
                return response['result']
            except (ValueError,ValidationError) as error:
                store.step(run_id,'validation_error',{'name':stage,'error':str(error)[:600]})
                input['validation_errors']=[str(error)[:1000]]
                if attempt:raise

    try:
        cached=store.conn.execute('SELECT * FROM creative_draft WHERE draft_id=?',(draft_id,)).fetchone()
        if cached:
            plan=json.loads(cached['payload']);Draft202012Validator(schema).validate(plan)
            store.step(run_id,'creative_plan_reused',{'draft_id':draft_id,'original_run_id':cached['run_id']})
        else:
            plan=task('creative_plan',packet,schema,PLAN_SYSTEM+PROMPT)
            with store.delivery(run_id,expected,job=store._active_job):
                store.conn.execute('INSERT INTO creative_draft VALUES(?,?,?,?,?,?,?,?,?)',
                    (draft_id,topic_id,topic['fingerprint'],context_version,model.model,run_id,now_iso(),'planned',dump(plan)))
            store.step(run_id,'creative_plan_saved',{'draft_id':draft_id,'title':plan['title'],'status':'production_pending'})
        production=task('creative_production',{'plan':plan,'intelligence':intel,'source_scope':packet['evidence'],
                         'business_context':context,'current_time':packet['current_time'],'risk_assessment':safety},PRODUCTION_SCHEMA,PRODUCTION_SYSTEM+PROMPT)
        source_ids=list(dict.fromkeys(f['evidence_id'] for f in intel['facts']))
        creative={k:v for k,v in plan.items() if k not in ('reuse_material_refs','reuse_source_asset_refs')}
        creative.update({'copy':production['copy'],
            'risk_assessment':safety,
            'materials':[{'description':'热点原文作为创作依据；制作画面另行确认','evidence_id':eid,
                          'rights_status':'用于研究参考，商业使用与授权待核查'} for eid in source_ids],
            'reuse_material_ids':[materials[i]['material_id'] for i in plan['reuse_material_refs']],
            'reuse_source_asset_ids':[packet['source_assets'][i]['asset_id'] for i in plan['reuse_source_asset_refs']],
            'deliverables':[{'kind':kind,'origin':'original','title':plan['title']+label,'content':production[field],
                             'tags':[],'evidence_ids':source_ids,'rights_status':production['rights_notes']}
                            for kind,label,field in [('copy',' / 传播文案','copy'),('script',' / 短视频脚本','script')]],
            'growth_hypothesis':intel['opportunity'].get('hypothesis',''),
            'validation_plan':intel['opportunity'].get('validation_plan',''),
            'prerequisites':intel['opportunity'].get('prerequisites',[]),'readiness':'unreviewed_draft'})
        opportunity=intel['opportunity']
        assessment={'title':topic['title'],'verdict':'related','topic_id':topic_id,'topic_fingerprint':topic['fingerprint'],
            'evidence_ids':source_ids,'summary':intel['summary'],'facts':intel['facts'],
            'inferences':[opportunity['reason'],opportunity.get('hypothesis','增长假设待验证')],
            'unknowns':intel['unknowns'],'taptap_fit':{'audience':plan['audience'],'motivation':opportunity['audience_need'],'reason':opportunity['taptap_bridge']},
            'opportunity':{'audience_need':opportunity['audience_need'],'spread_hook':plan['hook'],
                           'taptap_bridge':opportunity['taptap_bridge'],'why_now':plan['timing'],'growth_goal':plan['growth_goal']},
            'creatives':[creative]}
        if topic.get('event_id'):assessment['event_id']=topic['event_id']
        result={'summary':plan['title'],'assessments':[assessment]}
        assessments=tools.validate(result)
        with store.delivery(run_id,expected):
            event_ids=store.save_assessments(run_id,assessments)
            store.conn.execute("UPDATE creative_draft SET status='completed' WHERE draft_id=?",(draft_id,))
        store.step(run_id,'creative_production_saved',{'draft_id':draft_id,'event_ids':event_ids,'review_status':'unreviewed'})
        store.finish(run_id,'completed',result={**result,'event_ids':event_ids,'prompt_version':'growth-pack-v3.13.1',
                     'review_status':'unreviewed','transport':getattr(model,'transport','opencode-agent')},model=model.model,usage=usage)
    except Exception as error:
        with store.conn:store.conn.execute("UPDATE creative_draft SET status='production_deferred' WHERE draft_id=? AND status<>'completed'",(draft_id,))
        store.step(run_id,'failed',{'type':type(error).__name__,'error':str(error)[:600],'session_id':model.last_session})
        store.finish(run_id,'failed',error=str(error)[:600],model=model.model,usage=usage)
    return store.get_run(run_id)
