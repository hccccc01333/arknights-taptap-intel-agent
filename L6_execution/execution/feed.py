# -*- coding: utf-8 -*-
"""Intelligence Feed（§4 / §5 / §6 / §36）—— 第六层首页。

★ §4：业务团队每天看到的不是"最热的 10 个事件"，而是"**最值得行动的** 10 个事件"。

★ §5 ActionPriority 只用于排序，不是业务判断本身：
    Hot × Momentum × Confidence × Relevance × Opportunity × WindowFactor
  缺维度用 0.5 中性值补位并列入 missing_dims（§5：不要把判断压成一个神秘数字 ——
  卡片同时展示全部关键维度）。

★ §6 Opportunity Window：热点系统最重要的字段是"还能追多久"。
  现状如实：L3 数据无时间分辨率 → 小时级窗口算不出（不编造）。
  降级口径：lifecycle 阶段（L3 自己的 boost 表）× §39 运营资源 → **max_lead_time_hours**
  （时效约束变成产品约束：L4 CREATIVE_TYPES 里 lead_time 超限的类型直接从卡片排除）。
  等连续采集接通，这里换成小时级窗口，结构不变。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_ROOT, os.path.join(_ROOT, "L4_intelligence")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from execution import ops_context  # noqa: E402

FEED_VERSION = "feed-1.0"


# ---- L4 IntelligenceStore 访问助手（它暴露 conn 而非封装 query）----
def l4_query(store: Any, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in store.conn.execute(sql, params).fetchall()]


def l4_one(store: Any, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    r = store.conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def l4_json(store: Any, text: Any, default: Any = None) -> Any:
    import json
    if text is None:
        return default
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default

# §36 三段式分组
GROUPS = ("ACTION_NOW", "WATCH", "DECLINING")

# L3 lifecycle 自带的阶段权重（trend_engine/lifecycle.py boost 表）→ 窗口紧迫度
_LIFECYCLE_URGENCY = {
    "EMERGING": ("high", 1.0), "REACTIVATED": ("high", 0.9),
    "GROWING": ("high", 0.9), "PEAKING": ("medium", 0.7),
    "DECLINING": ("low", 0.4), "DORMANT": ("closed", 0.2),
}
_LIFECYCLE_CAP_HOURS = {          # 阶段 → 该阶段还敢上多长 lead_time 的创意
    "EMERGING": None,             # None = 用运营资源上限（§39）
    "REACTIVATED": None,
    "GROWING": None,
    "PEAKING": 24.0,
    "DECLINING": 6.0,
    "DORMANT": 6.0,
}
_NEUTRAL = 0.5


def window_for(lifecycle: str, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§6 窗口。诚实标注口径：lifecycle+资源 降级口径（无时间分辨率）。"""
    ctx = ctx or ops_context.load()
    urgency, factor = _LIFECYCLE_URGENCY.get(lifecycle or "", ("unknown", _NEUTRAL))
    cap = _LIFECYCLE_CAP_HOURS.get(lifecycle or "")
    if cap is None:
        cap = ops_context.max_lead_time_hours(ctx)
    return {"urgency": urgency, "window_factor": factor,
            "max_lead_time_hours": cap,
            "basis": "lifecycle + ops_context（降级：L3 无时间分辨率，小时级窗口不可得）"}


def allowed_creative_types(max_lead_time_hours: float) -> List[Dict[str, Any]]:
    """§6：lead_time 超过剩余窗口的创意类型直接排除（产品约束，不是建议）。"""
    from intelligence.knowledge import CREATIVE_TYPES      # L4 知识层（数据不是 prompt）
    out = []
    for tid, t in CREATIVE_TYPES.items():
        lead = float(t.get("lead_time_hours") or 0)
        out.append({"creative_type": tid, "name": t.get("name"),
                    "lead_time_hours": lead, "allowed": lead <= max_lead_time_hours})
    return sorted(out, key=lambda x: (not x["allowed"], x["lead_time_hours"]))


