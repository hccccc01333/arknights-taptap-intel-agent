# -*- coding: utf-8 -*-
"""三个 Gate 的判定（§14 / §15 / §31）+ 人工闸门（§34）。

★ 判定函数必须与执行引擎无关 —— langgraph 的条件边和标准库执行器**共用**这些函数，
  保证不会出现"两套业务规则"。
"""

from __future__ import annotations

from typing import Any, Dict

from ..nodes import evaluation as N_evaluation


def route_after_relevance(state: Dict[str, Any]) -> str:
    """§14 Relevance Router：archive(<.40) / light_analysis(<.65) / opportunity。"""
    return (state.get("relevance") or {}).get("route", "archive")


def route_evidence_gate(state: Dict[str, Any], sufficiency: Dict[str, Any]) -> str:
    """§15 Evidence Gate：证据不足 → research；足够 → 直接进 opportunity。

    ⚠️ 只有 opportunity 档才值得 Research（轻量档不做深研，省成本）。
    """
    if (state.get("route") or "") != "opportunity":
        return "opportunity"
    return "opportunity" if sufficiency.get("sufficient") else "research"


def route_quality(state: Dict[str, Any]) -> str:
    """§31 quality gate：全部通过 → pass；否则 revise（受 MAX_ITERATION 约束）。"""
    ev = state.get("evaluation") or {}
    items = ev.get("items") or []
    if not items:
        return "pass"
    passed = sum(1 for i in items if i.get("pass"))
    if passed == len(items):
        return "pass"
    if int(state.get("iteration", 0)) >= N_evaluation.MAX_ITERATION:
        return "pass"          # 有界：到上限就带着当前结果出去，不无限循环
    return "revise"


def route_after_review(state: Dict[str, Any]) -> str:
    """§34：人工决定。awaiting_review 时停在图外等恢复。"""
    status = state.get("status")
    if status == "approved":
        return "publish"
    return "end"
