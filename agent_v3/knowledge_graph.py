"""Incremental entity/event/claim graph. Evidence edges never imply truth or heat."""
import json
from datetime import datetime,timedelta,timezone

from agent_v2.store import dump,now_iso,stable_id
from . import graph_entities

VERSION='knowledge-graph-v3.17'
NEEDS={'find_players':'找共同兴趣的玩家','understand_play':'理解玩法与攻略','creation':'分享玩家创作',
       'payment_experience':'付费体验','performance':'性能与运行体验','discover_games':'发现适合自己的游戏'}


def initialize(store):
    store.conn.executescript('''
      CREATE TABLE IF NOT EXISTS kg_entity(entity_id TEXT PRIMARY KEY,kind TEXT,name TEXT,origin TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS kg_alias(alias TEXT,entity_id TEXT,ambiguous INTEGER,PRIMARY KEY(alias,entity_id));
      CREATE TABLE IF NOT EXISTS kg_document(evidence_id TEXT PRIMARY KEY,source_version TEXT,indexed_at TEXT);
      CREATE TABLE IF NOT EXISTS kg_mention(mention_id TEXT PRIMARY KEY,evidence_id TEXT,source_version TEXT,entity_id TEXT,
        surface TEXT,status TEXT,payload TEXT);
      CREATE INDEX IF NOT EXISTS kg_mention_entity ON kg_mention(entity_id,evidence_id);
      CREATE TABLE IF NOT EXISTS kg_event(event_id TEXT PRIMARY KEY,canonical_id TEXT,topic_id TEXT,fingerprint TEXT,
        title TEXT,status TEXT,last_seen_at TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS kg_claim(claim_id TEXT PRIMARY KEY,event_id TEXT,evidence_id TEXT,source_version TEXT,
        text TEXT,status TEXT,version TEXT,run_id TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS kg_relation(relation_id TEXT PRIMARY KEY,subject_id TEXT,predicate TEXT,object_id TEXT,
        status TEXT,version TEXT,valid_from TEXT,payload TEXT);
      CREATE INDEX IF NOT EXISTS kg_relation_subject ON kg_relation(subject_id,predicate);
      CREATE TABLE IF NOT EXISTS kg_need(need_id TEXT PRIMARY KEY,event_id TEXT,label TEXT,text TEXT,status TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS kg_community(community_id TEXT PRIMARY KEY,fingerprint TEXT,state TEXT,computed_at TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS kg_community_summary(community_id TEXT,fingerprint TEXT,run_id TEXT,created_at TEXT,payload TEXT,
        PRIMARY KEY(community_id,fingerprint));
      CREATE TABLE IF NOT EXISTS kg_index_state(name TEXT PRIMARY KEY,version TEXT,updated_at TEXT,payload TEXT);
    ''')
    store.conn.execute('BEGIN IMMEDIATE')
    try:
        columns={r['name'] for r in store.conn.execute('PRAGMA table_info(kg_document)')}
        if 'index_version' not in columns:store.conn.execute('ALTER TABLE kg_document ADD COLUMN index_version TEXT')
        store.conn.commit()
    except BaseException:
        store.conn.rollback();raise
    graph_entities.seed(store)
    from .taptap_profile import PROFILE
    with store.conn:
        for capability in PROFILE['capabilities']:
            eid=stable_id('capability_',capability)
            store.conn.execute('INSERT OR IGNORE INTO kg_entity VALUES(?,?,?,?,?)',(eid,'TapTapCapability',capability,'product_profile',dump({
                'profile_version':PROFILE['version'],'verified_at':PROFILE['verified_at'],'sources':PROFILE['sources'],
                'scope':'平台能力；不证明具体游戏、入口、资源或上线状态'})))


def source(store,eid):
    rows=store.evidence([eid])
    if not rows:return None
    item={k:rows[0][k] for k in ('evidence_id','title','body','url','published_at','platform','kind','content_scope','last_seen_at','content_hash')}
    item['source_version']=stable_id('kg_source_',dump([item['content_hash'],item['url'],item['published_at']]))
    return item


def proof(item,quote):
    if not isinstance(quote,str) or len(quote.strip())<2 or quote not in item['title']+'\n'+item['body']:
        raise ValueError('图谱引用不在实际来源中')
    return {k:item[k] for k in ('evidence_id','source_version','content_hash','url','published_at')}|{'quote':quote}


def current(store,fact):
    item=source(store,fact.get('evidence_id'))
    return bool(item and item['source_version']==fact.get('source_version') and
                isinstance(fact.get('quote'),str) and fact['quote'] in item['title']+'\n'+item['body'])


def current_facts(store,facts):
    return bool(facts) and all(current(store,f) for f in facts)


