"""Durable research questions: a disclaimer is not completion of delegated work."""
import json
from datetime import datetime, timedelta, timezone
from agent_v2.store import dump, now_iso, stable_id

TOOLS=('search_web','read_detail','sample_discussion','browse_page','screenshot_ocr','read_comments_visual')

def initialize(store):
    store.conn.executescript('''CREATE TABLE IF NOT EXISTS research_followup(
      followup_id TEXT PRIMARY KEY,topic_id TEXT,fingerprint TEXT,question TEXT,query TEXT,tool TEXT,
      status TEXT,attempts INTEGER,priority INTEGER,retry_at TEXT,created_at TEXT,updated_at TEXT,
      run_id TEXT,payload TEXT);
      CREATE INDEX IF NOT EXISTS followup_due ON research_followup(status,retry_at,priority);''')

def enqueue(store,topic_id,fingerprint,run_id,actions,unknowns=()):
    stamp=now_iso();saved=[];actions=list(actions)
    # Legacy returns receive a conservative executable fallback. Statistical
    # limitations, future outcomes and permissions are not searchable facts.
    if not actions:
        for text in unknowns:
            if any(w in text for w in ('总体','全体','增长效果','转化率','授权','预算','发布权限')):continue
            if not any(w in text for w in ('评论','正文','时间','日期','身份','是否','未核实','未确认','尚不清楚')):continue
            comment='评论' in text
            actions.append({'question':text,'query':text[:80], 'tool':'sample_discussion' if comment else 'search_web'})
            if len(actions)>=2:break
    topic=store.conn.execute('SELECT title FROM topic WHERE topic_id=?',(topic_id,)).fetchone()
    for action in actions[:2]:
        question=action['question'].strip();tool=action['tool']
        if not question or tool not in TOOLS:continue
        fid=stable_id('followup_',dump([topic_id,fingerprint,question]))
        if tool in ('sample_discussion','read_comments_visual'):
            duplicate=store.conn.execute("SELECT followup_id FROM research_followup WHERE topic_id=? AND tool IN ('sample_discussion','read_comments_visual') AND status NOT IN ('resolved','superseded') ORDER BY created_at LIMIT 1",(topic_id,)).fetchone()
            if duplicate:saved.append(duplicate[0]);continue
        if store.conn.execute('SELECT 1 FROM research_followup WHERE followup_id=?',(fid,)).fetchone():
            saved.append(fid);continue
        # A paraphrased disclaimer cannot create an unbounded new research tree.
        if store.conn.execute("SELECT COUNT(*) FROM research_followup WHERE topic_id=? AND status NOT IN ('resolved','superseded')",
                (topic_id,)).fetchone()[0]>=2:continue
        query=action.get('query','').strip()[:80]
        if tool=='search_web':query=((topic[0] if topic else '')[:40]+' '+query)[:80]
        payload={'origin_run':run_id,'resolution':None,'history':[]}
        with store.conn:
            store.conn.execute('INSERT OR IGNORE INTO research_followup VALUES(?,?,?,?,?,?,?,0,?,?,?, ?,?,?)',
                (fid,topic_id,fingerprint,question,query,tool,'pending',80 if '评论' in question else 60,
                 (datetime.now(timezone.utc)+timedelta(minutes=3)).isoformat(timespec='seconds'),stamp,stamp,run_id,dump(payload)))
        saved.append(fid)
    return saved

def due(store,limit=1):
    return [dict(r) for r in store.conn.execute('''SELECT f.* FROM research_followup f JOIN topic t USING(topic_id)
      WHERE f.status IN ('pending','deferred') AND f.retry_at<=? AND t.eligible=1 AND t.fingerprint=f.fingerprint
      AND t.last_seen_at>=? ORDER BY f.priority DESC,f.updated_at LIMIT ?''',
      (now_iso(),(datetime.now(timezone.utc)-timedelta(days=7)).isoformat(timespec='seconds'),limit))]

