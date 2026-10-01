#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Metadata Store（元数据存储）—— 数据库只存索引与状态，不存大块原始响应。

规格里的生产版是 PostgreSQL；本机没有，用 **SQLite（标准库 sqlite3）** 等价实现，
语义一致：原始 JSON/HTML 一律放 Raw Lake（本地分区目录，生产换 S3/OSS），
库里只留 `raw_ref` 指针。这样换生产存储时只改这一层。

表（对齐用户给的 DDL，SQLite 化：JSONB → TEXT 存 JSON）：
    source_registry    数据源注册中心
    source_checkpoint  游标 / 断点续采
    source_health      数据源健康（一等公民）
    crawl_run          每次采集运行（lineage 的 crawl_run_id）
    metric_snapshot    指标快照索引（时间 × 指标）
    raw_index          原始数据索引（raw_ref → 分区文件）
    dlq                死信队列
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

# 中文目录不做包导入：把 L1_data_source 挂进 sys.path（与 normalize.py / adapters 同一套姿势）
_L1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _L1 not in sys.path:
    sys.path.insert(0, _L1)

from schema.content_event import now_cn  # noqa: E402

# 模块级常量：绝不做函数默认参数（本项目踩过两次）
DEFAULT_DB_PATH = os.path.join("data", "state", "l1_source_registry.sqlite3")

_DDL = """
CREATE TABLE IF NOT EXISTS source_registry (
    source_id              TEXT PRIMARY KEY,
    platform               TEXT NOT NULL,
    source_name            TEXT,
    source_type            TEXT,
    signal_type            TEXT DEFAULT 'community',
    acquisition_mode       TEXT DEFAULT 'internal',
    connector              TEXT DEFAULT 'internal',
    enabled                INTEGER DEFAULT 1,
    priority               REAL DEFAULT 0.5,
    base_interval_seconds  INTEGER DEFAULT 600,
    min_interval_seconds   INTEGER DEFAULT 60,
    max_interval_seconds   INTEGER DEFAULT 3600,
    timeout_seconds        INTEGER DEFAULT 30,
    max_concurrency        INTEGER DEFAULT 1,
    rate_limit_per_minute  INTEGER DEFAULT 60,
    freshness_slo_seconds  INTEGER DEFAULT 900,
    parser_version         TEXT,
    config                 TEXT,
    compliance_config      TEXT,
    created_at             TEXT,
    updated_at             TEXT
);

CREATE TABLE IF NOT EXISTS source_checkpoint (
    source_id          TEXT PRIMARY KEY,
    cursor             TEXT,
    last_external_id   TEXT,
    last_published_at  TEXT,
    last_success_at    TEXT,
    metadata           TEXT
);

CREATE TABLE IF NOT EXISTS source_health (
    source_id            TEXT PRIMARY KEY,
    success_rate         REAL,
    request_latency_ms   REAL,
    records_per_run      REAL,
    freshness_lag_seconds REAL,
    parse_error_rate     REAL,
    null_rate            REAL,
    duplicate_rate       REAL,
    rate_limit_count     INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    circuit_state        TEXT DEFAULT 'closed',
    updated_at           TEXT
);

CREATE TABLE IF NOT EXISTS crawl_run (
    run_id       TEXT PRIMARY KEY,
    source_id    TEXT,
    request_id   TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    status       TEXT,
    records      INTEGER DEFAULT 0,
    error_type   TEXT,
    error_detail TEXT
);

CREATE TABLE IF NOT EXISTS metric_snapshot (
    snapshot_id  TEXT PRIMARY KEY,
    source_id    TEXT,
    content_key  TEXT,
    external_id  TEXT,
    observed_at  TEXT,
    views        INTEGER,
    likes        INTEGER,
    comments     INTEGER,
    shares       INTEGER,
    favorites    INTEGER,
    rank         INTEGER,
    raw_ref      TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_content_time ON metric_snapshot(content_key, observed_at);

CREATE TABLE IF NOT EXISTS raw_index (
    raw_ref        TEXT PRIMARY KEY,
    source_id      TEXT,
    observed_at    TEXT,
    parser_version TEXT,
    size_bytes     INTEGER
);

CREATE TABLE IF NOT EXISTS dlq (
    dlq_id      TEXT PRIMARY KEY,
    source_id   TEXT,
    error_type  TEXT,
    payload     TEXT,
    failed_at   TEXT,
    replayed    INTEGER DEFAULT 0
);
"""


