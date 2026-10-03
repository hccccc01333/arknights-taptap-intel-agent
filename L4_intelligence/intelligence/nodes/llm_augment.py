# -*- coding: utf-8 -*-
"""把 LLM 接进各节点（§41 Context Engineering / §11 三分 / §40 独立评审）。

★ 设计原则：**规则是兜底，也是校验基准**。
  模型输出必须通过结构校验（字段齐、取值在受控集合内、分数在 0~1）才被采纳；
  不通过就保留规则结果，并在 `mode` 里写明 `rule_fallback_after_llm_error`。
  ★★ 绝不允许"模型说什么就是什么"—— 那会让第三层辛苦建立的可核验性在这里丢掉。

★ §41：每个节点只收到**它需要的那一小片上下文**，不是整个系统的数据。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..knowledge import CREATIVE_TYPES, GROWTH_GOALS, MOTIVATION_BY_ID, ASSET_BY_ID
from ..prompts import render, system_for, PROMPT_VERSIONS
from ..llm import ModelRouter

_RISK_TYPES = ("fact", "brand", "copyright", "platform", "publisher", "timing", "operational")


def _clip01(x: Any) -> Optional[float]:
    """宽松裁剪：只在确认是数字但轻微越界时使用。"""
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return None


def _dim01(x: Any) -> Optional[float]:
    """★ 评分维度用**严格**校验：必须在 [0,1]，越界一律 None（整条不采纳）。

    为什么不做静默裁剪：模型打出 5 分说明它没按 0~1 的契约来，
    裁剪成 1.0 会把"模型理解错了"这个信号藏掉 —— 这正是要监控的东西。
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v < 0.0 or v > 1.0:
        return None
    return v


def _as_str_list(v: Any, limit: int = 6) -> List[str]:
    if not isinstance(v, list):
        return []
    return [str(x)[:200] for x in v if isinstance(x, (str, int, float))][:limit]


def _ctx(**kw: Any) -> str:
    """§41/§42：只把该节点需要的切片拼进 prompt。"""
    import json
    return json.dumps(kw, ensure_ascii=False)[:4000]


def _items(obj: Optional[Dict[str, Any]], key: str, node: str,
           router: ModelRouter,
           single_item_keys: Optional[tuple] = None) -> List[Dict[str, Any]]:
    """取 `obj[key]` 列表；**缺失/为空时留痕再回退**。

    ★ 实测踩坑（2026-10-02）：opportunity/creative 曾有两种失败，一种是解析失败（有报错），
    另一种是 **JSON 解析成功但关键列表为空** —— 后者**不报错、静默回退规则**，产物看起来
    正常，`llm_errors` 却是空的，完全查不出「模型其实没贡献」。
    所以这里必须显式记一条 `soft_empty`，让「解析成功但没内容」变成可观测信号。
    """
    if not isinstance(obj, dict):
        return []
    v = obj.get(key)
    if not isinstance(v, list) or not v:
        # ★ 模型给了**裸对象**（没套 `{key: [...]}`）时也接受，但必须留痕。
        #   实测 risk 节点就是这样：顶层键是 type/level/description。
        #   前提是这些键确实属于条目本身，避免把解析错的壳当成条目。
        if single_item_keys and all(k in obj for k in single_item_keys):
            router.last_errors.append(
                f"{node}: 模型返回裸对象未套 `{key}` → 按单条采纳（unwrapped_single）")
            return [obj]
        router.last_errors.append(
            f"{node}: JSON 解析成功但 `{key}` 缺失或为空（soft_empty）"
            f" 顶层键={list(obj.keys())[:8]}")
        router.last_raw.setdefault(f"{node}:soft_empty", str(obj)[:600])
        return []
    return [x for x in v if isinstance(x, dict)]


# ---------------------------------------------------------------- Trend Analyst

