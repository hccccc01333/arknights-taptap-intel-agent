"""Incremental candidate ledger. Signals are observations, never semantic verdicts."""
from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timedelta, timezone

from agent_v2.store import stable_id, dump, now_iso


def title_key(title):
    text=unicodedata.normalize("NFKC",title).casefold()
    return "".join(c for c in text if not c.isspace()) or title.strip()


def scan(store, hours=168):
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=max(1,min(168,int(hours))))).isoformat(timespec="seconds")
    ceiling=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec="seconds")
    rows=store.conn.execute("""SELECT e.* FROM evidence e WHERE last_seen_at>=? AND last_seen_at<=? AND
      ((kind='news' AND published_at>=? AND published_at<=?) OR (kind<>'news' AND (kind='ranking' OR EXISTS
       (SELECT 1 FROM channel_observation o WHERE o.evidence_id=e.evidence_id AND o.observed_at>=?))))""",(cutoff,ceiling,cutoff,ceiling,cutoff)).fetchall()
    touched=set(); processed=0
    with store.conn:
        # Polling an old feed item must not keep an expired candidate eligible.
        store.conn.execute('UPDATE topic SET eligible=0 WHERE eligible=1')
        for raw in rows:
            row=dict(raw); eid=row["evidence_id"]
            topic_id=stable_id("topic_",title_key(row["title"]))
            touched.add(topic_id)
            content=stable_id("text_",row["title"]+"\n"+row["body"])
            cursor=store.conn.execute("SELECT * FROM discovery_cursor WHERE evidence_id=?",(eid,)).fetchone()
            if cursor and cursor["observed_at"]==row["last_seen_at"] and cursor["content_hash"]==content:continue
            # Exact-title buckets aid discovery; AI decides semantic event identity later.
            store.conn.execute("UPDATE topic_member SET active=0 WHERE evidence_id=? AND topic_id<>?",(eid,topic_id))
            store.conn.execute("INSERT INTO topic(topic_id,title,first_seen_at,last_seen_at,fingerprint,reviewed_fingerprint,priority,signals,decision,event_id,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(topic_id) DO UPDATE SET last_seen_at=MAX(topic.last_seen_at,excluded.last_seen_at)",
                              (topic_id,row["title"],row["first_seen_at"],row["last_seen_at"],"",None,0,"[]",None,None,None))
            store.conn.execute("INSERT INTO topic_member VALUES(?,?,1) ON CONFLICT(topic_id,evidence_id) DO UPDATE SET active=1",(topic_id,eid))
            store.conn.execute("INSERT INTO discovery_cursor VALUES(?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET observed_at=excluded.observed_at,content_hash=excluded.content_hash",(eid,row["last_seen_at"],content))
            touched.add(topic_id);processed+=1
        for tid in touched:
            members=store.conn.execute("SELECT e.* FROM evidence e JOIN topic_member m USING(evidence_id) WHERE m.topic_id=? AND m.active=1",(tid,)).fetchall()
            from .tracking import confirmed_sources
            confirmed,_=confirmed_sources(store,tid)
            own={m['evidence_id'] for m in members}
            if confirmed:members=list(members)+[e for e in store.evidence(confirmed) if e['evidence_id'] not in own]
            signals=[]; channels=set(); platforms=set(); signature=[]; priority=0
            for member in members:
                eid=member["evidence_id"]; platforms.add(member["platform"])
                if member['kind']=='news':signals.append({'kind':'news_publication','evidence_id':eid,'published_at':member['published_at'],'note':'新闻发布线索，尚未证明热度升温'})
                signature.append([eid,stable_id("text_",member["title"]+"\n"+member["body"]),member['url'],member['published_at']])
                observations=store.conn.execute("SELECT * FROM channel_observation WHERE evidence_id=? AND observed_at>=? ORDER BY observed_at",(eid,cutoff)).fetchall()
                groups={}
                for obs in observations:groups.setdefault(obs["channel_id"],[]).append(obs)
                for channel,points in groups.items():
                    channels.add(channel); latest=points[-1]
                    # Significant movement uses an actual half-hour gap, within one board.
                    earlier=[p for p in points if (datetime.fromisoformat(latest["observed_at"])-datetime.fromisoformat(p["observed_at"])).total_seconds()>=1800]
                    if earlier and earlier[-1]["position"] is not None and latest["position"] is not None and earlier[-1]["position"]-latest["position"]>=5:
                        movement={"kind":"board_rank_rise","channel_id":channel,"evidence_id":eid,"from":earlier[-1]["position"],"to":latest["position"],"from_at":earlier[-1]["observed_at"],"to_at":latest["observed_at"]}
                        signals.append(movement);signature.append([channel,"rise",latest["position"]]);priority+=3
                if member["published_at"] and member["published_at"]<cutoff:
                    signals.append({"kind":"old_publication_recently_observed","evidence_id":eid})
            if channels:signals.append({"kind":"channel_presence","channels":sorted(channels)})
            if len(platforms)>1:
                signals.append({"kind":"same_title_multiple_platforms","platforms":sorted(platforms)});priority+=2
            signature.append(sorted(channels))
            research_versions=[[r['evidence_id'],stable_id('text_',r['title']+'\n'+r['body']),r['state'],r['role']] for r in store.conn.execute('''
              SELECT e.evidence_id,e.title,e.body,r.state,r.role FROM research_link r JOIN evidence e USING(evidence_id)
              WHERE r.topic_id=? ORDER BY e.evidence_id''',(tid,))]
            if research_versions:signature.append(['research',research_versions])
            fingerprint=stable_id("revision_",dump(sorted(signature,key=dump)))
            topic=store.conn.execute("SELECT * FROM topic WHERE topic_id=?",(tid,)).fetchone()
            if not topic["reviewed_fingerprint"]:priority+=1
            store.conn.execute("UPDATE topic SET fingerprint=?,priority=?,signals=?,eligible=1 WHERE topic_id=?",(fingerprint,priority,dump(signals),tid))
            from .retention import excluded
            if excluded(store,tid,fingerprint):store.conn.execute('UPDATE topic SET eligible=0 WHERE topic_id=?',(tid,))
            store.conn.execute("INSERT OR IGNORE INTO topic_history VALUES(?,?,?,?)",(tid,fingerprint,now_iso(),dump({"signals":signals,"member_ids":[r["evidence_id"] for r in members]})))
    return {"processed_evidence":processed,"touched_topics":len(touched),"eligible_evidence":len(rows),"hours":hours,"note":"榜单、渠道发现和近期资讯进入候选；普通历史帖和搜索命中仅作研究背景。按严格标题归组，排名仅比较同一榜单。"}


