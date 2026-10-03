# -*- coding: utf-8 -*-
"""第四层存储（§44 产物版本化 / §35 人工反馈 / §46 缓存）。

★ §44：同一 Event 会被反复分析（11:00 Emerging、12:00 Growing…）→ **不覆盖**，
        每次一个 `analysis_id`，可对比 v1/v2/v3。
★ §35：人工的 adopt/reject/edit + reason 本身就是训练数据，必须存。
★ §46：15 分钟内重复触发不要重跑 —— cache key = event_id + input_hash，
        只有"Event 发生实质变化"才重跑。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB_DEFAULT = os.path.join(_ROOT, "data", "state", "l4_intelligence.sqlite3")

_DDL = """
CREATE TABLE IF NOT EXISTS intelligence_analysis (
    analysis_id      TEXT PRIMARY KEY,
    event_id         TEXT,
    analysis_version TEXT,
    created_at       TEXT,
    input_hash       TEXT,
    engine           TEXT,
    tier             TEXT,
    status           TEXT,
    relevance_score  REAL,
    n_opportunities  INTEGER,
    n_creatives      INTEGER,
    mean_score       REAL,
    risk_level       TEXT,
    llm_used         INTEGER DEFAULT 0,
    payload          TEXT
);

CREATE TABLE IF NOT EXISTS intelligence_creative (
    idea_id      TEXT,
    analysis_id  TEXT,
    event_id     TEXT,
    creative_type TEXT,
    idea_name    TEXT,
    score        REAL,
    passed       INTEGER,
    source_refs  TEXT,
    payload      TEXT,
    PRIMARY KEY (analysis_id, idea_id)
);

