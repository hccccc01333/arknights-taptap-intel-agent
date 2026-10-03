# -*- coding: utf-8 -*-
"""Context Builder（§43 / §44）。

★ §44：**不同 Agent 获取不同 Memory** —— 不要所有 Agent 共用同一个超级 RAG：
    Relevance   → 产品能力 / 用户画像 / 类似热点
    Opportunity → 历史 Opportunity / 成功实验 / 失败案例
    Creative    → 历史创意 / 执行约束 / 品牌规范
    Evaluator   → 历史接受标准 / 失败模式 / 成本约束
  每个上下文都带 provenance（§21），Agent 引用时可以回查"这条记忆是谁给的"。

★ §52：builder 产出的 log_id 要回传给调用方 —— Agent 用完上下文后
  调 `RetrievalEngine.mark_used(log_id, used_ids)` 回填，Memory 利用率才可测量。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from memory.retrieval import RetrievalEngine
from memory.store import MemoryStore

CONTEXT_BUILDER_VERSION = "context-1.0"

# §44 各 Agent 的检索配方：(memory_type, query 模板, filters, top_k)
_RECIPES: Dict[str, List[Dict[str, Any]]] = {
    "relevance_agent": [
        {"memory_type": "business", "query": "产品能力 资产 社区 活动", "top_k": 5},
        {"memory_type": "trend", "query": "{query}", "top_k": 5},
        {"memory_type": "entity", "query": "{query}", "top_k": 3},
    ],
    "opportunity_agent": [
        {"memory_type": "experiment", "query": "{query}",
         "filters": {"performance_filter": {"relative_lift": ">0"}}, "top_k": 5},
        {"memory_type": "case", "query": "{query}", "top_k": 5},
        {"memory_type": "anti_pattern", "query": "{query}", "top_k": 3},
    ],
    "creative_agent": [
        {"memory_type": "case", "query": "{query}", "top_k": 5},
        {"memory_type": "playbook", "query": "{query}", "top_k": 2},
        {"memory_type": "business", "query": "品牌规范 运营约束 资源", "top_k": 3},
    ],
    "evaluator": [
        {"memory_type": "anti_pattern", "query": "{query}", "top_k": 3},
        {"memory_type": "decision", "query": "{query}", "top_k": 5},
        {"memory_type": "case", "query": "{query}", "top_k": 3},
    ],
    "research_agent": [
        {"memory_type": "trend", "query": "{query}", "top_k": 5},
        {"memory_type": "entity", "query": "{query}", "top_k": 3},
    ],
}


class ContextBuilder:
    def __init__(self, store: MemoryStore, engine: Optional[RetrievalEngine] = None):
        self.store = store
        self.engine = engine or RetrievalEngine(store)

    def build(self, agent_type: str, query: str = "",
              filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """组装某类 Agent 的 Memory Context。

        返回 {"agent_type", "sections": {memory_type: [package...]}, "log_ids": [...],
              "provenance": [...], "n_sections_with_hits"} —— 空库时 sections 为空但
        结构完整，调用方（第四层）不需要写任何 if-else。
        """
        recipes = _RECIPES.get(agent_type)
        if recipes is None:
            raise ValueError(f"未登记的 agent_type: {agent_type!r}（§44：上下文按 Agent 分配）")
        sections: Dict[str, List[Dict[str, Any]]] = {}
        log_ids: List[str] = []
        provenance: List[Dict[str, Any]] = []
        for recipe in recipes:
            q = (recipe.get("query") or "{query}").replace("{query}", query or agent_type)
            try:
                res = self.engine.retrieve(
                    q, memory_type=recipe["memory_type"],
                    filters={**(recipe.get("filters") or {}), **(filters or {})},
                    top_k=recipe.get("top_k", 5), caller=agent_type)
            except ValueError:
                continue                      # 该类型不支持某过滤列 → 跳过该段
            if res.get("log_id"):
                log_ids.append(res["log_id"])
            hits = res["items"]
            sections[recipe["memory_type"]] = hits
            for h in hits:
                provenance.append({"memory_type": h.get("memory_type"),
                                   "memory_id": h.get("memory_id"),
                                   "tier": h.get("tier"), "authority": h.get("authority")})
        return {"context_builder_version": CONTEXT_BUILDER_VERSION,
                "agent_type": agent_type, "query": query,
                "sections": sections, "log_ids": log_ids,
                "provenance": provenance,
                "n_sections_with_hits": sum(1 for v in sections.values() if v)}
