"""Read available details before AI, independently of model availability."""
from datetime import datetime,timedelta,timezone
import json

from .public_sources import read_source
from agent_v2.store import now_iso,dump
from .discovery import read_topic
from .materials import capture


def candidates(store,topic_id=None):
    if topic_id:
        topic=read_topic(store,topic_id,include_tracking=False)
        return topic['evidence']+topic.get('research_sources',[])
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=48)).isoformat(timespec='seconds')
    rows=store.conn.execute('''SELECT DISTINCT e.evidence_id FROM evidence e JOIN topic_member m USING(evidence_id)
      JOIN topic t USING(topic_id) LEFT JOIN source_read r USING(evidence_id)
      WHERE m.active=1 AND t.eligible=1 AND t.last_seen_at>=?
      AND e.platform IN ('gamemedia','bilibili','chinanews','tieba')
      ORDER BY r.attempted_at ASC,t.priority DESC,t.last_seen_at DESC LIMIT 1200''',(cutoff,)).fetchall()
    # Store.evidence bounds a single read to 20; do not silently drop the queue.
    return [item for start in range(0,len(rows),20) for item in store.evidence([r[0] for r in rows[start:start+20]])]


def prepare(store,*,topic_id=None,evidence_id=None,max_calls=6):
    max_calls=max(0,min(12,int(max_calls)))
    candidates_by_bucket={};results=[];calls=0;stamp=now_iso()
    for evidence in candidates(store,topic_id):
        if evidence_id and evidence['evidence_id']!=evidence_id:continue
        if evidence['platform'] not in ('gamemedia','bilibili','chinanews','tieba'):continue
        eid=evidence['evidence_id']
        previous=store.conn.execute('SELECT * FROM source_read WHERE evidence_id=?',(eid,)).fetchone()
        metadata=store.conn.execute('SELECT payload FROM source_read_meta WHERE evidence_id=?',(eid,)).fetchone()
        identity=store.conn.execute('SELECT requested_url FROM source_read_request WHERE evidence_id=?',(eid,)).fetchone()
        detail=json.loads(metadata[0]) if metadata else {}
        same_url=(identity[0]==evidence['url']) if identity else (not metadata or detail.get('requested_url',detail.get('resolved_url'))==evidence['url'])
        if previous and same_url and previous['retry_at']>stamp and (previous['status']!='ok' or previous['source_version']==evidence['content_hash']):continue
        bucket=evidence['platform']
        if bucket=='chinanews':
            row=store.conn.execute("SELECT MIN(channel_id) FROM channel_observation WHERE evidence_id=? AND channel_id LIKE 'chinanews:%'",(eid,)).fetchone()
            bucket=row[0] or bucket
        provider=store.conn.execute('SELECT status,retry_at FROM research_capability WHERE capability=?',('detail:'+bucket,)).fetchone()
        if provider and provider['status']=='failed' and provider['retry_at']>stamp:continue
        candidates_by_bucket.setdefault(bucket,[]).append(evidence)
    saved=store.conn.execute("SELECT value FROM settings WHERE key='enrichment_rotation'").fetchone()
    rotation=json.loads(saved[0]) if saved else {}
    while candidates_by_bucket and calls<max_calls:
        bucket=min(candidates_by_bucket,key=lambda b:(rotation.get(b,0),b))
        evidence=candidates_by_bucket[bucket].pop(0)
        if not candidates_by_bucket[bucket]:candidates_by_bucket.pop(bucket)
        if evidence:
            eid=evidence['evidence_id']
            previous=store.conn.execute('SELECT * FROM source_read WHERE evidence_id=?',(eid,)).fetchone()
            calls+=1;stamp=now_iso();output={}
            try:
                output=read_source(store,eid)
                status='ok' if output.get('status')=='ok' else 'unavailable'
                error=None if status=='ok' else '未取得可靠正文，保留标题或摘要范围'
            except Exception as failure:
                status='failed';error='详情读取未成功：'+type(failure).__name__
                code=getattr(getattr(failure,'response',None),'status_code',None)
                if code in (403,412,429):
                    error+='（HTTP '+str(code)+'，本轮停止该来源补读）'
                    candidates_by_bucket.pop(bucket,None)
                    from .discussion import record_capability
                    record_capability(store,'detail:'+bucket,'failed',error=error)
            current=store.evidence([eid])[0]
            retry=(datetime.now(timezone.utc)+timedelta(minutes=360 if status=='ok' else 30)).isoformat(timespec='seconds')
            succeeded=stamp if status=='ok' else previous['succeeded_at'] if previous else None
            with store.conn:
                store.conn.execute('''INSERT INTO source_read_request VALUES(?,?,?) ON CONFLICT(evidence_id)
                  DO UPDATE SET requested_url=excluded.requested_url,attempted_at=excluded.attempted_at''',
                  (eid,current['url'],stamp))
                store.conn.execute('''INSERT INTO source_read VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(evidence_id)
                  DO UPDATE SET status=excluded.status,attempted_at=excluded.attempted_at,succeeded_at=excluded.succeeded_at,
                  retry_at=excluded.retry_at,error=excluded.error,source_version=excluded.source_version,
                  scope=excluded.scope,characters=excluded.characters''',
                  (eid,status,stamp,succeeded,retry,error,current['content_hash'],current['content_scope'],len(current['body'])))
                if status=='ok':capture(store,eid,channel='detail:'+current['platform'],observed_at=stamp)
                if status=='ok':
                    metadata={**(output.get('metadata') or {}),'requested_url':current['url'],'comments_read':False}
                    store.conn.execute('''INSERT INTO source_read_meta VALUES(?,?,?) ON CONFLICT(evidence_id)
                      DO UPDATE SET source_version=excluded.source_version,payload=excluded.payload''',
                      (eid,current['content_hash'],dump(metadata)))
                rotation[bucket]=max(rotation.values(),default=0)+1
                store.conn.execute("INSERT INTO settings VALUES('enrichment_rotation',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(rotation),))
            results.append({'evidence_id':eid,'status':status,'scope':current['content_scope'],
                            'bucket':bucket,'characters':len(current['body']),'retry_at':retry,'error':error})
    return {'calls':calls,'results':results,'limit':max_calls,'selection':'持久轮换不同来源，失败或已读来源按重试时间跳过'}