CREATE TABLE IF NOT EXISTS human_feedback (
    feedback_id TEXT PRIMARY KEY,
    idea_id     TEXT,
    event_id    TEXT,
    decision    TEXT,
    reason      TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    event_id    TEXT PRIMARY KEY,
    input_hash  TEXT,
    analysis_id TEXT,
    created_at  TEXT
);
"""

# §45 Token / Cost Gate 分级
TIERS = ("T0", "T1", "T2", "T3")


def tier_of(event: Dict[str, Any], temporal_ok: bool = False) -> str:
    """§45：绝不能 5 万个事件全跑全套 Agent。

    ★★ 关键修正（2026-10-02 实测踩到）：规格的 T0 门槛是 `hot < .40`，
    但**在本项目现有数据上 hot_score 是结构性失效的**（第三层已证明：
    数据无时间分辨率 → velocity/acceleration 恒为 0 → hot 普遍 < .35）。
    照抄规格的结果是 **211 个事件全部被 T0 挡掉，第四层产出 0 条**。

    所以闸门分两种依据，且**如实标出用的是哪一种**（`gate_basis`）：
      - temporal_ok=True  → 按规格：hot / momentum / confidence
      - temporal_ok=False → 降级闸门：confidence + 体量（第三层结论：这两个在当前数据上可信）
    这不是绕过门槛，是**换一个在数据上有效的门槛**；等连续采集接通后自动切回规格口径。
    """
    if temporal_ok:
        hot = float(event.get("hot_score") or 0)
        mom = float(event.get("momentum_score") or 0)
        conf = float(event.get("confidence_score") or 0)
        if hot < 0.40:
            return "T0"        # 不调用 LLM
        if mom < 0.70 or conf < 0.60:
            return "T1"        # 轻量分析
        if conf >= 0.60:
            return "T2"        # 完整 AI workflow
        return "T3"            # 高价值 + 证据不足 → Deep Research
    # —— 降级闸门（数据无时间分辨率时）——
    conf = float(event.get("confidence_score") or 0)
    n = int(event.get("content_count") or 0)
    if conf >= 0.50 and n >= 10:
        return "T2"
    if conf >= 0.40 and n >= 3:
        return "T1"
    return "T0"


def gate_basis(temporal_ok: bool) -> str:
    return "hot_momentum_confidence(spec §45)" if temporal_ok else "confidence_volume(degraded: 无时间分辨率)"


def input_hash(event: Dict[str, Any]) -> str:
    """§46：只有实质变化才重跑。取"会影响结论"的字段，不含 last_updated_at。"""
    material = {
        "event_id": event.get("event_id"),
        "lifecycle": event.get("lifecycle"),
        "content_count": event.get("content_count"),
        "platform_count": event.get("platform_count"),
        "hot": round(float(event.get("hot_score") or 0), 2),
        "momentum": round(float(event.get("momentum_score") or 0), 2),
        "confidence": round(float(event.get("confidence_score") or 0), 2),
    }
    return hashlib.sha1(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()[:16]


class IntelligenceStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _DB_DEFAULT
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        # ★ 并发写保护：多事件并发时每个线程一套连接，会争同一个库文件。
        #   不给 busy_timeout 的话会直接抛 "database is locked"。
        self.conn.execute("PRAGMA busy_timeout=10000")
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass                      # 某些文件系统不支持 WAL，退回去即可
        self.conn.executescript(_DDL)
        self.conn.commit()

    # ---------- §46 缓存 ----------
    def cached_analysis(self, event: Dict[str, Any]) -> Optional[str]:
        r = self.conn.execute("SELECT input_hash, analysis_id FROM analysis_cache WHERE event_id=?",
                              (event.get("event_id"),)).fetchone()
        if r and r["input_hash"] == input_hash(event):
            return r["analysis_id"]
        return None

    def set_cache(self, event: Dict[str, Any], analysis_id: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO analysis_cache VALUES(?,?,?,?)",
                          (event.get("event_id"), input_hash(event), analysis_id,
                           datetime.now().isoformat()))
        self.conn.commit()

    # ---------- §44 版本化 ----------
    def save_analysis(self, event: Dict[str, Any], result: Dict[str, Any]) -> str:
        aid = f"anl_{uuid.uuid4().hex[:12]}"
        creatives = result.get("creatives") or []
        scores = [c.get("score") for c in creatives if c.get("score") is not None]
        self.conn.execute(
            "INSERT INTO intelligence_analysis VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, event.get("event_id"), result.get("analysis_version"),
             datetime.now().isoformat(), input_hash(event), result.get("engine"),
             result.get("tier"), result.get("status"),
             (result.get("relevance") or {}).get("score"),
             len(result.get("opportunities") or []), len(creatives),
             round(sum(scores) / len(scores), 3) if scores else None,
             (result.get("risk") or {}).get("risk_level"),
             1 if result.get("llm_used") else 0,
             json.dumps(result, ensure_ascii=False)))
        for c in creatives:
            self.conn.execute(
                "INSERT OR REPLACE INTO intelligence_creative VALUES(?,?,?,?,?,?,?,?,?)",
                (c.get("idea_id"), aid, event.get("event_id"), c.get("creative_type"),
                 c.get("idea_name"), c.get("score"),
                 1 if (c.get("evaluation") or {}).get("pass") else 0,
                 json.dumps(c.get("source_refs") or {}, ensure_ascii=False),
                 json.dumps(c, ensure_ascii=False)))
        self.set_cache(event, aid)
        self.conn.commit()
        return aid

    def analyses_for(self, event_id: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT analysis_id, created_at, status, relevance_score, n_creatives, mean_score, "
            "risk_level, tier, engine FROM intelligence_analysis WHERE event_id=? ORDER BY created_at",
            (event_id,)).fetchall()]

    def get_analysis(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT payload FROM intelligence_analysis WHERE analysis_id=?",
                              (analysis_id,)).fetchone()
        return json.loads(r["payload"]) if r else None

    # ---------- §35 人工反馈 ----------
    def save_feedback(self, idea_id: str, event_id: str, decision: str, reason: str = "") -> str:
        fid = f"fb_{uuid.uuid4().hex[:12]}"
        self.conn.execute("INSERT INTO human_feedback VALUES(?,?,?,?,?,?)",
                          (fid, idea_id, event_id, decision, reason, datetime.now().isoformat()))
        self.conn.commit()
        return fid

    def feedback_stats(self) -> Dict[str, Any]:
        rows = [dict(r) for r in self.conn.execute(
            "SELECT decision, COUNT(*) AS n FROM human_feedback GROUP BY decision").fetchall()]
        return {r["decision"]: r["n"] for r in rows}

    def adoption_rate(self) -> Optional[float]:
        """§47 Opportunity/Creative Adoption —— 没有反馈就返回 None，不返回 0 冒充。"""
        r = self.conn.execute("SELECT COUNT(*) FROM human_feedback").fetchone()[0]
        if not r:
            return None
        a = self.conn.execute("SELECT COUNT(*) FROM human_feedback WHERE decision='adopt'").fetchone()[0]
        return round(a / r, 4)

    def stats(self) -> Dict[str, Any]:
        n_anl = self.conn.execute("SELECT COUNT(*) FROM intelligence_analysis").fetchone()[0]
        n_cre = self.conn.execute("SELECT COUNT(*) FROM intelligence_creative").fetchone()[0]
        n_pass = self.conn.execute("SELECT COUNT(*) FROM intelligence_creative WHERE passed=1").fetchone()[0]
        by_tier = {r[0]: r[1] for r in self.conn.execute(
            "SELECT tier, COUNT(*) FROM intelligence_analysis GROUP BY tier").fetchall()}
        return {"analyses": n_anl, "creatives": n_cre, "passed": n_pass,
                "by_tier": by_tier, "feedback": self.feedback_stats(),
                "adoption_rate": self.adoption_rate()}

    def close(self) -> None:
        self.conn.close()
