from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Store:
    def __init__(self, path: str | Path | None = None, *, connection_factory=sqlite3.Connection):
        path = path if path is not None else ROOT / "data" / "v2" / "agent.sqlite3"
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=10, factory=connection_factory)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS evidence(
          evidence_id TEXT PRIMARY KEY, platform TEXT NOT NULL, external_id TEXT,
          kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, url TEXT,
          published_at TEXT, first_seen_at TEXT, last_seen_at TEXT, context TEXT,
          source_path TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS evidence_time ON evidence(last_seen_at);
        CREATE TABLE IF NOT EXISTS evidence_version(
          evidence_id TEXT, content_hash TEXT, captured_at TEXT, title TEXT,
          body TEXT, url TEXT, PRIMARY KEY(evidence_id,content_hash)
        );
        CREATE TABLE IF NOT EXISTS observation(
          evidence_id TEXT, observed_at TEXT, metrics TEXT NOT NULL,
          PRIMARY KEY(evidence_id, observed_at)
        );
        CREATE TABLE IF NOT EXISTS source(
          source_path TEXT PRIMARY KEY, platform TEXT, file_signature TEXT,
          last_imported_at TEXT, latest_observed_at TEXT, records INTEGER,
          status TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS run(
          run_id TEXT PRIMARY KEY, task TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL, finished_at TEXT, model TEXT,
          result TEXT, error TEXT, usage TEXT
        );
        CREATE TABLE IF NOT EXISTS step(
          run_id TEXT, sequence INTEGER, created_at TEXT, kind TEXT,
          payload TEXT, PRIMARY KEY(run_id,sequence)
        );
        CREATE TABLE IF NOT EXISTS event(
          event_id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT,
          verdict TEXT, assessment TEXT
        );
        CREATE TABLE IF NOT EXISTS event_evidence(
          event_id TEXT, evidence_id TEXT, PRIMARY KEY(event_id,evidence_id)
        );
        CREATE TABLE IF NOT EXISTS event_version(
          event_id TEXT, run_id TEXT, created_at TEXT, assessment TEXT,
          PRIMARY KEY(event_id,run_id)
        );
        CREATE TABLE IF NOT EXISTS creative(
          creative_id TEXT PRIMARY KEY, event_id TEXT, run_id TEXT, created_at TEXT,
          payload TEXT
        );
        CREATE TABLE IF NOT EXISTS connector_health(
          platform TEXT PRIMARY KEY, last_attempt TEXT, last_success TEXT,
          status TEXT, failures INTEGER, error TEXT, retry_after TEXT
        );
        CREATE TABLE IF NOT EXISTS feedback(
          feedback_id TEXT PRIMARY KEY, creative_id TEXT NOT NULL, actor TEXT,
          decision TEXT, reason TEXT, outcome TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS worker_lease(
          name TEXT PRIMARY KEY, owner TEXT, expires_at TEXT
        );
        """)

    def close(self):
        self.conn.close()

    def upsert_evidence(self, item: dict[str, Any]) -> bool:
        keys = ("evidence_id", "platform", "external_id", "kind", "title", "body", "url",
                "published_at", "first_seen_at", "last_seen_at", "context", "source_path")
        existing = self.conn.execute("SELECT 1 FROM evidence WHERE evidence_id=?",
                                     (item["evidence_id"],)).fetchone()
        self.conn.execute("""INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(evidence_id) DO UPDATE SET
          title=CASE WHEN excluded.last_seen_at>=evidence.last_seen_at THEN excluded.title ELSE evidence.title END,
          body=CASE WHEN excluded.last_seen_at>=evidence.last_seen_at AND length(excluded.body)>0 THEN excluded.body ELSE evidence.body END,
          url=COALESCE(excluded.url,evidence.url), published_at=COALESCE(excluded.published_at,evidence.published_at),
          source_path=CASE WHEN excluded.last_seen_at>=evidence.last_seen_at AND length(excluded.body)>0 THEN excluded.source_path ELSE evidence.source_path END,
          first_seen_at=MIN(evidence.first_seen_at,excluded.first_seen_at),
          last_seen_at=MAX(evidence.last_seen_at,excluded.last_seen_at)
          """, [item.get(key) for key in keys])
        self.conn.execute("INSERT OR IGNORE INTO observation VALUES(?,?,?)",
                          (item["evidence_id"], item["last_seen_at"], dump(item["metrics"])))
        self.snapshot(item["evidence_id"])
        return not bool(existing)

    def snapshot(self, evidence_id):
        row = self.conn.execute("SELECT title,body,url FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        if row is None:
            raise ValueError("证据不存在")
        key = stable_id("text_", row["title"]+"\n"+row["body"])
        self.conn.execute("INSERT OR IGNORE INTO evidence_version VALUES(?,?,?,?,?,?)", (evidence_id,key,now_iso(),row["title"],row["body"],row["url"]))
        return key

    def evidence(self, ids: list[str]) -> list[dict[str, Any]]:
        items = []
        for evidence_id in dict.fromkeys(ids[:20]):
            row = self.conn.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
            if row is None:
                continue
            item = dict(row)
            item["content_hash"] = stable_id("text_",item["title"]+"\n"+item["body"])
            item["content_scope"] = "title_only" if not item["body"] else ("source_detail" if item["source_path"] == "v2:read_source" else "collected_text_or_summary")
            observations = self.conn.execute("SELECT observed_at,metrics FROM observation WHERE evidence_id=? ORDER BY observed_at DESC LIMIT 8",
                                              (evidence_id,)).fetchall()
            item["observations"] = [{"observed_at": r[0], "metrics": json.loads(r[1])} for r in reversed(observations)]
            items.append(item)
        return items

    def candidates(self, limit: int = 24, since: str | None = None, query: str = "", platform: str = "") -> list[dict[str, Any]]:
        limit = max(1, min(80, int(limit)))
        clauses, params = [], []
        if platform:
            clauses.append("platform=?")
            params.append(platform)
        if since:
            clauses.append("last_seen_at>=?")
            params.append(since)
        if query:
            # Literal search, with bound parameters; wildcard characters are escaped.
            for term in query.split()[:6]:
                pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                clauses.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR context LIKE ? ESCAPE '\\')")
                params.extend([pattern] * 3)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute("SELECT evidence_id,platform,kind,title,url,published_at,last_seen_at,context FROM evidence"
                                 + where + " ORDER BY last_seen_at DESC, length(body) DESC, published_at DESC LIMIT ?", [*params, limit]).fetchall()
        return [dict(row) for row in rows]

    def create_run(self, task: str) -> str:
        run_id = "run_" + uuid.uuid4().hex
        self.conn.execute("INSERT INTO run(run_id,task,status,created_at) VALUES(?,?,?,?)",
                          (run_id, task, "queued", now_iso()))
        self.conn.commit()
        return run_id

    def step(self, run_id: str, kind: str, payload: Any):
        sequence = self.conn.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM step WHERE run_id=?", (run_id,)).fetchone()[0]
        self.conn.execute("INSERT INTO step VALUES(?,?,?,?,?)", (run_id, sequence, now_iso(), kind, dump(payload)))
        self.conn.commit()

    def finish(self, run_id: str, status: str, result=None, error=None, model=None, usage=None):
        self.conn.execute("UPDATE run SET status=?,finished_at=?,result=?,error=?,model=?,usage=? WHERE run_id=?",
                          (status, now_iso(), dump(result) if result is not None else None,
                           error, model, dump(usage or {}), run_id))
        self.conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM run WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["result"] = json.loads(result["result"]) if result["result"] else None
        result["usage"] = json.loads(result["usage"] or "{}")
        result["steps"] = [{**dict(r), "payload": json.loads(r["payload"])} for r in self.conn.execute("SELECT * FROM step WHERE run_id=? ORDER BY sequence", (run_id,))]
        return result

    def events(self, limit=50) -> list[dict[str, Any]]:
        return [{**dict(r), "assessment": json.loads(r["assessment"])} for r in self.conn.execute("SELECT * FROM event ORDER BY updated_at DESC LIMIT ?", (limit,))]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM event WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            return None
        result = {**dict(row), "assessment": json.loads(row["assessment"])}
        ids = [r[0] for r in self.conn.execute("SELECT evidence_id FROM event_evidence WHERE event_id=?", (event_id,))]
        result["evidence"] = self.evidence(list(dict.fromkeys([*result["assessment"]["evidence_ids"],*ids])))
        result["source_snapshots"] = []
        for eid,version in result["assessment"].get("source_versions",{}).items():
            snapshot = self.conn.execute("SELECT * FROM evidence_version WHERE evidence_id=? AND content_hash=?",(eid,version)).fetchone()
            if snapshot:
                result["source_snapshots"].append(dict(snapshot))
        result["versions"] = [{"run_id": r[0], "created_at": r[1], "assessment": json.loads(r[2])} for r in self.conn.execute("SELECT run_id,created_at,assessment FROM event_version WHERE event_id=? ORDER BY created_at", (event_id,))]
        from .tracking import event_changes, compare_observations
        result["changes"] = event_changes(result["versions"])
        result["trend"] = compare_observations(self, ids)
        return result

    def save_assessment(self, run_id: str, assessment: dict[str, Any]) -> str:
        ids = assessment["evidence_ids"]
        event_id = assessment.get("event_id")
        if event_id and self.get_event(event_id) is None:
            raise ValueError("引用的事件不存在")
        if not event_id:
            # Semantic updates must name an existing event explicitly. A shared
            # citation alone does not prove that two judgments concern one event.
            event_id = "event_" + uuid.uuid4().hex
        timestamp = now_iso()
        assessment = {**assessment, "event_id": event_id,
                      "source_versions":{eid:self.snapshot(eid) for eid in ids}}
        self.conn.execute("INSERT INTO event VALUES(?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET title=excluded.title,updated_at=excluded.updated_at,verdict=excluded.verdict,assessment=excluded.assessment",
                          (event_id, assessment["title"], timestamp, timestamp, assessment["verdict"], dump(assessment)))
        self.conn.execute("INSERT OR REPLACE INTO event_version VALUES(?,?,?,?)", (event_id, run_id, timestamp, dump(assessment)))
        self.conn.executemany("INSERT OR IGNORE INTO event_evidence VALUES(?,?)", [(event_id, eid) for eid in ids])
        for index, creative in enumerate(assessment.get("creatives") or []):
            creative_id = stable_id("creative_", event_id + "|" + json.dumps(creative,sort_keys=True,ensure_ascii=False))
            self.conn.execute("INSERT OR IGNORE INTO creative VALUES(?,?,?,?,?)", (creative_id, event_id, run_id, timestamp, dump({**creative, "evidence_ids": ids, "review_status": "unreviewed"})))
        return event_id

    def save_assessments(self, run_id: str, assessments: list[dict[str, Any]]) -> list[str]:
        # One transaction: a rejected event cannot leave half of a delivery saved.
        with self.conn:
            return [self.save_assessment(run_id, assessment) for assessment in assessments]

    def overview(self) -> dict[str, Any]:
        counts = {table: self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("evidence", "observation", "event", "creative", "run")}
        sources = [dict(r) for r in self.conn.execute("SELECT * FROM source ORDER BY platform,source_path")]
        runs = [{k: row[k] for k in ("run_id", "task", "status", "created_at", "finished_at", "model", "error")} for row in self.conn.execute("SELECT * FROM run ORDER BY created_at DESC LIMIT 12")]
        creatives = [{**dict(r), "payload": json.loads(r["payload"])} for r in self.conn.execute("SELECT * FROM creative ORDER BY created_at DESC LIMIT 30")]
        for source in sources:
            observed = source["latest_observed_at"]
            source["freshness"] = "missing" if not observed else ("stale" if observed < (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds") else "recent")
            if observed and observed>(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec="seconds"):
                source["freshness"]="invalid"
        feedback = [{**dict(r), "outcome": json.loads(r["outcome"])} for r in self.conn.execute("SELECT * FROM feedback ORDER BY created_at DESC LIMIT 100")]
        return {"version": "2.0.0", "counts": counts, "sources": sources, "runs": runs,
                "events": self.events(), "creatives": creatives, "feedback": feedback}

    def add_feedback(self, creative_id, actor, decision, reason, outcome=None):
        if decision not in ("accepted", "revise", "rejected"):
            raise ValueError("反馈类型须为采用、调整或拒绝")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError("请填写具体原因（最多 2000 字）")
        if not self.conn.execute("SELECT 1 FROM creative WHERE creative_id=?", (creative_id,)).fetchone():
            raise ValueError("创意不存在")
        outcome = outcome or {}
        if not isinstance(outcome, dict) or len(dump(outcome)) > 5000:
            raise ValueError("执行结果必须是有限的记录对象")
        feedback_id = "feedback_" + uuid.uuid4().hex
        self.conn.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?,?)", (feedback_id,creative_id,actor,decision,reason.strip(),dump(outcome),now_iso()))
        self.conn.commit()
        return {"feedback_id": feedback_id, "decision": decision, "note": "仅记录运营反馈；采用不等于已投放或已产生增长。"}

    def context(self):
        row = self.conn.execute("SELECT value FROM settings WHERE key='business_context'").fetchone()
        return json.loads(row[0]) if row else {
            "goal": "通过全网热点识别 TapTap 增长机会，产出具体创意与可用于制作的素材方案；每条说明目标用户和预期用户动作。",
            "audience": "TapTap 现有用户及可能被热点吸引的潜在用户；结合话题需求识别人群，不预设只关注现有游戏社区玩家。",
            "placements": "根据创意提出站外传播入口与 TapTap 内承接路径；具体位置、产品能力与发布资源待运营确认，不固定只做社区帖。",
            "resources": "目前未确认设计、活动预算和发布资源，不假定可直接投放。",
            "constraints": "先交付审核草案；不虚构热度、预期增长率、奖励或素材授权。",
        }

    def set_context(self, value):
        required = ("goal", "audience", "placements", "resources", "constraints")
        if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or len(value[k]) > 2000 for k in required) or not value.get("goal", "").strip():
            raise ValueError("业务配置需包含目标、人群、位置、资源和约束，每项最多 2000 字")
        self.conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("business_context", dump({k:value[k].strip() for k in required})))
        self.conn.commit()
        return self.context()

    def model_setting(self):
        row=self.conn.execute("SELECT value FROM settings WHERE key='model'").fetchone()
        return json.loads(row[0]) if row else None

    def set_model(self,value):
        if value not in ("openrouter/free","deepseek-chat","deepseek-flash","deepseek-v4-flash","nvidia/nemotron-3-ultra-550b-a55b:free","nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"):
            raise ValueError("请选择已支持的模型配置")
        from .model import model_status
        status=model_status(value)
        if not status["configured"]:
            raise ValueError("该模型提供商尚未配置凭证")
        self.conn.execute("INSERT INTO settings VALUES('model',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(value),))
        self.conn.commit()
        return status

    def active_owner(self):
        row = self.conn.execute("SELECT owner FROM worker_lease WHERE name='research' AND expires_at>?", (now_iso(),)).fetchone()
        return row[0] if row else None

    def acquire(self, owner, ttl=600):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT owner,expires_at FROM worker_lease WHERE name='research'").fetchone()
            if row and row["expires_at"] > now_iso() and row["owner"] != owner:
                self.conn.rollback()
                return False
            if row and row["owner"] != owner:
                self.conn.execute("UPDATE run SET status='interrupted',finished_at=?,error=? WHERE run_id=? AND status IN ('running','queued')",
                                  (now_iso(),"运行租约已过期，进度保留；请启动新研究。",row["owner"]))
            expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat(timespec="seconds")
            self.conn.execute("INSERT INTO worker_lease VALUES('research',?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at", (owner,expires))
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def release(self, owner):
        self.conn.execute("UPDATE worker_lease SET expires_at=? WHERE name='research' AND owner=?", (now_iso(),owner))
        self.conn.commit()
