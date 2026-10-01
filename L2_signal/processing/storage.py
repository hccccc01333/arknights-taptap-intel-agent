#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Processed Store（§25 / §26 / §27）—— 标准化数据资产。

生产版推荐 PostgreSQL + pgvector；本机用 SQLite 等价实现，
表结构与规格一致（向量那张表在 embedding.py 里，独立库，因为模型一定会换）。

★ Processing State（§27）是这一部分的重点：
每条内容有 RECEIVED→VALIDATED→CLEANED→DEDUPED→ENRICHED→EMBEDDED→READY/FAILED。
以后问"为什么这条热点没被发现"，能追到是 Embedding Failed 还是 Dedup 误杀，
而不是笼统地怀疑第三层。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

_DB_DEFAULT = os.path.join("data", "state", "l2_processed.sqlite3")

_DDL = """
CREATE TABLE IF NOT EXISTS content (
    content_id          TEXT PRIMARY KEY,
    platform            TEXT,
    source_id           TEXT,
    external_id         TEXT,
    content_type        TEXT,
    raw_title           TEXT,
    raw_text            TEXT,
    normalized_title    TEXT,
    normalized_text     TEXT,
    language            TEXT,
    published_at        TEXT,
    observed_at         TEXT,
    quality_score       REAL,
    spam_score          REAL,
    gaming_probability  REAL,
    category            TEXT,
    subcategory         TEXT,
    metrics             TEXT,
    platform_metrics    TEXT,
    entities            TEXT,
    topics              TEXT,
    extracted           TEXT,
    features            TEXT,
    source_features     TEXT,
    fingerprint         TEXT,
    dedup_group         TEXT,
    is_duplicate        INTEGER DEFAULT 0,
    embedding_ref       TEXT,
    raw_ref             TEXT,
    processing_version  TEXT,
    processor_versions  TEXT,
    state               TEXT,
    created_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_content_platform ON content(platform);
CREATE INDEX IF NOT EXISTS idx_content_category ON content(category);

CREATE TABLE IF NOT EXISTS entity (
    entity_id       TEXT PRIMARY KEY,
    entity_type     TEXT,
    canonical_name  TEXT,
    metadata        TEXT
);

CREATE TABLE IF NOT EXISTS content_entity (
    content_id   TEXT,
    entity_id    TEXT,
    mention_text TEXT,
    confidence   REAL,
    PRIMARY KEY (content_id, entity_id, mention_text)
);

CREATE TABLE IF NOT EXISTS processing_job (
    job_id      TEXT PRIMARY KEY,
    content_id  TEXT,
    processor   TEXT,
    version     TEXT,
    status      TEXT,
    started_at  TEXT,
    finished_at TEXT,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS processing_dlq (
    dlq_id       TEXT PRIMARY KEY,
    source_id    TEXT,
    platform     TEXT,
    schema_version TEXT,
    error_field  TEXT,
    error_type   TEXT,
    detail       TEXT,
    raw_ref      TEXT,
    event_id     TEXT,
    external_id  TEXT,
    failed_at    TEXT
);

CREATE TABLE IF NOT EXISTS quality_metrics (
    run_id           TEXT,
    metric           TEXT,
    value            REAL,
    recorded_at      TEXT
);
"""


class ProcessedStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.db_path = db_path or os.path.join(root, _DB_DEFAULT)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_DDL)
        self.conn.commit()

    # ---------- content ----------
    def upsert_content(self, c: Dict[str, Any]) -> None:
        row = dict(c)
        for k in ("metrics", "platform_metrics", "entities", "topics", "extracted",
                  "features", "source_features", "processor_versions", "subcategory"):
            if k in row and not isinstance(row[k], str):
                row[k] = json.dumps(row[k], ensure_ascii=False)
        cols = ",".join(row.keys())
        marks = ",".join(":" + k for k in row.keys())
        updates = ",".join(f"{k}=excluded.{k}" for k in row.keys() if k != "content_id")
        self.conn.execute(
            f"INSERT INTO content ({cols}) VALUES ({marks}) "
            f"ON CONFLICT(content_id) DO UPDATE SET {updates}", row)
        self.conn.commit()

    def get_content(self, content_id: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM content WHERE content_id=?", (content_id,)).fetchone()
        return dict(r) if r else None

    def count_content(self, where: str = "", args: Optional[list] = None) -> int:
        sql = "SELECT COUNT(*) FROM content" + (f" WHERE {where}" if where else "")
        return self.conn.execute(sql, args or []).fetchone()[0]

    def list_content(self, limit: int = 10, where: str = "", args: Optional[list] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM content" + (f" WHERE {where}" if where else "") + " LIMIT ?"
        return [dict(r) for r in self.conn.execute(sql, (args or []) + [limit]).fetchall()]

    # ---------- entity ----------
    def upsert_entity(self, entity_id: str, entity_type: str, canonical_name: str,
                      metadata: Optional[Dict] = None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO entity VALUES(?,?,?,?)",
                          (entity_id, entity_type, canonical_name,
                           json.dumps(metadata or {}, ensure_ascii=False)))
        self.conn.commit()

    def link_content_entity(self, content_id: str, entity_id: str,
                            mention: str, confidence: float) -> None:
        self.conn.execute("INSERT OR REPLACE INTO content_entity VALUES(?,?,?,?)",
                          (content_id, entity_id, mention, confidence))
        self.conn.commit()

    def entity_stats(self, limit: int = 15) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT e.entity_id, e.entity_type, e.canonical_name, COUNT(ce.content_id) AS n "
            "FROM entity e LEFT JOIN content_entity ce ON e.entity_id=ce.entity_id "
            "GROUP BY e.entity_id ORDER BY n DESC LIMIT ?", (limit,)).fetchall()]

    # ---------- processing job ----------
    def record_job(self, job: Dict[str, Any]) -> None:
        self.conn.execute("INSERT OR REPLACE INTO processing_job VALUES(?,?,?,?,?,?,?,?)",
                          (job["job_id"], job.get("content_id", ""), job.get("processor", ""),
                           job.get("version", ""), job.get("status", ""), job.get("started_at", ""),
                           job.get("finished_at", ""), job.get("error", "")))
        self.conn.commit()

    def state_counts(self) -> Dict[str, int]:
        return {r["state"]: r["n"] for r in self.conn.execute(
            "SELECT state, COUNT(*) AS n FROM content GROUP BY state").fetchall()}

    # ---------- DLQ ----------
    def push_dlq(self, rec: Dict[str, Any]) -> None:
        import uuid
        self.conn.execute("INSERT OR REPLACE INTO processing_dlq VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (uuid.uuid4().hex[:16], rec.get("source_id", ""), rec.get("platform", ""),
                           rec.get("schema_version", ""), rec.get("error_field", ""),
                           rec.get("error_type", ""), rec.get("detail", ""), rec.get("raw_ref", ""),
                           rec.get("event_id", ""), rec.get("external_id", ""),
                           datetime.now().isoformat()))
        self.conn.commit()

    def dlq_items(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM processing_dlq ORDER BY failed_at DESC LIMIT ?", (limit,)).fetchall()]

    def count_dlq(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM processing_dlq").fetchone()[0]

    # ---------- quality metrics ----------
    def record_quality(self, run_id: str, metrics: Dict[str, float]) -> None:
        now = datetime.now().isoformat()
        for k, v in metrics.items():
            self.conn.execute("INSERT INTO quality_metrics VALUES(?,?,?,?)", (run_id, k, v, now))
        self.conn.commit()

    def latest_quality(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for r in self.conn.execute(
                "SELECT metric, value FROM quality_metrics WHERE run_id="
                "(SELECT run_id FROM quality_metrics ORDER BY recorded_at DESC LIMIT 1)").fetchall():
            out[r["metric"]] = r["value"]
        return out

    def close(self) -> None:
        self.conn.close()
