"""Explicit Go free-model profiles; never select a paid replacement."""
from __future__ import annotations

from .providers import preset, validate
import json
import time

from L4_intelligence.intelligence.llm import LLMUnavailable
from L4_intelligence.intelligence.chat_response import usage_counts

BASE_URL = 'https://opencode.ai/zen/go/v1'
MODELS = ('step-5-preview-free', 'longcat-2.5-preview-free')
PRICING = {'currency': 'CNY', 'input_per_million': 0, 'output_per_million': 0,
           'source': 'https://opencode.ai/docs/go/', 'checked_at': '2026-10-10',
           'basis': 'limited_time_free_marginal_tokens_subscription_excluded'}


def profile(model, output_limit=8192):
    if model not in MODELS:
        raise ValueError('只授权 Step 5 / LongCat 2.5 Preview Free；不能切换付费模型')
    return validate(preset(
        'go-step5' if model == MODELS[0] else 'go-longcat',
        'OpenCode Go / ' + model, BASE_URL, model, 'OPENCODE_GO_API_KEY',
        reasoning='reasoning_effort' if model == MODELS[0] else 'thinking',
        effort='high' if model == MODELS[0] else 'none', output_limit=output_limit))


def headers(session):
    # These identify our application honestly, never impersonate OpenCode.
    return {'User-Agent': 'taptap-intel-agent/3.20', 'x-opencode-session': session}


def read_stream(response, started, timeout_seconds):
    """Bound SSE bytes; retain final content only, discard every thinking delta."""
    buffer = bytearray()
    size = 0
    content, reasoning, done, model, request_id, finish = [], False, False, None, None, None
    usage, refusal, tools = {}, False, False
    for chunk in response.iter_content(chunk_size=256):
        if time.monotonic() - started > timeout_seconds:
            raise LLMUnavailable('Go流式响应超过任务时间预算')
        size += len(chunk)
        if size > 2000000:
            raise LLMUnavailable('Go流式响应超过大小预算')
        buffer.extend(chunk)
        while b'\n' in buffer:
            line, _, rest = buffer.partition(b'\n')
            buffer = bytearray(rest)
            if not line.startswith(b'data:'):
                continue
            raw = bytes(line[5:]).strip()
            if raw == b'[DONE]':
                done = True
                break
            event = json.loads(raw)
            if not isinstance(event, dict) or event.get('error'):
                raise LLMUnavailable('Go流式接口未完成交付')
            if event.get('model'):
                if model is not None and model != event['model']:
                    raise LLMUnavailable('Go流式响应中模型发生变化')
                model = event['model']
            if event.get('id'):
                request_id = event['id']
            if isinstance(event.get('usage'), dict):
                usage = usage_counts(event['usage'])
                cost = event['usage'].get('cost')
                if type(cost) in (int,float):usage['cost'] = cost
            choices = event.get('choices', [])
            if not isinstance(choices,list):raise ValueError('Go流式choices无效')
            for choice in choices:
                if not isinstance(choice,dict) or choice.get('index',0) != 0:
                    raise ValueError('Go流式回答分支无效')
                delta = choice.get('delta') or {}
                if not isinstance(delta,dict):raise ValueError('Go流式delta无效')
                if delta.get('content') is not None:
                    if not isinstance(delta['content'],str):raise ValueError('Go流式最终文本无效')
                    content.append(delta['content'])
                reasoning |= any(bool(delta.get(k)) for k in ('reasoning_content','reasoning','reasoning_details'))
                refusal |= bool(delta.get('refusal'))
                tools |= bool(delta.get('tool_calls'))
                if choice.get('finish_reason') is not None:
                    finish = choice['finish_reason']
        if done:break
    if not done:
        raise LLMUnavailable('Go流式响应中断，未收到完整结束帧')
    message = {'content': ''.join(content)}
    if refusal:message['refusal'] = True
    if tools:message['tool_calls'] = [True]
    return {'id':request_id,'model':model,'usage':usage,
            'choices':[{'message':message,'finish_reason':finish}]}, reasoning
