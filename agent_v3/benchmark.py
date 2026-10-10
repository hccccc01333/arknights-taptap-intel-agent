"""Isolated, budgeted A/B/C experiment. No scheduler changes or LLM scoring."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import re
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone, timedelta
from contextlib import ExitStack
from unittest.mock import patch

from agent_v2.store import dump, now_iso

VERSION = 'agent-benchmark-v1'
ARMS = ('A', 'B', 'C')
METRICS = ('facts', 'dates', 'citations', 'unsupported_assertions', 'risk_refusal')
VERDICTS = ('pass', 'fail', 'unverified', 'not_applicable')
# Upper peak RMB rates, no cache discounts. Verify again before authorizing.
PRICING = {'currency': 'CNY', 'input_per_million': 2, 'output_per_million': 8,
           'source': 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
           'checked_at': '2026-10-10', 'basis': 'peak_no_cache_discount'}
BASE_SYSTEM = '''你为TapTap整理热点解读、游戏情报、可编辑素材和增长创意。
请说明主体、发生了什么、事件时间、实际可查依据、尚未确认的事情。
观测日期不是事件日期；搜索摘要不是完整正文；少数评论不代表总体玩家态度。
负面、争议或关键依据不足时不生成营销创意。允许没有情报、素材或创意。
已有事实、有限样本和影响假设分开；不要编造热度、来源、用户态度或产品能力。
所有内容包括创意中的事实都需要核查，不确定就说明。仅交付最终JSON。'''


def digest(value):
    return hashlib.sha256(dump(value).encode('utf-8')).hexdigest()


def code_inventory():
    base = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
                                    cwd=base, text=True, encoding='utf-8').splitlines()
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest()
            for name in sorted(set(names)) if Path(name).suffix in ('.py', '.cjs') and (base / name).is_file()}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, value):
    """Append-only artifacts: never replace an earlier experiment or review."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def study_path(root):
    root = Path(root).resolve()
    # Experimental databases must never replace a production runtime.
    if root.name in ('v2', 'v3') or root == Path(__file__).resolve().parents[1]:
        raise ValueError('请使用独立的实验子目录')
    return root


def report_schema():
    from .task_packets import object_schema
    text = {'type': 'string', 'maxLength': 3000}
    citation = object_schema({'url': text, 'quote': text})
    claim = object_schema({'text': text, 'kind': {'enum': ['fact', 'date', 'heat', 'user_view', 'product']},
                          'citations': {'type': 'array', 'items': citation, 'maxItems': 6}})
    strings = {'type': 'array', 'maxItems': 12, 'items': text}
    return object_schema({'headline': text, 'one_line': text,
                          'claims': {'type': 'array', 'items': claim, 'maxItems': 24},
                          'unknowns': strings, 'decision': {'enum': ['create', 'watch', 'archive']},
                          'decision_reason': text, 'intelligence': strings, 'materials': strings,
                          'creative': {'type': ['string', 'null'], 'maxLength': 6000}})


