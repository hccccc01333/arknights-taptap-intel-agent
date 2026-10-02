# -*- coding: utf-8 -*-
"""Node 9-10：Evaluator（§29-§31）+ Risk Engine & Fact Check（§32-§33）。

★ §32：风险**单独存在**，不混进 creative_score。
★ §33：Fact Check 必须回到 Evidence —— claim → evidence lookup →
       SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / CONFLICTING。
       不允许"自己提 claim 又自己判真"。
★ §40：Evaluator 用**与 Creative 完全独立的 prompt**（本机规则实现同样独立，不走同一套模板）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..knowledge import MOTIVATION_BY_ID
from ..prompts import PROMPT_VERSIONS

# §29 Rubric 权重
RUBRIC_WEIGHTS = {"R": 0.20, "U": 0.15, "T": 0.15, "G": 0.15, "F": 0.15, "N": 0.10, "D": 0.10}
PASS_SCORE = 0.75          # §31 quality gate
MAX_ITERATION = 2          # §31 有界修订，防无限烧 token

_COST_FEAS = {"low": 0.90, "medium": 0.65, "high": 0.40}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def evaluate(creative: Dict[str, Any], state: Dict[str, Any],
             peers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    pack = state.get("evidence_pack") or {}
    rel = state.get("relevance") or {}
    ev = pack.get("event") or {}

    # R 相关性：创意是否紧扣热点（有溯源 + 事件置信度）
    refs = creative.get("source_refs") or {}
    has_refs = bool(refs.get("event_id")) and bool(refs.get("evidence_ids"))
    r = _clip01((0.5 if has_refs else 0.15) + 0.5 * float(rel.get("score") or 0))

    # U 用户洞察：动机是否来自证据命中的信号词
    mot_id = None
    for o in (state.get("opportunities") or []):
        if o.get("opportunity_id") == creative.get("opportunity_id"):
            mot_id = o.get("motivation_id") or o.get("audience_motivation_id")
    grounded = bool(mot_id and MOTIVATION_BY_ID.get(mot_id))
    u = _clip01(0.85 if grounded else 0.45)

    # T 时效：上线周期是否赶得上窗口
    window = str(creative.get("launch_window") or "")
    lead = float(creative.get("lead_time_hours") or 0)
    if "已错过" in window or window == "无窗口":
        t = 0.25 if lead <= 8 else 0.10
    elif "6-12h" in window:
        t = _clip01(1.0 - max(0.0, lead - 6) / 12)
    else:
        t = _clip01(1.0 - max(0.0, lead - 24) / 48)

    # G 增长机制：链条是否完整可解释
    chain = creative.get("growth_mechanism") or ""
    steps = [s for s in chain.split("→") if s.strip()]
    g = _clip01(min(1.0, len(steps) / 5)) if steps else 0.0

    # F 可行性：成本 + 依赖数
    f = _COST_FEAS.get(creative.get("implementation_cost"), 0.6)
    f = _clip01(f - 0.05 * max(0, len(creative.get("dependencies") or []) - 2))

    # N 新颖性：同批里同类型是否重复
    peers = peers or []
    same = sum(1 for p in peers if p.get("creative_type") == creative.get("creative_type")
               and p.get("idea_id") != creative.get("idea_id"))
    n = _clip01(1.0 - 0.3 * same)

    # D 分发：渠道数
    d = _clip01(min(1.0, len(creative.get("distribution_channels") or []) / 3))

    dims = {"relevance": round(r, 3), "user_insight": round(u, 3), "timing": round(t, 3),
            "growth": round(g, 3), "feasibility": round(f, 3), "novelty": round(n, 3),
            "distribution": round(d, 3)}
    score = round(_clip01(sum(RUBRIC_WEIGHTS[k[:1].upper()] * v for k, v in dims.items())), 3)

    weaknesses: List[str] = []
    revisions: List[str] = []
    if not has_refs:
        weaknesses.append("创意缺少证据溯源（source_refs 不完整）")
        revisions.append("补齐 event_id / evidence_ids，否则不算产出")
    if not grounded:
        weaknesses.append("用户动机未落到证据命中的动机信号")
        revisions.append("改用证据中实际命中的动机")
    if t < 0.5:
        weaknesses.append(f"上线周期 {lead}h 可能超过热点窗口（{window}）")
        revisions.append("改为低成本模板化方案（如 content / community），缩短上线时间")
    if len(steps) < 3:
        weaknesses.append("增长机制链条不完整，说不清增长从哪来")
        revisions.append("补全 growth_mechanism 到 ≥3 环节")
    if f < 0.5:
        weaknesses.append("实现成本或依赖偏重")
        revisions.append("砍掉高成本依赖，保留核心环节")
    if not creative.get("primary_metric"):
        weaknesses.append("缺少主指标")
        revisions.append("指定 primary_metric")

    return {
        "idea_id": creative.get("idea_id"),
        "creative_score": score,
        "dimensions": dims,
        "weights": RUBRIC_WEIGHTS,
        "pass": score >= PASS_SCORE,
        "weaknesses": weaknesses,
        "recommended_revision": revisions,
        "mode": "rule",
        "prompt_version": PROMPT_VERSIONS["evaluator"],
    }


# ---------------------------------------------------------------- §32/§33 Risk

def fact_check(creative: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """claim → evidence lookup → SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / CONFLICTING。"""
    pack = state.get("evidence_pack") or {}
    evidence = {e["evidence_id"]: e for e in (pack.get("evidence") or [])}
    refs = (creative.get("source_refs") or {})
    ids = refs.get("evidence_ids") or []
    found = [i for i in ids if i in evidence]
    facts = refs.get("facts") or {}

    if not ids:
        status = "UNSUPPORTED"
    elif len(found) == len(ids) and facts.get("content_count"):
        tier_highest = max((evidence[i].get("tier") for i in found),
                           key=lambda t: {"PRIMARY": 3, "SECONDARY": 2,
                                          "COMMUNITY": 1, "INFERRED": 0}.get(t, 0))
        status = "SUPPORTED" if tier_highest in ("PRIMARY", "SECONDARY") else "PARTIALLY_SUPPORTED"
    elif found:
        status = "PARTIALLY_SUPPORTED"
    else:
        status = "UNSUPPORTED"
    return {"idea_id": creative.get("idea_id"), "claim": creative.get("concept"),
            "status": status, "evidence_found": len(found), "evidence_claimed": len(ids),
            "facts": facts}


def risk(state: Dict[str, Any], creatives: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    pack = state.get("evidence_pack") or {}
    ev = pack.get("event") or {}
    creatives = creatives if creatives is not None else (state.get("creatives") or [])
    risks: List[Dict[str, Any]] = []

    # Fact：没有官方信源 → 文案不可断言
    if not pack.get("primary_ratio"):
        risks.append({"type": "fact", "level": "high",
                      "description": "事件无官方/权威信源确认",
                      "constraint": "对外文案禁止断言式措辞（如「正式公布」），只能用「讨论正在升温」"})
    # Timing
    lc = str(ev.get("lifecycle") or "").upper()
    if lc in ("DECLINING", "DORMANT"):
        risks.append({"type": "timing", "level": "medium",
                      "description": f"生命周期 {lc}，热点窗口已过或即将关闭",
                      "constraint": "只做低成本动作，或转为沉淀案例"})
    # Publisher：涉及厂商负面
    if any(m in " ".join(str(e.get("excerpt")) for e in (pack.get("evidence") or []))
           for m in ("吐槽", "差评", "退游", "氪金", "骗")):
        risks.append({"type": "publisher", "level": "medium",
                      "description": "事件含厂商负面情绪",
                      "constraint": "避免与厂商联合动作的正面包装；不做商业化引导"})
    # Operational：高成本创意遇上短窗口
    for c in creatives:
        if c.get("implementation_cost") == "high" and (c.get("lead_time_hours") or 0) > 24:
            risks.append({"type": "operational", "level": "medium",
                          "description": f"{c.get('idea_name')} 开发成本高于热点窗口",
                          "constraint": "改为模板化/轻量版本"})
    # Research blocked → 证据缺口本身就是风险
    rr = state.get("research_result") or {}
    if rr.get("status") == "incomplete":
        risks.append({"type": "fact", "level": "medium",
                      "description": "补充证据的检索未全部完成（工具不可用）",
                      "constraint": "结论标为待核实，不做最终决策依据"})

    level = "high" if any(r["level"] == "high" for r in risks) else (
        "medium" if risks else "low")
    return {
        "risk_level": level,
        "risks": risks,
        "fact_checks": [fact_check(c, state) for c in creatives],
        "mode": "rule",
        "prompt_version": PROMPT_VERSIONS["risk"],
    }
