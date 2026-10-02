# -*- coding: utf-8 -*-
"""Node 5：Evidence Gate + Research Agent（§15-§17）。

★ 规格：不是所有热点都要 Deep Research。只在证据不足/冲突/源头不明时启动。
★ Harness 的真正位置就在这里（§16）：Research 可以自己决定查什么、是否够了 ——
  但**有界**（max_steps），且工具全部只读。

本机现实：外部检索工具不可用。处理方式不是"假装有检索结果"，
而是**如实记录 blocked 与缺什么**，并把缺口传给 Risk 节点（§32 的 Fact Risk）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..prompts import PROMPT_VERSIONS
from ..tools import ToolUnavailable

MAX_RESEARCH_STEPS = 3

# 证据充分性判定阈值
MIN_EVIDENCE = 4
MIN_CONFIDENCE = 0.55


def evidence_sufficiency(pack: Dict[str, Any]) -> Dict[str, Any]:
    """§15 Evidence Sufficiency：够不够，决定要不要 Research。"""
    reasons: List[str] = []
    n = pack.get("n_evidence") or 0
    conf = float((pack.get("event") or {}).get("confidence") or 0)
    primary_ratio = pack.get("primary_ratio") or 0.0
    unknowns = pack.get("unknowns") or []

    if n < MIN_EVIDENCE:
        reasons.append(f"证据条数 {n} < {MIN_EVIDENCE}")
    if conf < MIN_CONFIDENCE:
        reasons.append(f"第三层 confidence {conf} < {MIN_CONFIDENCE}")
    if primary_ratio == 0:
        reasons.append("没有官方/权威信源（PRIMARY）")
    if len(unknowns) >= 2:
        reasons.append(f"未解问题 {len(unknowns)} 个 ≥ 2")

    sufficient = not reasons
    return {"sufficient": sufficient, "reasons": reasons,
            "needed": [] if sufficient else _what_is_needed(pack, reasons)}


def _what_is_needed(pack: Dict[str, Any], reasons: List[str]) -> List[str]:
    need: List[str] = []
    if any("PRIMARY" in r for r in reasons):
        need.append("官方确认信源（公告/官宣）")
    if any("confidence" in r for r in reasons):
        need.append("更多平台或更多条数的交叉证据")
    if any("证据条数" in r for r in reasons):
        need.append("补充同期相关内容")
    if (pack.get("timeline") or {}).get("n_distinct_times", 0) <= 1:
        need.append("带时间线的连续观测（当前数据无时间分辨率）")
    return need or ["外部背景信息"]


def research(state: Dict[str, Any], upstream: Any, tools: Dict[str, Any],
             max_steps: int = MAX_RESEARCH_STEPS) -> Dict[str, Any]:
    """有界的研究循环。只读工具；不可用则如实标记。"""
    pack = state.get("evidence_pack") or {}
    goal = {"needs": _what_is_needed(pack, (state.get("research_needed") or [])),
            "entities": (pack.get("entities") or [])[:3],
            "event_id": state.get("event_id")}
    steps: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    blocked: List[Dict[str, str]] = []

    plan = ["search_event_content"]
    if goal["entities"]:
        plan.append("get_entity_history")
    plan.append("search_web")

    for name in plan[:max_steps]:
        tool = tools.get(name)
        if tool is None:
            blocked.append({"tool": name, "reason": "未注册"})
            continue
        if not tool.available:
            blocked.append({"tool": name, "reason": tool.unavailable_reason})
            continue
        try:
            if name == "search_event_content":
                res = tool.run(event_id=state["event_id"], limit=20)
            elif name == "get_entity_history":
                res = tool.run(entity=goal["entities"][0])
            else:
                res = tool.run(query=state.get("event_title"))
            steps.append({"tool": name, "ok": True, "n_results": len(res) if isinstance(res, list) else 1})
            findings.append({"tool": name, "result": res})
        except ToolUnavailable as e:
            blocked.append({"tool": name, "reason": str(e)})
            steps.append({"tool": name, "ok": False})

    still_missing = bool(blocked)
    return {
        "goal": goal,
        "steps": steps,
        "findings": findings,
        "blocked_tools": blocked,
        # ★ 有 blocked 就标 incomplete —— 不能因为"跑过 research"就当作证据补齐了
        "status": "incomplete" if still_missing else "sufficient",
        "facts": [f"执行 {len([s for s in steps if s['ok']])}/{len(plan)} 个检索步骤"],
        "inferences": [],
        "unknowns": ([f"工具 {b['tool']} 不可用：{b['reason']}" for b in blocked]
                     + (goal["needs"] if still_missing else [])),
        "mode": "rule",
        "prompt_version": PROMPT_VERSIONS["research"],
    }


def node(state: Dict[str, Any], upstream: Any, tools: Optional[Dict[str, Any]] = None,
         **kw: Any) -> Dict[str, Any]:
    from ..tools import build_tools
    tools = tools or build_tools(upstream)
    res = research(state, upstream, tools)
    return {"research_result": res}
