# -*- coding: utf-8 -*-
"""模型路由（§39）与降级（本机无 key）。

★ 纪律：没模型就**显式标 rule，不假装模型跑过**。
   每一节点都要记录 `model_used` / `prompt_version` / `mode`，
   否则将来无法回答"为什么本周 Agent 表现变差"（§43）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

# §39 Model Routing：不是所有节点都用最强模型
# node → (tier, 需要的模型档位)
NODE_TIERS: Dict[str, str] = {
    "evidence": "none",          # 规格：无 LLM / 小模型
    "trend_analyst": "medium",
    "relevance": "medium",
    "audience": "medium",
    "research": "strong",
    "opportunity": "strong",
    "strategist": "strong",
    "creative": "strong",
    "evaluator": "medium_strong",  # §40：尽量与 creative 不同模型/不同 prompt
    "risk": "medium",
}

TIER_MODELS: Dict[str, str] = {
    "none": None,
    "small": "gpt-4o-mini",
    "medium": "gpt-4o-mini",
    "medium_strong": "gpt-4o",
    "strong": "gpt-4o",
}

MODEL_ROUTER_VERSION = "router-1.0"


def has_llm() -> bool:
    """本机是否具备调用模型的能力（有 key 即可；生产换成任一家都只改这里）。"""
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
                or os.environ.get("ANTHROPIC_API_KEY"))


class ModelRouter:
    """按节点路由模型；不可用时返回 (None, 'rule') 让节点走规则实现。"""

    def __init__(self, enabled: Optional[bool] = None, overrides: Optional[Dict[str, str]] = None):
        self.enabled = has_llm() if enabled is None else enabled
        self.overrides = overrides or {}
        self.calls: int = 0

    def resolve(self, node: str) -> Dict[str, Any]:
        tier = NODE_TIERS.get(node, "medium")
        model = self.overrides.get(node) or TIER_MODELS.get(tier)
        if not self.enabled or tier == "none":
            return {"node": node, "tier": tier, "model": None, "mode": "rule",
                    "reason": ("本节点规格上不用 LLM" if tier == "none"
                               else "无 LLM key / 未启用 → 规则兜底")}
        self.calls += 1
        return {"node": node, "tier": tier, "model": model, "mode": "llm",
                "reason": "已启用模型调用"}

    def call(self, node: str, prompt: str, schema: Optional[str] = None) -> Dict[str, Any]:
        """真实调用入口。**无 key 时直接抛 LLMUnavailable**，绝不返回假输出冒充模型结果。"""
        if not self.enabled:
            raise LLMUnavailable(f"节点 {node} 需要模型，但本机未配置 LLM key")
        raise LLMUnavailable("模型调用尚未接入具体供应商（只改本方法即可）")


class LLMUnavailable(RuntimeError):
    pass


def stamp(state: Dict[str, Any], node: str, route_info: Dict[str, Any],
          prompt_version: Optional[str] = None) -> None:
    """把模型/prompt 版本写进 State 的审计区（§43）。"""
    state.setdefault("model_versions", {})[node] = route_info.get("model") or "rule"
    if prompt_version:
        state.setdefault("prompt_versions", {})[node] = prompt_version
    if route_info.get("mode") == "llm":
        state["llm_used"] = True
