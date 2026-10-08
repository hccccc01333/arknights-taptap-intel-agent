from __future__ import annotations

import os
import json
import time
from typing import Any
import requests

from L4_intelligence.intelligence.llm import (
    ModelRouter, PINNED_MODEL, LLMUnavailable, provider_key, resolve_provider,
)
from L4_intelligence.intelligence.chat_response import CompletionError, completion_parts, require_finished, tool_decisions


def model_status(selected=None) -> dict[str, Any]:
    model = selected or os.environ.get("V2_MODEL") or PINNED_MODEL
    return {"model": model, "configured": bool(model and provider_key(resolve_provider(model))),
            "fallback_model":"openrouter/free" if model.endswith(":free") else None,
            "note": "沿用本机已有模型配置；V2_MODEL 可覆盖。配置存在不等于调用可用。"}


class LiveModel:
    def __init__(self, selected=None):
        status = model_status(selected)
        self.model = status["model"]
        self.fallback = status["fallback_model"]
        if not status["configured"]:
            raise LLMUnavailable("未配置可用模型凭证；V2 不使用规则冒充 Agent 研究结果")
        self.router = ModelRouter(enabled=True, timeout=45, max_retries=1)

    def decide(self, messages: list[dict[str, Any]], definitions: list[dict[str, Any]]) -> dict[str, Any]:
        for attempt in range(2):
            try:
                return self._chat(messages,definitions)
            except LLMUnavailable as error:
                if attempt:
                    raise
                if "REASONING_UNSUPPORTED" in str(error):
                    self.router.reasoning_effort = None
                elif self.fallback:
                    self.model=self.fallback
        raise LLMUnavailable("模型重试未返回结果")

    def _chat(self,messages,definitions):
        provider=resolve_provider(self.model)
        payload={"model":self.model,"messages":messages,"max_tokens":6500,"temperature":0.2,"tools":definitions,"tool_choice":"auto"}
        if self.model!="openrouter/free" and self.router.reasoning_effort and provider["base_url"].startswith("https://openrouter.ai"):
            payload["reasoning"]={"max_tokens":768} if self.model.startswith("nvidia/nemotron-3-") else {"effort":self.router.reasoning_effort}
        if provider['base_url'].startswith('https://api.deepseek.com/'):
            effort=self.router.reasoning_effort or 'low'
            payload.update(thinking={'type':'enabled'},reasoning_effort=effort)
            payload.pop('temperature',None);payload.pop('tool_choice',None)
        started=time.monotonic()
        try:
            # Some gateways send heartbeat whitespace while a model is thinking.
            # A socket timeout alone cannot bound that response. Reading bytes
            # lets us enforce an elapsed budget even when the socket stays alive.
            with requests.post(provider["base_url"],json=payload,headers={"Authorization":"Bearer "+provider_key(provider),"Content-Type":"application/json"},timeout=(10,15),stream=True) as response:
                if response.status_code!=200:
                    reason={402:"账户不可用，请检查额度或服务配置",429:"服务限流，请稍后重试"}.get(response.status_code,"模型服务拒绝或不可用")
                    try:
                        message=str((response.json().get("error") or {}).get("message") or "")
                        if "free-models-per-day" in message:
                            reason="免费模型当天调用额度已用尽；请等待重置或选择有可用额度的模型"
                        elif "insufficient balance" in message.lower():
                            reason="模型账户额度不足，请更新可用服务配置"
                    except (ValueError,AttributeError):
                        pass
                    raise LLMUnavailable(f"HTTP {response.status_code}: {reason}")
                data=bytearray()
                for chunk in response.iter_content(chunk_size=1):
                    if time.monotonic()-started>50:
                        raise LLMUnavailable("模型响应超过 50 秒预算")
                    data.extend(chunk)
                    if len(data)>2*1024*1024:
                        raise LLMUnavailable("模型响应超过长度预算")
                result=json.loads(data)
        except LLMUnavailable:
            raise
        except (requests.RequestException,ValueError) as error:
            raise LLMUnavailable(f"{type(error).__name__}: 模型响应未完成") from error
        if result.get("error"):
            error=result["error"]
            message=str(error.get("message") or "未返回有效决策")[:180]
            raise LLMUnavailable(f"模型服务错误 {error.get('code','unknown')}: {message}")
        try:
            message,parts=completion_parts(result)
            require_finished(parts,tools=True,allow_legacy=not provider['base_url'].startswith('https://api.deepseek.com/'))
            calls=tool_decisions(message)
        except CompletionError as error:raise LLMUnavailable(str(error)) from error
        content=parts['content']
        if not content and not calls:
            raise LLMUnavailable("模型返回空 content 且无工具决策")
        output={"content":content,"tool_calls":calls,"model":result.get("model") or self.model,"usage":parts['usage'],"seconds":round(time.monotonic()-started,2),
                'finish_reason':parts['finish_reason'],'reasoning_present':parts['reasoning_present']}
        if definitions and provider['base_url'].startswith('https://api.deepseek.com/'):
            # Transient continuation only: the engine persists a metadata allowlist.
            output['_continuation']={'reasoning_content':message.get('reasoning_content') or ''}
        return output