def augment_trend_analyst(rule_out: Dict[str, Any], pack: Dict[str, Any],
                          router: ModelRouter) -> Dict[str, Any]:
    if not getattr(router, "enabled", False):
        return rule_out
    prompt = _ctx(
        event=pack.get("event"),
        platforms={k: v.get("count") for k, v in (pack.get("platforms") or {}).items()},
        signals=pack.get("signals"),
        tier_counts=pack.get("tier_counts"),
        evidence=[{"id": e["evidence_id"], "tier": e["tier"], "platform": e["platform"],
                   "excerpt": e["excerpt"]} for e in (pack.get("evidence") or [])[:8]],
        unknowns=pack.get("unknowns"),
    )
    obj = router.call_json("trend_analyst", prompt, system=system_for("trend_analyst"),
                           max_tokens=4200,
                           repair_hint="上一次输出不完整。字段照旧，必须输出完整可解析的 JSON 对象。")
    if not obj or not isinstance(obj.get("what_happened"), str):
        return _mark(rule_out, "trend_analyst", router, ok=False)
    out = dict(rule_out)
    out["what_happened"] = str(obj["what_happened"])[:300]
    out["why_now"] = str(obj.get("why_now") or rule_out.get("why_now"))[:200]
    out["trigger"] = (str(obj["trigger"])[:200] if obj.get("trigger") else rule_out.get("trigger"))
    out["narratives"] = _as_str_list(obj.get("narratives"), 6) or rule_out.get("narratives")
    out["key_uncertainties"] = _as_str_list(obj.get("key_uncertainties"), 6)
    out["facts"] = _as_str_list(obj.get("facts"), 8) or rule_out.get("facts")
    out["inferences"] = _as_str_list(obj.get("inferences"), 8)
    out["unknowns"] = _as_str_list(obj.get("unknowns"), 8)
    out["lifecycle_interpretation"] = str(
        obj.get("lifecycle_interpretation") or rule_out.get("lifecycle_interpretation"))[:200]
    out["confidence"] = _clip01(obj.get("confidence")) if obj.get("confidence") is not None \
        else rule_out.get("confidence")
    return _mark(out, "trend_analyst", router, ok=True, llm=obj.get("_llm"))


# ---------------------------------------------------------------- Relevance

def augment_relevance(rule_out: Dict[str, Any], pack: Dict[str, Any],
                      router: ModelRouter) -> Dict[str, Any]:
    if not getattr(router, "enabled", False):
        return rule_out
    from .analysis import RELEVANCE_WEIGHTS, RELEVANCE_ARCHIVE, RELEVANCE_LIGHT
    prompt = _ctx(
        event=pack.get("event"),
        signals=pack.get("signals"),
        tier_counts=pack.get("tier_counts"),
        platforms={k: v.get("count") for k, v in (pack.get("platforms") or {}).items()},
        evidence_excerpts=[e["excerpt"] for e in (pack.get("evidence") or [])[:6]],
        taptap_assets=[{"id": a, "name": v.get("name"), "goals": v.get("goals"), "cost": v.get("cost")}
                       for a, v in list(ASSET_BY_ID.items())],
        rule_baseline=rule_out.get("dimensions"),
    )
    obj = router.call_json("relevance", prompt, system=system_for("relevance"),
                           max_tokens=4200,
                           repair_hint="上一次输出不完整。字段照旧，必须输出完整可解析的 JSON 对象。")
    dims_raw = (obj or {}).get("dimensions")
    if not isinstance(dims_raw, dict):
        return _mark(rule_out, "relevance", router, ok=False)
    keys = {"user_overlap": "U", "community_fit": "C", "platform_asset_fit": "A",
            "growth_potential": "G", "timing_fit": "T"}
    dims = {}
    for k in keys:
        v = _dim01(dims_raw.get(k))
        if v is None:
            return _mark(rule_out, "relevance", router, ok=False)   # 少一维/越界就整体不采纳
        dims[k] = round(v, 3)
    score = round(sum(RELEVANCE_WEIGHTS[keys[k]] * v for k, v in dims.items()), 3)
    route = ("archive" if score < RELEVANCE_ARCHIVE
             else "light_analysis" if score < RELEVANCE_LIGHT else "opportunity")
    out = dict(rule_out)
    out["dimensions"] = dims
    out["score"] = score
    out["route"] = route
    # ★ 层级坑：reasons 在 obj 根下，不在 dimensions 里（最初写成 dims_raw.get("reasons")
    #   → 永远是空 dict，模型给的打分理由被静默丢弃，只留下分数看不出依据）。
    reasons = (obj or {}).get("reasons")
    out["reasons"] = ({k: str(v)[:120] for k, v in reasons.items()}
                      if isinstance(reasons, dict) and reasons else rule_out.get("reasons"))
    out["dimensions_source"] = "llm"
    return _mark(out, "relevance", router, ok=True, llm=obj.get("_llm"))


# ---------------------------------------------------------------- Audience

