# -*- coding: utf-8 -*-
"""三个工作区数据装配（§7-§11）。

★ §7：Trend Workspace 不是只有 AI Summary —— What happened / 趋势信号 / 生命周期 /
  平台扩散 / Top Evidence / Narratives / 受众 / 信源可信度 / Timeline 一起给，
  运营看到的是**可验证情报**，不是"AI 告诉它在爆"。

★ §8：Evidence Panel 必须常驻 —— L4 的 facts / inferences / unknowns 三分要原样展示。
  否则运营会直接把 AI 推断当事实复制发布。

★ §10/§11：Creative Studio 围绕**已确认的 Opportunity** 工作；支持约束重生成
  （资源/上线时间/预算/渠道/目标），约束来自 ops_context（§39）而不是拍脑袋。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from execution import ops_context
from execution.feed import allowed_creative_types, window_for

WORKSPACE_VERSION = "workspace-1.0"


class Workspace:
    def __init__(self, upstream: Any, l4_store: Any, workflow: Any,
                 memory_store: Any = None):
        self.up = upstream
        self.l4 = l4_store
        self.wf = workflow
        self.memory = memory_store       # L5（可选：历史案例注入 Creative Studio 左栏）

    # -------------------------------------------------------------- §7 Trend Workspace
    def trend(self, event_id: str) -> Optional[Dict[str, Any]]:
        ev = self.up.event(event_id)
        if not ev:
            return None
        anl = self._latest_analysis(event_id) or {}
        pack = anl.get("evidence_pack") or {}
        trend_analysis = anl.get("trend_analysis") or {}
        members = self.up.members(event_id)[:8]
        return {
            "workspace_version": WORKSPACE_VERSION,
            "event_id": event_id,
            "what_happened": {
                "title": ev.get("canonical_title"),
                "summary": anl.get("event_summary") or trend_analysis.get("trigger"),
                "why_now": trend_analysis.get("why_now"),
                "narratives": trend_analysis.get("narratives") or [],
                "key_uncertainties": trend_analysis.get("key_uncertainties") or [],
            },
            "trend_signal": {
                "lifecycle": ev.get("lifecycle"),
                "hot_score": ev.get("hot_score"),
                "momentum_score": ev.get("momentum_score"),
                "confidence_score": ev.get("confidence_score"),
                "content_count": ev.get("content_count"),
                "platform_count": ev.get("platform_count"),
                # 时间分辨率缺失时曲线/速度如实标注不可得（L3 已证明的结构性事实）
                "temporal_resolution": "unavailable（当前数据无时间分辨率，曲线/速度不可算）",
            },
            "platform_diffusion": _platforms_of(self.up.members(event_id)),
            "evidence_panel": self._evidence_panel(pack),          # §8 常驻
            "audience": anl.get("audiences") or [],
            "source_credibility": pack.get("event", {}).get("confidence"),
            "timeline": [{"content_id": m.get("content_id"),
                          "platform": m.get("platform"),
                          "observed_at": (m.get("source_features") or {}).get("observed_at")}
                         for m in members],
            "window": window_for(ev.get("lifecycle")),
            "analysis_id": anl.get("analysis_id"),
            "risk": (anl.get("risk") or {}).get("risk_level"),
        }

    # -------------------------------------------------------------- §8 Evidence Panel
    @staticmethod
    def _evidence_panel(pack: Dict[str, Any]) -> Dict[str, Any]:
        """FACT / INFERENCE / UNKNOWN 三分原样透出（§8：推断不当事实卖）。"""
        evidence = pack.get("evidence") or []
        return {
            "facts": [{"tier": e.get("tier"), "excerpt": e.get("excerpt"),
                       "content_id": e.get("content_id")} for e in evidence
                      if e.get("tier") in ("PRIMARY", "SECONDARY")],
            "community": [{"tier": e.get("tier"), "excerpt": e.get("excerpt"),
                           "content_id": e.get("content_id")} for e in evidence
                          if e.get("tier") == "COMMUNITY"],
            "inferences": pack.get("inferences") or [],
            "unknowns": pack.get("unknowns") or [],
            "primary_ratio": pack.get("primary_ratio"),
            "n_evidence": pack.get("n_evidence"),
        }

    # -------------------------------------------------------------- §9 Opportunity Workspace
    def opportunities(self, event_id: str) -> Optional[Dict[str, Any]]:
        ev = self.up.event(event_id)
        if not ev:
            return None
        anl = self._latest_analysis(event_id) or {}
        win = window_for(ev.get("lifecycle"))
        items = []
        for o in anl.get("opportunities") or []:
            items.append({
                "opportunity_id": o.get("opportunity_id"),
                "name": o.get("name"),
                "score": o.get("opportunity_score"),
                "audience": o.get("audience"),
                "motivation": o.get("user_motivation"),
                "mechanism": o.get("growth_mechanism"),
                "goal": o.get("growth_goal"),
                "expected_metrics": o.get("expected_metrics"),
                "window": o.get("opportunity_window"),
                "feasibility": o.get("feasibility"),
            })
        return {"event_id": event_id, "analysis_id": anl.get("analysis_id"),
                "opportunities": items, "window": win,
                "ops_constraints": {k: ctx_val for k, ctx_val in
                                    (ops_context.load().get("resources") or {}).items()}}

    # -------------------------------------------------------------- §10/§11 Creative Studio
    def creative_studio(self, event_id: str, opportunity_id: Optional[str] = None,
                        constraints: Optional[Dict[str, Any]] = None
                        ) -> Optional[Dict[str, Any]]:
        ev = self.up.event(event_id)
        if not ev:
            return None
        anl = self._latest_analysis(event_id) or {}
        creatives = anl.get("creatives") or []
        if opportunity_id:
            creatives = [c for c in creatives
                         if c.get("opportunity_id") == opportunity_id]
        ctx = ops_context.load()
        cons = constraints or {}
        resource_cap = ops_context.max_lead_time_hours(ctx)
        time_cap = float(cons.get("max_lead_time_hours") or resource_cap)
        win = window_for(ev.get("lifecycle"), ctx)
        time_cap = min(time_cap, float(win["max_lead_time_hours"]))
        for c in creatives:                                    # §6：超窗创意如实标记
            lead = float(c.get("lead_time_hours") or 0)
            c["within_window"] = lead <= time_cap
        history = self._historical_cases(creatives)
        return {
            "event_id": event_id,
            "left_panel": {                                     # §10 左栏：围绕确认的机会
                "trend": ev.get("canonical_title"),
                "lifecycle": ev.get("lifecycle"),
                "audience": (anl.get("audiences") or []),
                "opportunity": next(({"name": o.get("name"),
                                      "mechanism": o.get("growth_mechanism"),
                                      "goal": o.get("growth_goal")}
                                     for o in (anl.get("opportunities") or [])
                                     if o.get("opportunity_id") == opportunity_id), None),
                "constraints": {
                    "ops_resources": ctx.get("resources"),
                    "ops_degraded": ctx.get("degraded"),
                    "budget_level": ctx.get("budget_level"),
                    "channels_allowed": ctx.get("channels_allowed"),
                    "max_lead_time_hours": time_cap,
                    "basis": "ops_context(§39) + lifecycle 窗口(§6)",
                },
                "historical_cases": history,                    # §43：第五层记忆注入
            },
            "creatives": creatives,
            "regenerate": {                                     # §11 约束重生成接口
                "accepted_constraints": ["max_lead_time_hours", "budget_level",
                                         "channels_allowed", "goal"],
                "note": "Regenerate 按真实约束重设计（重跑 L4 creative 节点），不是'再想几个'",
            },
        }

    def _historical_cases(self, creatives: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """§43：按创意类型查第五层 Case Memory，注入左栏（无记忆 → 空列表，不编）。"""
        if self.memory is None:
            return []
        try:
            from memory.retrieval import RetrievalEngine
            engine = RetrievalEngine(self.memory)
            out: List[Dict[str, Any]] = []
            seen_types = {c.get("creative_type") for c in creatives if c.get("creative_type")}
            for t in list(seen_types)[:3]:
                res = engine.retrieve(str(t), memory_type="case",
                                      caller="creative_agent", top_k=3)
                out.extend(res["items"])
            return out
        except Exception:
            return []

    def _latest_analysis(self, event_id: str) -> Optional[Dict[str, Any]]:
        from execution.feed import l4_one, l4_json
        if not hasattr(self.l4, "conn"):
            getter = getattr(self.l4, "latest", None)
            return getter(event_id) if getter else None
        row = l4_one(self.l4,
            "SELECT payload FROM intelligence_analysis WHERE event_id=?"
            " ORDER BY created_at DESC LIMIT 1", (event_id,))
        return l4_json(self.l4, row["payload"]) if row else None


def _platforms_of(members: List[Dict[str, Any]]) -> List[str]:
    seen = []
    for m in members:
        p = m.get("platform")
        if p and p not in seen:
            seen.append(p)
    return seen
