# -*- coding: utf-8 -*-
"""模型路由（§39）+ OpenRouter 客户端（免费模型）+ 降级（§43 审计）。

★ 三条纪律：
  ① **只用免费模型**（pricing 为 0）。模型清单来自 OpenRouter `/api/v1/models` 实测，
     不靠记忆猜名字 —— 付费模型一律不进 `TIER_MODELS`，路由层也拦一道。
  ② 没 key / 调用失败 → **回退规则实现**并如实标注 `mode`，绝不返回假输出冒充模型结果。
  ③ 每次调用记录 model / prompt_version / tokens，否则无法回答"本周为什么变差"（§43）。

Key 的存放：优先进程环境 `FREE_API_KEY`；本机实测它在 **Windows 机器级环境变量**（HKLM），
进程没继承到，所以再从注册表兜底读（只读，不写入任何文件）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"

# ---------------------------------------------------------------- §39 路由
NODE_TIERS: Dict[str, str] = {
    "evidence": "none",            # 规格：无 LLM / 小模型
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

# ★ 实测可用（2026-10-02 逐个 ping 过）：其余免费模型多为 429 限流或 403 仅限 agentic harness。
#   付费模型一律不在此表。
TIER_MODELS: Dict[str, Optional[str]] = {
    "none": None,
    "small": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "medium": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    # §40：evaluator 换一个厂商/系列，降低"自己评价自己作品"的风险
    "medium_strong": "poolside/laguna-s-2.1:free",
    "strong": "nvidia/nemotron-3-ultra-550b-a55b:free",
}

# 同一档位的备选（主模型限流/空回复时按顺序退让）
FALLBACKS: Dict[str, List[str]] = {
    # ★ 只留 2 个备选：免费模型限流频繁，备选链越长退避越久（曾跑到 560s 超时）
    "nvidia/nemotron-3-ultra-550b-a55b:free": ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                                               "poolside/laguna-s-2.1:free"],
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": ["poolside/laguna-s-2.1:free",
                                                           "openrouter/free"],
    "poolside/laguna-s-2.1:free": ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                                   "openrouter/free"],
    "openrouter/free": ["nvidia/nemotron-3-ultra-550b-a55b:free"],
}

MODEL_ROUTER_VERSION = "router-2.0-openrouter-free"


class LLMUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- Key

def _registry_key() -> Optional[str]:
    """Windows 机器级/用户级环境变量兜底（本机实测 key 在 HKLM，进程未继承）。"""
    if sys.platform != "win32":
        return None
    import winreg
    for hive, path in ((winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                       (winreg.HKEY_CURRENT_USER, r"Environment")):
        try:
            with winreg.OpenKey(hive, path) as k:
                v, _ = winreg.QueryValueEx(k, "FREE_API_KEY")
                if v:
                    return v
        except OSError:
            continue
    return None


def get_api_key() -> Optional[str]:
    return os.environ.get("FREE_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or _registry_key()


def has_llm() -> bool:
    return bool(get_api_key())


def list_free_models(timeout: int = 30) -> List[Dict[str, Any]]:
    """列出当前免费模型（有 key 就带上；models 接口公开也能读）。"""
    key = get_api_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    req = urllib.request.Request(MODELS_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    out = []
    for m in data.get("data", []):
        p = m.get("pricing") or {}
        try:
            free = float(p.get("prompt") or 0) == 0 and float(p.get("completion") or 0) == 0
        except (TypeError, ValueError):
            free = False
        if free:
            out.append({"id": m.get("id"), "name": m.get("name"),
                        "context_length": m.get("context_length")})
    return out


def is_free_model(model_id: str, cache: Optional[List[Dict[str, Any]]] = None) -> Optional[bool]:
    """确认是免费模型。拿不到清单返回 None（未知，不武断判定）。"""
    try:
        models = cache if cache is not None else list_free_models()
    except Exception:
        return None
    for m in models or []:
        if m.get("id") == model_id:
            p = m.get("pricing") or {}
            try:
                return float(p.get("prompt") or 0) == 0 and float(p.get("completion") or 0) == 0
            except (TypeError, ValueError):
                return None
    return None


# ---------------------------------------------------------------- 用量

class Usage:
    """§ observability：token 与成本（免费模型 cost=0，但 token 仍要记）。"""

    def __init__(self) -> None:
        self.calls: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.failures: int = 0
        self.by_model: Dict[str, int] = {}

    def add(self, model: str, usage: Dict[str, Any]) -> None:
        self.calls += 1
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        self.by_model[model] = self.by_model.get(model, 0) + 1

    def as_dict(self) -> Dict[str, Any]:
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "failures": self.failures,
                "by_model": self.by_model, "cost_usd": 0.0,
                "note": "全部走 OpenRouter 免费模型（pricing=0），无费用"}


# ---------------------------------------------------------------- 路由 + 客户端

class ModelRouter:
    def __init__(self, enabled: Optional[bool] = None, overrides: Optional[Dict[str, str]] = None,
                 timeout: int = 45, max_retries: int = 1, usage: Optional[Usage] = None):
        self.enabled = has_llm() if enabled is None else (enabled and has_llm())
        self.overrides = overrides or {}
        self.timeout = timeout
        self.max_retries = max_retries
        self.usage = usage or Usage()
        self.last_errors: List[str] = []
        self.last_raw: Dict[str, str] = {}      # 解析失败时留证据（诊断截断 vs 不听话）
        self._repairing: bool = False           # 自愈重试只做一次，防止递归
        self.quota_exhausted: bool = False      # 免费额度熔断
        self.quota_reason: str = ""

    # ---- 路由 ----
    def resolve(self, node: str) -> Dict[str, Any]:
        tier = NODE_TIERS.get(node, "medium")
        model = self.overrides.get(node) or TIER_MODELS.get(tier)
        if not self.enabled or tier == "none":
            return {"node": node, "tier": tier, "model": None, "mode": "rule",
                    "reason": ("本节点规格上不用 LLM" if tier == "none"
                               else ("无 key / 未启用 → 规则兜底" if model else "无可用模型"))}
        return {"node": node, "tier": tier, "model": model, "mode": "llm",
                "reason": "OpenRouter 免费模型"}

    # ---- 底层单次调用 ----
    def chat(self, model: str, messages: List[Dict[str, str]],
             max_tokens: int = 1200, temperature: float = 0.4,
             json_mode: bool = False) -> Dict[str, Any]:
        key = get_api_key()
        if not key:
            raise LLMUnavailable("无 FREE_API_KEY")
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            OPENROUTER_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "HTTP-Referer": "http://localhost", "X-Title": "L4 Intelligence"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode("utf-8", "replace")
            if json_mode and ("response_format" in body or e.code == 400):
                raise LLMUnavailable(f"JSON_MODE_UNSUPPORTED: {body[:120]}")
            raise LLMUnavailable(f"HTTP {e.code}: {body}")
        except Exception as e:
            raise LLMUnavailable(f"{type(e).__name__}: {e}")
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        content = (msg.get("content") or "").strip()
        if not content:
            # 部分 reasoning 模型只回 reasoning 不回 content → 视为该模型不可用，上层换模型
            raise LLMUnavailable(f"模型 {model} 返回空 content（疑似只输出 reasoning）")
        self.usage.add(model, data.get("usage") or {})
        return {"content": content, "model": model,
                "usage": data.get("usage") or {}, "seconds": round(time.time() - t0, 2)}

    def call(self, node: str, prompt: str, system: Optional[str] = None,
             max_tokens: int = 1200, json_mode: bool = False) -> Dict[str, Any]:
        """按节点路由 + 主备模型退让。全部失败抛 LLMUnavailable。

        ★ 熔断：免费额度是**每日硬上限**（实测 `X-RateLimit-Limit: 50`，超限全部 429
        `free-models-per-day`）。不熔断的话，一次跑批会让**每个节点都各撞一次 429**——
        既浪费时间（每个都要等重试退避），又把失败噪声灌满 `llm_errors`。
        一旦确认是额度耗尽，本次进程内直接停用 LLM，后续节点走规则，并在产物里标清楚。
        """
        if self.quota_exhausted:
            raise LLMUnavailable(f"QUOTA_EXHAUSTED（{self.quota_reason}）→ 本进程停用 LLM，走规则")
        info = self.resolve(node)
        if info["mode"] != "llm":
            raise LLMUnavailable(info.get("reason") or "未启用模型")
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        tried = [info["model"]] + [m for m in FALLBACKS.get(info["model"], [])]
        last = ""
        for m in tried:
            for attempt in range(self.max_retries):
                try:
                    return self.chat(m, msgs, max_tokens=max_tokens, json_mode=json_mode)
                except LLMUnavailable as e:
                    last = str(e)
                    if "JSON_MODE_UNSUPPORTED" in str(e):
                        json_mode = False     # 该模型不支持强制 JSON → 后面改用提示词约束
                        continue
                    if "空 content" in str(e):
                        break                 # 换模型，不重试同一模型
                    if _is_quota_error(str(e)):
                        self.quota_exhausted = True
                        self.quota_reason = str(e)[:160]
                        self.last_errors.append(f"{node}: 免费额度耗尽 → 熔断，后续节点走规则")
                        raise LLMUnavailable(f"QUOTA_EXHAUSTED: {str(e)[:160]}")
                    time.sleep(1.0)
        self.usage.failures += 1
        self.last_errors.append(f"{node}: {last}")
        raise LLMUnavailable(f"节点 {node} 全部模型失败：{last}")

    # ---- 结构化输出 ----
    def call_json(self, node: str, prompt: str, system: Optional[str] = None,
                  max_tokens: int = 1500, repair_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """要求模型返回 JSON；解析失败或不是对象 → 返回 None（调用方回退规则）。

        ★ 实测踩坑（2026-10-02）：opportunity / creative 两个节点**输出最长**（4 个机会、每条带
        user_flow 列表），默认 1500 tokens 不够 → 生成到一半被截断 → 半截 JSON 解析失败 →
        整条回退规则，白烧一次调用。三处对策：
          ① 调用方按输出长度给足 max_tokens（见 `llm_augment`）；
          ② **失败时留证据**：把原始输出头尾记进 `last_errors`，否则只看到「不是 JSON 对象」
             根本不知道是截断还是模型不听话；
          ③ **一次自愈重试**：带上 `repair_hint`（要求少输出几条），仍失败才放弃。
        """
        if self.quota_exhausted:
            return None                       # 已熔断：不再重复记错误，产物顶层有总标记
        try:
            res = self.call(node, prompt, system=system, max_tokens=max_tokens, json_mode=True)
        except LLMUnavailable as e:
            if "QUOTA_EXHAUSTED" not in str(e):
                self.last_errors.append(f"{node}: {e}")
            return None
        obj = _extract_json(res["content"])
        if not isinstance(obj, dict):
            raw = (res.get("content") or "").strip()
            truncated = bool(raw) and not raw.rstrip().endswith(("}", "]"))
            why = "疑似被 max_tokens 截断" if truncated else "模型未按要求输出 JSON"
            self.last_errors.append(
                f"{node}: 输出不是 JSON 对象（{why}，len={len(raw)}）"
                f" head={raw[:120]!r} tail={raw[-80:]!r}")
            self.last_raw[node] = raw[:2000]
            if repair_hint and not self._repairing:
                self._repairing = True
                try:
                    return self.call_json(node, f"{prompt}\n\n{repair_hint}", system=system,
                                          max_tokens=max_tokens, repair_hint=None)
                finally:
                    self._repairing = False
            return None
        obj["_llm"] = {"model": res["model"], "seconds": res["seconds"],
                       "usage": res["usage"]}
        return obj


def _is_quota_error(msg: str) -> bool:
    """免费额度耗尽（每日硬上限）判定。

    实测错误体：`Rate limit exceeded: free-models-per-day. Add 10 credits to unlock
    1000 free model requests per day`，响应头 `X-RateLimit-Limit: 50`。
    这跟"临时限流"不是一回事——重试也没用，必须熔断。
    """
    low = (msg or "").lower()
    return "free-models-per-day" in low or "quota" in low or (
        "rate limit" in low and "per day" in low)


def _extract_json(text: str) -> Optional[Any]:
    """从可能带 ```json 围栏 / 前后散文的输出里抠出**第一个合法 JSON 对象**。

    ★ 实测踩坑：最初用「第一个 { 到最后一个 }」切片，模型在 JSON **后面**再写一段散文时，
      切片会把尾巴也吃进去 → 解析失败 → 节点整条回退规则（5 次成功调用里 3 次白烧）。
      改用 `raw_decode` 逐起点尝试：找到第一个能完整解析的对象就返回。
    """
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    cands = [m.group(1)] if m else []
    cands.append(text)
    dec = json.JSONDecoder()
    for cand in cands:
        cand = cand.strip()
        try:
            obj, _ = dec.raw_decode(cand)
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            pass
        for i, ch in enumerate(cand):
            if ch != "{":
                continue
            try:
                obj, _ = dec.raw_decode(cand[i:])
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict):
                return obj
    return None


def stamp(state: Dict[str, Any], node: str, route_info: Dict[str, Any],
          prompt_version: Optional[str] = None) -> None:
    """把模型/prompt 版本写进 State 的审计区（§43）。"""
    state.setdefault("model_versions", {})[node] = route_info.get("model") or "rule"
    if prompt_version:
        state.setdefault("prompt_versions", {})[node] = prompt_version
    if route_info.get("mode") == "llm":
        state["llm_used"] = True
