#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Event Store（§4 / §36 / §37）—— 事件及其完整时间序列。

★ 三件必须分开存的东西：
1. `started_at` vs `first_detected_at`（§4）—— 算 **DetectionLatency = 发现 - 发生**，
   回答"我们比外部早多久发现热点"，这是最有商业价值的指标之一。
2. `event_score_history`（§37）—— 分数只追加，**绝不 UPDATE 覆盖**，
   否则无法回答"系统第一次该报警是什么时候"。
3. `event_lifecycle` —— 状态变更历史（§32 状态机需要）。

Event 关系：Event → Content / Entity / Platform / Metric Timeseries / Lifecycle / Score History。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

_DB_DEFAULT = os.path.join("data", "state", "l3_trend.sqlite3")

_DDL = """
CREATE TABLE IF NOT EXISTS trend_event (
    event_id            TEXT PRIMARY KEY,
    canonical_title     TEXT,
    title_source        TEXT,
    event_type          TEXT,
    primary_topic       TEXT,
    status              TEXT DEFAULT 'active',
    started_at          TEXT,
    first_detected_at   TEXT,
    last_updated_at     TEXT,
    peak_at             TEXT,
    ended_at            TEXT,
    lifecycle           TEXT,
    hot_score           REAL,
    momentum_score      REAL,
    confidence_score    REAL,
    opportunity_score   REAL,
    novelty_score       REAL,
    velocity_score      REAL,
    acceleration_score  REAL,
    burst_score         REAL,
    diffusion_score     REAL,
    engagement_score    REAL,
    source_diversity_score REAL,
    credibility_score   REAL,
    content_count       INTEGER DEFAULT 0,
    platform_count      INTEGER DEFAULT 0,
    primary_platform    TEXT,
    entity_ids          TEXT,
    platforms           TEXT,
    representative_content_id TEXT,
    split_flag          INTEGER DEFAULT 0,
    parent_event_id     TEXT,
    metadata            TEXT,
    engine_version      TEXT
);

CREATE TABLE IF NOT EXISTS event_content (
    event_id          TEXT,
    content_id        TEXT,
    similarity_score  REAL,
    assigned_at       TEXT,
    assignment_version TEXT,
    PRIMARY KEY (event_id, content_id)
);

CREATE TABLE IF NOT EXISTS event_score_history (
    event_id        TEXT,
    scored_at       TEXT,
    hot_score       REAL,
    momentum_score  REAL,
    confidence_score REAL,
    velocity        REAL,
    acceleration    REAL,
    diffusion       REAL,
    engagement      REAL,
    novelty         REAL,
    lifecycle       TEXT
);

CREATE TABLE IF NOT EXISTS event_lifecycle (
    event_id   TEXT,
    from_state TEXT,
    to_state   TEXT,
    changed_at TEXT,
    reason     TEXT
);

CREATE TABLE IF NOT EXISTS event_metric_timeseries (
    event_id       TEXT,
    window         TEXT,
    window_start   TEXT,
    mention_count  INTEGER,
    unique_author_count INTEGER,
    view_delta     REAL,
    like_delta     REAL,
    comment_delta  REAL,
    platform_count INTEGER
);

CREATE TABLE IF NOT EXISTS event_merge_history (
    merge_id     TEXT PRIMARY KEY,
    kept_event_id TEXT,
    merged_event_id TEXT,
    similarity   REAL,
    merged_at    TEXT,
    reason       TEXT
);
"""


class EventStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.db_path = db_path or os.path.join(root, _DB_DEFAULT)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_DDL)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """加列迁移：老库没有 parent_event_id。缺列才加，已存在就跳过。"""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(trend_event)").fetchall()}
        if "parent_event_id" not in cols:
            self.conn.execute("ALTER TABLE trend_event ADD COLUMN parent_event_id TEXT")

    # ---------- 父子事件（§42）----------
    def set_parent(self, child_id: str, parent_id: Optional[str]) -> None:
        self.conn.execute("UPDATE trend_event SET parent_event_id=? WHERE event_id=?",
                          (parent_id, child_id))

    def clear_parents(self) -> None:
        """每轮重算前清空（父子关系是派生的，不累积）。"""
        self.conn.execute("UPDATE trend_event SET parent_event_id=NULL")

    def children_of(self, parent_id: str) -> List[str]:
        return [r["event_id"] for r in self.conn.execute(
            "SELECT event_id FROM trend_event WHERE parent_event_id=?", (parent_id,)).fetchall()]

    def parent_child_counts(self) -> Dict[str, int]:
        n_child = self.conn.execute(
            "SELECT COUNT(*) FROM trend_event WHERE parent_event_id IS NOT NULL").fetchone()[0]
        n_parent = self.conn.execute(
            "SELECT COUNT(DISTINCT parent_event_id) FROM trend_event WHERE parent_event_id IS NOT NULL"
        ).fetchone()[0]
        return {"children": n_child, "parents": n_parent}

    # ---------- event ----------
    def upsert_event(self, e: Dict[str, Any]) -> None:
        row = {k: v for k, v in e.items()}
        for k in ("entity_ids", "platforms", "metadata"):
            if k in row and not isinstance(row[k], str):
                row[k] = json.dumps(row[k], ensure_ascii=False)
        cols = ",".join(row.keys())
        marks = ",".join(":" + k for k in row.keys())
        updates = ",".join(f"{k}=excluded.{k}" for k in row.keys() if k != "event_id")
        self.conn.execute(f"INSERT INTO trend_event ({cols}) VALUES ({marks}) "
                          f"ON CONFLICT(event_id) DO UPDATE SET {updates}", row)
        self.conn.commit()

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM trend_event WHERE event_id=?", (event_id,)).fetchone()
        return dict(r) if r else None

    def list_events(self, limit: int = 50, order_by: str = "hot_score") -> List[Dict[str, Any]]:
        col = order_by if order_by in ("hot_score", "momentum_score", "confidence_score",
                                       "opportunity_score", "last_updated_at") else "hot_score"
        return [dict(r) for r in self.conn.execute(
            f"SELECT * FROM trend_event ORDER BY {col} DESC LIMIT ?", (limit,)).fetchall()]

    def count_events(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM trend_event").fetchone()[0]

    def lifecycle_counts(self) -> Dict[str, int]:
        """只统计活跃事件：被合并的（status='merged'）不该再占生命周期分布。"""
        return {r["lifecycle"]: r["n"] for r in self.conn.execute(
            "SELECT lifecycle, COUNT(*) AS n FROM trend_event "
            "WHERE status='active' GROUP BY lifecycle").fetchall()}

    # ---------- members ----------
    def link_content(self, event_id: str, content_id: str, sim: float, version: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO event_content VALUES(?,?,?,?,?)",
                          (event_id, content_id, sim, datetime.now().isoformat(), version))
        self.conn.commit()

    def members(self, event_id: str) -> List[str]:
        return [r["content_id"] for r in self.conn.execute(
            "SELECT content_id FROM event_content WHERE event_id=?", (event_id,)).fetchall()]

    def count_members(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM event_content").fetchone()[0]

    # ---------- history（只追加，不覆盖）----------
    def append_score_history(self, h: Dict[str, Any]) -> None:
        cols = ",".join(h.keys())
        marks = ",".join(":" + k for k in h.keys())
        self.conn.execute(f"INSERT INTO event_score_history ({cols}) VALUES ({marks})", h)
        self.conn.commit()

    def score_history(self, event_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM event_score_history WHERE event_id=? ORDER BY scored_at LIMIT ?",
            (event_id, limit)).fetchall()]

    def append_lifecycle(self, event_id: str, frm: Optional[str], to: str, reason: str) -> None:
        self.conn.execute("INSERT INTO event_lifecycle VALUES(?,?,?,?,?)",
                          (event_id, frm, to, datetime.now().isoformat(), reason))
        self.conn.commit()

    def save_timeseries(self, event_id: str, window: str, rows: List[Dict[str, Any]]) -> None:
        for r in rows:
            self.conn.execute("INSERT OR REPLACE INTO event_metric_timeseries VALUES(?,?,?,?,?,?,?,?,?)",
                              (event_id, window, r["window_start"], r["mention_count"],
                               r["unique_author_count"], r["view_delta"], r["like_delta"],
                               r["comment_delta"], r["platform_count"]))
        self.conn.commit()

    def record_merge(self, kept: str, merged: str, sim: float, reason: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO event_merge_history VALUES(?,?,?,?,?,?)",
                          (uuid.uuid4().hex[:16], kept, merged, sim, datetime.now().isoformat(), reason))
        # ★ 被合并的事件必须标 status='merged'：否则它会继续参与统计、继续给第四层报警，
        #   而它的成员已经归到 kept 里 —— 会出现"同一个事件报两次"。
        self.conn.execute("UPDATE trend_event SET status='merged' WHERE event_id=?", (merged,))
        self.conn.commit()

    def count_active_events(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM trend_event WHERE status='active'").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
