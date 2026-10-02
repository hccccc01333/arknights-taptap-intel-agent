# -*- coding: utf-8 -*-
"""Node 2-4：Trend Analyst（§10）/ TapTap Relevance（§12-§14）/ Audience Mapper（§18-§19）。

共同纪律（§11）：每个节点输出都带 facts / inferences / unknowns 三段，
**不许把推断写成事实**。无 LLM 时全部规则实现，并在 `mode` 字段标 `rule`。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..knowledge import (ASSET_BY_ID, AUDIENCE_TEMPLATES, GROWTH_GOALS,
                         detect_motivations, assets_for_goal)
from ..prompts import PROMPT_VERSIONS

# §12 权重
RELEVANCE_WEIGHTS = {"U": 0.30, "C": 0.25, "A": 0.20, "G": 0.15, "T": 0.10}
# §14 路由阈值
RELEVANCE_ARCHIVE = 0.40
RELEVANCE_LIGHT = 0.65

_LIFECYCLE_TIMING = {"EMERGING": 0.95, "GROWING": 0.90, "PEAKING": 0.60,
                     "DECLINING": 0.30, "DORMANT": 0.10, "REACTIVATED": 0.75}

# 事件类型 → 天然适配的增长目标（§23 有限集合内）
_TYPE_GOALS = {
    "release": ["game_follow", "registration", "game_detail_visit"],
    "update": ["community_activation", "engagement", "retention"],
    "controversy": ["engagement", "community_activation", "ugc"],
    "leak": ["reach", "engagement", "game_detail_visit"],
    "meme": ["reach", "ugc", "share"],
    "creator_event": ["creator_activation", "ugc", "reach"],
    "esports": ["reach", "engagement", "community_activation"],
    "unknown": ["engagement", "reach"],
}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ---------------------------------------------------------------- §10 Trend Analyst

def trend_analyst(pack: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    """只解释事件，不给创意、不给增长建议（§10）。"""
    sig = pack.get("signals") or {}
    platforms = pack.get("platforms") or {}
    ev = pack.get("event") or {}
    lifecycle = ev.get("lifecycle") or "UNKNOWN"
    etype = ev.get("event_type") or "unknown"

    what = f"{ev.get('title')}：{pack.get('n_members', 0)} 条内容 / {len(platforms)} 个平台 / 生命周期 {lifecycle}"
    # 触发点：只能从证据里找（官方证据优先），找不到就留 None —— 不许编
    trigger = None
    primary = [e for e in pack.get("evidence", []) if e.get("tier") == "PRIMARY"]
    if primary:
        trigger = f"官方信源：{primary[0].get('excerpt', '')[:60]}（{primary[0].get('content_id')}）"

    narratives: List[str] = []
    for e in pack.get("evidence", [])[:5]:
        ex = (e.get("excerpt") or "").strip()
        if ex:
            narratives.append(f"[{e['tier']}] {ex[:60]}")

    audience_signals = [
        f"唯一作者 {sig.get('unique_authors')} 人 / 搬运占比 {sig.get('dup_ratio')}",
        f"游戏相关度 {sig.get('gaming_share')}",
    ]
    if sig.get("comment_per_1k_view") is not None:
        audience_signals.append(f"每千次浏览评论数 {sig['comment_per_1k_view']}")

    if lifecycle in ("EMERGING", "GROWING"):
        interp = "处于扩散早期/上升期，行动窗口较宽"
    elif lifecycle == "PEAKING":
        interp = "接近峰值，需在窗口关闭前完成上线"
    elif lifecycle == "DECLINING":
        interp = "已在衰减，只适合低成本动作或作为沉淀案例"
    else:
        interp = f"生命周期为 {lifecycle}，窗口判断需结合外部数据"

    uncertainties = list(pack.get("unknowns") or [])
    if not trigger:
        uncertainties.append("触发点未找到官方/权威证据，暂标 UNKNOWN")

    return {
        "what_happened": what,
        "trigger": trigger,
        "why_now": (f"生命周期 {lifecycle}，momentum={ev.get('momentum_score')}" if ev.get("momentum_score") is not None
                    else UNKNOWN),
        "narratives": narratives,
        "audience_signals": audience_signals,
        "propagation_summary": f"平台分布 { {k: v['count'] for k, v in platforms.items()} }",
        "lifecycle_interpretation": interp,
        "key_uncertainties": uncertainties,
        "confidence": _clip01(float(ev.get("confidence") or 0.0)),
        "event_type": etype,
        "facts": pack.get("facts", []),
        "inferences": pack.get("inferences", []) + ([interp] if interp else []),
        "unknowns": uncertainties,
        "mode": "rule",
        "prompt_version": PROMPT_VERSIONS["trend_analyst"],
    }


UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------- §12 Relevance

def _dim_u(pack: Dict[str, Any]) -> float:
    """User overlap：TapTap 用户会不会关心 —— 用"游戏相关度 + 游戏实体数"代理。"""
    sig = pack.get("signals") or {}
    gs = sig.get("gaming_share")
    base = float(gs) if gs is not None else 0.5
    ge = sig.get("game_entity_count") or 0
    bonus = min(0.2, 0.05 * ge)
    return _clip01(0.8 * base + bonus)


def _dim_c(pack: Dict[str, Any]) -> float:
    """Community fit：是不是"能聊起来"的事 —— 评论密度 + 社区层证据占比 + 事件类型。"""
    sig = pack.get("signals") or {}
    cpv = sig.get("comment_per_1k_view")
    s = 0.4
    if cpv is not None:
        s += min(0.3, cpv / 30.0)
    tier = pack.get("tier_counts") or {}
    total = max(1, sum(tier.values()))
    s += 0.3 * (tier.get("COMMUNITY", 0) / total)
    etype = (pack.get("event") or {}).get("event_type")
    if etype in ("controversy", "meme", "update"):
        s += 0.1
    return _clip01(s)


def _dim_a(pack: Dict[str, Any]) -> float:
    """TapTap asset fit（§13）：有没有资产能承接 —— 看目标可达资产的覆盖面与成本。"""
    etype = (pack.get("event") or {}).get("event_type") or "unknown"
    goals = _TYPE_GOALS.get(etype, _TYPE_GOALS["unknown"])
    assets: Dict[str, Any] = {}
    for g in goals:
        for a in assets_for_goal(g):
            assets[a["asset_id"]] = a
    if not assets:
        return 0.3
    low = sum(1 for a in assets.values() if a["cost"] == "low")
    return _clip01(0.4 + 0.6 * (low / len(assets)))


def _dim_g(pack: Dict[str, Any]) -> float:
    """Growth potential：热度和证据强度带来的增长潜力。"""
    ev = pack.get("event") or {}
    conf = float(ev.get("confidence") or 0)
    hot = float(ev.get("hot_score") or 0)
    mom = float(ev.get("momentum_score") or 0)
    return _clip01(0.4 * conf + 0.3 * hot + 0.3 * mom)


def _dim_t(pack: Dict[str, Any]) -> float:
    """Timing fit：生命周期决定窗口。"""
    lc = (pack.get("event") or {}).get("lifecycle") or "UNKNOWN"
    return _LIFECYCLE_TIMING.get(str(lc).upper(), 0.4)


def relevance(pack: Dict[str, Any], analysis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    dims = {
        "user_overlap": round(_dim_u(pack), 3),
        "community_fit": round(_dim_c(pack), 3),
        "platform_asset_fit": round(_dim_a(pack), 3),
        "growth_potential": round(_dim_g(pack), 3),
        "timing_fit": round(_dim_t(pack), 3),
    }
    score = sum(RELEVANCE_WEIGHTS[k] * dims[v]
                for k, v in (("U", "user_overlap"), ("C", "community_fit"),
                             ("A", "platform_asset_fit"), ("G", "growth_potential"),
                             ("T", "timing_fit")))
    score = round(_clip01(score), 3)
    if score < RELEVANCE_ARCHIVE:
        route = "archive"
    elif score < RELEVANCE_LIGHT:
        route = "light_analysis"
    else:
        route = "opportunity"

    reasons = {
        "user_overlap": f"游戏相关度 {pack.get('signals', {}).get('gaming_share')} / 游戏实体 {(pack.get('signals') or {}).get('game_entity_count')} 个",
        "community_fit": f"评论密度 {(pack.get('signals') or {}).get('comment_per_1k_view')}、社区层证据占比",
        "platform_asset_fit": f"事件类型 {(pack.get('event') or {}).get('event_type')} → 目标 {_TYPE_GOALS.get((pack.get('event') or {}).get('event_type') or 'unknown')}",
        "growth_potential": f"confidence {(pack.get('event') or {}).get('confidence')} / hot {(pack.get('event') or {}).get('hot_score')}",
        "timing_fit": f"生命周期 {(pack.get('event') or {}).get('lifecycle')}",
    }
    return {
        "score": score,
        "dimensions": dims,
        "weights": RELEVANCE_WEIGHTS,
        "route": route,
        "reasons": reasons,
        # ★ 规格提醒：Hot 高 ≠ Relevance 高（社会热点 Hot .99 / Relevance .12）
        "note": "Hot Score 高不代表相关性高；低相关事件应 archive，不该浪费 Agent Token",
        "facts": [f"相关性五维 {dims}", f"路由 {route}（阈值 archive<{RELEVANCE_ARCHIVE} light<{RELEVANCE_LIGHT}）"],
        "inferences": ["维度分由证据包聚合信号推得，非业务方确认"],
        "unknowns": [] if pack.get("primary_ratio") else ["缺少官方证据，相关性判定可能偏高"],
        "mode": "rule",
        "prompt_version": PROMPT_VERSIONS["relevance"],
    }


def relevance_route(state: Dict[str, Any]) -> str:
    """§14 Relevance Router（图的条件边）。"""
    rel = state.get("relevance") or {}
    return rel.get("route", "archive")


# ---------------------------------------------------------------- §18/§19 Audience

def audience(pack: Dict[str, Any], analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """先定"谁关心 + 为什么关心"，后续所有 Opportunity 必须关联 Audience（§18）。"""
    texts = [e.get("excerpt") for e in pack.get("evidence", [])]
    mots = detect_motivations([t or "" for t in texts], top_k=3)
    if not mots:
        mots = [{"motivation_id": "info_seeking", "name": "获取信息与攻略",
                 "strength": 0.6, "evidence": "未命中动机信号词，取默认", "source": "fallback"}]

    sig = pack.get("signals") or {}
    reach_high = (sig.get("views") or 0) > 50000
    creative_friendly = any(m["motivation_id"] in ("show_off", "aesthetic_display",
                                                   "identity_expression", "meme") for m in mots)
    nostalgia = (pack.get("event") or {}).get("event_type") in ("update", "release")

    out: List[Dict[str, Any]] = []
    for tpl in AUDIENCE_TEMPLATES:
        when = tpl["when"]
        if when == "high_reach" and not reach_high:
            continue
        if when == "creative_friendly" and not creative_friendly:
            continue
        if when == "nostalgia_or_update" and not nostalgia:
            continue
        mot = mots[0]
        strength = _clip01(float(tpl["interest_base"]) * (0.6 + 0.4 * float(mot["strength"])))
        out.append({
            "segment": tpl["segment"],
            "motivation": mot["name"],
            "motivation_id": mot["motivation_id"],
            "interest_strength": round(strength, 3),
            "evidence": mot["evidence"],
            "signals": [f"唯一作者 {sig.get('unique_authors')}", f"浏览 {sig.get('views')}"],
            "mode": "rule",
        })
    # 其余动机也各挂一个受众（保证 Opportunity 有足够组合素材）
    for mot in mots[1:]:
        out.append({
            "segment": f"被「{mot['name']}」驱动的用户",
            "motivation": mot["name"],
            "motivation_id": mot["motivation_id"],
            "interest_strength": mot["strength"],
            "evidence": mot["evidence"],
            "signals": [],
            "mode": "rule",
        })
    return out