def augment_audience(rule_out: List[Dict[str, Any]], pack: Dict[str, Any],
                     router: ModelRouter) -> List[Dict[str, Any]]:
    if not getattr(router, "enabled", False):
        return rule_out
    prompt = _ctx(
        event=pack.get("event"),
        evidence_excerpts=[e["excerpt"] for e in (pack.get("evidence") or [])[:8]],
        signals=pack.get("signals"),
        motivation_candidates=[{"id": m["motivation_id"], "name": m["name"]}
                               for m in MOTIVATION_BY_ID.values()],
        rule_baseline=rule_out,
    )
    obj = router.call_json("audience", prompt, system=system_for("audience"),
                           max_tokens=4200,
                           repair_hint="上一次输出不完整。只输出 3 个人群，必须完整可解析。")
    items = _items(obj, "audiences", "audience", router)
    if not isinstance(items, list) or not items:
        return rule_out
    out = []
    for it in items[:5]:
        if not isinstance(it, dict):
            continue
        mid = it.get("motivation_id")
        if mid not in MOTIVATION_BY_ID:
            continue                      # 动机必须来自受控集合，不许模型自创
        strength = _dim01(it.get("interest_strength"))
        out.append({
            "segment": str(it.get("segment") or "")[:60],
            "motivation": MOTIVATION_BY_ID[mid]["name"],
            "motivation_id": mid,
            "interest_strength": round(strength if strength is not None else 0.6, 3),
            "evidence": str(it.get("evidence") or "模型判定")[:120],
            "signals": _as_str_list(it.get("signals"), 4),
            "mode": "llm",
        })
    if not out:
        return rule_out
    for a in out:
        a["prompt_version"] = PROMPT_VERSIONS["audience"]
    return out


# ---------------------------------------------------------------- Opportunity

def augment_opportunity(rule_out: List[Dict[str, Any]], state: Dict[str, Any],
                        router: ModelRouter) -> List[Dict[str, Any]]:
    if not getattr(router, "enabled", False):
        return rule_out
    pack = state.get("evidence_pack") or {}
    prompt = _ctx(
        event=pack.get("event"),
        relevance=(state.get("relevance") or {}).get("score"),
        audiences=state.get("audiences"),
        growth_goals=GROWTH_GOALS,
        mechanisms=["曝光→点击→互动→UGC→分享→站外回流→新用户",
                    "热点→游戏讨论→详情页访问→关注游戏→预约/下载",
                    "热点→话题讨论→社区沉淀→长期活跃",
                    "热点→达人产能→内容供给→分发放大→触达",
                    "热点→精准触达→唤起兴趣→回访→留存",
                    "热点→生成物料→站外分享→回流"],
        taptap_assets=[{"id": a, "name": v.get("name"), "cost": v.get("cost")}
                       for a, v in ASSET_BY_ID.items()],
        rule_baseline=[{k: o.get(k) for k in ("name", "growth_goal", "growth_mechanism")}
                       for o in rule_out],
        constraint="每个机会必须说清增长从哪来；没有 growth_mechanism 的不成立",
    )
    # ★ 输出最长：4 个机会 × 每个约 10 个字段，1500 tokens 不够 → 会被截成半截 JSON
    obj = router.call_json("opportunity", prompt, system=system_for("opportunity"),
                           max_tokens=4200,
                           repair_hint="上一次输出不完整。这次**只输出 2 个机会**，字段照旧，"
                                       "必须输出完整可解析的 JSON 对象，不要截断。")
    items = _items(obj, "opportunities", "opportunity", router)
    if not items:
        return rule_out
    from .opportunity import OPP_WEIGHTS, _mechanism_for, _platform_fit, _WINDOW_BY_LIFECYCLE
    rel_dims = (state.get("relevance") or {}).get("dimensions") or {}
    rel_score = float((state.get("relevance") or {}).get("score") or 0)
    lifecycle = str((pack.get("event") or {}).get("lifecycle") or "UNKNOWN").upper()
    window, w = _WINDOW_BY_LIFECYCLE.get(lifecycle, ("未知", 0.5))
    out = []
    for i, it in enumerate(items[:4]):
        if not isinstance(it, dict):
            continue
        goal = it.get("growth_goal")
        if goal not in GROWTH_GOALS:
            continue                       # §23：增长目标必须是有限集合内的
        mech = _mechanism_for(goal)
        chain = str(it.get("growth_mechanism") or "").strip()
        if len([s for s in chain.split("→") if s.strip()]) < 2:
            chain = mech["chain"]          # 说不清机制就用受控机制链兜底
        mot = _dim01(it.get("motivation_strength"))
        f = _platform_fit(mech, rel_dims)
        m = mot if mot is not None else 0.6
        e = {"low": 0.90, "medium": 0.65, "high": 0.35}.get(mech["cost"], 0.65)
        score = round(max(0.0, min(1.0, OPP_WEIGHTS["R"] * rel_score + OPP_WEIGHTS["M"] * m
                                   + OPP_WEIGHTS["F"] * f + OPP_WEIGHTS["W"] * w
                                   + OPP_WEIGHTS["D"] * 0.6 + OPP_WEIGHTS["E"] * e)), 3)
        out.append({
            "opportunity_id": f"opp_{i + 1}",
            "name": str(it.get("name") or f"{it.get('audience')} × {GROWTH_GOALS[goal]}")[:80],
            "audience": str(it.get("audience") or "")[:60],
            "user_motivation": str(it.get("user_motivation") or "")[:60],
            "motivation_id": it.get("motivation_id"),
            "observed_signal": str(it.get("observed_signal") or "")[:200],
            "platform_advantage": str(it.get("platform_advantage")
                                      or ", ".join(ASSET_BY_ID.get(a, {}).get("name", a)
                                                   for a in mech["assets"]))[:120],
            "growth_mechanism": chain,
            "growth_goal": goal,
            "opportunity_window": window,
            "expected_metrics": _as_str_list(it.get("expected_metrics"), 3) or [goal],
            "feasibility": e,
            "opportunity_score": score,
            "dimensions": {"R": round(rel_score, 3), "M": round(m, 3), "F": round(f, 3),
                           "W": round(w, 3), "D": 0.6, "E": e},
            "weights": OPP_WEIGHTS,
            "source_refs": {"event_id": state.get("event_id"),
                            "evidence_ids": [e2["evidence_id"] for e2 in (pack.get("evidence") or [])[:5]],
                            "audience_motivation_id": it.get("motivation_id")},
            "mode": "llm",
            "prompt_version": PROMPT_VERSIONS["opportunity"],
        })
    return out or rule_out