def graph_version(store):
    row=store.conn.execute("SELECT version FROM kg_index_state WHERE name='graph'").fetchone()
    return row[0] if row else None


def relation(store,subject,predicate,object_id,status,facts,*,identity=None,extra=None):
    key=identity or stable_id('kg_rel_',dump([subject,predicate,object_id,facts,VERSION]))
    store.conn.execute('INSERT INTO kg_relation VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(relation_id) DO UPDATE SET status=excluded.status,payload=excluded.payload',
        (key,subject,predicate,object_id,status,VERSION,now_iso(),dump({'facts':facts,**(extra or {})})))
    return key


def index_document(store,item):
    registry_version=store.conn.execute("SELECT version FROM kg_index_state WHERE name='registry'").fetchone()[0]
    index_version=graph_entities.VERSION+':'+registry_version
    old=store.conn.execute('SELECT source_version,index_version FROM kg_document WHERE evidence_id=?',(item['evidence_id'],)).fetchone()
    if old and tuple(old)==(item['source_version'],index_version):return False
    text=item['title']+'\n'+item['body'][:12000]
    for mention in graph_entities.mentions(store,text):
        eid=(mention.get('entity') or {}).get('entity_id')
        key=stable_id('mention_',dump([item['evidence_id'],item['source_version'],mention['start'],mention['surface'],graph_entities.VERSION]))
        fact=proof(item,mention['quote'])
        store.conn.execute('INSERT OR IGNORE INTO kg_mention VALUES(?,?,?,?,?,?,?)',(key,item['evidence_id'],item['source_version'],eid,
            mention['surface'],mention['status'],dump({**mention,'facts':[fact]})))
        if eid:relation(store,'document:'+item['evidence_id'],'MENTIONS',eid,'source_attributed',[fact])
    store.conn.execute('INSERT OR REPLACE INTO kg_document(evidence_id,source_version,indexed_at,index_version) VALUES(?,?,?,?)',(item['evidence_id'],item['source_version'],now_iso(),index_version))
    return True


def store_claim(store,event_id,text,facts,run_id=None,status='source_attributed'):
    if not current_facts(store,facts):raise ValueError('图谱主张必须有当前来源逐字引用')
    key=stable_id('claim_',dump([event_id,text,facts,VERSION]))
    store.conn.execute('INSERT OR IGNORE INTO kg_claim VALUES(?,?,?,?,?,?,?,?,?)',
        (key,event_id,facts[0]['evidence_id'],facts[0]['source_version'],text,status,VERSION,run_id,dump({'facts':facts,'note':'有来源归属，不等于已独立核实'})))
    relation(store,event_id,'HAS_CLAIM',key,status,facts)
    return key