def prepare_pool(production_db, root, limit=160):
    """Read-only neutral candidates; neither ranking nor AI labels certify truth."""
    root = study_path(root)
    conn = sqlite3.connect(Path(production_db).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        rows = conn.execute('''SELECT e.*,o.channel_id,o.observed_at,o.position rank,o.metrics channel_metrics
          FROM channel_observation o JOIN evidence e USING(evidence_id)
          WHERE o.observed_at>=? ORDER BY o.observed_at DESC, o.position ASC LIMIT ?''',
                            (cutoff, min(10000, max(limit, limit * 20)))).fetchall()
        pool, seen = [], set()
        for row in rows:
            key = re.sub(r'\s+', '', row['title']).casefold()
            if key in seen:
                continue
            seen.add(key)
            seed = {k: row[k] for k in ('title', 'url', 'platform', 'kind', 'published_at',
                                       'channel_id', 'observed_at', 'rank')}
            # Same minimal starting excerpt is given to all three arms.
            seed['body'] = row['body'][:450]
            seed['metrics'] = json.loads(row['channel_metrics'] or '{}')
            pool.append({'case_id': 'case-' + digest(seed)[:12], 'seed': seed})
            if len(pool) >= limit:
                break
        value = {'version': VERSION, 'created_at': now_iso(), 'candidates': pool,
                 'warning': '候选不是已核实热点；日期、重复事件与入选类别需在模型运行前核查。'}
        write_new(root / 'pool.json', value)
        return {'candidates': len(pool), 'path': str(root / 'pool.json')}
    finally:
        conn.close()


def freeze(root, selected, *, phase='formal', order_seed=20261010):
    root = study_path(root)
    selection = read(selected)
    wanted = selection['cases']
    expected = 10 if phase == 'formal' else 2
    if len(wanted) != expected or len({c['case_id'] for c in wanted}) != expected:
        raise ValueError(f'{phase} 必须预先选择 {expected} 个不同事件')
    pool = {c['case_id']: c for c in read(root / 'pool.json')['candidates']}
    cases, gold = [], {}
    for c in wanted:
        if c['case_id'] not in pool:
            raise ValueError('入选事件不在候选快照中')
        if not c.get('selection_reason') or not c.get('category'):
            raise ValueError('运行前填写类别和选择原因；不能按结果选样本')
        facts = c.get('reference_facts', [])
        if not facts or any(not isinstance(f, dict) or not f.get('text') or not f.get('quote')
                            or not str(f.get('url', '')).startswith('https://') for f in facts):
            raise ValueError('预先填写必要事实及支持它的原文和来源；核验基准不交给模型')
        gold[c['case_id']] = copy.deepcopy(facts)
        cases.append({**copy.deepcopy(pool[c['case_id']]),
                      'category': c['category'], 'selection_reason': c['selection_reason']})
    if phase == 'formal':
        categories = {c['category'] for c in cases}
        if not {'new_event', 'old_or_revival', 'negative_or_disputed', 'incomplete'} <= categories:
            raise ValueError('正式样本必须覆盖新事件、旧闻/翻红、负面/争议、信息不完整')
        pilot = root / 'pilot.json'
        if pilot.exists() and {c['case_id'] for c in read(pilot)['cases']} & {c['case_id'] for c in cases}:
            raise ValueError('校准样本与正式样本须分开')
    trials, rng = [], random.Random(order_seed)
    for c in cases:
        arms = list(ARMS)
        rng.shuffle(arms)
        trials += [{'trial_id': f"{c['case_id']}-{arm}", 'case_id': c['case_id'], 'arm': arm}
                   for arm in arms]
    from . import __version__
    from .contracts import VERSION as contract_version
    from .taptap_profile import PROFILE
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        diff = subprocess.check_output(['git', 'diff', '--binary'])
        code_dirty_hash = hashlib.sha256(diff).hexdigest()
    except (OSError, subprocess.CalledProcessError):
        commit, code_dirty_hash = None, None
    value = {'version': VERSION, 'phase': phase, 'cases': cases, 'trials': trials,
             'order_seed': order_seed, 'frozen_at': now_iso(), 'project_version': __version__,
             'git_commit': commit, 'tracked_diff_sha256': code_dirty_hash,
             'contract_version': contract_version, 'business_profile': copy.deepcopy(PROFILE),
             'code_inventory': code_inventory(),
             'gold_hash': digest(gold),
             'research_units_per_trial': 6, 'model_calls_per_trial': 16,
             'repetitions': 1, 'pricing': PRICING}
    value['manifest_hash'] = digest(value)
    write_new(root / f'{phase}-gold.json', gold)
    write_new(root / f'{phase}.json', value)
    return {'manifest_hash': value['manifest_hash'], 'trials': len(trials)}


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    """Reserve before transport; crashes keep the full conservative reservation."""
    def __init__(self, root):
        self.conn = sqlite3.connect(study_path(root) / 'budget.sqlite3', timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
          CREATE TABLE IF NOT EXISTS authorization(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS calls(call_id TEXT PRIMARY KEY,trial_id TEXT,phase TEXT,stage TEXT,
            status TEXT,reserved INTEGER,charged INTEGER,usage TEXT,actual_model TEXT,seconds REAL);
          CREATE TABLE IF NOT EXISTS trials(trial_id TEXT,phase TEXT,status TEXT,PRIMARY KEY(trial_id,phase));
        ''')

    def close(self):
        self.conn.close()

    def authorize(self, cap_yuan, pilot_yuan, note):
        if not note or not 0 < pilot_yuan <= cap_yuan <= 100 or not all(
                math.isfinite(n) for n in (cap_yuan, pilot_yuan)):
            raise ValueError('需要明确授权说明及有限的人民币预算')
        value = {'cap_microyuan': round(cap_yuan * 1000000),
                 'pilot_microyuan': round(pilot_yuan * 1000000),
                 'at': now_iso(), 'note': note, 'pricing': PRICING,
                 'model': 'deepseek-flash', 'reasoning_effort': 'high', 'output_limit': 8192}
        with self.conn:
            self.conn.execute('INSERT INTO authorization VALUES(1,?)', (dump(value),))
        return value

    def authorization(self):
        row = self.conn.execute('SELECT payload FROM authorization WHERE id=1').fetchone()
        if not row:
            raise BudgetExceeded('尚未记录充值完成和实验费用授权')
        return json.loads(row[0])

    def start_trial(self, trial_id, phase):
        # A crashed or failed trial is never silently rerun to improve a score.
        with self.conn:
            self.conn.execute('INSERT INTO trials VALUES(?,?,?)', (trial_id, phase, 'running'))

    def reserve(self, trial_id, phase, stage, input_bound, output_bound):
        auth = self.authorization()
        reserved = input_bound * 2 + output_bound * 8  # integer microyuan
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            rows = self.conn.execute('SELECT phase,COALESCE(charged,reserved) charge FROM calls').fetchall()
            spent = sum(r['charge'] for r in rows)
            pilot = sum(r['charge'] for r in rows if r['phase'] == 'pilot')
            count = self.conn.execute('SELECT COUNT(*) FROM calls WHERE trial_id=? AND phase=?',
                                      (trial_id, phase)).fetchone()[0]
            if spent + reserved > auth['cap_microyuan'] or (
                    phase == 'pilot' and pilot + reserved > auth['pilot_microyuan']) or count >= 16:
                raise BudgetExceeded('费用或单组调用上限已到，停止实验并保留未完成记录')
            call_id = 'call-' + uuid.uuid4().hex
            self.conn.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?)',
                              (call_id, trial_id, phase, stage, 'reserved', reserved, None, None, None, None))
            self.conn.commit()
            return call_id
        except BaseException:
            self.conn.rollback()
            raise

    def settle(self, call_id, response, seconds, status):
        usage = response.get('usage') or {}
        valid = all(type(usage.get(k)) is int and usage[k] >= 0
                    for k in ('prompt_tokens', 'completion_tokens'))
        charged = usage['prompt_tokens'] * 2 + usage['completion_tokens'] * 8 if valid else None
        # Reasoning tokens are already included in completion_tokens, never add twice.
        with self.conn:
            self.conn.execute('''UPDATE calls SET status=?,charged=?,usage=?,actual_model=?,seconds=?
              WHERE call_id=? AND status='reserved' ''',
                              (status if valid else 'usage_unknown', charged, dump(usage),
                               response.get('api_model'), seconds, call_id))
        if valid:
            row = self.conn.execute('SELECT reserved FROM calls WHERE call_id=?', (call_id,)).fetchone()
            if charged > row['reserved']:
                raise BudgetExceeded('供应商报告用量超过预留上界，停止并核查计费')

    def summary(self):
        rows = [dict(r) for r in self.conn.execute('SELECT * FROM calls ORDER BY rowid')]
        return {'calls': len(rows), 'estimated_peak_cost_yuan': sum(r['charged'] or 0 for r in rows) / 1000000,
                'reserved_unknown_yuan': sum(r['reserved'] for r in rows if r['charged'] is None) / 1000000,
                'cost_basis': '高峰价估算；非账户实际扣费，未知用量保守占用预算', 'records': rows}


class RecordedModel:
    supports_tasks = supports_main_agent = compact_tasks = True
    last_session = None

    def __init__(self, profile, ledger, trial, phase, directory, client_factory=None):
        from .provider_adapter import ProfileModel
        if (profile['base_url'] != 'https://api.deepseek.com' or profile['model_id'] != 'deepseek-flash'
                or profile['protocol'] != 'openai_chat' or profile['budget_scope'] != 'combined'
                or profile['reasoning_effort'] != 'high' or profile['output_limit'] != 8192):
            raise ValueError('当前费用上界只批准官方Flash文本接口、high与8192输出；其他接口需重新定价授权')
        self.profile, self.ledger, self.trial, self.phase = profile, ledger, trial, phase
        self.directory = Path(directory)
        self.current = None
        self.model = 'profile/' + profile['id']
        self.transport = profile['protocol']
        self.stopped = False
        self.client = (client_factory or ProfileModel)(profile, final_observer=self.observe)

    def observe(self, content, metadata):
        write_new(self.directory / (self.current + '.final.json'),
                  {'final_content': content, 'metadata': metadata})

    def run_task(self, stage, packet, schema, system, *, timeout_seconds=180):
        from .provider_adapter import request_body
        from .opencode_zen import StructuredDeliveryError
        if self.stopped:
            raise BudgetExceeded('此运行已停止，不能通过业务重试继续花费')
        body, limit = request_body(self.profile, stage, packet, schema, system)
        # UTF-8 byte count plus framing allowance is a deliberately loose input bound.
        # Authorized adapter is text-only DeepSeek BPE, not multimodal or a gateway.
        input_bound = len(dump(body).encode('utf-8')) + 4096
        try:
            call_id = self.ledger.reserve(self.trial, self.phase, stage, input_bound, limit)
        except BudgetExceeded:
            self.stopped = True
            raise
        self.current = call_id
        write_new(self.directory / (call_id + '.input.json'),
                  {'stage': stage, 'packet': packet, 'schema': schema, 'system': system,
                   'request_fingerprint': digest(body), 'output_limit': limit})
        started = time.monotonic()
        response = {}
        status = 'failed'
        try:
            response = self.client.run_task(stage, packet, schema, system, timeout_seconds=timeout_seconds)
            status = 'returned'
            return response
        except StructuredDeliveryError as error:
            response, status = error.response, 'invalid_final'
            raise
        finally:
            elapsed = round(time.monotonic() - started, 3)
            write_new(self.directory / (call_id + '.delivery.json'),
                      {'status': status, 'response': response, 'seconds': elapsed})
            self.ledger.settle(call_id, response, elapsed, status)
            if not all(type((response.get('usage') or {}).get(k)) is int and response['usage'][k] >= 0
                       for k in ('prompt_tokens', 'completion_tokens')):
                self.stopped = True
            actual = response.get('api_model')
            if actual:
                models = {r[0] for r in self.ledger.conn.execute(
                    'SELECT DISTINCT actual_model FROM calls WHERE actual_model IS NOT NULL')}
                if len(models) > 1 or actual.lower() not in ('deepseek-flash', 'deepseek-v4.1-flash'):
                    self.stopped = True
                    raise BudgetExceeded('实际模型版本变化，停止跨版本混合实验')


class PublicArchive:
    """Public HTTP snapshots shared within an event; never wraps model HTTP."""
    def __init__(self, root, trial_directory):
        self.root, self.directory = Path(root), Path(trial_directory)
        self.stack = ExitStack()

    def __enter__(self):
        from . import public_sources, web_research, browser_tools
        original = public_sources.read_public

        def archived(url, hosts, **kwargs):
            from urllib.parse import urlsplit, parse_qsl
            parsed = urlsplit(url)
            if parsed.username or parsed.password or any(k.lower() in
                    ('api_key', 'apikey', 'access_token', 'token', 'key') for k, _ in parse_qsl(parsed.query)):
                raise ValueError('来源快照不接受含凭证的URL')
            signature = digest({'url': url, 'hosts': sorted(hosts), 'options': kwargs})
            path = self.root / (signature + '.json')
            cached, started = path.exists(), time.monotonic()
            audit = {'url': url, 'at': now_iso(), 'cached': cached, 'response_hash': signature}
            try:
                if cached:
                    value = read(path)
                    raw, resolved = base64.b64decode(value['body_base64']), value['resolved_url']
                    if hashlib.sha256(raw).hexdigest() != value['body_sha256']:
                        raise ValueError('已存来源快照内容不匹配')
                else:
                    raw, resolved = original(url, hosts, **kwargs)
                    write_new(path, {'requested_url': url, 'resolved_url': resolved, 'at': now_iso(),
                              'body_sha256': hashlib.sha256(raw).hexdigest(),
                              'body_base64': base64.b64encode(raw).decode('ascii')})
                audit['status'] = 'ok'
                return raw, resolved
            except Exception as error:
                audit.update(status='failed', error_type=type(error).__name__)
                raise
            finally:
                audit['seconds'] = round(time.monotonic() - started, 3)
                write_new(self.directory / 'public_calls' / (uuid.uuid4().hex + '.json'), audit)

        self.stack.enter_context(patch.object(public_sources, 'read_public', archived))
        self.stack.enter_context(patch.object(web_research, 'read_public', archived))
        self.stack.enter_context(patch.object(browser_tools, 'ARTIFACT_ROOT', self.directory / 'captures'))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


def seed_store(store, seed):
    from agent_v2.ingest import normalize
    from .discovery import scan
    item = normalize({**seed, 'description': seed['body'], 'observed_at': seed['observed_at']},
                     seed['platform'], 'v3:benchmark:seed')
    if not item:
        raise ValueError('初始线索无效')
    item['kind'] = seed['kind']
    with store.conn:
        store.upsert_evidence(item)
        store.conn.execute('INSERT INTO channel_observation VALUES(?,?,?,?,?)',
                           (seed['channel_id'], item['evidence_id'], seed['observed_at'], seed['rank'], dump(seed['metrics'])))
    scan(store)
    row = store.conn.execute('SELECT topic_id FROM topic_member WHERE evidence_id=? AND active=1',
                             (item['evidence_id'],)).fetchone()
    return row[0] if row else None


def source_snapshot(store):
    result = []
    for row in store.conn.execute('SELECT * FROM evidence ORDER BY evidence_id'):
        value = dict(row)
        value['sha256'] = digest(value)
        result.append(value)
    return result


def baseline(store, run_id, model, arm, seed, topic_id):
    from .taptap_profile import PROMPT, PROFILE
    from .tool_executor import ToolExecutor
    sources = []
    if arm == 'B':
        executor = ToolExecutor(store, run_id, limit=6)
        if topic_id:
            executor.execute('search_web', {'topic_id': topic_id, 'evidence_id': store.conn.execute(
                'SELECT evidence_id FROM topic_member WHERE topic_id=?', (topic_id,)).fetchone()[0],
                'query': seed['title'][:80]})
        else:
            # B still researches a seed rejected by C's discovery/date rules.
            # Keep the same executor and unit cap, without importing C's guards.
            def retrieve(units):
                from .web_research import search, article
                calls = 1
                for row in search(seed['title'][:80])[:6]:
                    if calls < units:
                        calls += 1
                        try:
                            row['description'] = article(row['url'])['body']
                        except Exception:
                            pass
                    sources.append(row)
                return {'calls': calls, 'status': 'ok', 'count': len(sources)}
            executor.invoke('baseline_search', {'query': seed['title'][:80]}, retrieve, maximum=3)
        sources.extend({k: r[k] for k in ('title', 'body', 'url', 'published_at')} for r in source_snapshot(store))
    packet = {'initial_information': seed, 'business_profile': PROFILE,
              'business_context': store.context(), 'as_of': now_iso(), 'retrieved_sources': sources}
    # A/B use adapter syntax checks, no production date/reference/risk guards.
    response = model.run_task('baseline', packet, report_schema(), BASE_SYSTEM + PROMPT)
    return response['result']


def project_full(store, topic_id, outcome):
    """Deterministic display projection, no extra model that could repair C."""
    row = store.conn.execute('SELECT fingerprint FROM topic WHERE topic_id=?', (topic_id,)).fetchone() if topic_id else None
    interpretation = store.interpretation(topic_id, row[0]) if row else None
    intelligence = store.intelligence(topic_id) if topic_id else None
    p = interpretation['payload'] if interpretation else {}
    b = intelligence['payload'] if intelligence else {}
    decision_row = store.conn.execute('SELECT payload FROM main_decision WHERE topic_id=? ORDER BY created_at DESC LIMIT 1',
                                      (topic_id,)).fetchone() if topic_id else None
    screening = json.loads(decision_row[0]) if decision_row else {}
    lookup = {r['evidence_id']: r for r in source_snapshot(store)}
    claims = []
    for section in ('background', 'core', 'timeline', 'views', 'controversies'):
        for item in p.get(section, []):
            claims.append({'text': item['text'] + (('；时间：' + item.get('time_text', '')) if section == 'timeline' else ''),
                           'kind': 'date' if section == 'timeline' else 'user_view' if section == 'views' else 'fact',
                           'citations': [{'url': lookup.get(f['evidence_id'], {}).get('url'), 'quote': f['quote']}
                                         for f in item.get('facts', [])]})
    recency = p.get('recency') or {}
    if recency.get('date_iso'):
        claims.append({'text': '事件时间判断：' + recency['date_iso'] + '；依据类型：' + recency.get('kind', 'unknown'),
                       'kind': 'date', 'citations': [{'url': lookup.get(f['evidence_id'], {}).get('url'), 'quote': f['quote']}
                                                   for f in recency.get('facts', [])]})
    creatives = store.creative_feed() if topic_id else []
    decision = b.get('opportunity', {}).get('decision', screening.get('action', 'watch'))
    return {'headline': p.get('headline', ''), 'one_line': p.get('one_line', ''), 'claims': claims,
            'unknowns': list(dict.fromkeys(p.get('unknowns', []) + b.get('unknowns', []))),
            'decision': 'create' if creatives else 'archive' if decision == 'archive' else 'watch',
            'decision_reason': b.get('opportunity', {}).get('reason', screening.get('reason', outcome.get('reason', outcome.get('status', '')))),
            'intelligence': [dump({**{k: s['payload'].get(k) for k in
                ('title', 'observed_change', 'game_context', 'why_it_matters', 'hypothesis', 'next_watch')},
                'citations': [{'url': lookup.get(f['evidence_id'], {}).get('url'), 'quote': f['quote']}
                              for f in s['payload'].get('facts', [])]})
                for s in store.game_signals()],
            'materials': [dump({'content': s['content'], 'application': s.get('application'),
                                'rights_status': s.get('rights_status')}) for s in store.usable_materials()],
            'creative': '\n'.join(dump({**{k: s['payload'].get(k) for k in
                ('title', 'audience', 'placement', 'user_action', 'steps', 'timing', 'risks', 'measurement',
                 'growth_goal', 'hook', 'distribution', 'journey', 'copy', 'prerequisites', 'growth_hypothesis', 'validation_plan')},
                'deliverables': [{k: d.get(k) for k in ('kind', 'content', 'rights_status')}
                                 for d in s['payload'].get('deliverables', [])]})
                for s in creatives) if creatives else None,
            'production_outcome': outcome}


def run_phase(root, phase):
    from .store import Store
    from .providers import PRESETS
    from .pipeline import process
    root = study_path(root)
    manifest = read(root / f'{phase}.json')
    value = {k: v for k, v in manifest.items() if k != 'manifest_hash'}
    if digest(value) != manifest['manifest_hash']:
        raise ValueError('样本或协议在冻结后被修改')
    if manifest['code_inventory'] != code_inventory():
        raise ValueError('实验代码在冻结后发生变化，请建立新批次')
    if datetime.now(timezone.utc) - datetime.fromisoformat(manifest['frozen_at']) > timedelta(hours=24):
        raise ValueError('冻结超过24小时，请建立新批次；不能把过期样本当实时实验')
    ledger = Ledger(root)
    try:
        auth = ledger.authorization()
        profile = next(copy.deepcopy(p) for p in PRESETS if p['id'] == 'deepseek')
        profile.update(reasoning_effort=auth['reasoning_effort'], output_limit=auth['output_limit'])
        cases = {c['case_id']: c for c in manifest['cases']}
        for trial in manifest['trials']:
            previous = ledger.conn.execute('SELECT status FROM trials WHERE trial_id=? AND phase=?',
                                           (trial['trial_id'], phase)).fetchone()
            if previous:
                continue
            trial_id, arm = trial['trial_id'], trial['arm']
            directory = root / phase / trial_id
            directory.mkdir(parents=True, exist_ok=False)
            ledger.start_trial(trial_id, phase)
            store = Store(directory / 'runtime.sqlite3')
            outcome, report, status, model = {}, None, 'failed', None
            run_id = None
            started = time.monotonic()
            try:
                seed = cases[trial['case_id']]['seed']
                tid = seed_store(store, seed)
                run_id = store.create_run('隔离对照实验；不影响正式自动化')
                model = RecordedModel(profile, ledger, trial_id, phase, directory)
                with PublicArchive(root / 'public_snapshots' / trial['case_id'], directory):
                    if arm == 'C':
                        outcome = process(store, run_id, topic_id=tid, model=model) if tid else {
                            'status': 'seed_not_eligible', 'reason': '真实发现时效规则未接受该线索'}
                        report = project_full(store, tid, outcome)
                        status = 'returned' if outcome.get('status') in ('completed', 'intelligence_ready', 'no_selected_work', 'no_pending_work', 'seed_not_eligible') else 'partial'
                    else:
                        report = baseline(store, run_id, model, arm, seed, tid)
                        status = 'returned'
            except Exception as error:
                outcome = {'error_type': type(error).__name__}
                if model and model.stopped:
                    status = 'budget_stopped'
            finally:
                if model and model.stopped:
                    status = 'budget_stopped'
                write_new(directory / 'sources.json', source_snapshot(store))
                if run_id:
                    write_new(directory / 'execution.json', store.get_run(run_id))
                write_new(directory / 'result.json', {'trial_id': trial_id, 'case_id': trial['case_id'],
                          'arm': arm, 'status': status, 'report': report, 'outcome': outcome,
                          'seconds': round(time.monotonic() - started, 3),
                          'manifest_hash': manifest['manifest_hash']})
                with ledger.conn:
                    ledger.conn.execute('UPDATE trials SET status=? WHERE trial_id=? AND phase=?',
                                        (status, trial_id, phase))
                store.close()
            print(dump({'trial_id': trial_id, 'status': status, 'budget': {k: v for k, v in ledger.summary().items() if k != 'records'}}), flush=True)
            if model and model.stopped:
                break
        return ledger.summary()
    finally:
        ledger.close()


def review_export(root, phase='formal'):
    root = study_path(root)
    manifest = read(root / f'{phase}.json')
    gold = read(root / f'{phase}-gold.json')
    if digest(gold) != manifest['gold_hash']:
        raise ValueError('预先核验基准发生变化')
    results = []
    for trial in manifest['trials']:
        path = root / phase / trial['trial_id'] / 'result.json'
        results.append(read(path) if path.exists() else {**trial, 'status': 'not_run', 'report': None})
    random.SystemRandom().shuffle(results)
    token = uuid.uuid4().hex[:12]
    mapping, items = {}, []
    for index, result in enumerate(results, 1):
        review_id = f'review-{token}-{index:02d}'
        mapping[review_id] = {'trial_id': result['trial_id'], 'case_id': result['case_id'], 'arm': result['arm']}
        # Do not expose internal tool names, cost, schema flags or group identity.
        report = result.get('report')
        if report:
            report = {k: report.get(k) for k in report_schema()['properties']}
        items.append({'review_id': review_id, 'case_id': result['case_id'], 'report': report,
                      'report_hash': digest(report),
                      'delivery_present': report is not None,
                      'checks': {k: {'verdict': 'unverified', 'notes': '', 'supporting_urls': []} for k in METRICS},
                      'claims_reviewed': 0, 'claims_passed': 0, 'claims_failed': 0, 'claims_unverified': 0,
                      'required_facts_covered': 0, 'required_facts_total': len(gold[result['case_id']]),
                      'reference_facts': gold[result['case_id']]})
    write_new(root / f'{phase}-mapping-{token}.json', mapping)
    path = root / f'{phase}-review-{token}.json'
    write_new(path, {'version': VERSION, 'manifest_hash': manifest['manifest_hash'], 'items': items,
                     'warning': '部分盲化；开发者可能从文风推测架构。来源支持性仍需逐条人工查证。'})
    return {'review': str(path), 'mapping': str(root / f'{phase}-mapping-{token}.json')}


def summarize(root, review_path, mapping_path, phase='formal'):
    root = study_path(root)
    review, mapping, manifest = read(review_path), read(mapping_path), read(root / f'{phase}.json')
    if review['manifest_hash'] != manifest['manifest_hash']:
        raise ValueError('核验表与冻结批次不同')
    gold = read(root / f'{phase}-gold.json')
    if digest(gold) != manifest['gold_hash']:
        raise ValueError('预先核验基准发生变化')
    expected = {t['trial_id'] for t in manifest['trials']}
    items = review['items']
    ids = [i['review_id'] for i in items]
    if len(items) != len(expected) or len(ids) != len(set(ids)) or set(ids) != set(mapping) or {m['trial_id'] for m in mapping.values()} != expected:
        raise ValueError('核验映射遗漏、重复或混入其他批次')
    identities = {t['trial_id']: t for t in manifest['trials']}
    for item in items:
        identity = mapping[item['review_id']]
        if identity != identities[identity['trial_id']] or item['case_id'] != identity['case_id']:
            raise ValueError('核验样本身份已修改')
        path = root / phase / identity['trial_id'] / 'result.json'
        report = read(path).get('report') if path.exists() else None
        if report:
            report = {k: report.get(k) for k in report_schema()['properties']}
        if item['report_hash'] != digest(report) or digest(item['report']) != digest(report):
            raise ValueError('核验不能修改原始交付内容')
        if item['delivery_present'] != (report is not None):
            raise ValueError('交付状态须与原始结果一致')
        if item['reference_facts'] != gold[identity['case_id']] or item['required_facts_total'] != len(gold[identity['case_id']]):
            raise ValueError('必要事实分母必须使用实验前的核验基准')
    totals = {a: {'cases': 0, 'metrics': {m: {v: 0 for v in VERDICTS} for m in METRICS},
                  'claims_reviewed': 0, 'claims_passed': 0, 'claims_failed': 0, 'claims_unverified': 0,
                  'required_facts_covered': 0, 'required_facts_total': 0, 'deliveries_missing': 0} for a in ARMS}
    rows = []
    coverage_denominators = {}
    for item in items:
        identity = mapping[item['review_id']]
        arm = identity['arm']
        target = totals[arm]
        target['cases'] += 1
        target['deliveries_missing'] += not item['delivery_present']
        for metric in METRICS:
            check = item['checks'][metric]
            verdict = check['verdict']
            if verdict not in VERDICTS:
                raise ValueError('未知核验结论')
            if verdict in ('pass', 'fail') and (not check['notes'] or not check['supporting_urls']):
                raise ValueError('合格/错误必须说明理由并给核查依据；不能把自动检查当人工事实核验')
            if not item['delivery_present'] and verdict == 'pass':
                raise ValueError('无交付不能记合格')
            target['metrics'][metric][verdict] += 1
        numeric = ('claims_reviewed', 'claims_passed', 'claims_failed', 'claims_unverified',
                   'required_facts_covered', 'required_facts_total')
        if any(type(item[k]) is not int or item[k] < 0 for k in numeric):
            raise ValueError('核验计数须为非负整数')
        if item['claims_reviewed'] != sum(item[k] for k in ('claims_passed', 'claims_failed', 'claims_unverified')) or item['required_facts_covered'] > item['required_facts_total']:
            raise ValueError('核验计数不一致')
        if item['claims_reviewed'] == 0 and item['checks']['facts']['verdict'] == 'pass':
            raise ValueError('没有核验断言，不能把空输出记为事实合格')
        if not item['delivery_present'] and (item['claims_reviewed'] or item['required_facts_covered']):
            raise ValueError('未交付的结果不能记录事实覆盖')
        cid = identity['case_id']
        if cid in coverage_denominators and coverage_denominators[cid] != item['required_facts_total']:
            raise ValueError('同一事件三组必须使用相同的必要事实分母')
        coverage_denominators[cid] = item['required_facts_total']
        for key in numeric:
            target[key] += item[key]
        rows.append({**identity, 'checks': item['checks'], **{k: item[k] for k in numeric}})
    ledger = Ledger(root)
    try:
        costs = ledger.summary()
    finally:
        ledger.close()
    for arm in ARMS:
        calls = [r for r in costs['records'] if r['phase'] == phase and r['trial_id'].endswith('-' + arm)]
        totals[arm].update(model_calls=len(calls),
                          estimated_peak_cost_yuan=sum(r['charged'] or 0 for r in calls) / 1000000,
                          reserved_unknown_yuan=sum(r['reserved'] for r in calls if r['charged'] is None) / 1000000)
    result = {'version': VERSION, 'manifest_hash': manifest['manifest_hash'], 'groups': totals,
              'case_results': rows, 'budget': costs,
              'conclusion_status': 'exploratory_only',
              'limitations': ['10例不能证明统计显著性或TapTap增长效果', '未核实单独计数，不当作正确',
                              '空输出与漏答单独计数；同时核对覆盖率', '一次生成有随机性，不代表稳定优于基线',
                              '部分盲化不能完全消除开发者偏见']}
    path = root / f'{phase}-summary-{uuid.uuid4().hex[:12]}.json'
    write_new(path, result)
    return {'summary': str(path), 'groups': totals}


def main(argv=None):
    parser = argparse.ArgumentParser(description='可核查的独立LLM/搜索/Agent对照实验')
    parser.add_argument('--root', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--production-db', default='data/v3/agent.sqlite3')
    p.add_argument('--limit', type=int, default=160)
    p = sub.add_parser('freeze')
    p.add_argument('--selection', required=True)
    p.add_argument('--phase', choices=('pilot', 'formal'), default='formal')
    p = sub.add_parser('authorize')
    p.add_argument('--cap-yuan', type=float, required=True)
    p.add_argument('--pilot-yuan', type=float, default=2)
    p.add_argument('--note', required=True)
    for command in ('run', 'review', 'summary'):
        p = sub.add_parser(command)
        p.add_argument('--phase', choices=('pilot', 'formal'), default='formal')
        if command == 'summary':
            p.add_argument('--review', required=True)
            p.add_argument('--mapping', required=True)
    args = parser.parse_args(argv)
    root = study_path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    if args.command == 'prepare':
        result = prepare_pool(args.production_db, root, args.limit)
    elif args.command == 'freeze':
        result = freeze(root, args.selection, phase=args.phase)
    elif args.command == 'authorize':
        ledger = Ledger(root)
        try:
            result = ledger.authorize(args.cap_yuan, args.pilot_yuan, args.note)
        finally:
            ledger.close()
    elif args.command == 'run':
        result = run_phase(root, args.phase)
    elif args.command == 'review':
        result = review_export(root, args.phase)
    else:
        result = summarize(root, args.review, args.mapping, args.phase)
    print(dump(result))


if __name__ == '__main__':
    main()
