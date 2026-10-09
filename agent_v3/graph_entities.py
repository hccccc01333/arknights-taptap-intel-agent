"""Shared game registry plus conservative, source-bound entity linking."""
import json
import re
from functools import lru_cache

from agent_v2.store import dump,stable_id
from L2_signal.processing.entities import GameKnowledgeRegistry,ENTITY_DICT

VERSION='entity-link-v3.17'
AMBIGUOUS={
    '方舟':('明日方舟','arknights','罗德岛','干员','鹰角'),
    '悟空':('黑神话','black myth','游戏科学'),
    '三角洲':('三角洲行动','delta force','哈夫克'),
    'lol':('英雄联盟','league of legends','召唤师峡谷','lpl','拳头'),
    '吃鸡':('和平精英','pubg mobile'),
    '农药':('王者荣耀','王者峡谷'),
}


@lru_cache(maxsize=1)
def registry():
    result={eid:{'entity_id':eid,'kind':'GAME','name':m['canonical_name'],
        'aliases':m['aliases'],'origin':m['source']} for eid,m in GameKnowledgeRegistry().games.items()}
    for kind in ('PLATFORM','DEVELOPER','PUBLISHER'):
        for alias,name in ENTITY_DICT[kind]:
            # A company acting as developer/publisher keeps a single identity;
            # its role belongs on a relation, not a second company node.
            canonical_kind='ORGANIZATION' if kind in ('DEVELOPER','PUBLISHER') else kind
            eid=stable_id('entity_',dump([canonical_kind,name]))
            if eid not in result:result[eid]={'entity_id':eid,'kind':canonical_kind,'name':name,'aliases':[],'origin':'L2_registry'}
            if alias not in result[eid]['aliases']:result[eid]['aliases'].append(alias)
    for item in result.values():
        item['aliases']=list(dict.fromkeys([item['name'],*item['aliases']]))
    return result


def seed(store):
    rows=registry()
    version=stable_id('registry_',dump(rows))
    previous=store.conn.execute("SELECT version FROM kg_index_state WHERE name='registry'").fetchone()
    if previous and previous[0]==version:return
    with store.conn:
        for eid,item in rows.items():
            store.conn.execute('INSERT INTO kg_entity VALUES(?,?,?,?,?) ON CONFLICT(entity_id) DO UPDATE SET name=excluded.name,payload=excluded.payload',
                (eid,item['kind'],item['name'],'registry',dump(item)))
            for alias in item['aliases']:
                store.conn.execute('INSERT OR IGNORE INTO kg_alias VALUES(?,?,?)',(alias.casefold(),eid,int(alias.casefold() in AMBIGUOUS)))
        store.conn.execute("INSERT OR REPLACE INTO kg_index_state VALUES('registry',?,NULL,?)",(version,dump({'entities':len(rows)})))


def resolve(store,surface,context=''):
    if not isinstance(surface,str) or not 1<=len(surface.strip())<=100:raise ValueError('实体名称长度无效')
    surface=surface.strip();key=surface.casefold()
    matches=[dict(r) for r in store.conn.execute('''SELECT e.entity_id,e.kind,e.name,a.ambiguous FROM kg_alias a
        JOIN kg_entity e USING(entity_id) WHERE a.alias=? ORDER BY e.entity_id''',(key,))]
    context=(context or '').casefold()
    allowed=[r for r in matches if not r['ambiguous'] or any(word.casefold() in context for word in AMBIGUOUS.get(key,()))]
    linked=allowed[0] if len(allowed)==1 else None
    return {'surface':surface,'status':'linked' if linked else 'unresolved','entity':linked,
        'candidates':[{k:r[k] for k in ('entity_id','kind','name')} for r in matches],
        'reason':'注册表唯一匹配且通过语境检查' if linked else '别名歧义或知识库未收录；保留缺口',
        'link_version':VERSION}


def mentions(store,text):
    aliases=[r[0] for r in store.conn.execute('SELECT DISTINCT alias FROM kg_alias ORDER BY length(alias) DESC,alias')]
    found=[];covered=[]
    for alias in aliases:
        pattern=re.escape(alias)
        if alias.isascii():pattern=r'(?<![A-Za-z0-9])'+pattern+r'(?![A-Za-z0-9])'
        for match in re.finditer(pattern,text,re.I):
            left,right=match.span()
            if any(left<end and right>start for start,end in covered):continue
            covered.append((left,right))
            linked=resolve(store,match.group(),text[max(0,left-80):right+80])
            found.append({**linked,'start':left,'end':right,'quote':text[max(0,left-35):min(len(text),right+60)]})
    return sorted(found,key=lambda x:x['start'])[:32]