def action_priority(dims: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """§5：六维乘积排序分。缺维度 0.5 中性 + missing_dims 如实列出。"""
    keys = ("hot", "momentum", "confidence", "relevance", "opportunity", "window")
    product = 1.0
    missing = []
    for k in keys:
        v = dims.get(k)
        if v is None:
            missing.append(k)
            v = _NEUTRAL
        product *= max(0.0, min(1.35, float(v)))
    return {"priority": round(product, 4), "missing_dims": missing,
            "dims": {k: dims.get(k) for k in keys}}


class IntelligenceFeed:
    """从 L3 事件 + L4 分析 + 工作流指派状态拼 Feed 卡片。"""

    def __init__(self, upstream: Any, l4_store: Any, workflow: Any):
        self.up = upstream
        self.l4 = l4_store
        self.wf = workflow

    def _latest_analysis(self, event_id: str) -> Optional[Dict[str, Any]]:
        # 测试替身（无 conn）走显式 latest() 分支；真实 IntelligenceStore 走 SQL
        if not hasattr(self.l4, "conn"):
            getter = getattr(self.l4, "latest", None)
            return getter(event_id) if getter else None
        row = l4_one(self.l4,
            "SELECT a.payload, a.relevance_score FROM intelligence_analysis a"
            " WHERE a.event_id=? ORDER BY a.created_at DESC LIMIT 1", (event_id,))
        return l4_json(self.l4, row["payload"]) if row else None

    def build(self, limit: int = 50) -> Dict[str, Any]:
        ctx = ops_context.load()
        assigned = {(r["object_type"], r["object_id"]): r
                    for r in self.wf.items_by_state()}
        cards: List[Dict[str, Any]] = []
        for ev in self.up.events(limit=limit):
            event_id = ev["event_id"]
            anl = self._latest_analysis(event_id) or {}
            hot = ev.get("hot_score")
            mom = ev.get("momentum_score")
            conf = ev.get("confidence_score")
            relevance = anl.get("relevance", {}).get("score") \
                if isinstance(anl.get("relevance"), dict) else anl.get("relevance_score")
            opps = anl.get("opportunities") or []
            opp_score = max((float(o.get("opportunity_score") or 0) for o in opps),
                            default=None) or None
            win = window_for(ev.get("lifecycle"), ctx)
            pri = action_priority({"hot": hot, "momentum": mom, "confidence": conf,
                                   "relevance": relevance, "opportunity": opp_score,
                                   "window": win["window_factor"]})
            item = assigned.get(("event", event_id))
            card = {
                "event_id": event_id,
                "title": ev.get("canonical_title"),
                "lifecycle": ev.get("lifecycle"),
                "hot_score": hot, "momentum_score": mom, "confidence_score": conf,
                "relevance": relevance,
                "opportunity_score": opp_score,
                "n_opportunities": len(opps),
                "n_creatives": len(anl.get("creatives") or []),
                "content_count": ev.get("content_count"),
                "platform_count": ev.get("platform_count"),
                "platforms": ev.get("platforms"),
                "window": win,
                "allowed_creatives": [c for c in
                                      allowed_creative_types(win["max_lead_time_hours"])
                                      if c["allowed"]][:6],
                "ai_judgement": (anl.get("trend_analysis") or {}).get("trigger"),
                "top_opportunities": [{"name": o.get("name"),
                                       "score": o.get("opportunity_score"),
                                       "window": o.get("opportunity_window")}
                                      for o in opps[:3]],
                "analysis_id": anl.get("analysis_id"),
                "has_analysis": bool(anl),
                "workflow": {"state": item["state"], "owner": item["owner"],
                             "reviewer": item["reviewer"], "deadline": item["deadline"]}
                if item else {"state": None, "owner": None, "reviewer": None,
                              "deadline": None},
                **pri,                                   # priority / dims / missing_dims
            }
            cards.append(card)

        for c in cards:                                   # §36 分组
            life = (c.get("lifecycle") or "")
            if life in ("DECLINING", "DORMANT"):
                c["group"] = "DECLINING"
            elif c["priority"] >= 0.04 and life in ("EMERGING", "GROWING",
                                                    "PEAKING", "REACTIVATED"):
                c["group"] = "ACTION_NOW"
            else:
                c["group"] = "WATCH"
        cards.sort(key=lambda c: -c["priority"])
        return {"feed_version": FEED_VERSION, "generated_at": _now_iso(),
                "window_basis": win["basis"],
                "ops_degraded": ctx.get("degraded"),
                "counts": {g: sum(1 for c in cards if c["group"] == g) for g in GROUPS},
                "cards": cards}

    def card(self, event_id: str) -> Optional[Dict[str, Any]]:
        feed = self.build(limit=5000)
        for c in feed["cards"]:
            if c["event_id"] == event_id:
                return c
        return None


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")
