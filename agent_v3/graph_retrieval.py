"""Bounded Local/Global graph context; communities are research themes, not heat."""
from collections import defaultdict
from datetime import datetime,timedelta,timezone
import json
import math

from agent_v2.store import dump,now_iso,stable_id
from . import knowledge_graph as kg,graph_entities


def active_events(store,hours=168):
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=hours)).isoformat(timespec='seconds')
    rows=store.conn.execute('''SELECT k.*,v.revision AS tracked_revision FROM kg_event k JOIN tracked_event v ON v.tracked_id=k.event_id
        JOIN topic t ON t.topic_id=k.topic_id WHERE v.merged_into IS NULL AND t.eligible=1
        AND k.fingerprint=t.fingerprint AND k.last_seen_at>=? ORDER BY k.last_seen_at DESC,k.event_id LIMIT 1200''',(cutoff,))
    result={}
    for row in rows:
        value=dict(row);payload=json.loads(row['payload']);versions=payload.get('source_versions',{})
        if payload.get('event_revision')!=row['tracked_revision']:continue
        if payload.get('historical_only'):continue
        if any(not (s:=kg.source(store,eid)) or s['source_version']!=version for eid,version in versions.items()):continue
        result[row['event_id']]={**value,'payload':payload}
    return result


def event_edges(store,events):
    rows=store.conn.execute("SELECT * FROM kg_relation WHERE predicate IN ('FOLLOWED_BY','DEVELOPMENT','RELATED_TO') AND status='source_supported'")
    result=[]
    for r in rows:
        value=dict(r);value['payload']=json.loads(r['payload'])
        if value['payload'].get('pair_id'):
            pair=store.conn.execute('SELECT status FROM event_relation WHERE pair_id=?',(value['payload']['pair_id'],)).fetchone()
            if not pair or pair[0] not in ('development','related'):continue
        if r['subject_id'] in events and r['object_id'] in events and kg.current_facts(store,value['payload']['facts']):result.append(value)
    return result


def rebuild_communities(store):
    import networkx as nx
    events=active_events(store);graph=nx.Graph();graph.add_nodes_from(events)
    for edge in event_edges(store,events):graph.add_edge(edge['subject_id'],edge['object_id'],weight=3.0,reason='grounded_event_relation')
    # Sharing a game/platform/company alone never creates a community edge.
    # Specific characters/activities require current, fully grounded linking.
    shared=defaultdict(set)
    for r in store.conn.execute('''SELECT r.*,e.kind FROM kg_relation r JOIN kg_entity e ON e.entity_id=r.object_id
        WHERE r.predicate='MENTIONS' AND r.status='source_attributed' AND e.kind IN ('CHARACTER','ACTIVITY','VERSION')'''):
        if r['subject_id'] in events and kg.current_facts(store,json.loads(r['payload'])['facts']):shared['entity:'+r['object_id']].add(r['subject_id'])
    for r in store.conn.execute('SELECT * FROM kg_need'):
        if r['event_id'] in events and kg.current_facts(store,json.loads(r['payload'])['facts']):shared['need:'+r['label']].add(r['event_id'])
    for label,members in sorted(shared.items()):
        # Common global labels and massive groups are weak signals. Retain the
        # individual needs but do not connect the whole corpus through one hub.
        if len(members)<2 or len(members)>24:continue
        ids=sorted(members);weight=1/math.log2(2+len(ids))
        for i,left in enumerate(ids):
            for right in ids[i+1:]:
                old=graph.get_edge_data(left,right,{}).get('weight',0)
                graph.add_edge(left,right,weight=old+weight,reason=label)
    groups=nx.algorithms.community.greedy_modularity_communities(graph,weight='weight') if graph.number_of_edges() else []
    groups=[sorted(g) for g in groups if len(g)>=2];groups.sort(key=lambda x:(-len(x),x))
    with store.conn:
        store.conn.execute("UPDATE kg_community SET state='superseded' WHERE state='active'")
        for members in groups[:40]:
            links=[e for e in event_edges(store,events) if e['subject_id'] in members and e['object_id'] in members]
            labels=[label[5:] for label,items in shared.items() if label.startswith('need:') and len(items.intersection(members))>=2]
            facts=[]
            for edge in links:facts.extend(edge['payload']['facts'])
            for row in store.conn.execute('SELECT * FROM kg_need'):
                if row['event_id'] in members and row['label'] in labels and kg.current_facts(store,json.loads(row['payload'])['facts']):facts.extend(json.loads(row['payload'])['facts'])
            for row in store.conn.execute('SELECT * FROM kg_claim'):
                if row['event_id'] in members and kg.current_facts(store,json.loads(row['payload'])['facts']):facts.extend(json.loads(row['payload'])['facts'])
            for row in store.conn.execute("SELECT * FROM kg_relation WHERE predicate='MENTIONS' AND status='source_attributed'"):
                if row['subject_id'] in members and kg.current_facts(store,json.loads(row['payload'])['facts']):facts.extend(json.loads(row['payload'])['facts'])
            facts=list({dump(f):f for f in facts}.values())[:16]
            identity=stable_id('community_',dump(members))
            version=stable_id('community_version_',dump([[events[e]['fingerprint'] for e in members],facts,labels,[[e['relation_id'],e['status']] for e in links]]))
            headline='、'.join(kg.NEEDS.get(label,label) for label in labels[:2]) or '有依据关联的事件主题'
            payload={'event_ids':members,'headline':headline,'one_line':f'{len(members)}个独立事件提供共同主题线索，需分别核查发生语境。',
                'need_labels':labels,'facts':facts,'method':'networkx_greedy_modularity','summary_kind':'structured_index',
                'note':'社区大小不代表热度；事件不因属于同一主题而合并。'}
            store.conn.execute('INSERT INTO kg_community VALUES(?,?,?,?,?) ON CONFLICT(community_id) DO UPDATE SET fingerprint=excluded.fingerprint,state=excluded.state,computed_at=excluded.computed_at,payload=excluded.payload',
                (identity,version,'active',now_iso(),dump(payload)))
    return len(groups[:40])


