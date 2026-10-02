# -*- coding: utf-8 -*-
"""第四层 State（§7）+ State 守卫。

★ 规格原话：**不要把几十篇原文全部塞进 LangGraph State**。
State 保存的是「引用 / 摘要 / 结构化结果 / 状态」，原始数据永远从数据库或 Retriever 查。

★ 本项目已在别处立的规矩（见 `docs/Agent-v2-架构设计.md` §1.1）：
  「原始文本不进 State」= State 里不放原文。需要原文时按 id 检索，作为**那一次调用的临时输入**，
  用完只留结论（带 source id 可回查）。守卫是**体积闸门**，真正把文本挤出 State 的是契约。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

STATE_VERSION = "1.0"

# §9 事实层级：LLM 最容易把「玩家猜测」写成「官方事实」，所以每条证据必须带层级
FACT_TIERS = ("PRIMARY", "SECONDARY", "COMMUNITY", "INFERRED")
TIER_RANK = {"PRIMARY": 3, "SECONDARY": 2, "COMMUNITY": 1, "INFERRED": 0}

# 看起来像原文的字段名 → 不许进 State
_RAW_FIELD_HINTS = ("raw_text", "normalized_text", "full_text", "body", "content_text",
                    "thread_text", "原文", "正文")
_MAX_STR = 300          # 单字段超 300 字符视为疑似原文搬运
_MAX_EVIDENCE_CHARS = 120  # evidence excerpt 只放摘要级短引文


class IntelligenceState(TypedDict, total=False):
    # ---------- Trend（来自第三层，只读）----------
    event_id: str
    event_title: str
    event_summary: str
    hot_score: float
    momentum_score: float
    trend_confidence: float
    lifecycle: str

    # ---------- Evidence ----------
    evidence_pack: Dict[str, Any]

    # ---------- Analysis ----------
    trend_analysis: Optional[Dict[str, Any]]

    # ---------- TapTap ----------
    relevance: Optional[Dict[str, Any]]
    audiences: List[Dict[str, Any]]

    # ---------- Research ----------
    research_required: bool
    research_result: Optional[Dict[str, Any]]

    # ---------- Opportunity ----------
    opportunities: List[Dict[str, Any]]
    growth_hypotheses: List[Dict[str, Any]]

    # ---------- Creative ----------
    creatives: List[Dict[str, Any]]

    # ---------- Evaluation ----------
    evaluation: Optional[Dict[str, Any]]
    risk: Optional[Dict[str, Any]]

    # ---------- Workflow ----------
    status: str            # running / archived / research / ready / review / published / rejected
    route: str             # 最近一次路由决策
    iteration: int
    revision_notes: List[str]

    # ---------- Audit ----------
    analysis_version: str
    model_versions: Dict[str, str]
    prompt_versions: Dict[str, str]
    llm_used: bool


def _scan(node: Any, path: str, out: List[str]) -> None:
    if isinstance(node, str):
        if len(node) > _MAX_STR:
            out.append(f"{path}: 字符串 {len(node)} 字符 > {_MAX_STR}（疑似原文）")
        return
    if isinstance(node, dict):
        for k, v in node.items():
            ks = str(k).lower()
            if any(h in ks for h in _RAW_FIELD_HINTS):
                out.append(f"{path}.{k}: 字段名像原文")
            _scan(v, f"{path}.{k}", out)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            _scan(v, f"{path}[{i}]", out)


def assert_state_clean(state: Dict[str, Any], strict: bool = False) -> List[str]:
    """State 体积闸门。返回违规列表（空 = 通过）。

    ⚠️ 边界（与既有实现同源，别误读）：这是**体积闸门不是语义闸门** ——
    帖 summary 平均 90 字是能过闸的。真正把文本挡在外面的是 `nodes/evidence.py` 只写 excerpt 短引文。
    """
    violations: List[str] = []
    _scan(state, "state", violations)
    if strict and violations:
        raise ValueError("State 守卫未通过: " + "; ".join(violations))
    return violations


def empty_state(event: Dict[str, Any]) -> Dict[str, Any]:
    """从第三层 Event 初始化 State（只放引用与数字，不放内容）。"""
    return {
        "event_id": event.get("event_id"),
        "event_title": event.get("canonical_title"),
        "event_summary": None,
        "hot_score": event.get("hot_score"),
        "momentum_score": event.get("momentum_score"),
        "trend_confidence": event.get("confidence_score"),
        "lifecycle": event.get("lifecycle"),
        "evidence_pack": {},
        "trend_analysis": None,
        "relevance": None,
        "audiences": [],
        "research_required": False,
        "research_result": None,
        "opportunities": [],
        "growth_hypotheses": [],
        "creatives": [],
        "evaluation": None,
        "risk": None,
        "status": "running",
        "route": None,
        "iteration": 0,
        "revision_notes": [],
        "analysis_version": STATE_VERSION,
        "model_versions": {},
        "prompt_versions": {},
        "llm_used": False,
    }