# ---------------------------------------------------------------- Creative

def augment_creative(rule_out: List[Dict[str, Any]], state: Dict[str, Any],
                     router: ModelRouter) -> List[Dict[str, Any]]:
    if not getattr(router, "enabled", False) or not rule_out:
        return rule_out
    pack = state.get("evidence_pack") or {}
    # §42 Context Package：只给事件摘要 / 受众 / 机会 / 资产 / 约束
    hypo = {h["opportunity_id"]: h.get("hypothesis")
            for h in (state.get("growth_hypotheses") or [])}
    prompt = _ctx(
        event=pack.get("event"),
        audiences=[a.get("segment") for a in (state.get("audiences") or [])],
        opportunities=[{"id": o["opportunity_id"], "name": o["name"], "goal": o["growth_goal"],
                        "mechanism": o["growth_mechanism"], "window": o["opportunity_window"],
                        "hypothesis": hypo.get(o["opportunity_id"])}
                       for o in (state.get("opportunities") or [])],
        creative_types={k: {"name": v["name"], "cost": v["cost"], "lead_time_hours": v["lead_time_hours"]}
                        for k, v in CREATIVE_TYPES.items()},
        taptap_assets=[{"id": a, "name": v.get("name")} for a, v in ASSET_BY_ID.items()],
        constraints={"launch_window": (state.get("opportunities") or [{}])[0].get("opportunity_window"),
                     "per_opportunity_max": 2, "quality_over_quantity": True},
        rule_baseline=[{"idea_name": c.get("idea_name"), "type": c.get("creative_type")}
                       for c in rule_out],
    )
    # ★ 创意含 user_flow / risks 等多个列表，输出比 opportunity 还长
    obj = router.call_json("creative", prompt, system=system_for("creative"),
                           max_tokens=4200,
                           repair_hint="上一次输出不完整。这次**只输出 2 条创意**，字段照旧，"
                                       "必须输出完整可解析的 JSON 对象，不要截断。")
    items = _items(obj, "creatives", "creative", router)
    if not isinstance(items, list) or not items:
        return rule_out
    by_type = {c.get("creative_type"): c for c in rule_out}
    out: List[Dict[str, Any]] = []
    for it in items[:8]:
        if not isinstance(it, dict):
            continue
        ctype = it.get("creative_type")
        if ctype not in CREATIVE_TYPES:
            continue                       # §26：类型必须在有限集合内
        base = by_type.get(ctype) or rule_out[0]
        c = dict(base)                     # ★ 溯源字段沿用规则版（不信任模型编 id）
        c["idea_id"] = f"idea_{len(out) + 1}"
        c["idea_name"] = str(it.get("idea_name") or base.get("idea_name"))[:80]
        c["creative_type"] = ctype
        c["creative_type_name"] = CREATIVE_TYPES[ctype]["name"]
        c["insight"] = str(it.get("insight") or base.get("insight"))[:200]
        c["concept"] = str(it.get("concept") or base.get("concept"))[:250]
        c["user_flow"] = _as_str_list(it.get("user_flow"), 8) or base.get("user_flow")
        c["distribution_channels"] = _as_str_list(it.get("distribution_channels"), 5) \
            or base.get("distribution_channels")
        c["primary_metric"] = str(it.get("primary_metric") or base.get("primary_metric"))[:40]
        c["secondary_metrics"] = _as_str_list(it.get("secondary_metrics"), 3) \
            or base.get("secondary_metrics")
        c["dependencies"] = _as_str_list(it.get("dependencies"), 5) or base.get("dependencies")
        c["risks"] = _as_str_list(it.get("risks"), 4)
        c["mode"] = "llm"
        c["prompt_version"] = PROMPT_VERSIONS["creative"]
        out.append(c)
    return out or rule_out


