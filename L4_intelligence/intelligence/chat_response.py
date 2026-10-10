"""Read final answers independently of reasoning; never rescue from thinking."""
import json


class CompletionError(ValueError):
    pass


def usage_counts(raw):
    if not isinstance(raw, dict):
        return {}
    keys = ('prompt_tokens', 'completion_tokens', 'total_tokens',
            'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'reasoning_tokens')
    counts = {k: raw[k] for k in keys if type(raw.get(k)) is int and raw[k] >= 0}
    details = raw.get('completion_tokens_details')
    if isinstance(details, dict) and type(details.get('reasoning_tokens')) is int and details['reasoning_tokens'] >= 0:
        counts['reasoning_tokens'] = details['reasoning_tokens']
    return counts


def completion_parts(data):
    if not isinstance(data, dict) or data.get('error'):
        raise CompletionError('接口未返回有效 Chat Completion')
    choices = data.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise CompletionError('接口缺少 choices[0]')
    choice = choices[0]
    message = choice.get('message')
    if not isinstance(message, dict):
        raise CompletionError('接口缺少最终 message')
    content = message.get('content')
    if content is not None and not isinstance(content, str):
        raise CompletionError('最终 content 不是字符串')
    # Thinking has its own field. It is neither an answer nor a JSON fallback.
    reasoning_present = any(isinstance(message.get(k), str) and bool(message[k])
                            for k in ('reasoning_content', 'reasoning')) or bool(
                                isinstance(message.get('reasoning_details'), list) and message['reasoning_details'])
    return message, {'content': content or '', 'finish_reason': choice.get('finish_reason'),
                     'reasoning_present': reasoning_present, 'usage': usage_counts(data.get('usage'))}


def require_finished(parts, *, tools=False, allow_legacy=False):
    finish = parts['finish_reason']
    if finish == 'length':
        raise CompletionError('输出总预算耗尽（finish_reason=length）；未使用截断结果或推理内容')
    if finish == 'content_filter':
        raise CompletionError('模型未交付最终结果（content_filter）')
    allowed = ('stop', 'tool_calls') if tools else ('stop',)
    if finish not in allowed and not (finish is None and allow_legacy):
        raise CompletionError('模型响应未正常结束')


def tool_decisions(message):
    raw = message.get('tool_calls') or []
    if not isinstance(raw, list):
        raise CompletionError('tool_calls 格式错误')
    calls = []
    for call in raw:
        if not isinstance(call, dict) or call.get('type', 'function') != 'function':
            raise CompletionError('工具调用格式错误')
        fn = call.get('function') or {}
        try:
            arguments = json.loads(fn.get('arguments') or '{}')
        except (ValueError, TypeError) as error:
            raise CompletionError('工具参数不是完整 JSON') from error
        if not isinstance(arguments, dict) or not isinstance(fn.get('name'), str):
            raise CompletionError('工具参数需为 JSON 对象且包含工具名')
        calls.append({'id': call.get('id'), 'name': fn['name'], 'arguments': arguments})
    return calls


def assistant_history(response, tool_calls=None):
    """Only the transport consumes private continuation, never run records."""
    message = {'role': 'assistant', 'content': response.get('content') or ''}
    private = response.get('_continuation')
    if isinstance(private, dict) and isinstance(private.get('reasoning_content'), str):
        message['reasoning_content'] = private['reasoning_content']
    if tool_calls:
        message['tool_calls'] = tool_calls
    return message