def ingest_interpretation(store,topic_id,fingerprint,value,run_id=None):
    """Reuse grounded AI interpretation; optional extraction adds no model call."""
    from .tracking import root_id
    mapped={r['evidence_id']:root_id(store,r['tracked_id']) for r in store.conn.execute('''SELECT t.* FROM tracked_member t
        JOIN topic_member m USING(evidence_id) WHERE m.topic_id=? AND m.active=1''',(topic_id,))}
    anchor=next(iter(mapped.values()),None)
    for row in store.conn.execute("SELECT evidence_id FROM research_link WHERE topic_id=? AND role='discussion'",(topic_id,)):
        if anchor:mapped[row[0]]=anchor
    for match in value.get('source_matches',[]):
        if match.get('relation')=='same_event' and anchor:
            for fact in match.get('facts',[]):mapped[fact['evidence_id']]=anchor
    for section in ('core','controversies'):
        for claim in value.get(section,[]):
            facts=[]
            for f in claim.get('facts',[]):
                item=source(store,f['evidence_id'])
                if item and f['quote'] in item['title']+'\n'+item['body']:facts.append(proof(item,f['quote']))
            for event_id in {mapped[f['evidence_id']] for f in facts if f['evidence_id'] in mapped}:
                store_claim(store,event_id,claim['text'],facts,run_id)
    knowledge=value.get('knowledge',{});entities=[]
    for mention in knowledge.get('entities',[]):
        facts=mention.get('facts',[])
        if not current_facts(store,facts) or not any(mention['surface'] in f['quote'] for f in facts):raise ValueError('AI 实体抽取必须引用包含实体原名的来源')
        linked=graph_entities.resolve(store,mention['surface'],' '.join(f['quote'] for f in facts))
        entity_id=(linked.get('entity') or {}).get('entity_id')
        if not entity_id:
            if mention['canonical_name']!=mention['surface']:
                raise ValueError('未注册实体不能凭模型猜测改名；保留来源原名')
            entity_id=stable_id('proposed_entity_',dump([mention['kind'],mention['surface'],facts[0]['evidence_id']]))
            store.conn.execute('INSERT OR IGNORE INTO kg_entity VALUES(?,?,?,?,?)',(entity_id,mention['kind'],mention['canonical_name'],'proposed',dump({'facts':facts,'scope':'未注册实体按来源分隔，尚未跨文档消歧'})))
        entities.append(entity_id)
        for fact in facts:
            key=stable_id('mention_',dump([entity_id,fact,VERSION]))
            store.conn.execute('INSERT OR IGNORE INTO kg_mention VALUES(?,?,?,?,?,?,?)',(key,fact['evidence_id'],fact['source_version'],entity_id,mention['surface'],
                'linked' if linked['status']=='linked' else 'proposed',dump({'facts':[fact],'kind':mention['kind'],'candidates':linked['candidates']})))
    for edge in knowledge.get('relations',[]):
        facts=edge['facts']
        if edge['predicate'] not in ('ABOUT','PUBLISHED','PARTICIPATES_IN','DISCUSSES','SOURCE_ASSERTS') or edge['target'] not in ('entity','event'):
            raise ValueError('未知或因果关系不允许写入')
        if not 0<=edge['subject_ref']<len(entities) or edge['target']=='entity' and not 0<=edge['object_ref']<len(entities):
            raise ValueError('关系引用不存在于本次实体抽取')
        subject=entities[edge['subject_ref']]
        target=entities[edge['object_ref']] if edge['target']=='entity' else mapped.get(facts[0]['evidence_id'])
        if not target or not current_facts(store,facts):raise ValueError('AI 关系没有事件身份或实际来源')
        relation(store,subject,edge['predicate'],target,'proposed',facts,extra={'note':'LLM抽取建议，尚待研究核查'})
    for need in knowledge.get('needs',[]):
        if need['label'] not in NEEDS:raise ValueError('未知需求标签')
        if not current_facts(store,need['facts']):raise ValueError('需求归纳缺少实际来源')
        for event_id in {mapped[f['evidence_id']] for f in need['facts'] if f['evidence_id'] in mapped}:
            key=stable_id('need_',dump([event_id,need['label'],need['text'],need['facts']]))
            store.conn.execute('INSERT OR IGNORE INTO kg_need VALUES(?,?,?,?,?,?)',(key,event_id,need['label'],need['text'],'inferred',dump({'facts':need['facts'],'note':'有限来源需求推断，不代表全部玩家'})))
            relation(store,event_id,'REVEALS_NEED',key,'inferred',need['facts'])
    for review in knowledge.get('relation_reviews',[]):
        row=store.conn.execute("SELECT * FROM kg_relation WHERE relation_id=? AND status='proposed'",(review['relation_id'],)).fetchone()
        if not row or not current_facts(store,json.loads(row['payload'])['facts']) or not current_facts(store,review['facts']):
            raise ValueError('待核查关系已变化或缺少当前引文')
        payload=json.loads(row['payload']);payload['review']={'reason':review['reason'],'facts':review['facts'],'run_id':run_id}
        status={'supported':'source_supported','contradicted':'contradicted','unresolved':'proposed'}[review['decision']]
        store.conn.execute('UPDATE kg_relation SET status=?,payload=? WHERE relation_id=?',(status,dump(payload),row['relation_id']))


