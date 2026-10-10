"""Editable provider profiles; credentials stay in environment, never SQLite."""
from __future__ import annotations

import copy
import ipaddress
import json
import re
from urllib.parse import urlsplit

from agent_v2.store import dump, stable_id
from L4_intelligence.intelligence.llm import provider_key

VERSION = 'provider-profile-v3.18'
SETTING = 'model_profiles_v18'
PROTOCOLS = ('openai_chat', 'openai_responses', 'anthropic_messages')
REASONING = {
    'omit': ('default',),
    'reasoning_effort': ('default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
    'reasoning_object': ('default', 'none', 'low', 'medium', 'high', 'xhigh', 'max'),
    'deepseek': ('default', 'none', 'low', 'high', 'max'),
    'thinking': ('default', 'none'),
    'enable_thinking': ('default', 'none', 'enabled'),
    'anthropic_effort': ('default', 'low', 'medium', 'high', 'max'),
}


def preset(pid, label, base, model, key, *, protocol='openai_chat', reasoning='omit',
           effort='default', output='json_object', token_field='max_tokens', **extra):
    return {'id': pid, 'label': label, 'base_url': base, 'model_id': model,
            'api_key_env': key, 'protocol': protocol, 'reasoning_mode': reasoning,
            'reasoning_effort': effort, 'output_mode': output, 'token_field': token_field,
            'output_limit': 16384, 'thinking_budget': 1024, 'budget_scope': 'combined',
            'auth_required': True, 'allow_http': False, 'reasoning_split': False, **extra}


# These are editable starting configurations, not an entitlement/model catalog.
PRESETS = [
    preset('openai', 'OpenAI', 'https://api.openai.com/v1', 'gpt-5-mini', 'OPENAI_API_KEY',
           reasoning='reasoning_effort', effort='low', token_field='max_completion_tokens'),
    preset('anthropic', 'Anthropic / Claude', 'https://api.anthropic.com/v1', 'claude-sonnet-4-6',
           'ANTHROPIC_API_KEY', protocol='anthropic_messages', reasoning='anthropic_effort',
           effort='low', output='prompt'),
    preset('gemini', 'Google / Gemini', 'https://generativelanguage.googleapis.com/v1beta/openai',
           'gemini-2.5-flash', 'GEMINI_API_KEY', reasoning='reasoning_effort', effort='low', output='prompt'),
    preset('xai', 'xAI / Grok', 'https://api.x.ai/v1', 'grok-4.7', 'XAI_API_KEY',
           protocol='openai_responses', output='prompt'),
    preset('deepseek', 'DeepSeek 官方', 'https://api.deepseek.com', 'deepseek-flash',
           'DEEPSEEK_API_KEY', reasoning='deepseek', effort='low'),
    preset('qwen', '阿里云百炼 / 千问', 'https://dashscope.aliyuncs.com/compatible-mode/v1',
           'qwen-plus', 'DASHSCOPE_API_KEY', reasoning='enable_thinking', effort='none'),
    preset('zhipu', '智谱 / GLM', 'https://open.bigmodel.cn/api/paas/v4', 'glm-4.7',
           'ZHIPU_API_KEY', reasoning='thinking'),
    preset('moonshot', 'Moonshot / Kimi', 'https://api.moonshot.cn/v1', 'kimi-k2.5',
           'MOONSHOT_API_KEY', reasoning='thinking', output='prompt'),
    preset('minimax', 'MiniMax', 'https://api.minimax.io/v1', 'MiniMax-M2.5',
           'MINIMAX_API_KEY', output='prompt', reasoning_split=True),
    preset('doubao', '火山方舟 / 豆包', 'https://ark.cn-beijing.volces.com/api/v3', '',
           'ARK_API_KEY', reasoning='thinking', output='prompt'),
    preset('siliconflow', '硅基流动', 'https://api.siliconflow.cn/v1', 'Qwen/Qwen3-8B',
           'SILICONFLOW_API_KEY', reasoning='enable_thinking', effort='none', budget_scope='separate'),
    preset('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', 'openrouter/free',
           'OPENROUTER_API_KEY', reasoning='reasoning_object'),
    preset('ollama', 'Ollama 本地', 'http://127.0.0.1:11434/v1', 'qwen3:8b', '',
           auth_required=False, allow_http=True),
]


def credential(profile):
    name = profile['api_key_env']
    value = provider_key({'env_keys': (name,)}) if name else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('模型配置必须是对象')
    allowed = set(PRESETS[0])
    if set(value) - allowed:
        raise ValueError('配置含未知字段；API Key 请放在环境变量中，配置只保存变量名')
    p = preset('custom', '自定义接口', '', '', '')
    p.update(copy.deepcopy(value))
    for key, maximum in (('id', 40), ('label', 80), ('model_id', 180), ('api_key_env', 100)):
        v = p[key]
        if not isinstance(v, str) or len(v.strip()) > maximum or any(ord(c) < 32 for c in v):
            raise ValueError('模型配置文字字段无效')
        p[key] = v.strip()
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', p['id']):
        raise ValueError('配置编号需为小写字母、数字、横线或下划线')
    if not p['label'] or not p['model_id'] or not re.fullmatch(r'[A-Za-z0-9_./:@+\-]+', p['model_id']):
        raise ValueError('需填写显示名称和供应商的实际模型 ID')
    if p['api_key_env'] and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', p['api_key_env']):
        raise ValueError('API Key 环境变量名无效')
    for key in ('auth_required', 'allow_http', 'reasoning_split'):
        if type(p[key]) is not bool:
            raise ValueError('配置开关需为布尔值')
    for key in ('protocol', 'reasoning_mode', 'reasoning_effort', 'output_mode', 'token_field', 'budget_scope'):
        if not isinstance(p[key], str):
            raise ValueError('接口与参数选项需为字符串')
    if p['auth_required'] and not p['api_key_env']:
        raise ValueError('需要凭证时请填写 API Key 环境变量名')
    if p['protocol'] not in PROTOCOLS or p['reasoning_mode'] not in REASONING:
        raise ValueError('接口协议或推理参数方式无效')
    if p['reasoning_effort'] not in REASONING[p['reasoning_mode']]:
        raise ValueError('所选推理方式不支持该强度')
    if (p['protocol'] == 'anthropic_messages') != (p['reasoning_mode'] == 'anthropic_effort') and p['reasoning_mode'] != 'omit':
        raise ValueError('推理参数方式与接口协议不兼容')
    if p['protocol']=='openai_responses' and p['reasoning_mode'] not in ('omit','reasoning_effort'):
        raise ValueError('Responses 接口请选择不发送或 reasoning_effort 推理参数')
    if p['output_mode'] not in ('prompt', 'json_object', 'json_schema'):
        raise ValueError('输出方式无效')
    if p['protocol'] == 'anthropic_messages' and p['output_mode'] == 'json_object':
        raise ValueError('Claude 请选择提示词 JSON 或原生 JSON Schema')
    if p['token_field'] not in ('max_tokens', 'max_completion_tokens') or p['budget_scope'] not in ('combined', 'separate'):
        raise ValueError('输出预算方式无效')
    if p['budget_scope'] == 'separate' and p['reasoning_mode'] != 'enable_thinking':
        raise ValueError('分开预算需要 enable_thinking 参数方式')
    for key, low, high in (('output_limit', 256, 32768), ('thinking_budget', 128, 32768)):
        if type(p[key]) is not int or not low <= p[key] <= high:
            raise ValueError('输出或思考 token 预算超出范围')
    if not isinstance(p['base_url'], str) or len(p['base_url']) > 400 or any(c.isspace() for c in p['base_url']):
        raise ValueError('接口地址无效')
    base = p['base_url'].rstrip('/')
    suffix = {'anthropic_messages':'/messages','openai_responses':'/responses','openai_chat':'/chat/completions'}[p['protocol']]
    if base.endswith(suffix):
        base = base[:-len(suffix)]
    try:
        u = urlsplit(base)
        _ = u.port
    except ValueError as error:
        raise ValueError('接口地址无效') from error
    if not u.hostname or u.scheme not in ('https', 'http') or u.username or u.password or u.query or u.fragment:
        raise ValueError('接口地址需为 HTTP(S) 基础地址，不能包含凭证、查询参数或片段')
    if u.scheme == 'http' and not p['allow_http']:
        raise ValueError('HTTP 接口需明确允许；公网接口建议使用 HTTPS')
    try:
        address = ipaddress.ip_address(u.hostname)
    except ValueError:
        address = None
    if address and (address.is_link_local or address.is_multicast or address.is_unspecified):
        raise ValueError('该地址不适合作为模型服务')
    p['base_url'] = base
    return p


def profiles(store):
    row = store.conn.execute('SELECT value FROM settings WHERE key=?', (SETTING,)).fetchone()
    return json.loads(row[0]) if row else {}


def get(store, selected):
    if not isinstance(selected, str) or not selected.startswith('profile/'):
        return None
    p = profiles(store).get(selected[8:])
    if not p:
        raise ValueError('模型配置不存在')
    return validate(p)


def fingerprint(profile):
    return stable_id('profile_version_', dump([VERSION, profile]))


def status(profile):
    return {'model': 'profile/' + profile['id'], 'label': profile['label'] + ' · ' + profile['model_id'],
            'configured': not profile['auth_required'] or bool(credential(profile)),
            'transport': profile['protocol'], 'fallback_model': None, 'profile_id': profile['id'],
            'profile_version': fingerprint(profile), 'reasoning_options': list(REASONING[profile['reasoning_mode']]),
            'note': '配置存在不代表模型可用；保存不调用模型，不自动切换供应商。'}


def listing(store):
    from .model import model_status, model_options, gate
    saved = profiles(store)
    return {'version': VERSION, 'presets': copy.deepcopy(PRESETS),
            'profiles': [{**status(p), **p} for p in saved.values()],
            'reasoning_modes': {k: list(v) for k, v in REASONING.items()}, 'protocols': list(PROTOCOLS),
            'selected': store.model_setting(), 'model': model_status(store.model_setting(),store),
            'model_options': model_options(store), 'model_gate': gate(store), 'active_run': store.active_owner()}


def save(store, value):
    p = validate(value)
    # Check and write under one lock so an automatic worker cannot claim between.
    store.conn.execute('BEGIN IMMEDIATE')
    try:
        if store.active_owner():
            raise ValueError('当前自动运行结束后再修改模型配置')
        rows = profiles(store)
        if p['id'] not in rows and len(rows) >= 30:
            raise ValueError('最多保存30份模型配置')
        rows[p['id']] = p
        store.conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (SETTING, dump(rows)))
        store.conn.commit()
    except Exception:
        store.conn.rollback()
        raise
    return {**status(p), **p}


def scope(profile):
    # Editing a preset must not bypass an existing hold on the same Flash API.
    host = urlsplit(profile['base_url']).hostname
    if host == 'api.deepseek.com' and profile['model_id'] in ('deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4.1-flash'):
        return 'deepseek:flash'
    return stable_id('provider_', dump([profile['protocol'], profile['base_url'],
                                      profile['model_id'], profile['api_key_env']]))
