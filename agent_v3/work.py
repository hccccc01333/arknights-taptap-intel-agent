"""Durable, versioned intelligence and creative work, separate from collection."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from agent_v2.store import dump, now_iso, stable_id

INTELLIGENCE_VERSION = "intelligence-v3.17"


def enqueue(store, *, topic_id=None,topic_ids=None):
    cutoff = (datetime.now(timezone.utc)-timedelta(hours=168)).isoformat(timespec="seconds")
    params = [cutoff]
    clause = " AND topic_id=?" if topic_id else ""
    if topic_id:
        params.append(topic_id)
    if topic_ids is not None:
        clause+=' AND topic_id IN ('+','.join('?' for _ in topic_ids)+')' if topic_ids else ' AND 0'
        params.extend(topic_ids)
    topics = store.conn.execute("SELECT * FROM topic WHERE eligible=1 AND last_seen_at>=?"+clause+
                                " ORDER BY priority DESC,last_seen_at DESC LIMIT 1200",params).fetchall()
    counts = {"intelligence":0,"creative":0}
    context_version = stable_id("context_",dump(store.context()))
    with store.conn:
        for topic in topics:
            tid, fingerprint = topic["topic_id"],topic["fingerprint"]
            from .tracking import confirmed_sources
            _,canonical=confirmed_sources(store,tid)
            if tid!=canonical:
                store.conn.execute("UPDATE work_item SET status='superseded',updated_at=? WHERE topic_id=? AND status IN ('pending','deferred')",(now_iso(),tid))
                continue
            bucket = store.conn.execute("""SELECT MIN(o.channel_id) FROM channel_observation o
              JOIN topic_member m ON m.evidence_id=o.evidence_id WHERE m.topic_id=? AND m.active=1""",(tid,)).fetchone()[0] or "imported"
            analysis = store.conn.execute("SELECT payload FROM topic_intelligence WHERE topic_id=? AND fingerprint=? AND prompt_version LIKE ? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                          (tid,fingerprint,INTELLIGENCE_VERSION+'%')).fetchone()
            if analysis and json.loads(analysis[0]).get('context_version',context_version)!=context_version:analysis=None
            from .risk import policy
            stages = ["creative"] if analysis and json.loads(analysis[0])["opportunity"]["decision"]=="opportunity" and policy(store,tid,fingerprint)['growth_allowed'] else []
            if not analysis:
                stages.append("intelligence")
            for stage in stages:
                version = INTELLIGENCE_VERSION+':'+context_version if stage=="intelligence" else "creative-v3.2:"+context_version
                key = stable_id("job_",dump([stage,tid,fingerprint,version]))
                cursor = store.conn.execute("""INSERT OR IGNORE INTO work_item
                  (job_id,stage,topic_id,fingerprint,prompt_version,bucket,priority,status,attempts,created_at,updated_at,context_version)
                  VALUES(?,?,?,?,?,?,?,'pending',0,?,?,?)""",(key,stage,tid,fingerprint,version,bucket,topic["priority"],now_iso(),now_iso(),context_version))
                counts[stage] += cursor.rowcount
                if not cursor.rowcount:
                    # A reversible event split may restore an earlier content
                    # version. Reactivate its retained job instead of losing it.
                    counts[stage]+=store.conn.execute("""UPDATE work_item SET status='pending',retry_at=NULL,error=NULL,
                      run_id=NULL,lease_until=NULL,updated_at=? WHERE job_id=? AND status IN ('superseded','held_risk')""",(now_iso(),key)).rowcount
        # Keep obsolete work as history, rather than executing it against new content.
        store.conn.execute("""UPDATE work_item SET status='superseded',updated_at=? WHERE
          status IN ('pending','deferred') AND EXISTS (SELECT 1 FROM topic t WHERE
          t.topic_id=work_item.topic_id AND (t.fingerprint<>work_item.fingerprint OR t.last_seen_at<?))""",(now_iso(),cutoff))
        store.conn.execute("""UPDATE work_item SET status='superseded',updated_at=? WHERE stage='creative'
          AND status IN ('pending','deferred') AND prompt_version<>?""",(now_iso(),"creative-v3.2:"+context_version))
        store.conn.execute("""UPDATE work_item SET status='superseded',updated_at=? WHERE stage='intelligence'
          AND status IN ('pending','deferred') AND prompt_version NOT IN (?,?)""",(now_iso(),INTELLIGENCE_VERSION,INTELLIGENCE_VERSION+':'+context_version))
        from .risk import policy
        for row in store.conn.execute("SELECT job_id,topic_id,fingerprint FROM work_item WHERE stage='creative' AND status IN ('pending','deferred')").fetchall():
            safety=policy(store,row['topic_id'],row['fingerprint'])
            if not safety['growth_allowed']:
                store.conn.execute("UPDATE work_item SET status='held_risk',error=?,updated_at=? WHERE job_id=?",(safety['gate_reason'],now_iso(),row['job_id']))
    return {**counts,"examined_topics":len(topics),"limit":1200}


def claim(store,run_id,stage,*,topic_id=None,topic_ids=None):
    stamp=now_iso()
    store.conn.execute("BEGIN IMMEDIATE")
    try:
        store.conn.execute("""UPDATE work_item SET status='pending',run_id=NULL,lease_until=NULL,lease_token=NULL,
          updated_at=?,error='处理租约到期，等待恢复' WHERE status='running' AND lease_until<=?""",(stamp,stamp))
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=168)).isoformat(timespec='seconds')
        params=[stage,stamp,cutoff,stable_id('context_',dump(store.context()))]
        risk_clause=''
        if stage=='creative':
            from .risk import POLICY_VERSION
            risk_clause=""" AND EXISTS (SELECT 1 FROM topic_risk p WHERE p.topic_id=w.topic_id AND p.fingerprint=w.fingerprint
              AND json_extract(p.payload,'$.policy_version')=? AND json_extract(p.payload,'$.stage') IN ('business_main','safety_review')
              AND json_extract(p.payload,'$.polarity') IN ('positive','neutral') AND json_extract(p.payload,'$.level')='low')"""
        clause=" AND w.topic_id=?" if topic_id else ""
        if topic_id:params.append(topic_id)
        if topic_ids is not None:
            if not topic_ids:
                store.conn.commit();return None
            clause+=' AND w.topic_id IN ('+','.join('?' for _ in topic_ids)+')'
            params.extend(topic_ids)
        if risk_clause:params.append(POLICY_VERSION)
        row=store.conn.execute("""SELECT w.* FROM work_item w JOIN topic t ON t.topic_id=w.topic_id
          WHERE w.stage=? AND w.status IN ('pending','deferred') AND (w.retry_at IS NULL OR w.retry_at<=?)
          AND w.fingerprint=t.fingerprint AND t.eligible=1 AND t.last_seen_at>=?
          AND (w.context_version IS NULL OR w.context_version=?)"""+clause+risk_clause+"""
          ORDER BY (SELECT MAX(done.updated_at) FROM work_item done WHERE done.stage=w.stage
                    AND done.bucket=w.bucket AND done.attempts>0) ASC,
                   w.attempts ASC,w.priority DESC,w.created_at,w.job_id LIMIT 1""",params).fetchone()
        if row:
            lease=(datetime.now(timezone.utc)+timedelta(minutes=8)).isoformat(timespec="seconds")
            token=uuid.uuid4().hex
            store.conn.execute("UPDATE work_item SET status='running',attempts=attempts+1,run_id=?,lease_until=?,lease_token=?,updated_at=?,context_version=COALESCE(context_version,?) WHERE job_id=?",
                               (run_id,lease,token,stamp,stable_id('context_',dump(store.context())),row["job_id"]))
            row=store.conn.execute('SELECT * FROM work_item WHERE job_id=?',(row['job_id'],)).fetchone()
        store.conn.commit()
        job=dict(row) if row else None
        store._active_job=job
        return job
    except Exception:
        store.conn.rollback();raise


def assert_claim(store,job):
    from .runtime_guard import LeaseLost, StaleBasis
    row=store.conn.execute('SELECT * FROM work_item WHERE job_id=?',(job['job_id'],)).fetchone()
    if not row or row['status']!='running' or not job.get('lease_token') or row['lease_token']!=job['lease_token'] or row['run_id']!=job['run_id'] or row['lease_until']<=now_iso() or any(row[key]!=job[key] for key in ('stage','fingerprint','prompt_version','context_version')):
        raise LeaseLost('工作项领取凭证过期或已被替换')
    topic=store.conn.execute('SELECT fingerprint,eligible,last_seen_at FROM topic WHERE topic_id=?',(job['topic_id'],)).fetchone()
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=168)).isoformat(timespec='seconds')
    if not topic or not topic['eligible'] or topic['last_seen_at']<cutoff or topic['fingerprint']!=job['fingerprint'] or job.get('context_version')!=stable_id('context_',dump(store.context())):
        raise StaleBasis('工作项的内容或业务条件已变化')
    if job['stage']=='creative':
        from .risk import require_growth
        require_growth(store,job['topic_id'],job['fingerprint'])
    return dict(row)


def renew(store,job,*,seconds=480):
    """Only the current, still-live claimant may extend its lease."""
    outer=not store.conn.in_transaction
    if outer:store.conn.execute('BEGIN IMMEDIATE')
    try:
        assert_claim(store,job);store.assert_owner(job['run_id'])
        expiry=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='seconds')
        store.conn.execute("UPDATE work_item SET lease_until=? WHERE job_id=? AND lease_token=? AND status='running'",
                           (expiry,job['job_id'],job['lease_token']))
        if outer:store.conn.commit()
        return expiry
    except BaseException:
        if outer:store.conn.rollback()
        raise


def finish(store,job,status,*,result=None,error=None,retry_at=None):
    if status not in ("succeeded","deferred","failed","superseded"):
        raise ValueError("无效任务状态")
    # Compatibility for a caller on the same connection; another connection
    # cannot complete a job merely by knowing its public job_id.
    if isinstance(job,str):
        if not store._active_job or store._active_job['job_id']!=job:
            from .runtime_guard import LeaseLost
            raise LeaseLost('完成工作项需要本次领取凭证')
        job=store._active_job
    receipt=store.conn.execute('SELECT * FROM delivery_receipt WHERE job_id=? AND lease_token=?',
                               (job['job_id'],job.get('lease_token'))).fetchone()
    if receipt and status=='succeeded':
        return False
    outer=not store.conn.in_transaction
    if outer:store.conn.execute('BEGIN IMMEDIATE')
    try:
        if status=='succeeded':assert_claim(store,job)
        else:
            from .runtime_guard import LeaseLost
            row=store.conn.execute('SELECT * FROM work_item WHERE job_id=?',(job['job_id'],)).fetchone()
            if not row or row['status']!='running' or row['lease_token']!=job.get('lease_token') or row['run_id']!=job['run_id'] or row['lease_until']<=now_iso():
                raise LeaseLost('不能修改已过期或被其他执行者领取的工作项')
        store.assert_owner(job['run_id'])
        stamp=now_iso()
        store.conn.execute("""UPDATE work_item SET status=?,result=?,error=?,retry_at=?,lease_until=NULL,
          updated_at=? WHERE job_id=? AND lease_token=? AND status='running'""",(status,dump(result) if result is not None else None,error,retry_at,stamp,job['job_id'],job['lease_token']))
        if status=='succeeded':store.conn.execute('INSERT INTO delivery_receipt VALUES(?,?,?,?,?)',
            (job['job_id'],job['lease_token'],job['run_id'],stamp,dump(result)))
        if outer:store.conn.commit()
        return True
    except BaseException:
        if outer:store.conn.rollback()
        raise


def defer_due(store,gate):
    with store.conn:
        store.conn.execute("""UPDATE work_item SET status='deferred',retry_at=?,error=?,updated_at=?
          WHERE status IN ('pending','deferred') AND (retry_at IS NULL OR retry_at<=?)""",
          (gate["retry_at"],gate["reason"],now_iso(),now_iso()))


def wake_provider_work(store):
    """Switching to a usable provider need not inherit the old provider's retry time."""
    with store.conn:
        store.conn.execute("""UPDATE work_item SET status='pending',retry_at=NULL,updated_at=?
          WHERE status='deferred' AND error IN (?,?,?,?,?,?,?,?,?,?)""",(now_iso(),"免费模型日额度耗尽",
          "模型服务限流","模型账户额度或服务配置不可用","模型响应未完成，等待重试","OpenCode Zen 免费层拒绝调用（HTTP 403）",
          "已确认 Zen 免费额度耗尽，AI 等待恢复","太空兔上游请求失败（HTTP 502）",
          "太空兔密钥或访问权限不可用","太空兔接口或模型配置不可用","用户确认模型额度不足，AI 等待恢复"))


def overview(store):
    counts=[dict(r) for r in store.conn.execute("SELECT stage,status,COUNT(*) count FROM work_item GROUP BY stage,status")]
    jobs=[{**dict(r),"result":json.loads(r["result"]) if r["result"] else None} for r in store.conn.execute("SELECT w.*,t.title FROM work_item w JOIN topic t USING(topic_id) ORDER BY w.updated_at DESC,w.job_id LIMIT 30")]
    return {"counts":counts,"recent":jobs,"budgets":{"intelligence_per_cycle":2,"creative_per_cycle":1}}
