"""Evidence-backed reputation policy shared by prompts, scheduling and delivery."""
import json
from agent_v2.store import dump,now_iso,stable_id

POLICY_VERSION='hotspot-risk-v3.12'
PROMPT='''
先判断事件正负面与品牌传播风险，区分事件属性和某一条评论的情绪。polarity=positive/neutral/negative/mixed/unknown；level=low/medium/high/unknown。依据实际来源写理由和引文索引。
负面包括事故灾害、违法侵害、玩家受损、恶性争议、群体对立、针对人或品牌的嘲讽批判等；混合或判断不清先观察。孤立的差评不自动把正常游戏发布判为负面，不能靠正面措辞洗白负面事件。
负面、混合或风险未明不生成任何增长创意，也不能改名、反转、去掉奖金或换成普通玩家后继续蹭该事件。只有明确正面或正常中性且风险低才可考虑创意。
游戏情报保留负面风吹草动，用中立监测语言，区分事实、传言和影响假设；不得渲染对立或改写成营销机会。
素材限于可安全使用的表达和原创游戏场景。负面事件中的怒气、嘲讽、羞辱或受损经历只作内部研究资料，不进入可传播素材库；通用形式也不能借改编绕过负面限制。'''


def schema(count):
    from .task_packets import object_schema,references
    return object_schema({'polarity':{'enum':['positive','neutral','negative','mixed','unknown']},
        'level':{'enum':['low','medium','high','unknown']},'reason':{'type':'string','minLength':4,'maxLength':300},
        'basis_refs':references(count,maximum=3)})


def grounded(value,packet):
    result={k:value[k] for k in ('polarity','level','reason')}
    quotes=packet['quote_candidates'];sources={e['evidence_id']:e for e in packet['evidence']}
    result['facts']=[{k:quotes[r][k] for k in ('evidence_id','quote')} for r in value['basis_refs']]
    if result['polarity']!='unknown' and not result['facts']:raise ValueError('明确的正负面判断须有来源依据')
    if any(sources[f['evidence_id']]['role']=='background_unverified' for f in result['facts']):
        raise ValueError('未核对的搜索命中不能作为正负面判断依据')
    return result


def combine(child,main):
    ranks={'positive':0,'neutral':0,'unknown':1,'mixed':2,'negative':3}
    levels={'low':0,'unknown':1,'medium':2,'high':3}
    # Keep only two bounded public assessments, not a growing nested history.
    values=[{k:v[k] for k in ('polarity','level','reason','facts') if k in v} for v in (child,main) if isinstance(v,dict)]
    if not values:return {'polarity':'unknown','level':'unknown','reason':'正负面与风险尚未评估','facts':[]}
    worst=max(values,key=lambda v:ranks.get(v.get('polarity'),1))
    return {**worst,'level':max((v.get('level','unknown') for v in values),key=lambda v:levels.get(v,1)),
            'assessments':values}


def save(store,topic_id,fingerprint,run_id,value,stage='business_main'):
    p={**value,'policy_version':POLICY_VERSION,'stage':stage}
    old=store.conn.execute('SELECT * FROM topic_risk WHERE topic_id=? AND fingerprint=?',(topic_id,fingerprint)).fetchone()
    if stage=='research_child' and old:
        previous=json.loads(old['payload'])
        if previous.get('policy_version')==POLICY_VERSION and previous.get('polarity') in ('negative','mixed'):
            p={**combine(previous,value),'policy_version':POLICY_VERSION,'stage':stage}
    with store.conn:
        if old and json.loads(old['payload'])!=p:
            store.conn.execute('INSERT OR IGNORE INTO topic_risk_revision VALUES(?,?,?,?,?,?)',
                (stable_id('risk_revision_',dump(dict(old))),topic_id,fingerprint,old['run_id'],old['created_at'],old['payload']))
        store.conn.execute('INSERT INTO topic_risk VALUES(?,?,?,?,?) ON CONFLICT(topic_id,fingerprint) DO UPDATE SET run_id=excluded.run_id,created_at=excluded.created_at,payload=excluded.payload',
            (topic_id,fingerprint,run_id,now_iso(),dump(p)))
    return policy(store,topic_id,fingerprint)


def verdict(p):
    allowed=p.get('policy_version')==POLICY_VERSION and p.get('stage') in ('business_main','safety_review') and p.get('polarity') in ('positive','neutral') and p.get('level')=='low'
    return {**p,'growth_allowed':allowed,'material_allowed':allowed,
        'gate_reason':p['reason'] if not allowed else '正面或正常中性，已评估为低风险；增长效果仍需验证'}


def policy(store,topic_id,fingerprint):
    row=store.conn.execute('SELECT payload FROM topic_risk WHERE topic_id=? AND fingerprint=?',(topic_id,fingerprint)).fetchone()
    p=json.loads(row[0]) if row else {'polarity':'unknown','level':'unknown','reason':'尚未完成正负面与传播风险评估','facts':[]}
    return verdict(p)


def require_growth(store,topic_id,fingerprint):
    p=policy(store,topic_id,fingerprint)
    if not p['growth_allowed']:raise ValueError('热点禁止生成增长创意：'+p['gate_reason'])
    return p