def event_context(store,event,*,claim_limit=4):
    claims=[]
    for r in store.conn.execute('SELECT * FROM kg_claim WHERE event_id=? ORDER BY rowid DESC',(event['event_id'],)):
        p=json.loads(r['payload'])
        if kg.current_facts(store,p['facts']):claims.append({'claim_id':r['claim_id'],'text':r['text'],'status':r['status'],'facts':p['facts']})
        if len(claims)>=claim_limit:break
    subjects=[]
    for r in store.conn.execute('''SELECT r.payload,e.entity_id,e.kind,e.name FROM kg_relation r JOIN kg_entity e ON e.entity_id=r.object_id
        WHERE r.subject_id=? AND r.predicate='MENTIONS' AND r.status='source_attributed' ''',(event['event_id'],)):
        if kg.current_facts(store,json.loads(r['payload'])['facts']) and r['entity_id'] not in {e['entity_id'] for e in subjects}:
            subjects.append({k:r[k] for k in ('entity_id','kind','name')})
    needs=[]
    for row in store.conn.execute('SELECT * FROM kg_need WHERE event_id=?',(event['event_id'],)):
        p=json.loads(row['payload'])
        if kg.current_facts(store,p['facts']):needs.append({'label':row['label'],'text':row['text'],'status':'inferred','facts':p['facts']})
    return {k:event[k] for k in ('event_id','topic_id','fingerprint','title','status','last_seen_at')}|{'entities':subjects[:10],'claims':claims,'needs':needs[:4],
        'recency':event['payload'].get('recency'),'note':'事实主张有来源归属；需求为有限样本推断，候选尚未确认热点'}