def consolidate(store):
    """Retain one discussion question, leaving room for a different gap."""
    with store.conn:
        for topic in store.conn.execute("SELECT topic_id FROM research_followup WHERE tool IN ('sample_discussion','read_comments_visual') AND status NOT IN ('resolved','superseded') GROUP BY topic_id HAVING COUNT(*)>1").fetchall():
            duplicates=store.conn.execute("SELECT followup_id FROM research_followup WHERE topic_id=? AND tool IN ('sample_discussion','read_comments_visual') AND status NOT IN ('resolved','superseded') ORDER BY (status='running') DESC,created_at LIMIT -1 OFFSET 1",(topic[0],)).fetchall()
            for row in duplicates:store.conn.execute("UPDATE research_followup SET status='superseded',updated_at=? WHERE followup_id=?",(now_iso(),row[0]))

def missions(store,run_id,limit=1):
    result=[]
    consolidate(store)
    with store.conn:
        # The cycle lease is the worker lock. A crash leaves these recoverable.
        store.conn.execute("UPDATE research_followup SET status='deferred' WHERE status='running' AND updated_at<?",
            ((datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat(timespec='seconds'),))
        for row in due(store,limit):
            store.conn.execute("UPDATE research_followup SET status='running',attempts=attempts+1,run_id=?,updated_at=? WHERE followup_id=?",
                (run_id,now_iso(),row['followup_id']))
            decision=store.conn.execute("SELECT json_extract(payload,'$.route') FROM main_decision WHERE topic_id=? AND fingerprint=? ORDER BY created_at DESC LIMIT 1",
                (row['topic_id'],row['fingerprint'])).fetchone()
            result.append({'topic_id':row['topic_id'],'fingerprint':row['fingerprint'],'route':decision[0] if decision else 'insufficient',
                'action':'delegate_research','reason':'自动补查尚未确认事项','questions':[row['question']],
                'query':row['query'],'preferred_tool':row['tool'],'followup_ids':[row['followup_id']]})
    return result

def settle(store,mission,run_id,answers=()):
    for fid in mission.get('followup_ids',[]):
        row=store.conn.execute('SELECT * FROM research_followup WHERE followup_id=?',(fid,)).fetchone()
        if not row:continue
        answer=next((a for a in answers if a.get('followup_id')==fid),None)
        resolved=bool(answer and answer.get('status')=='resolved' and answer.get('facts'))
        state='resolved' if resolved else 'awaiting_source' if row['attempts']>=3 else 'deferred'
        delay=(15,60,360)[min(row['attempts'],3)-1]
        payload=json.loads(row['payload']);history=payload.get('history',[])
        history.append({'run_id':run_id,'at':now_iso(),'status':state,'answer':answer or {'reason':'本轮未取得足以回答问题的证据'}})
        payload.update(history=history[-8:],resolution=answer if resolved else None)
        with store.conn:store.conn.execute('UPDATE research_followup SET status=?,retry_at=?,updated_at=?,payload=? WHERE followup_id=?',
            (state,(datetime.now(timezone.utc)+timedelta(minutes=delay)).isoformat(timespec='seconds'),now_iso(),dump(payload),fid))

def wake_changed(store):
    # New event content reopens exhausted questions, but a new collection
    # timestamp alone is not new evidence and does not reset retries.
    consolidate(store)
    with store.conn:
        for topic in store.conn.execute("SELECT topic_id FROM research_followup WHERE status NOT IN ('resolved','superseded') GROUP BY topic_id HAVING COUNT(*)>2").fetchall():
            extra=store.conn.execute("SELECT followup_id FROM research_followup WHERE topic_id=? AND status NOT IN ('resolved','superseded') ORDER BY (status='running') DESC,priority DESC,created_at LIMIT -1 OFFSET 2",(topic[0],)).fetchall()
            for row in extra:store.conn.execute("UPDATE research_followup SET status='superseded',updated_at=? WHERE followup_id=?",(now_iso(),row[0]))
        store.conn.execute('''UPDATE research_followup SET fingerprint=(SELECT fingerprint FROM topic t WHERE t.topic_id=research_followup.topic_id),
          status='pending',attempts=0,retry_at=?,updated_at=? WHERE status IN ('pending','deferred','awaiting_source')
          AND EXISTS(SELECT 1 FROM topic t WHERE t.topic_id=research_followup.topic_id AND t.fingerprint<>research_followup.fingerprint AND t.eligible=1)''',
          (now_iso(),now_iso()))

def read(store,topic_id):
    return [{**dict(r),'payload':json.loads(r['payload'])} for r in store.conn.execute(
        'SELECT * FROM research_followup WHERE topic_id=? ORDER BY created_at DESC LIMIT 12',(topic_id,))]