# ---------------------------------------------------------------- Evaluator

def augment_evaluation(rule_evals: List[Dict[str, Any]], creatives: List[Dict[str, Any]],
                       state: Dict[str, Any], router: ModelRouter) -> List[Dict[str, Any]]:
    """§40：Evaluator 用**另一个模型 + 完全独立的 prompt** 评审。

    ★ 实测教训：最初**每条创意单独调一次**，8 条创意 = 8 次调用，叠加免费模型的限流退避，
      单事件跑到 560 秒超时（exit=124）。改为**一次批量评审**：一次调用评完所有创意。
    """
    if not getattr(router, "enabled", False) or not creatives:
        return rule_evals
    pack = state.get("evidence_pack") or {}
    prompt = _ctx(
        event={"title": (pack.get("event") or {}).get("title"),
               "lifecycle": (pack.get("event") or {}).get("lifecycle")},
        creatives=[{"idea_id": c.get("idea_id"), "idea_name": c.get("idea_name"),
                    "creative_type": c.get("creative_type"),
                    "target_audience": c.get("target_audience"),
                    "concept": c.get("concept"), "user_flow": c.get("user_flow"),
                    "growth_mechanism": c.get("growth_mechanism"),
                    "primary_metric": c.get("primary_metric"),
                    "launch_window": c.get("launch_window"),
                    "lead_time_hours": c.get("lead_time_hours")} for c in creatives],
    )
    obj = router.call_json("evaluator", prompt, system=system_for("evaluator"),
                           max_tokens=4200,
                           repair_hint="上一次输出不完整。请为**每条** idea_id 输出一个评分对象，"
                                       "必须输出完整可解析的 JSON 对象，不要截断。")
    items = _items(obj, "evaluations", "evaluator", router)
    # ★ 判空必须看 `obj` 本身：`_items` 恒返回 list（obj=None 时返回 []），
    #   只判 `isinstance(items, list)` 是死守卫 → 走到 obj.get("_llm") 直接 AttributeError。
    if obj is None or not items:
        return [_mark(e, "evaluator", router, ok=False) for e in rule_evals]
    from .evaluation import RUBRIC_WEIGHTS, PASS_SCORE
    keys = {"relevance": "R", "user_insight": "U", "timing": "T", "growth": "G",
            "feasibility": "F", "novelty": "N", "distribution": "D"}
    by_id = {r.get("idea_id"): r for r in rule_evals}
    llm_meta = obj.get("_llm")
    out: List[Dict[str, Any]] = []
    used = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        iid = it.get("idea_id")
        base = by_id.get(iid)
        if base is None:
            continue
        dims_raw = it.get("dimensions")
        if not isinstance(dims_raw, dict):
            out.append(_mark(base, "evaluator", router, ok=False))
            used.add(iid)
            continue
        dims, ok = {}, True
        for k in keys:
            v = _dim01(dims_raw.get(k))
            if v is None:
                ok = False
                break
            dims[k] = round(v, 3)
        if not ok:
            out.append(_mark(base, "evaluator", router, ok=False))
            used.add(iid)
            continue
        score = round(max(0.0, min(1.0, sum(RUBRIC_WEIGHTS[keys[k]] * v
                                            for k, v in dims.items()))), 3)
        merged = dict(base)
        merged.update({"dimensions": dims, "creative_score": score,
                       "pass": score >= PASS_SCORE,
                       "weaknesses": _as_str_list(it.get("weaknesses"), 5),
                       "recommended_revision": _as_str_list(it.get("recommended_revision"), 5)})
        out.append(_mark(merged, "evaluator", router, ok=True, llm=llm_meta))
        used.add(iid)
    missed = [r.get("idea_id") for r in rule_evals if r.get("idea_id") not in used]
    if missed:
        # ★ 批量评审的**部分覆盖**必须留痕。实测：4 条创意模型只评了 1 条，
        #   另外 3 条悄悄回退规则，而 `llm_errors` 是空的 —— 又一种静默降级。
        router.last_errors.append(
            f"evaluator: 批量评审只覆盖 {len(used)}/{len(rule_evals)} 条"
            f"（partial_coverage）漏评={missed}")
    for r in rule_evals:                      # 模型漏评的保留规则结果
        if r.get("idea_id") not in used:
            out.append(_mark(r, "evaluator", router, ok=False))
    order = {c.get("idea_id"): i for i, c in enumerate(creatives)}
    out.sort(key=lambda e: order.get(e.get("idea_id"), 999))
    return out


