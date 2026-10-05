# -*- coding: utf-8 -*-
"""Node 6-7：Opportunity Agent（§20-§24）+ Growth Strategist（§25）。

★ 规格：第四层**最重要的 Agent 不是 Creative，而是 Opportunity**。
  它回答"TapTap 能通过什么机制承接这个热点"。

★ §22 硬门槛：**没有 growth_mechanism 的创意不进入 Creative 阶段** —— 这里就先把它挡掉。
★ §23：growth_goal 只从有限集合取，不让模型自创 KPI。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..knowledge import ASSET_BY_ID, CREATIVE_TYPES, GROWTH_GOALS, MOTIVATION_BY_ID, assets_for_goal
from ..prompts import PROMPT_VERSIONS

# §24 权重
OPP_WEIGHTS = {"R": 0.25, "M": 0.20, "F": 0.20, "W": 0.15, "D": 0.10, "E": 0.10}

# §22 增长机制链（每条都必须说清"增长从哪里来"）
GROWTH_MECHANISMS: Dict[str, Dict[str, Any]] = {
    "ugc_loop": {"goal": "ugc",
                 "chain": "曝光 → 点击 → 互动 → UGC → 分享 → 站外回流 → 新用户",
                 "assets": ["forum", "ranking", "ugc_tool"], "cost": "medium"},
    "topic_funnel": {"goal": "game_detail_visit",
                     "chain": "热点 → 游戏讨论 → 详情页访问 → 关注游戏 → 预约/下载",
                     "assets": ["content", "game_detail", "follow"], "cost": "low"},
    "discussion_activation": {"goal": "community_activation",
                             "chain": "热点 → 话题讨论 → 社区沉淀 → 长期活跃",
                             "assets": ["forum", "moment"], "cost": "low"},
    "creator_amplify": {"goal": "creator_activation",
                        "chain": "热点 → 达人产能 → 内容供给 → 分发放大 → 触达",
                        "assets": ["creator", "moment", "recommend"], "cost": "high"},
    "reactivation": {"goal": "reactivation",
                     "chain": "热点 → 精准触达 → 唤起兴趣 → 回访 → 留存",
                     "assets": ["push", "game_detail"], "cost": "low"},
    "share_spread": {"goal": "share",
                     "chain": "热点 → 生成物料 → 站外分享 → 回流",
                     "assets": ["h5", "ugc_tool"], "cost": "medium"},
}

_WINDOW_BY_LIFECYCLE = {"EMERGING": ("24-48h", 0.95), "GROWING": ("12-24h", 0.90),
                        "PEAKING": ("6-12h", 0.65), "DECLINING": ("已错过主窗口", 0.25),
                        "DORMANT": ("无窗口", 0.10), "REACTIVATED": ("12-24h", 0.75)}
_COST_FEASIBILITY = {"low": 0.90, "medium": 0.65, "high": 0.35}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _mechanism_for(goal: str) -> Dict[str, Any]:
    for m in GROWTH_MECHANISMS.values():
        if m["goal"] == goal:
            return m
    return GROWTH_MECHANISMS["discussion_activation"]


def _platform_fit(mechanism: Dict[str, Any], relevance_dims: Dict[str, float]) -> float:
    """★ 实测修正：最初写成"low 成本资产占比"，结果几乎恒等于 1.0（区分度为 0）。

    改成**以该增长机制实际需要的资产成本为主**：
      - 热点专题链（内容/详情/关注，全 low）→ 0.90
      - UGC 链（含榜单/UGC工具，medium）→ 0.73
      - 达人链（high）→ 0.35
    这样不同机会的可行性才真正拉开差距。
    """
    base = float(relevance_dims.get("platform_asset_fit", 0.5))
    costs = [_COST_FEASIBILITY.get(ASSET_BY_ID.get(a, {}).get("cost", "medium"), 0.65)
             for a in mechanism["assets"] if ASSET_BY_ID.get(a)]
    asset_ease = sum(costs) / len(costs) if costs else 0.6
    return _clip01(0.35 * base + 0.65 * asset_ease)


def opportunity(state: Dict[str, Any], max_opportunities: int = 4) -> List[Dict[str, Any]]:
    """Trend × Audience × Motivation × Asset × Growth Goal → Growth Opportunity。"""
    pack = state.get("evidence_pack") or {}
    rel = state.get("relevance") or {}
    rel_score = float(rel.get("score") or 0.0)
    dims = rel.get("dimensions") or {}
    audiences = state.get("audiences") or []
    ev = pack.get("event") or {}
    lifecycle = str(ev.get("lifecycle") or "UNKNOWN").upper()
    window, w_score = _WINDOW_BY_LIFECYCLE.get(lifecycle, ("未知", 0.5))
    novelty = ev.get("novelty_score")

    # 候选目标：事件类型优先，其次由动机反推
    goals: List[str] = []
    for aud in audiences:
        mid = aud.get("motivation_id")
        mot = MOTIVATION_BY_ID.get(mid) or {}
        for t in (mot.get("best_types") or []):
            for g in GROWTH_GOALS:
                if g in ("ugc", "engagement", "reach", "share", "community_activation",
                         "game_detail_visit", "creator_activation", "reactivation"):
                    if t in ("ugc", "ai_interactive", "product") and g == "ugc" and g not in goals:
                        goals.append(g)
                    if t in ("community", "content") and g == "engagement" and g not in goals:
                        goals.append(g)
                    if t == "creator" and g == "creator_activation" and g not in goals:
                        goals.append(g)
                    if t == "crm" and g == "reactivation" and g not in goals:
                        goals.append(g)
                    if t in ("h5", "social") and g == "share" and g not in goals:
                        goals.append(g)
    if not goals:
        goals = ["engagement", "ugc"]

    out: List[Dict[str, Any]] = []
    seen = set()
    for aud in audiences[:3]:
        for goal in goals:
            if (aud.get("motivation_id"), goal) in seen:
                continue
            seen.add((aud.get("motivation_id"), goal))
            mech = _mechanism_for(goal)
            f = _platform_fit(mech, dims)
            m = float(aud.get("interest_strength") or 0.5)
            e = _COST_FEASIBILITY.get(mech["cost"], 0.65)
            d = float(novelty) if isinstance(novelty, (int, float)) else 0.6
            score = (OPP_WEIGHTS["R"] * rel_score + OPP_WEIGHTS["M"] * m + OPP_WEIGHTS["F"] * f
                     + OPP_WEIGHTS["W"] * w_score + OPP_WEIGHTS["D"] * d + OPP_WEIGHTS["E"] * e)
            out.append({
                "opportunity_id": f"opp_{len(out) + 1}",
                "name": f"{aud.get('segment')} × {GROWTH_GOALS.get(goal, goal)}",
                "audience": aud.get("segment"),
                "user_motivation": aud.get("motivation"),
                "motivation_id": aud.get("motivation_id"),
                "observed_signal": (
                    f"事件 {ev.get('title')}：{pack.get('n_members')} 条内容 / "
                    f"{len(pack.get('platforms') or {})} 平台 / confidence {ev.get('confidence')}"),
                "platform_advantage": ", ".join(
                    ASSET_BY_ID.get(a, {}).get("name", a) for a in mech["assets"]),
                "growth_mechanism": mech["chain"],
                "growth_goal": goal,
                "opportunity_window": window,
                "expected_metrics": ["ugc_count" if goal == "ugc" else
                                     "engagement_rate" if goal == "engagement" else
                                     "share_rate" if goal == "share" else
                                     "game_detail_visit" if goal == "game_detail_visit" else
                                     "reach"],
                "feasibility": round(e, 3),
                "opportunity_score": round(_clip01(score), 3),
                "dimensions": {"R": round(rel_score, 3), "M": round(m, 3), "F": round(f, 3),
                               "W": round(w_score, 3), "D": round(d, 3), "E": round(e, 3)},
                "weights": OPP_WEIGHTS,
                # ★ 溯源：这条机会是从哪些证据来的
                "source_refs": {"event_id": state.get("event_id"),
                                "evidence_ids": [e["evidence_id"] for e in (pack.get("evidence") or [])[:5]],
                                "audience_motivation_id": aud.get("motivation_id")},
                "mode": "rule",
                "prompt_version": PROMPT_VERSIONS["opportunity"],
            })

    out.sort(key=lambda o: -o["opportunity_score"])
    return out[:max_opportunities]


def strategist(state: Dict[str, Any], opportunities: Optional[List[Dict[str, Any]]] = None
               ) -> List[Dict[str, Any]]:
    """§25：把 Opportunity 转成**可被验证**的 Growth Hypothesis。"""
    opps = opportunities if opportunities is not None else (state.get("opportunities") or [])
    pack = state.get("evidence_pack") or {}
    lifecycle = str((pack.get("event") or {}).get("lifecycle") or "UNKNOWN")
    out = []
    for o in opps:
        hypothesis = (f"如果在【{lifecycle}】阶段为【{o['audience']}】提供"
                      f"【{o['platform_advantage']}】支撑的【{o['growth_goal']}】机制，"
                      f"那么会提升【{', '.join(o['expected_metrics'])}】，"
                      f"因为其动机是【{o['user_motivation']}】。")
        out.append({
            "opportunity_id": o["opportunity_id"],
            "hypothesis": hypothesis,
            "mechanism": o["growth_mechanism"],
            "validation_metric": o["expected_metrics"][0] if o["expected_metrics"] else None,
            "window": o["opportunity_window"],
            "falsifiable_by": f"对比同期未投放的同类事件的 {o['expected_metrics'][0] if o['expected_metrics'] else '指标'}",
            "mode": "rule",
            "prompt_version": PROMPT_VERSIONS["strategist"],
        })
    return out
