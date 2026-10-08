"""Revise a saved plan's consumer copy without rescreening or inflating idea counts."""
import copy
import json
import time
from agent_v2.store import dump,now_iso,stable_id
from .main_agent import task
from .task_packets import PLAN_SCHEMA,PRODUCTION_SCHEMA,PRODUCTION_SYSTEM
from .tools import GrowthTools


def revise(store,run_id,model,creative_id):
    row=store.conn.execute('SELECT * FROM creative WHERE creative_id=?',(creative_id,)).fetchone()
    if not row:raise ValueError('待修订创意不存在')
    old=json.loads(row['payload']);event=store.get_event(row['event_id']);assessment=store.creative_basis(row)
    tools=GrowthTools(store,0);tools.run_id=run_id;tools.assigned_topic=assessment['topic_id']
    topic=tools.call('read_topic',{'topic_id':assessment['topic_id']})
    from .risk import require_growth,PROMPT
    require_growth(store,topic['topic_id'],topic['fingerprint'])
    if topic['fingerprint']!=assessment['topic_fingerprint']:raise ValueError('来源版本已变化，须重新判断机会')
    from .freshness import delivery_current
    if not delivery_current(store,topic,(topic.get('interpretation') or {}).get('payload',{}))['business_eligible']:
        raise ValueError('事件时效已过，不能沿用计划修订宣传内容')
    plan={k:old[k] for k in PLAN_SCHEMA['properties'] if k in old}
    packet={'plan':plan,'verified_source_quotes':assessment['facts'],'business_context':store.context(),
            'unknowns':assessment['unknowns'],'current_time':now_iso()}
    started=time.monotonic()
    for attempt in range(2):
        production,_=task(store,run_id,model,'creative_production',packet,PRODUCTION_SCHEMA,PRODUCTION_SYSTEM+PROMPT,
                          timeout_seconds=max(5,180-(time.monotonic()-started)))
        if not any(s in production['copy'] for s in ('上线前配置','经确认的TapTap入口','经确认的入口','待确认入口')):break
        packet['validation_errors']=['用户文案不能写入口配置等制作过程，制作条件写 rights_notes']
        if attempt:raise ValueError('用户文案仍包含制作过程，旧稿保留')
    revised=copy.deepcopy(old);revised['copy']=production['copy']
    for d in revised['deliverables']:
        if d['kind'] in ('copy','script'):
            d['content']=production['copy' if d['kind']=='copy' else 'script'];d['rights_status']=production['rights_notes']
            tools.validate_asset(d,tools.read_ids)
    revised['production_revision_run']=run_id;revised['review_status']='unreviewed'
    new_assessment=copy.deepcopy(event['assessment'])
    matches=[c for c in new_assessment['creatives'] if c['title']==old['title'] and c.get('copy')==old['copy']]
    if len(matches)!=1:raise ValueError('当前事件中的创意版本不一致，未更新')
    matches[0].update({k:revised[k] for k in ('copy','deliverables','production_revision_run','review_status')})
    with store.conn:
        current=store.conn.execute('SELECT payload FROM creative WHERE creative_id=?',(creative_id,)).fetchone()
        if current[0]!=row['payload']:raise ValueError('创意已由另一任务更新，未覆盖')
        store.conn.execute('INSERT OR IGNORE INTO creative_content_revision VALUES(?,?,?,?,?,?)',
            (stable_id('creative_revision_',dump(dict(row))),creative_id,row['event_id'],row['run_id'],row['created_at'],row['payload']))
        store.conn.execute('UPDATE creative SET payload=?,run_id=?,created_at=? WHERE creative_id=?',
            (dump(revised),run_id,now_iso(),creative_id))
        store.conn.execute('UPDATE event SET assessment=?,updated_at=? WHERE event_id=?',(dump(new_assessment),now_iso(),row['event_id']))
        store.conn.execute('INSERT INTO event_version VALUES(?,?,?,?)',(row['event_id'],run_id,now_iso(),dump(new_assessment)))
        for d in revised['deliverables']:
            store.save_material(d,run_id,event_id=row['event_id'],creative_id=creative_id)
    store.step(run_id,'creative_copy_revised',{'creative_id':creative_id,'previous_run_id':row['run_id'],
        'note':'沿用已保存计划与来源，仅修订制作文案/脚本；旧稿保留，不计新增创意'})
    return {'creative_id':creative_id,'title':revised['title'],'copy':revised['copy']}
