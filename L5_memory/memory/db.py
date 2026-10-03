# -*- coding: utf-8 -*-
"""第五层存储底座：SQLite schema + 连接管理。

★ 设计 §33 / §56 / §57 明确 MVP **不上**图数据库、**不上**专用 Vector DB；
  本项目连 PostgreSQL 都不上 —— 全项目惯例是 stdlib + SQLite（L3/L4 同款），
  语义检索用词面 bigram 顶住（retrieval.py 标 backend=lexical_bigram，不假装有向量），
  L2 的 embedding 库有数据后可换 backend，接口不变。

★ 设计 §3：读路径与写路径分离 —— 本模块只管库和 schema；
  写走 `store.py`（受 Write Policy 治理），读走 `retrieval.py`。
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "l5-memory-1.0"

_DDL = """
CREATE TABLE IF NOT EXISTS memory_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- §5 Business Knowledge + §6 Entity Knowledge（事实型知识，含时效与版本）
CREATE TABLE IF NOT EXISTS knowledge_item (
    item_id     TEXT PRIMARY KEY,
    memory_type TEXT,                -- business | entity
    subject     TEXT,                -- 自然键：asset_id / 实体名
    title       TEXT,
    payload     TEXT,                -- JSON（结构化事实，不放原文）
    tags        TEXT,                -- JSON list（检索辅助）
    tier        TEXT DEFAULT 'candidate',   -- §53 verified | candidate | agent_generated
    authority   TEXT DEFAULT 'P3',   -- §39 P0..P4
    source      TEXT,
    source_type TEXT,
    confidence  REAL DEFAULT 0.5,
    version     INTEGER DEFAULT 1,   -- §23 知识版本化
    valid_from  TEXT,                -- §22 时效（不是 active=true）
    valid_to    TEXT,
    verified_by TEXT,
    human_verified INTEGER DEFAULT 0,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_subject ON knowledge_item(memory_type, subject);

-- §8 Trend Memory
CREATE TABLE IF NOT EXISTS trend_memory (
    trend_id     TEXT PRIMARY KEY,
    event_id     TEXT UNIQUE,
    title        TEXT,
    event_type   TEXT,
    entities     TEXT,               -- JSON list
    started_at   TEXT,
    peak_at      TEXT,
    ended_at     TEXT,
    lifecycle_duration_hours REAL,
    peak_hot_score  REAL,
    final_hot_score REAL,
    platform_diffusion TEXT,         -- JSON list
    diffusion_path TEXT,             -- JSON：平台顺序 + 时延（§9 传播先验）
    audiences    TEXT,               -- JSON
    narratives   TEXT,               -- JSON
    content_count  INTEGER,
    platform_count INTEGER,
    outcome      TEXT,               -- JSON 结局备注
    tier TEXT DEFAULT 'candidate', authority TEXT DEFAULT 'P3',
    source TEXT, source_type TEXT, confidence REAL DEFAULT 0.5,
    version INTEGER DEFAULT 1, valid_from TEXT, valid_to TEXT,
    verified_by TEXT, human_verified INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_trend_event_type ON trend_memory(event_type);

-- §11 Creative Memory —— 连被拒绝的都要存（§10：失败方案也是信息）
CREATE TABLE IF NOT EXISTS creative_memory (
    creative_id  TEXT PRIMARY KEY,   -- 确定性 id：cre_<analysis_id>_<idea_id>
    event_id     TEXT,
    opportunity_id TEXT,
    analysis_id  TEXT,
    creative_type TEXT,
    title        TEXT,
    target_audience TEXT,
    user_motivation TEXT,
    growth_mechanism TEXT,
    concept      TEXT,               -- 摘要级，不带原文（与 L4 State 同纪律）
    primary_metric TEXT,
    launch_window TEXT,
    estimated_cost TEXT,
    evaluator_score REAL,
    passed       INTEGER,
    human_decision TEXT DEFAULT 'pending',   -- pending | approve | reject | edit
    rejection_reason TEXT,           -- §17 taxonomy
    tier TEXT DEFAULT 'agent_generated', authority TEXT DEFAULT 'P4',
    source TEXT, source_type TEXT, confidence REAL DEFAULT 0.5,
    version INTEGER DEFAULT 1, valid_from TEXT, valid_to TEXT,
    verified_by TEXT, human_verified INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_creative_event ON creative_memory(event_id);
CREATE INDEX IF NOT EXISTS idx_creative_decision ON creative_memory(human_decision);

-- §13 Experiment Memory（Creative ≠ Experiment：创意是假设，实验才是真实反馈）
CREATE TABLE IF NOT EXISTS experiment_memory (
    experiment_id TEXT PRIMARY KEY,
    creative_id   TEXT,
    event_id      TEXT,
    experiment_name TEXT,
    experiment_type TEXT,
    audience_segment TEXT,
    treatment     TEXT,
    control_definition TEXT,
    started_at    TEXT,
    ended_at      TEXT,
    primary_metric TEXT,
    status        TEXT,              -- planned | running | completed | aborted
    result_summary TEXT,
    statistical_significance REAL,
    context       TEXT,              -- JSON §15：渠道/游戏/资源位/季节/预算/版本……
    reliability_score REAL,          -- §35
    tier TEXT DEFAULT 'candidate', authority TEXT DEFAULT 'P1',
    source TEXT, source_type TEXT, confidence REAL DEFAULT 0.8,
    version INTEGER DEFAULT 1, valid_from TEXT, valid_to TEXT,
    verified_by TEXT, human_verified INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_experiment_creative ON experiment_memory(creative_id);

-- §13 保存相对 lift 而不仅是绝对数（§14）
CREATE TABLE IF NOT EXISTS experiment_metric (
    experiment_id TEXT,
    metric_name   TEXT,
    baseline_value     REAL,
    experiment_value   REAL,
    absolute_lift      REAL,
    relative_lift      REAL,
    p_value            REAL,
    sample_size        INTEGER,
    PRIMARY KEY (experiment_id, metric_name)
);

-- §16 Decision Memory（什么类型的 AI 建议业务团队真正愿意采用）
CREATE TABLE IF NOT EXISTS decision_memory (
    decision_id  TEXT PRIMARY KEY,
    object_type  TEXT,               -- creative | opportunity | playbook ...
    object_id    TEXT,
    decision     TEXT,               -- approve | reject | edit
    reason_code  TEXT,               -- §17 taxonomy
    reason_text  TEXT,
    editor_diff  TEXT,               -- JSON
    reviewer_role TEXT,
    source_ref   TEXT,               -- 反馈来自哪张表（l4.human_feedback / cli）
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_decision_object ON decision_memory(object_type, object_id);

-- §29 / §60 Growth Case —— 最有价值的 Memory 单元
CREATE TABLE IF NOT EXISTS growth_case (
    case_id      TEXT PRIMARY KEY,
    title        TEXT,
    trend_type   TEXT,
    event_id     TEXT,
    entity_ids   TEXT,               -- JSON
    audience_segments TEXT,          -- JSON
    user_motivations  TEXT,          -- JSON
    opportunity_type TEXT,
    strategy     TEXT,
    creative_type TEXT,
    creative_summary TEXT,
    growth_goal  TEXT,
    primary_metric TEXT,
    outcome      TEXT,               -- JSON lifts（无实验时如实 null）
    lessons      TEXT,               -- JSON，每条带 evidence（§31）
    anti_patterns TEXT,              -- JSON
    applicability_conditions TEXT,   -- JSON
    reliability_score REAL,          -- §34/§35
    source_refs  TEXT,               -- JSON：analysis_ids / experiment_ids 溯源
    embedding_model TEXT,
    tier TEXT DEFAULT 'candidate', authority TEXT DEFAULT 'P2',
    source TEXT, source_type TEXT, confidence REAL DEFAULT 0.6,
    version INTEGER DEFAULT 1, valid_from TEXT, valid_to TEXT,
    verified_by TEXT, human_verified INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_case_trend_type ON growth_case(trend_type);

-- §40 Anti-pattern Memory（什么失败了，Agent 以后规避）
CREATE TABLE IF NOT EXISTS anti_pattern (
    pattern_id   TEXT PRIMARY KEY,
    pattern      TEXT,
    lesson       TEXT,
    evidence_cases TEXT,             -- JSON list of case_ids
    tier TEXT DEFAULT 'candidate', authority TEXT DEFAULT 'P2',
    source TEXT, source_type TEXT, confidence REAL DEFAULT 0.6,
    version INTEGER DEFAULT 1, valid_from TEXT, valid_to TEXT,
    verified_by TEXT, human_verified INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);

-- §41 Playbook（趋势类型 → 推荐打法；§42 必须人工审批才能升级为 approved）
CREATE TABLE IF NOT EXISTS playbook (
    playbook_id  TEXT PRIMARY KEY,
    trend_type   TEXT,
    strategy     TEXT,
    steps        TEXT,               -- JSON
    status       TEXT DEFAULT 'candidate',   -- candidate | approved | superseded
    supersedes   TEXT,
    version      INTEGER DEFAULT 1,
    approved_by  TEXT,
    approved_at  TEXT,
    source_cases TEXT,                -- JSON case_ids
    tier TEXT DEFAULT 'candidate', authority TEXT DEFAULT 'P3',
    source TEXT, source_type TEXT, confidence REAL DEFAULT 0.5,
    valid_from TEXT, valid_to TEXT,
    created_at TEXT, updated_at TEXT
);

-- §18 Short-term Memory（LLM 推断 / 进行中热点；生命周期天/周，到期清扫）
CREATE TABLE IF NOT EXISTS short_term_memory (
    memory_id  TEXT PRIMARY KEY,
    kind       TEXT,                 -- active_trend | llm_inference | ...
    payload    TEXT,                 -- JSON
    expires_at TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_short_term_expiry ON short_term_memory(expires_at);

-- §52 检索观测：哪些 Memory 真正产生价值
CREATE TABLE IF NOT EXISTS retrieval_log (
    log_id   TEXT PRIMARY KEY,
    ts       TEXT,
    caller   TEXT,
    query    TEXT,
    memory_type TEXT,
    filters  TEXT,
    candidate_count INTEGER,
    returned_ids TEXT,               -- JSON
    ranking_scores TEXT,             -- JSON
    used_ids TEXT,                   -- JSON，事后 mark_used 回填
    final_decision TEXT,
    backend  TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON retrieval_log(ts);
"""


def default_db_path() -> str:
    """库文件路径：env `L5_MEMORY_DB` 可覆盖（测试用），默认 data/state/l5_memory.sqlite3。"""
    env = os.environ.get("L5_MEMORY_DB")
    if env:
        return env
    from paths import STATE          # ★ 跨层一律走根级 paths.py，不手拼路径
    return str(STATE / "l5_memory.sqlite3")


class MemoryDB:
    """裸连接：schema 初始化 + WAL/忙等（与 L3/L4 同款并发纪律）。"""

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
        except sqlite3.DatabaseError:    # pragma: no cover - 某些文件系统不支持 WAL
            pass
        self.conn.executescript(_DDL)
        self.conn.execute("INSERT OR REPLACE INTO memory_meta VALUES('schema_version',?)",
                          (SCHEMA_VERSION,))
        self.conn.commit()

    # ---- 薄封装：统一 JSON 编解码，调用方传 dict/list 即可 ----
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
        if obj is None:
            return None
        return json.dumps(obj, ensure_ascii=False)

    @staticmethod
    def loads(text: Any, default: Any = None) -> Any:
        if text is None:
            return default
        if not isinstance(text, str):
            return text
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return default

    def meta(self, key: str) -> Optional[str]:
        r = self.conn.execute("SELECT value FROM memory_meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO memory_meta VALUES(?,?)", (key, str(value)))
        self.conn.commit()

    def table_count(self, table: str) -> int:
        assert table.replace("_", "").isalnum(), f"非法表名 {table}"
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