def local_context(store,*,topic_id=None,entity=None,event_id=None,query='',limit=6,hops=2):
    if type(limit) is not int or not 1<=limit<=12 or type(hops) is not int or not 0<=hops<=2:raise ValueError('图检索范围无效')
    events=active_events(store);anchors=set();resolution=None
    if topic_id:anchors.update(e['event_id'] for e in events.values() if e['topic_id']==topic_id)
    if event_id and event_id in events:anchors.add(event_id)
    if entity or query:
        resolution=graph_entities.resolve(store,entity or query,query)
        ids={(resolution.get('entity') or {}).get('entity_id')}
        for row in store.conn.execute("SELECT * FROM kg_relation WHERE predicate='MENTIONS' AND status='source_attributed'"):
            if row['object_id'] in ids and row['subject_id'] in events and kg.current_facts(store,json.loads(row['payload'])['facts']):anchors.add(row['subject_id'])
    ordered=sorted(anchors,key=lambda x:(events[x]['status']=='interpreted',events[x]['last_seen_at'],x),reverse=True)[:limit]
    selected=set(ordered);all_edges=event_edges(store,events)
    for _ in range(hops):
        neighbors=set()
        for edge in all_edges:
            if edge['subject_id'] in selected:neighbors.add(edge['object_id'])
            if edge['object_id'] in selected:neighbors.add(edge['subject_id'])
        for eid in sorted(neighbors-selected):
            if len(ordered)>=limit:break
            ordered.append(eid);selected.add(eid)
    nodes=[event_context(store,events[e]) for e in ordered]
    edges=[{k:e[k] for k in ('relation_id','subject_id','predicate','object_id','status')}|{'facts':e['payload']['facts'],'reason':e['payload'].get('reason')}
        for e in all_edges if e['subject_id'] in selected and e['object_id'] in selected][:12]
    pending=[]
    for row in store.conn.execute("SELECT * FROM kg_relation WHERE status IN ('proposed','legacy_unquoted') ORDER BY rowid DESC LIMIT 120"):
        if row['subject_id'] in selected or row['object_id'] in selected:
            p=json.loads(row['payload'])
            if not p['facts'] or kg.current_facts(store,p['facts']):pending.append({k:row[k] for k in ('relation_id','subject_id','predicate','object_id','status')}|p)
    facts=[f for n in nodes for c in n['claims']+n['needs'] for f in c['facts']]+[f for e in edges for f in e['facts']]
    facts.extend(f for r in pending[:4] for f in r['facts'])
    for row in store.conn.execute("SELECT * FROM kg_relation WHERE predicate='MENTIONS' AND status='source_attributed'"):
        if row['subject_id'] in selected and kg.current_facts(store,json.loads(row['payload'])['facts']):facts.extend(json.loads(row['payload'])['facts'])
    source_ids=list(dict.fromkeys(f['evidence_id'] for f in facts))[:8]
    documents=[]
    for eid in source_ids:
        item=kg.source(store,eid)
        documents.append({k:item[k] for k in ('evidence_id','title','url','published_at','content_scope','content_hash','source_version')}|{'body':item['body'][:900],'body_truncated':len(item['body'])>900})
    return {'mode':'local','graph_version':kg.graph_version(store),'resolution':resolution,'events':nodes,'relations':edges,'pending_relations':pending[:4],
        'sources':documents,'evidence_ids':source_ids,'note':'仅检索；不修改身份或关系。邻接、时间顺序与共同主题不证明因果。'}


def community_context(store,community_id,*,events=None):
    row=store.conn.execute("SELECT * FROM kg_community WHERE community_id=? AND state='active'",(community_id,)).fetchone()
    if not row:raise ValueError('主题社区不存在或已过期')
    payload=json.loads(row['payload']);events=events if events is not None else active_events(store)
    ids=payload['event_ids']
    if not all(e in events for e in ids) or not kg.current_facts(store,payload['facts']):raise ValueError('主题依据已变化，等待自动重建')
    saved=store.conn.execute('SELECT payload FROM kg_community_summary WHERE community_id=? AND fingerprint=?',(community_id,row['fingerprint'])).fetchone()
    summary=json.loads(saved[0]) if saved else None
    signals=[]
    for eid in ids:
        for signal in json.loads(store.conn.execute('SELECT signals FROM topic WHERE topic_id=?',(events[eid]['topic_id'],)).fetchone()[0]):
            if signal.get('kind')=='board_rank_rise':signals.append(signal)
    return {'community_id':community_id,'fingerprint':row['fingerprint'],**payload,'events':[event_context(store,events[e],claim_limit=2) for e in ids[:8]],
        'ai_summary':summary,'heat':{'board_rise_signals':signals[:8],'qualified_as_hotspot':False,'note':'主题索引不自动认定热点；热度和事件时效另行核查'}}