def refresh(store,*,limit=1200):
    """Bounded incremental indexing; never calls AI and never labels size as heat."""
    from .tracking import root_id
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=168)).isoformat(timespec='seconds')
    rows=store.conn.execute('''SELECT DISTINCT e.evidence_id FROM evidence e JOIN tracked_member m USING(evidence_id)
        JOIN topic_member t USING(evidence_id) JOIN topic p USING(topic_id) WHERE t.active=1 AND p.eligible=1 AND p.last_seen_at>=?
        ORDER BY e.last_seen_at DESC,e.evidence_id LIMIT ?''',(cutoff,limit)).fetchall()
    added=0
    with store.conn:
        for row in rows:
            item=source(store,row[0]);added+=index_document(store,item)
            member=store.conn.execute('SELECT tracked_id FROM tracked_member WHERE evidence_id=?',(row[0],)).fetchone()
            event_id=root_id(store,member[0]);event=store.conn.execute('SELECT * FROM tracked_event WHERE tracked_id=?',(event_id,)).fetchone()
            topic=store.conn.execute('''SELECT t.topic_id,t.fingerprint FROM topic t JOIN topic_member m USING(topic_id)
                WHERE m.evidence_id=? AND m.active=1 ORDER BY t.first_seen_at,t.topic_id LIMIT 1''',(row[0],)).fetchone()
            interpreted=store.interpretation(topic['topic_id'],topic['fingerprint']) if topic else None
            payload=(interpreted or {}).get('payload',{});title=payload.get('headline') or event['title']
            versions={r[0]:source(store,r[0])['source_version'] for r in store.conn.execute('SELECT evidence_id FROM tracked_member WHERE tracked_id=?',(event_id,))}
            store.conn.execute('INSERT INTO kg_event VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET canonical_id=excluded.canonical_id,topic_id=excluded.topic_id,fingerprint=excluded.fingerprint,title=excluded.title,status=excluded.status,last_seen_at=excluded.last_seen_at,payload=excluded.payload',
                (event_id,event_id,topic['topic_id'],topic['fingerprint'],title,'interpreted' if payload.get('status')=='ready' else 'candidate',event['last_observed_at'],dump({'event_revision':event['revision'],'recency':payload.get('recency'),'source_versions':versions,
                    'historical_only':payload.get('freshness_assessment',{}).get('status')=='historical_only',
                    'note':'事件身份依据来源与关系核查；仅实体共现不能归并事件'})))
            for mention in store.conn.execute('SELECT * FROM kg_mention WHERE evidence_id=? AND source_version=? AND status=\'linked\'',(row[0],item['source_version'])):
                relation(store,event_id,'MENTIONS',mention['entity_id'],'source_attributed',json.loads(mention['payload'])['facts'])
            if interpreted:ingest_interpretation(store,topic['topic_id'],topic['fingerprint'],payload,interpreted['run_id'])
        # Only grounded, current explicit relations enter the useful event graph.
        for edge in store.conn.execute("SELECT * FROM event_relation WHERE status IN ('same','development','related')"):
            saved=store.conn.execute('SELECT payload FROM event_relation_basis WHERE pair_id=?',(edge['pair_id'],)).fetchone()
            value=json.loads(saved[0]) if saved else {'facts':[],'direction':'unknown'}
            facts=[]
            for f in value['facts']:
                item=source(store,f['evidence_id'])
                if item and all(item.get(k)==f.get(k) for k in ('content_hash','url','published_at')) and f['quote'] in item['title']+'\n'+item['body']:facts.append(proof(item,f['quote']))
            left,right=root_id(store,edge['left_id']),root_id(store,edge['right_id'])
            if edge['status']=='same' or left==right:continue
            predicate='FOLLOWED_BY' if edge['status']=='development' and value.get('direction')!='unknown' else 'DEVELOPMENT' if edge['status']=='development' else 'RELATED_TO'
            if value.get('direction')=='right':left,right=right,left
            # Compare original sides; directional swaps must not swap revisions.
            versions=[store.conn.execute('SELECT revision FROM tracked_event WHERE tracked_id=?',(root_id(store,e),)).fetchone()[0] for e in (edge['left_id'],edge['right_id'])]
            status='source_supported' if len(facts)==2 and current_facts(store,facts) and versions==[edge['left_version'],edge['right_version']] else 'legacy_unquoted'
            relation(store,left,predicate,right,status,facts,identity='relation:'+edge['pair_id'],extra={'pair_id':edge['pair_id'],'reason':edge['reason'],'direction':value.get('direction','unknown')})
        # Retain history, but deactivate withdrawn or stale relation assertions.
        for row in store.conn.execute("SELECT relation_id,payload FROM kg_relation WHERE relation_id LIKE 'relation:%'").fetchall():
            pair=store.conn.execute('SELECT status FROM event_relation WHERE pair_id=?',(row['relation_id'][9:],)).fetchone()
            if not pair or pair[0] not in ('development','related'):store.conn.execute("UPDATE kg_relation SET status='stale' WHERE relation_id=?",(row['relation_id'],))
    from .graph_retrieval import rebuild_communities
    communities=rebuild_communities(store)
    signature=[list(r) for r in store.conn.execute('SELECT evidence_id,source_version,index_version FROM kg_document ORDER BY evidence_id')]
    signature.append([list(r) for r in store.conn.execute('SELECT relation_id,status FROM kg_relation ORDER BY relation_id')])
    signature.append([list(r) for r in store.conn.execute('SELECT event_id,fingerprint,title,status FROM kg_event ORDER BY event_id')])
    signature.append([list(r) for r in store.conn.execute('SELECT community_id,fingerprint,state FROM kg_community ORDER BY community_id')])
    version=stable_id('graph_',dump(signature))
    with store.conn:store.conn.execute("INSERT OR REPLACE INTO kg_index_state VALUES('graph',?,?,?)",(version,now_iso(),dump({'indexed_documents':len(rows),'updated_documents':added,'communities':communities,'limit':limit,'method':VERSION})))
    return {'updated_documents':added,'indexed_documents':len(rows),'communities':communities,'graph_version':version,'limit':limit}
