# -*- coding: utf-8 -*-
"""Data Lineage（§32）+ 业务漏斗（§34）+ 价值看板（§33）。

★ §32：任何一个真实指标都能追溯到"它最开始来自哪个热点"：
    event_id → analysis_id → opportunity_id → creative_id → asset_id
    → plan_id → experiment_id → metric → outcome → growth_case
  各层各自有库，lineage 负责把链拼起来 —— 拼不上的环节如实标 missing。

★ §33/§34 的纪律与全项目一致：**数字全部由代码从真实库计算**，
  没有数据的格子返回 None/insufficient，绝不编漏斗。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from execution.feed import l4_query, l4_one


class Lineage:
    def __init__(self, l4_store: Any, db: Any, memory_store: Any = None):
        self.l4 = l4_store                 # IntelligenceStore
        self.db = db                       # ExecutionDB
        self.memory = memory_store         # L5 MemoryStore（可选）

    # -------------------------------------------------------------- §32 链路
    def trace_event(self, event_id: str) -> Dict[str, Any]:
        analyses = l4_query(self.l4,
            "SELECT analysis_id, created_at, relevance_score, n_opportunities, n_creatives"
            " FROM intelligence_analysis WHERE event_id=? ORDER BY created_at", (event_id,))
        creatives = l4_query(self.l4,
            "SELECT idea_id, analysis_id, creative_type, idea_name, score, passed"
            " FROM intelligence_creative WHERE event_id=?", (event_id,))
        wf_items = self.db.query(
            "SELECT * FROM workflow_item WHERE event_id=?", (event_id,))
        plans = self.db.query(
            "SELECT * FROM execution_plan WHERE event_id=?", (event_id,))
        experiments = self.db.query(
            "SELECT experiment_id, creative_id, name, status, result_state"
            " FROM experiment WHERE event_id=?", (event_id,))
        case = None
        if self.memory is not None:
            case = self.memory.db.query_one(
                "SELECT case_id, strategy, reliability_score FROM growth_case"
                " WHERE event_id=?", (event_id,))
        return {
            "event_id": event_id,
            "analyses": analyses,
            "creatives": creatives,
            "workflow": [{"object_id": w["object_id"], "state": w["state"],
                          "owner": w["owner"]} for w in wf_items],
            "execution_plans": [{"plan_id": p["plan_id"], "status": p["status"],
                                 "channels": self.db.loads(p.get("channels"))}
                                for p in plans],
            "experiments": experiments,
            "growth_case": case,       # §25 闭环完成时非空
            "lineage_complete": bool(case) and any(
                e.get("result_state") for e in experiments),
        }

    # -------------------------------------------------------------- §34 漏斗
    def funnel(self, upstream: Any) -> Dict[str, Any]:
        """Signal → Trend → Relevant → Opportunity → Creative → Adopted → Launched → Won。

        每一级都从真实库数出来； Adoption/Launched/Won 为 0 时如实给 0
        （0 就是 0 —— 漏斗断了多少要让人看见），但不给没有的率值。
        """
        n_signals = 0
        try:
            if upstream.l2 is not None:
                n_signals = len(upstream.l2.list_content(limit=200000))
        except Exception:
            n_signals = None
        n_events = len(upstream.events(limit=5000))
        anl_rows = l4_query(self.l4,
            "SELECT COUNT(DISTINCT event_id) AS n, SUM(n_opportunities) AS opp,"
            " SUM(n_creatives) AS cre FROM intelligence_analysis")
        n_analyzed = (anl_rows[0]["n"] or 0) if anl_rows else 0
        n_opportunities = (anl_rows[0]["opp"] or 0) if anl_rows else 0
        n_creatives = self.l4.conn.execute(
            "SELECT COUNT(*) FROM intelligence_creative").fetchone()[0]
        adopted = self._count_adopted()
        launched = self.db.query_one(
            "SELECT COUNT(*) AS n FROM execution_plan"
            " WHERE status IN ('live','completed')")["n"]
        won = self.db.query_one(
            "SELECT COUNT(*) AS n FROM experiment WHERE result_state='WIN'")["n"]
        return {
            "signals": n_signals,
            "events": n_events,
            "analyzed_events": n_analyzed,
            "opportunities": n_opportunities or 0,
            "creatives": n_creatives,
            "adopted": adopted,
            "launched": launched,
            "won": won,
            "definition": "§34；Adopted=L5 决策 approve（或 L4 反馈 adopt）；"
                          "Launched=计划 live/completed；Won=实验 WIN",
        }

    def _count_adopted(self) -> int:
        if self.memory is not None:
            row = self.memory.db.query_one(
                "SELECT COUNT(*) AS n FROM decision_memory"
                " WHERE object_type='creative' AND decision='approve'")
            if row and row["n"]:
                return row["n"]
        row = l4_one(self.l4,
            "SELECT COUNT(*) AS n FROM human_feedback WHERE decision='adopt'")
        return (row["n"] or 0) if row else 0

    # -------------------------------------------------------------- §33 价值看板
    def value(self, workflow: Any, upstream: Any) -> Dict[str, Any]:
        """§33：负责人看的不是"Agent 调了多少次"，而是发现提前量/时延/采用率/实验胜率。"""
        tta = workflow.time_to_action()
        funnel = self.funnel(upstream)
        n_creatives = funnel["creatives"]
        adoption = round(funnel["adopted"] / n_creatives, 4) if n_creatives else None
        completed = self.db.query_one(
            "SELECT COUNT(*) AS n FROM experiment WHERE status IN ('completed','stopped')")["n"]
        won = funnel["won"]
        win_rate = round(won / completed, 4) if completed else None
        return {
            "funnel": funnel,
            "median_time_to_insight_h": tta["time_to_insight_h"],
            "median_time_to_decision_h": tta["time_to_decision_h"],
            "median_time_to_action_h": tta["time_to_action_h"],
            "tta_samples": {k: tta[k] for k in ("n_insight", "n_decision", "n_action")},
            "creative_adoption": adoption,
            "positive_experiment_rate": win_rate,
            # §33 Incremental UGC / Follow / Registration：需要 §31 归因（A/B/holdout/DiD）
            # 有真实增量数据之前如实 None —— "AI 带来的增量"不允许拍脑袋。
            "incremental_outcomes": None,
            "incremental_note": "需要 §31 归因设计（A/B / Holdout / DiD / Matching）落地后计算",
        }
