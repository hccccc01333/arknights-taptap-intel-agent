"""Reject unrelated candidates; compact only redundant, unreferenced observations."""
from datetime import datetime,timedelta,timezone
from agent_v2.store import dump,now_iso,stable_id

def initialize(store):
    store.conn.execute('''CREATE TABLE IF NOT EXISTS candidate_exclusion(
      topic_id TEXT,fingerprint TEXT,context_version TEXT,reason TEXT,created_at TEXT,
      PRIMARY KEY(topic_id,fingerprint,context_version))''')

def exclude(store,decision,context):
    if decision['route']!='unrelated' or decision['action']!='archive':return
    with store.conn:
        store.conn.execute('INSERT OR IGNORE INTO candidate_exclusion VALUES(?,?,?,?,?)',
            (decision['topic_id'],decision['fingerprint'],context,decision['reason'],now_iso()))
        store.conn.execute('UPDATE topic SET eligible=0 WHERE topic_id=? AND fingerprint=?',
            (decision['topic_id'],decision['fingerprint']))

def excluded(store,topic_id,fingerprint):
    context=stable_id('context_',dump(store.context()))
    return bool(store.conn.execute('SELECT 1 FROM candidate_exclusion WHERE topic_id=? AND fingerprint=? AND context_version=?',
        (topic_id,fingerprint,context)).fetchone())

def compact(store,limit=200):
    cutoff=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(timespec='seconds')
    # Keep current text, snapshots, rejection reasons and newest observation per
    # channel. Never remove files, quoted sources, uncertain candidates or output.
    ids=[r[0] for r in store.conn.execute('''SELECT DISTINCT m.evidence_id FROM candidate_exclusion x
      JOIN topic t ON t.topic_id=x.topic_id AND t.fingerprint=x.fingerprint
      JOIN topic_member m ON m.topic_id=t.topic_id AND m.active=1
      WHERE t.eligible=0 AND NOT EXISTS(SELECT 1 FROM event_evidence a WHERE a.evidence_id=m.evidence_id)
      AND NOT EXISTS(SELECT 1 FROM research_link a WHERE a.evidence_id=m.evidence_id)
      AND NOT EXISTS(SELECT 1 FROM topic_member b JOIN topic u ON u.topic_id=b.topic_id
                     WHERE b.evidence_id=m.evidence_id AND b.active=1 AND u.eligible=1) LIMIT ?''',(limit,))]
    removed=0
    with store.conn:
        for eid in ids:
            removed+=store.conn.execute('''DELETE FROM channel_observation AS o WHERE o.evidence_id=? AND o.observed_at<?
              AND o.observed_at<(SELECT MAX(n.observed_at) FROM channel_observation n WHERE n.evidence_id=o.evidence_id AND n.channel_id=o.channel_id)''',
              (eid,cutoff)).rowcount
    return {'rejected_sources_examined':len(ids),'redundant_observations_removed':removed,
            'scope':'仅清理完全无关线索的一天前重复观测；保留最新观测、原文、拒绝依据与成果引用'}