class MetadataStore:
    """SQLite 元数据 store。所有写操作自带 commit；连接可跨调用复用。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.db_path = db_path or os.path.join(root, DEFAULT_DB_PATH)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_DDL)
        self.conn.commit()

    # ---------- source_registry ----------
    def upsert_source(self, s: Dict[str, Any]) -> None:
        now = now_cn().isoformat()
        row = dict(s)
        row["config"] = json.dumps(row.get("config") or {}, ensure_ascii=False)
        row["compliance_config"] = json.dumps(row.get("compliance_config") or {}, ensure_ascii=False)
        row.setdefault("created_at", now)
        row["updated_at"] = now
        cols = ",".join(row.keys())
        marks = ",".join(":" + k for k in row.keys())
        updates = ",".join(f"{k}=excluded.{k}" for k in row.keys() if k != "source_id")
        self.conn.execute(
            f"INSERT INTO source_registry ({cols}) VALUES ({marks}) "
            f"ON CONFLICT(source_id) DO UPDATE SET {updates}", row)
        self.conn.commit()

    def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM source_registry WHERE source_id=?", (source_id,)).fetchone()
        return self._load_json(r)

    def list_sources(self, enabled_only: bool = False, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM source_registry WHERE 1=1"
        args: List[Any] = []
        if enabled_only:
            sql += " AND enabled=1"
        if platform:
            sql += " AND platform=?"
            args.append(platform)
        sql += " ORDER BY priority DESC, source_id"
        return [self._load_json(r) for r in self.conn.execute(sql, args).fetchall()]

    @staticmethod
    def _load_json(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        for k in ("config", "compliance_config", "metadata"):
            if k in d and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except (ValueError, TypeError):
                    pass
        return d

    # ---------- checkpoint ----------
    def save_checkpoint(self, source_id: str, cursor: Optional[str] = None,
                        last_external_id: Optional[str] = None,
                        last_published_at: Optional[str] = None,
                        metadata: Optional[Dict] = None) -> None:
        self.conn.execute(
            "INSERT INTO source_checkpoint(source_id,cursor,last_external_id,last_published_at,last_success_at,metadata)"
            " VALUES(?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
            "cursor=excluded.cursor,last_external_id=excluded.last_external_id,"
            "last_published_at=excluded.last_published_at,last_success_at=excluded.last_success_at,"
            "metadata=excluded.metadata",
            (source_id, cursor, last_external_id, last_published_at, now_cn().isoformat(),
             json.dumps(metadata or {}, ensure_ascii=False)))
        self.conn.commit()

    def get_checkpoint(self, source_id: str) -> Dict[str, Any]:
        r = self.conn.execute("SELECT * FROM source_checkpoint WHERE source_id=?", (source_id,)).fetchone()
        return self._load_json(r) or {"source_id": source_id}

    # ---------- health ----------
    def update_health(self, source_id: str, **metrics: Any) -> None:
        metrics["updated_at"] = now_cn().isoformat()
        cols = ",".join(metrics.keys())
        marks = ",".join(":" + k for k in metrics.keys())
        updates = ",".join(f"{k}=excluded.{k}" for k in metrics.keys())
        self.conn.execute(
            "INSERT INTO source_health(source_id," + cols + ") VALUES(:source_id," + marks + ")"
            " ON CONFLICT(source_id) DO UPDATE SET " + updates,
            {"source_id": source_id, **metrics})
        self.conn.commit()

    def get_health(self, source_id: str) -> Dict[str, Any]:
        r = self.conn.execute("SELECT * FROM source_health WHERE source_id=?", (source_id,)).fetchone()
        return dict(r) if r else {}

    def all_health(self) -> Dict[str, Dict[str, Any]]:
        return {r["source_id"]: dict(r) for r in self.conn.execute("SELECT * FROM source_health").fetchall()}

    # ---------- crawl_run ----------
    def record_run(self, run: Dict[str, Any]) -> None:
        cols = ",".join(run.keys())
        marks = ",".join(":" + k for k in run.keys())
        self.conn.execute(f"INSERT OR REPLACE INTO crawl_run ({cols}) VALUES ({marks})", run)
        self.conn.commit()

    def recent_runs(self, source_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM crawl_run"
        args: List[Any] = []
        if source_id:
            sql += " WHERE source_id=?"
            args.append(source_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # ---------- metric_snapshot ----------
    def save_snapshot(self, snap: Dict[str, Any]) -> None:
        cols = ",".join(snap.keys())
        marks = ",".join(":" + k for k in snap.keys())
        self.conn.execute(
            f"INSERT INTO metric_snapshot ({cols}) VALUES ({marks}) "
            f"ON CONFLICT(snapshot_id) DO NOTHING", snap)
        self.conn.commit()

    def snapshots_of(self, content_key: str, limit: int = 50) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM metric_snapshot WHERE content_key=? ORDER BY observed_at LIMIT ?",
            (content_key, limit)).fetchall()]

    def count_snapshots(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM metric_snapshot").fetchone()[0]

    # ---------- raw_index ----------
    def index_raw(self, raw_ref: str, source_id: str, observed_at: str,
                  parser_version: str, size_bytes: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO raw_index VALUES(?,?,?,?,?)",
            (raw_ref, source_id, observed_at, parser_version, size_bytes))
        self.conn.commit()

    # ---------- DLQ ----------
    def push_dlq(self, dlq_id: str, source_id: str, error_type: str, payload: Dict) -> None:
        self.conn.execute("INSERT OR REPLACE INTO dlq VALUES(?,?,?,?,?,0)",
                          (dlq_id, source_id, error_type,
                           json.dumps(payload, ensure_ascii=False), now_cn().isoformat()))
        self.conn.commit()

    def dlq_items(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM dlq ORDER BY failed_at DESC LIMIT ?", (limit,)).fetchall()]

    def close(self) -> None:
        self.conn.close()