def queue(store, limit=30, *, include_reviewed=False, hours=168, domain=''):
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=max(1,min(168,int(hours))))).isoformat(timespec="seconds")
    extra="" if include_reviewed else " AND fingerprint<>COALESCE(reviewed_fingerprint,'')"
    params=[cutoff]
    if domain:
        from .connectors import CHANNELS
        channels=[c['id'] for c in CHANNELS if domain in c.get('domains',[c['domain']])]
        if not channels:return []
        extra+=' AND EXISTS (SELECT 1 FROM topic_member m JOIN channel_observation o USING(evidence_id) WHERE m.topic_id=topic.topic_id AND m.active=1 AND o.observed_at>=? AND o.channel_id IN ('+','.join('?' for c in channels)+'))'
        params.extend([cutoff,*channels])
    params.append(max(1,min(100,int(limit))))
    rows=store.conn.execute("SELECT * FROM topic WHERE eligible=1 AND last_seen_at>=?"+extra+" ORDER BY priority DESC,last_seen_at DESC LIMIT ?",params)
    result=[]
    for row in rows:
        item=dict(row);item["signals"]=json.loads(item["signals"])
        item["state"]="new" if not item["reviewed_fingerprint"] else "updated" if item["fingerprint"]!=item["reviewed_fingerprint"] else "reviewed"
        item["evidence_ids"]=[r[0] for r in store.conn.execute("SELECT evidence_id FROM topic_member WHERE topic_id=? AND active=1",(row["topic_id"],))]
        item['candidate_type']='news' if item['signals'] and all(s['kind'] in ('news_publication','channel_presence','same_title_multiple_platforms') for s in item['signals']) and any(s['kind']=='news_publication' for s in item['signals']) else 'observed_topic'
        result.append(item)
    return result


def read_topic(store, topic_id, *, include_tracking=True):
    row=store.conn.execute("SELECT * FROM topic WHERE topic_id=?",(topic_id,)).fetchone()
    if not row:raise ValueError("候选话题不存在")
    result=dict(row);result["signals"]=json.loads(row["signals"])
    ids=[r[0] for r in store.conn.execute("SELECT evidence_id FROM topic_member WHERE topic_id=? AND active=1",(topic_id,))]
    from .tracking import confirmed_sources,topic_events
    confirmed,canonical=confirmed_sources(store,topic_id)
    ids=list(dict.fromkeys(ids+confirmed));result['canonical_topic_id']=canonical
    result["evidence"]=store.evidence(ids[:12])
    result["history"]=[{**dict(r),"payload":json.loads(r["payload"])} for r in store.conn.execute("SELECT * FROM topic_history WHERE topic_id=? ORDER BY observed_at DESC LIMIT 12",(topic_id,))]
    result["insights"]=[{"evidence_id":e["evidence_id"],"content_hash":e["content_hash"],"payload":store.insight(e["evidence_id"],e["content_hash"])} for e in result["evidence"]]
    result["intelligence"]=store.intelligence(topic_id,result["fingerprint"])
    result['interpretation']=store.interpretation(topic_id,result['fingerprint'])
    from .followups import read
    result['followups']=read(store,topic_id)
    from .presentation import recognized_captures,business_outputs
    result['research_captures']=recognized_captures(store.conn.execute(
        '''SELECT * FROM research_capture WHERE topic_id=? OR capture_id IN
        (SELECT json_extract(m.payload,'$.capture_id') FROM source_read_meta m JOIN research_link r USING(evidence_id)
         WHERE r.topic_id=? AND r.role='discussion') ORDER BY created_at DESC LIMIT 6''',(topic_id,topic_id)))
    result['business_outputs']=business_outputs(store,topic_id,result['fingerprint'],result['intelligence'])
    from .discussion import read_samples
    result['discussion_samples']=read_samples(store,topic_id)
    result['research_sources']=[{**e,'research_state':r['state'],'research_reason':r['reason']} for r in store.conn.execute(
        "SELECT * FROM research_link WHERE topic_id=? AND role='background' ORDER BY created_at DESC LIMIT 5",(topic_id,))
        for e in store.evidence([r['evidence_id']])]
    latest=store.conn.execute('SELECT * FROM research_task WHERE topic_id=? ORDER BY updated_at DESC LIMIT 1',(topic_id,)).fetchone()
    result['research']={**dict(latest),'payload':json.loads(latest['payload'])} if latest else None
    if include_tracking:result['tracked_events']=topic_events(store,topic_id)
    all_ids=ids+[e['evidence_id'] for e in result['discussion_samples']]+[e['evidence_id'] for e in result['research_sources']]
    result["source_assets"]=store.source_assets(limit=60,evidence_ids=all_ids)
    from .research import inspect
    result['research_gaps']=inspect(store,result)
    return result