def global_context(store,*,limit=4,query=''):
    if type(limit) is not int or not 1<=limit<=12 or not isinstance(query,str) or len(query)>100:raise ValueError('全局检索范围无效')
    events=active_events(store);communities=[]
    for row in store.conn.execute("SELECT community_id FROM kg_community WHERE state='active' ORDER BY community_id"):
        try:value=community_context(store,row[0],events=events)
        except ValueError:continue
        if query and query.casefold() not in dump(value).casefold():continue
        communities.append(value)
    communities.sort(key=lambda x:(not bool(x['ai_summary']),-len(x['event_ids']),x['community_id']))
    return {'mode':'global','graph_version':kg.graph_version(store),'communities':communities[:limit],
        'evidence_ids':list(dict.fromkeys(f['evidence_id'] for c in communities[:limit] for f in c['facts'])),
        'note':'跨事件主题及摘要用于研究和机会判断；不是热搜列表，也不是已证实的增长机会。'}


def overview(store):
    return {'version':kg.VERSION,'indexed_at':(dict(store.conn.execute("SELECT updated_at FROM kg_index_state WHERE name='graph'").fetchone() or {}) or {}).get('updated_at'),
        'counts':{table:store.conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('kg_entity','kg_mention','kg_claim','kg_need')},
        **global_context(store,limit=4)}


def tool(store,name,args):
    topic_id=args.get('topic_id');query=args.get('query') or ''
    if name=='resolve_entity':return {'calls':0,'status':'ok',**graph_entities.resolve(store,query,query)}
    if name=='get_entity_neighborhood':value=local_context(store,entity=query,query=query)
    elif name=='find_related_events':value=local_context(store,topic_id=topic_id,query=query)
    elif name=='trace_event_development':value=local_context(store,topic_id=topic_id,hops=2)
    elif name=='retrieve_community_context':value=global_context(store,query=query,limit=3)
    else:raise ValueError('图工具未注册')
    return {'calls':0,'status':'ok',**value}


TOOLS=('resolve_entity','get_entity_neighborhood','find_related_events','trace_event_development','retrieve_community_context')
PROMPT='''
graph_context 是只读图谱检索上下文，不是独立事实证明。source_attributed 表示来源声称；source_supported 表示引文支持关系，仍不保证事实真伪；inferred 为有限样本推断；proposed/legacy_unquoted/contradicted 不可当已证实关系。
事件邻接、时间先后和主题社区都不证明因果或热度。保留事件时间与报道时间、待核查关系及冲突。仅共用游戏/平台不归并事件。图谱提供检索线索，重要结论仍以当前来源引文为准。'''


def attach(store,packet,topic_id):
    value=local_context(store,topic_id=topic_id,limit=4)
    packet['graph_context']=value
    # Retrieved sources become explicitly labelled research leads, with the
    # same quote contract as all other sources. Never silently certify them.
    existing={e['evidence_id'] for e in packet['evidence']}
    for item in value['sources'][:4]:
        if item['evidence_id'] in existing:continue
        packet['evidence'].append({**item,'role':'graph_context_unverified'})
        existing.add(item['evidence_id'])
        import re
        pieces=[item['title']]+re.findall(r'[^。！？\n]+[。！？]?',item['body'])
        for quote in list(dict.fromkeys(p.strip() for p in pieces if len(p.strip())>=4))[:4]:
            packet['quote_candidates'].append({'ref':len(packet['quote_candidates']),'evidence_id':item['evidence_id'],'quote':quote[:300]})
    return packet


def guard(store,context):
    """Version every graph source and topic used, even before the next refresh."""
    result={'graph_version':context.get('graph_version'),'topics':{},'sources':{},'communities':{}}
    def visit(value):
        if isinstance(value,dict):
            if value.get('topic_id') and value.get('fingerprint'):result['topics'][value['topic_id']]=value['fingerprint']
            if value.get('community_id') and value.get('fingerprint'):result['communities'][value['community_id']]=value['fingerprint']
            if value.get('evidence_id'):
                item=kg.source(store,value['evidence_id'])
                if item:result['sources'][item['evidence_id']]={k:value[k] if k in value else item[k] for k in ('content_hash','url','published_at')}
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(context)
    return result
