"""Fenced workers and short, atomic delivery transactions. Never lock around AI I/O."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
import uuid

from agent_v2.store import now_iso, dump, stable_id


class LeaseLost(RuntimeError):
    pass


class StaleBasis(LeaseLost):
    pass


class AtomicConnection(sqlite3.Connection):
    """Nested legacy helpers cannot commit half of a protected delivery."""
    atomic_depth = 0
    atomic_failed = False
    pending_business = False
    pending_worker = False
    store = None

    def commit(self):
        if not self.atomic_depth:
            if self.pending_worker and self.store and self.store._run_token:
                try:self.store.assert_owner(self.store._run_owner)
                except LeaseLost:
                    sqlite3.Connection.rollback(self)
                    self.pending_business=self.pending_worker=False
                    raise
            super().commit()
            self.pending_business=self.pending_worker=False

    def rollback(self):
        if self.atomic_depth:
            self.atomic_failed = True
        else:
            super().rollback()
            self.pending_business=self.pending_worker=False

    def __exit__(self, kind, value, traceback):
        if self.atomic_depth:
            if kind:
                self.atomic_failed = True
            return False
        if kind:self.rollback()
        else:self.commit()
        return False

    def _before_write(self, sql):
        # All worker mutations, including tool evidence, are fenced. Trace rows
        # remain writable so rejected stale attempts are still observable.
        match = re.match(r'\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|REPLACE\s+INTO|DELETE\s+FROM)\s+(\w+)', sql, re.I)
        if match and match[1].lower() in ('topic_intelligence','game_signal','material','material_context','material_application_run','event','event_version','creative','topic_interpretation'):
            self.pending_business=True
        if not self.store or not self.store._run_token:
            return
        if not match or match[1].lower() in ('step', 'run', 'worker_lease', 'worker_fence'):
            return
        self.pending_worker=True
        started = not self.in_transaction
        if started:
            sqlite3.Connection.execute(self, 'BEGIN IMMEDIATE')
        try:
            self.store.assert_owner(self.store._run_owner)
        except Exception:
            if self.atomic_depth:
                self.atomic_failed = True
            else:
                sqlite3.Connection.rollback(self)
                self.pending_business=self.pending_worker=False
            raise

    def execute(self, sql, parameters=()):
        self._before_write(sql)
        return super().execute(sql, parameters)

    def executemany(self, sql, parameters):
        self._before_write(sql)
        return super().executemany(sql, parameters)


def initialize(store):
    store._run_owner = None
    store._run_token = None
    store._active_job = None
    store.conn.executescript('''
      CREATE TABLE IF NOT EXISTS worker_fence(name TEXT PRIMARY KEY, owner TEXT, token TEXT);
      CREATE TABLE IF NOT EXISTS delivery_receipt(
        job_id TEXT,lease_token TEXT,run_id TEXT,created_at TEXT,payload TEXT,
        PRIMARY KEY(job_id,lease_token));
    ''')
    store.conn.execute('BEGIN IMMEDIATE')
    try:
        columns = {r['name'] for r in store.conn.execute('PRAGMA table_info(work_item)')}
        for name in ('lease_token', 'context_version'):
            if name not in columns:store.conn.execute('ALTER TABLE work_item ADD COLUMN '+name+' TEXT')
    except BaseException:
        store.conn.rollback();raise
    store.conn.commit()
    from .tool_executor import initialize as initialize_tools
    initialize_tools(store)
    store.conn.store = store


def basis(store, topic_id, fingerprint=None, *, packet=None, context=True):
    row = store.conn.execute('SELECT fingerprint FROM topic WHERE topic_id=?', (topic_id,)).fetchone()
    if not row:
        raise StaleBasis('话题不存在')
    if fingerprint is not None and row[0] != fingerprint:
        raise StaleBasis('话题版本已变化')
    sources = {}
    if packet:
        for e in packet.get('evidence', []):
            sources[e['evidence_id']] = {k:e.get(k) for k in ('content_hash','url','published_at')}
    result = {'topics':{topic_id:row[0]}, 'sources':sources}
    if context:
        result['context_version'] = stable_id('context_',dump(store.context()))
    return result


class RuntimeStore:
    def heartbeat(self,run_id):
        if self._run_token:
            if not self.acquire(run_id,ttl=1200):raise LeaseLost('运行续租失败')
        if self._active_job:
            from .work import renew
            row=self.conn.execute('SELECT status FROM work_item WHERE job_id=?',(self._active_job['job_id'],)).fetchone()
            if row and row['status']=='running':renew(self,self._active_job)

    def assert_owner(self, run_id):
        row = sqlite3.Connection.execute(self.conn, '''SELECT l.owner,l.expires_at,f.token
          FROM worker_lease l LEFT JOIN worker_fence f USING(name) WHERE l.name='research' ''').fetchone()
        # Unmanaged, isolated invocations remain possible; a managed worker must
        # never silently become unmanaged after its lease has expired.
        if not row and not self._run_token:
            return
        if not row or row['owner'] != run_id or row['expires_at'] <= now_iso():
            raise LeaseLost('运行租约已失效，拒绝旧执行者写入')
        if self._run_token and row['token'] != self._run_token:
            raise LeaseLost('运行领取凭证已被替换')

    def acquire(self, owner, ttl=600):
        if type(ttl) is not int or ttl <= 0:
            raise ValueError('租约时长须为正整数')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            row = self.conn.execute("SELECT * FROM worker_lease WHERE name='research'").fetchone()
            fence = self.conn.execute("SELECT * FROM worker_fence WHERE name='research'").fetchone()
            stamp = now_iso()
            if row and row['owner'] == owner:
                if row['expires_at'] <= stamp or (self._run_token and (not fence or fence['token'] != self._run_token)):
                    self.conn.rollback()
                    return False
                token = fence['token'] if fence and fence['owner'] == owner else uuid.uuid4().hex
            else:
                if row and row['expires_at'] > stamp:
                    self.conn.rollback()
                    return False
                if row:
                    self.conn.execute("UPDATE run SET status='interrupted',finished_at=?,error=? WHERE run_id=? AND status IN ('queued','running')",
                                      (stamp,'运行租约过期；待办和已提交成果保留',row['owner']))
                    sqlite3.Connection.execute(self.conn,"UPDATE cycle SET status='interrupted',finished_at=?,error=? WHERE status IN ('queued','collecting','discovered','researching') AND run_ids LIKE ?",
                                      (stamp,'运行租约过期', '%'+row['owner']+'%'))
                    sqlite3.Connection.execute(self.conn,"UPDATE tool_execution SET status='interrupted',finished_at=?,error='worker_expired',cost_status='unknown_after_interruption' WHERE run_id=? AND status='running'",(stamp,row['owner']))
                token = uuid.uuid4().hex
            expiry = (datetime.now(timezone.utc)+timedelta(seconds=ttl)).isoformat(timespec='seconds')
            sqlite3.Connection.execute(self.conn,"INSERT OR REPLACE INTO worker_lease VALUES('research',?,?)",(owner,expiry))
            sqlite3.Connection.execute(self.conn,"INSERT OR REPLACE INTO worker_fence VALUES('research',?,?)",(owner,token))
            self.conn.commit()
            self._run_owner, self._run_token = owner, token
            return True
        except Exception:
            self.conn.rollback()
            raise

    def release(self, owner):
        if self.conn.atomic_depth:
            raise RuntimeError('交付事务内不能释放运行租约')
        # Release is a fencing operation, not a business write. Drop any unfinished
        # writes first; committing after expiry must never publish stale results.
        self.conn.rollback()
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            cursor=sqlite3.Connection.execute(self.conn, '''UPDATE worker_lease SET expires_at=? WHERE name='research' AND owner=?
              AND EXISTS(SELECT 1 FROM worker_fence WHERE name='research' AND owner=? AND token=?)''',
              (now_iso(),owner,owner,self._run_token))
            sqlite3.Connection.commit(self.conn)
        except BaseException:
            sqlite3.Connection.rollback(self.conn)
            raise
        if owner == self._run_owner:
            self._run_owner=self._run_token=None

    def check_basis(self, expected):
        if not expected:
            return
        for tid, version in expected.get('topics',{}).items():
            row = self.conn.execute('SELECT fingerprint FROM topic WHERE topic_id=?',(tid,)).fetchone()
            if not row or row[0] != version:
                raise StaleBasis('交付依据的话题版本已变化')
        if expected.get('context_version') != None and expected['context_version'] != stable_id('context_',dump(self.context())):
            raise StaleBasis('TapTap 业务条件已变化')
        for eid, version in expected.get('sources',{}).items():
            values = self.evidence([eid])
            if not values or any(values[0].get(k) != v for k,v in version.items()):
                raise StaleBasis('来源正文、URL或日期已变化，拒绝过期交付')

    @contextmanager
    def delivery(self, run_id, expected=None, *, job=None, result=None):
        """Commit business artifacts and their job receipt together, or neither."""
        outer = self.conn.atomic_depth == 0
        if outer:
            if self.conn.in_transaction:
                if self.conn.pending_business:raise RuntimeError('业务写入须在保护事务内进行')
                # Evidence snapshot reads can leave INSERT OR IGNORE bookkeeping
                # pending. Flush it before acquiring the short delivery lock.
                self.conn.commit()
            self.conn.execute('BEGIN IMMEDIATE')
            self.conn.atomic_failed = False
        self.conn.atomic_depth += 1
        try:
            self.assert_owner(run_id)
            self.check_basis(expected)
            if job:
                from .work import assert_claim
                assert_claim(self, job)
            yield
            self.assert_owner(run_id)
            self.check_basis(expected)
            if job and result is not None:
                from .work import finish
                finish(self,job,'succeeded',result=result)
            elif job:
                from .work import assert_claim
                assert_claim(self,job)
            if self.conn.atomic_failed:
                raise RuntimeError('嵌套保存失败，整份交付回滚')
        except BaseException:
            self.conn.atomic_failed = True
            if outer:
                sqlite3.Connection.rollback(self.conn)
                self.conn.pending_business=self.conn.pending_worker=False
            raise
        else:
            if outer:
                sqlite3.Connection.commit(self.conn)
                self.conn.pending_business=self.conn.pending_worker=False
        finally:
            self.conn.atomic_depth -= 1

    def step(self, run_id, kind, payload):
        # Serialise sequence allocation as well as INSERT; two independent
        # connections must not pick the same sequence for one run.
        outer = not self.conn.in_transaction
        if outer:
            self.conn.execute('BEGIN IMMEDIATE')
        try:
            super().step(run_id,kind,payload)
        except Exception:
            self.conn.rollback()
            raise

    def finish(self, run_id, status, *args, **kwargs):
        try:
            with self.delivery(run_id):
                super().finish(run_id,status,*args,**kwargs)
            return True
        except LeaseLost as error:
            self.step(run_id,'stale_finish_rejected',{'reason':str(error)})
            return False
