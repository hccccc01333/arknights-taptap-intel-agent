# -*- coding: utf-8 -*-
"""第六层存储底座：SQLite schema + 连接管理（与 L3/L4/L5 同款纪律：WAL + busy_timeout）。

★ §32 数据 lineage 不用单独建图——外键链就够：
  event_id → analysis_id → opportunity_id → creative_id → asset_id / plan_id
  → experiment_id → metric → outcome →（第五层）growth_case
  每张表都带全链 id，lineage.py 负责拼。
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "l6-execution-1.0"

_DDL = """
-- §12/§14 决策工作流：事件/机会/创意三类对象都走同一状态机
CREATE TABLE IF NOT EXISTS workflow_item (
    object_type   TEXT,                 -- event | opportunity | creative
    object_id     TEXT,
    event_id      TEXT,                 -- lineage 根
    analysis_id   TEXT,
    opportunity_id TEXT,
    state         TEXT DEFAULT 'DRAFT', -- DRAFT/AI_READY/REVIEWING/APPROVED/EXECUTING/LIVE/COMPLETED/REJECTED
    title         TEXT,
    owner         TEXT,                 -- §14
    reviewer      TEXT,
    deadline      TEXT,
    payload       TEXT,                 -- JSON 快照（不含原文）
    created_at    TEXT,
    updated_at    TEXT,
    PRIMARY KEY (object_type, object_id)
);
CREATE INDEX IF NOT EXISTS idx_wf_state ON workflow_item(state);

