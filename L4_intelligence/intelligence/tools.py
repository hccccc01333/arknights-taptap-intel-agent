# -*- coding: utf-8 -*-
"""Research 工具集（§17）。

★ 两条硬约束：
  ① **全部 Read Only** —— 绝不让 Research Agent 发 Push / 建 Campaign / 改数据库。
  ② 每个工具必须声明 `text_access`（复用 `runtime/task_contracts.py` 的同一套常量）：
     读哪些源、什么粒度（none|short|full）、是否送 LLM、**读完只回传什么**。
     声明不全 = 盲目系统（读了什么都说不清）或失控系统（把原文回传进 State）。

外部检索工具（search_web / search_social）在本机**不可用**，调用时显式抛 `ToolUnavailable`，
绝不返回假结果冒充检索过。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime.task_contracts import (  # noqa: E402
    TEXT_GRANULARITIES, RAW_TEXT_MARKS, LLM_TOOLS,
)

TOOLS_VERSION = "tools-1.0"


class ToolUnavailable(RuntimeError):
    pass


def validate_text_access(ta: Dict[str, Any]) -> List[str]:
    """与 `runtime/task_contracts.validate` 中 text_access 段同一套规则（此处只校验单工具）。"""
    problems: List[str] = []
    if not isinstance(ta, dict) or not ta:
        return ["text_access 缺失"]
    gran = ta.get("granularity")
    src = ta.get("sources") or []
    if gran not in TEXT_GRANULARITIES:
        problems.append(f"granularity 非法：{gran!r}")
    if gran == "none" and src:
        problems.append("granularity=none 却声明了 sources")
    if gran in ("short", "full") and not src:
        problems.append(f"granularity={gran} 必须声明 sources —— 读哪里都不写就是盲目")
    if ta.get("via_llm") and not (ta.get("returns") or []):
        problems.append("via_llm=True 却没有 returns：必须回传结论，不许回传原文")
    for r in ta.get("returns") or []:
        name = str(r).lower()
        # ★ 继承守卫的假阳性：`RAW_TEXT_MARKS` 含 "content"，子串匹配会把 `content_id` 也拦下。
        #   但 `content_id` 正是"**引用而非搬运**"的载体 —— 回传 id 才能溯源，回传原文才失控。
        #   所以 `*_id` 一律放行（它是对原文的引用，不是原文本身）。
        #   TODO: 这个规则应上移到 `runtime/task_contracts.py`，让全项目口径一致。
        if name.endswith("_id"):
            continue
        hit = [m for m in RAW_TEXT_MARKS if m in name]
        if hit:
            problems.append(f"returns 含原文类字段 {r!r}（命中 {hit}）")
    return problems


class Tool:
    def __init__(self, name: str, description: str, fn: Callable[..., Any],
                 text_access: Dict[str, Any], read_only: bool = True,
                 available: bool = True, unavailable_reason: str = ""):
        problems = validate_text_access(text_access)
        if problems:
            raise ValueError(f"工具 {name} 的 text_access 非法：{'; '.join(problems)}")
        if not read_only:
            raise ValueError(f"工具 {name} 必须是 read_only（§17 第一版全部只读）")
        self.name = name
        self.description = description
        self.fn = fn
        self.text_access = text_access
        self.read_only = read_only
        self.available = available
        self.unavailable_reason = unavailable_reason

    def run(self, **kwargs: Any) -> Any:
        if not self.available:
            raise ToolUnavailable(f"工具 {self.name} 不可用：{self.unavailable_reason}")
        return self.fn(**kwargs)


# ---------------------------------------------------------------- 工具实现

def _search_event_content(upstream: Any, event_id: str, limit: int = 20,
                          tier: Optional[str] = None) -> List[Dict[str, Any]]:
    """读事件内的原始内容 —— **granularity=full**（在工具内部读，只回传结构化结论）。"""
    members = upstream.members(event_id)
    out = []
    for c in members[:limit]:
        sf = c.get("source_features") or {}
        t = "PRIMARY" if sf.get("is_official") else "COMMUNITY"
        if tier and t != tier:
            continue
        # ★ 全文只在函数局部使用，不出现在返回值里
        full = upstream.read_full_text(c.get("content_id")) or ""
        out.append({
            "content_id": c.get("content_id"),
            "platform": c.get("platform"),
            "tier": t,
            "excerpt": full[:80],
            "text_length": len(full),
            "has_official_signal": any(k in full for k in ("官方", "公告", "官宣")),
        })
    return out


def _get_entity_history(upstream: Any, entity: str) -> Dict[str, Any]:
    """某实体过去出现在哪些事件里（历史事件检索）。"""
    rows = upstream.events(limit=5000)
    hits = [{"event_id": r.get("event_id"), "title": r.get("canonical_title"),
             "lifecycle": r.get("lifecycle"), "content_count": r.get("content_count")}
            for r in rows if entity in (r.get("entity_ids") or "")]
    return {"entity": entity, "n_events": len(hits), "events": hits[:10]}


def _search_experiments(upstream: Any, growth_goal: Optional[str] = None) -> List[Dict[str, Any]]:
    """历史增长实验（§37）。本机没有真实实验库 → 返回空并标明，**不编造历史效果数据**。"""
    return []


def build_tools(upstream: Any) -> Dict[str, Tool]:
    tools = [
        Tool("search_event_content", "查询 Event 内的原始内容（返回结构化结论，不返回全文）",
             lambda **kw: _search_event_content(upstream, **kw),
             {"sources": ["l2.processed_content"], "granularity": "full", "via_llm": False,
              "returns": ["content_id", "tier", "excerpt", "has_official_signal"]}),
        Tool("get_game_profile", "游戏知识（实体与别名）",
             lambda entity: {"entity": entity, "note": "游戏档案库未接入，返回实体本身"},
             {"sources": ["games.registry"], "granularity": "short", "via_llm": False,
              "returns": ["entity", "aliases"]}),
        Tool("get_entity_history", "该实体过去出现在哪些事件",
             lambda entity: _get_entity_history(upstream, entity),
             {"sources": ["l3.trend_event"], "granularity": "short", "via_llm": False,
              "returns": ["entity", "n_events", "events"]}),
        Tool("get_taptap_history", "TapTap 站内历史讨论",
             lambda **kw: (_ for _ in ()).throw(ToolUnavailable("TapTap 站内历史库未接入")),
             {"sources": ["taptap.internal"], "granularity": "short", "via_llm": False,
              "returns": ["hit_count", "sample_ids"]}, available=False,
             unavailable_reason="未接入 TapTap 站内历史库"),
        # granularity=none：读的是结构化实验记录，不是文本 → 按契约不许声明 sources
        Tool("search_experiments", "历史增长实验结果",
             lambda **kw: _search_experiments(upstream, **kw),
             {"sources": [], "granularity": "none", "via_llm": False,
              "returns": ["case_id", "lift"]}),
        Tool("search_web", "外部检索",
             lambda **kw: (_ for _ in ()).throw(ToolUnavailable("本机无外网检索凭据")),
             {"sources": ["web"], "granularity": "full", "via_llm": True,
              "returns": ["claim", "source_url", "tier"]}, available=False,
             unavailable_reason="本机无外网检索凭据 / 未接入搜索 API"),
        Tool("search_social", "社媒证据检索",
             lambda **kw: (_ for _ in ()).throw(ToolUnavailable("本机无社媒检索凭据")),
             {"sources": ["social"], "granularity": "full", "via_llm": True,
              "returns": ["claim", "platform", "tier"]}, available=False,
             unavailable_reason="本机无社媒检索凭据"),
    ]
    return {t.name: t for t in tools}


def assert_all_read_only(tools: Dict[str, Tool]) -> None:
    bad = [n for n, t in tools.items() if not t.read_only]
    if bad:
        raise ValueError(f"存在非只读工具（§17 禁止）：{bad}")