# ---------------------------------------------------------------- Risk

def augment_risk(rule_out: Dict[str, Any], state: Dict[str, Any],
                 router: ModelRouter) -> Dict[str, Any]:
    if not getattr(router, "enabled", False):
        return rule_out
    pack = state.get("evidence_pack") or {}
    prompt = _ctx(
        event=pack.get("event"),
        tier_counts=pack.get("tier_counts"),
        primary_ratio=pack.get("primary_ratio"),
        evidence=[{"id": e["evidence_id"], "tier": e["tier"], "excerpt": e["excerpt"]}
                  for e in (pack.get("evidence") or [])[:6]],
        creatives=[{"id": c.get("idea_id"), "concept": c.get("concept")}
                   for c in (state.get("creatives") or [])[:5]],
        rule_baseline=rule_out.get("risks"),
    )
    obj = router.call_json("risk", prompt, system=system_for("risk"),
                           max_tokens=4200,
                           repair_hint="上一次输出不完整。只输出 3 条风险，必须完整可解析。")
    items = _items(obj, "risks", "risk", router,
                   single_item_keys=("type", "level"))
    if obj is None or not items:                 # 同上：必须判 obj，不是判 items 的类型
        return _mark(rule_out, "risk", router, ok=False)
    merged = list(rule_out.get("risks") or [])
    for it in items[:6]:
        if not isinstance(it, dict):
            continue
        t = it.get("type")
        if t not in _RISK_TYPES:
            continue                       # 风险类型也受控
        merged.append({"type": t,
                       "level": it.get("level") if it.get("level") in ("low", "medium", "high") else "medium",
                       "description": str(it.get("description") or "")[:160],
                       "constraint": str(it.get("constraint") or "")[:160],
                       "source": "llm"})
    level = "high" if any(r.get("level") == "high" for r in merged) else (
        "medium" if merged else "low")
    out = dict(rule_out)
    out["risks"] = merged
    out["risk_level"] = level
    return _mark(out, "risk", router, ok=True, llm=obj.get("_llm"))


# ---------------------------------------------------------------- 标记

def _mark(out: Dict[str, Any], node: str, router: ModelRouter, ok: bool,
          llm: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = dict(out)
    out["mode"] = "llm" if ok else "rule_fallback_after_llm_error"
    out["prompt_version"] = PROMPT_VERSIONS.get(node)
    if ok and llm:
        out["model"] = llm.get("model")
        out["llm_seconds"] = llm.get("seconds")
    else:
        out["llm_error"] = (router.last_errors or ["unknown"])[-1][:160]
    return out
