"""Keep business content versions separate from changing board observations."""
import json
from agent_v2.store import dump,now_iso,stable_id

def initialize(store):
    store.conn.executescript('''CREATE TABLE IF NOT EXISTS topic_revision_state(
      topic_id TEXT PRIMARY KEY, content_key TEXT, observation_key TEXT, fingerprint TEXT, updated_at TEXT);
      CREATE TABLE IF NOT EXISTS topic_observation_revision(
      topic_id TEXT, observation_key TEXT, observed_at TEXT,payload TEXT,PRIMARY KEY(topic_id,observation_key));''')

def revision(store,tid,content,observations,legacy_fingerprint):
    content_key=stable_id('content_',dump(sorted(content,key=dump)))
    observation_key=stable_id('observed_',dump(observations))
    prior=store.conn.execute('SELECT * FROM topic_revision_state WHERE topic_id=?',(tid,)).fetchone()
    # First observation recomputes the old identifier. Never assume that an
    # old result matches today's source merely to keep it on the screen.
    fingerprint=legacy_fingerprint if not prior else prior['fingerprint'] if prior['content_key']==content_key else stable_id('revision_',content_key)
    store.conn.execute('INSERT OR REPLACE INTO topic_revision_state VALUES(?,?,?,?,?)',
        (tid,content_key,observation_key,fingerprint,now_iso()))
    if not prior or prior['observation_key']!=observation_key:
        store.conn.execute('INSERT OR IGNORE INTO topic_observation_revision VALUES(?,?,?,?)',
            (tid,observation_key,now_iso(),dump(observations)))
    return fingerprint

def delivery_freshness(store,tid):
    row=store.conn.execute('SELECT * FROM topic WHERE topic_id=?',(tid,)).fetchone()
    if not row:return {'business_eligible':False,'reason':'来源事件不存在'}
    ids=[r[0] for r in store.conn.execute('SELECT evidence_id FROM topic_member WHERE topic_id=? AND active=1',(tid,))]
    from .tracking import confirmed_sources
    confirmed,_=confirmed_sources(store,tid)
    from .discussion import read_samples
    value={**dict(row),'signals':json.loads(row['signals']),'evidence':store.evidence(list(dict.fromkeys(ids+confirmed))[:12]),
        'discussion_samples':read_samples(store,tid)}
    interpreted=store.interpretation(tid,row['fingerprint'])
    from .freshness import delivery_current
    return delivery_current(store,value,interpreted['payload'] if interpreted else {})