-- §12 每次状态变化都记录（可回放 → §13 Time-to-Action 的数据源）
CREATE TABLE IF NOT EXISTS workflow_event (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    object_type TEXT,
    object_id   TEXT,
    from_state  TEXT,
    to_state    TEXT,
    action      TEXT,                   -- §37 决策动作
    actor       TEXT,
    role        TEXT,
    note        TEXT,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_wfe_object ON workflow_event(object_type, object_id);

-- §18-§20 素材：策略与素材分离（§19），版本化到"哪个最终上线"（§20）
CREATE TABLE IF NOT EXISTS production_asset (
    asset_id    TEXT PRIMARY KEY,
    creative_id TEXT,
    event_id    TEXT,
    kind        TEXT,                   -- push_copy / feed_copy / campaign_brief / design_brief / ops_sop / creator_brief
    version     INTEGER,
    status      TEXT DEFAULT 'draft',   -- draft / approved / published / retired
    content     TEXT,                   -- 结构化 JSON（文案字段 + 占位符清单），数字绝不编造
    placeholders TEXT,                  -- JSON list：需要人工/LLM 补的位
    approved_by TEXT,
    published_at TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_asset_creative ON production_asset(creative_id, kind);

-- §22 Execution Plan（Level 2：审批通过后才生成；launch 必须人工）
CREATE TABLE IF NOT EXISTS execution_plan (
    plan_id      TEXT PRIMARY KEY,
    creative_id  TEXT,
    event_id     TEXT,
    channels     TEXT,                  -- JSON
    audience     TEXT,
    start_at     TEXT,
    end_at       TEXT,
    asset_ids    TEXT,                  -- JSON
    experiment_enabled INTEGER DEFAULT 0,
    control      TEXT,
    treatment    TEXT,
    status       TEXT DEFAULT 'planned',-- planned/live/paused/stopped/completed/rolled_back
    created_by   TEXT,
    created_at   TEXT,
    updated_at   TEXT
);

-- §23-§31 实验引擎：假设驱动 + 护栏 + 五种结果态（§30：失败必须允许存在）
CREATE TABLE IF NOT EXISTS experiment (
    experiment_id TEXT PRIMARY KEY,
    creative_id   TEXT,
    plan_id       TEXT,
    event_id      TEXT,
    name          TEXT,
    hypothesis    TEXT,                 -- §24：来自 L4 的 Growth Hypothesis，不是拍脑袋
    population    TEXT,
    treatment     TEXT,
    control       TEXT,
    primary_metric  TEXT,
    secondary_metrics TEXT,             -- JSON
    guardrail_metrics TEXT,             -- JSON：§28
    guardrail_max_relative_increase REAL,
    duration_hours  REAL,
    status        TEXT DEFAULT 'planned',  -- planned/running/completed/stopped/invalid
    result_state  TEXT,                 -- WIN/LOSS/INCONCLUSIVE/STOPPED/INVALID（完成时填）
    result_reason TEXT,
    guardrail_breached INTEGER DEFAULT 0,
    started_at    TEXT,
    ended_at      TEXT,
    created_at    TEXT,
    updated_at    TEXT
);

-- §28 三类指标分开记账；§14 存相对 lift
CREATE TABLE IF NOT EXISTS experiment_metric (
    experiment_id TEXT,
    metric_name   TEXT,
    metric_class  TEXT,                 -- primary / secondary / guardrail
    baseline_value REAL,
    treatment_value REAL,
    x_t INTEGER, n_t INTEGER,           -- 比例指标：treatment 成功数/样本数（z 检验用）
    x_c INTEGER, n_c INTEGER,
    absolute_lift REAL,
    relative_lift REAL,
    p_value REAL,
    sample_size INTEGER,
    PRIMARY KEY (experiment_id, metric_name)
);

-- §16 告警：不是每个热点都通知；同事件同级别同日去重
CREATE TABLE IF NOT EXISTS alert (
    alert_id   TEXT PRIMARY KEY,
    event_id   TEXT,
    tier       TEXT,                    -- P0/P1/P2
    title      TEXT,
    body       TEXT,                    -- §17：通知本身可行动
    basis      TEXT,                    -- 阈值口径（规格阈值 or 降级口径），如实标注
    created_at TEXT,
    acknowledged_by TEXT,
    acknowledged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_event ON alert(event_id, tier);

-- §44 Audit Log：谁/何时/看了/改了/批了/发了 + 模型与 prompt 版本
CREATE TABLE IF NOT EXISTS audit_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT,
    actor       TEXT,
    role        TEXT,
    action      TEXT,
    object_type TEXT,
    object_id   TEXT,
    detail      TEXT,                   -- JSON
    model_version TEXT,
    prompt_version TEXT,
    analysis_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_object ON audit_log(object_type, object_id);

CREATE TABLE IF NOT EXISTS exec_meta(key TEXT PRIMARY KEY, value TEXT);
"""


def default_db_path() -> str:
    env = os.environ.get("L6_EXECUTION_DB")
    if env:
        return env
    from paths import STATE              # ★ 跨层一律走根级 paths.py
    return str(STATE / "l6_execution.sqlite3")


class ExecutionDB:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or default_db_path()
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=10000")
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:    # pragma: no cover
            pass
        self.conn.executescript(_DDL)
        self.conn.execute("INSERT OR REPLACE INTO exec_meta VALUES('schema_version',?)",
                          (SCHEMA_VERSION,))
        self.conn.commit()

    # ---- 薄封装 ----
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def commit(self) -> None:
        self.conn.commit()

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        r = self.conn.execute(sql, params).fetchone()
        return dict(r) if r else None

    @staticmethod
    def dumps(obj: Any) -> Optional[str]:
        return None if obj is None else __import__("json").dumps(obj, ensure_ascii=False)

    @staticmethod
    def loads(text: Any, default: Any = None) -> Any:
        if text is None:
            return default
        if not isinstance(text, str):
            return text
        try:
            return __import__("json").loads(text)
        except (ValueError, TypeError):
            return default

    def meta(self, key: str) -> Optional[str]:
        r = self.conn.execute("SELECT value FROM exec_meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def table_count(self, table: str) -> int:
        assert table.replace("_", "").isalnum(), f"非法表名 {table}"
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
