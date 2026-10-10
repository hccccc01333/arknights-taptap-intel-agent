"""Experiment mechanics only; fixtures are never real accuracy measurements."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from agent_v2.store import now_iso
from agent_v3 import benchmark as b
from agent_v3.providers import PRESETS
from agent_v3.provider_adapter import ProfileModel
from agent_v3.opencode_zen import StructuredDeliveryError
from agent_v3.tests.test_deepseek import response, reply, SCHEMA, PRIVATE
from agent_v3.tests import test_main_agent as main_fixtures


def profile():
    p = copy.deepcopy(next(p for p in PRESETS if p['id'] == 'deepseek'))
    p.update(reasoning_effort='high', output_limit=8192)
    return p


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        # Retained ignored fixtures: this project requires approval to delete files.
        self.root = Path('.toolchain/benchmark-tests') / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.ledger = b.Ledger(self.root)

    def tearDown(self):
        self.ledger.close()

    def authorize(self, cap=10, pilot=2):
        self.ledger.authorize(cap, pilot, '离线测试授权；不能用于真实模型')

    def test_no_authorization_never_reaches_paid_transport(self):
        with patch('agent_v3.providers.credential', return_value='fixture'), patch('requests.post') as post:
            model = b.RecordedModel(profile(), self.ledger, 'test-A', 'formal', self.root / 'outputs')
            with self.assertRaises(b.BudgetExceeded):
                model.run_task('baseline', {}, SCHEMA, 'fixture')
            post.assert_not_called()

    def test_crash_reservation_is_retained_across_connections(self):
        self.authorize(cap=0.01, pilot=0.01)
        self.ledger.reserve('case-A', 'formal', 'baseline', 1000, 1000)
        other = b.Ledger(self.root)
        try:
            with self.assertRaises(b.BudgetExceeded):
                other.reserve('case-B', 'formal', 'baseline', 1, 1)
            self.assertEqual(other.summary()['reserved_unknown_yuan'], 0.01)
        finally:
            other.close()

    def test_reasoning_is_not_billed_twice_and_failure_is_not_free(self):
        self.authorize()
        call = self.ledger.reserve('case-C', 'formal', 'intelligence', 1000, 1000)
        self.ledger.settle(call, {'api_model': 'deepseek-flash', 'usage': {
            'prompt_tokens': 10, 'completion_tokens': 50, 'reasoning_tokens': 40, 'total_tokens': 60}}, 1, 'invalid_final')
        self.assertAlmostEqual(self.ledger.summary()['estimated_peak_cost_yuan'], 0.00042)
        self.assertEqual(self.ledger.summary()['records'][0]['status'], 'invalid_final')

    def test_unknown_usage_cannot_release_reserved_money(self):
        self.authorize()
        call = self.ledger.reserve('case-B', 'formal', 'baseline', 1000, 1000)
        self.ledger.settle(call, {}, 2, 'failed')
        self.assertEqual(self.ledger.summary()['reserved_unknown_yuan'], 0.01)

    def test_pilot_and_per_trial_limits_are_enforced(self):
        self.authorize(cap=1, pilot=0.01)
        self.ledger.reserve('p-A', 'pilot', 'baseline', 1000, 1000)
        with self.assertRaises(b.BudgetExceeded):
            self.ledger.reserve('p-B', 'pilot', 'baseline', 1, 1)
        for _ in range(16):
            self.ledger.reserve('f-C', 'formal', 'research_plan', 1, 1)
        with self.assertRaises(b.BudgetExceeded):
            self.ledger.reserve('f-C', 'formal', 'research_plan', 1, 1)

    def test_trial_cannot_silently_restart_after_failure(self):
        self.ledger.start_trial('case-A', 'formal')
        with self.assertRaises(Exception):
            self.ledger.start_trial('case-A', 'formal')

    def test_malformed_final_is_preserved_without_private_reasoning(self):
        self.authorize()
        with patch('agent_v3.providers.credential', return_value='fixture'), patch('requests.post', return_value=response(reply('broken JSON'))):
            model = b.RecordedModel(profile(), self.ledger, 'case-A', 'formal', self.root / 'outputs')
            with self.assertRaises(StructuredDeliveryError):
                model.run_task('baseline', {}, SCHEMA, 'fixture')
        files = list((self.root / 'outputs').glob('*.json'))
        text = '\n'.join(p.read_text(encoding='utf-8') for p in files)
        self.assertIn('broken JSON', text)
        self.assertNotIn(PRIVATE, text)
        self.assertNotIn('Bearer fixture', text)
        self.assertGreater(self.ledger.summary()['estimated_peak_cost_yuan'], 0)

    def test_transport_uncertainty_stops_business_retries(self):
        self.authorize()
        import requests
        with patch('agent_v3.providers.credential', return_value='fixture'), patch('requests.post', side_effect=requests.Timeout) as post:
            model = b.RecordedModel(profile(), self.ledger, 'case-C', 'formal', self.root / 'outputs')
            with self.assertRaises(Exception):
                model.run_task('main_plan', {}, SCHEMA, 'fixture')
            self.assertTrue(model.stopped)
            with self.assertRaises(b.BudgetExceeded):
                model.run_task('main_plan', {}, SCHEMA, 'fixture')
            self.assertEqual(post.call_count, 1)
        self.assertGreater(self.ledger.summary()['reserved_unknown_yuan'], 0)

    def test_other_provider_cannot_use_deepseek_fee_authorization(self):
        self.authorize()
        p = profile()
        p['base_url'] = 'https://another-provider.test'
        with self.assertRaises(ValueError):
            b.RecordedModel(p, self.ledger, 'case-A', 'formal', self.root)

    def test_model_version_drift_is_charged_and_stops_run(self):
        self.authorize()
        raw = reply()
        raw['model'] = 'deepseek-flash-updated-unexpectedly'
        with patch('agent_v3.providers.credential', return_value='fixture'), patch('requests.post', return_value=response(raw)):
            model = b.RecordedModel(profile(), self.ledger, 'case-A', 'formal', self.root / 'outputs')
            with self.assertRaises(b.BudgetExceeded):
                model.run_task('baseline', {}, SCHEMA, 'fixture')
            self.assertTrue(model.stopped)
        self.assertGreater(self.ledger.summary()['estimated_peak_cost_yuan'], 0)

    def pool(self, n=12):
        rows = [{'case_id': f'case-{i}', 'seed': {'title': f'离线测试事件{i}', 'observed_at': now_iso()}}
                for i in range(n)]
        b.write_new(self.root / 'pool.json', {'candidates': rows})
        return rows

    def frozen_formal(self):
        self.pool()
        categories = ['new_event', 'old_or_revival', 'negative_or_disputed', 'incomplete']
        selection = {'cases': [{'case_id': f'case-{i}', 'category': categories[i % 4],
                                'selection_reason': '预先选取的离线测试样本',
                                'reference_facts': [{'text': '离线测试事实', 'quote': '仅测试引用',
                                                     'url': 'https://example.test/fixture'}]} for i in range(10)]}
        path = self.root / 'selection.json'
        b.write_new(path, selection)
        b.freeze(self.root, path)
        return b.read(self.root / 'formal.json')

    def test_freeze_prevents_overwriting_or_missing_sample_categories(self):
        self.pool()
        selection = {'cases': [{'case_id': f'case-{i}', 'category': 'new_event',
                                'selection_reason': '测试', 'reference_facts': [{'text': '测试', 'quote': '测试',
                                                                             'url': 'https://example.test/fixture'}]} for i in range(10)]}
        path = self.root / 'selection.json'
        b.write_new(path, selection)
        with self.assertRaises(ValueError):
            b.freeze(self.root, path)
        self.assertFalse((self.root / 'formal.json').exists())
        with self.assertRaises(FileExistsError):
            b.write_new(path, selection)

    def test_frozen_order_is_paired_and_initial_inputs_have_no_oracle(self):
        m = self.frozen_formal()
        self.assertEqual(len(m['trials']), 30)
        for c in m['cases']:
            self.assertEqual({t['arm'] for t in m['trials'] if t['case_id'] == c['case_id']}, set(b.ARMS))
            self.assertNotIn('category', c['seed'])
            self.assertNotIn('selection_reason', c['seed'])
        self.assertEqual(m['manifest_hash'], b.digest({k: v for k, v in m.items() if k != 'manifest_hash'}))

    def test_no_results_cannot_be_reported_as_success(self):
        self.frozen_formal()
        paths = b.review_export(self.root)
        review = b.read(paths['review'])
        self.assertEqual(len(review['items']), 30)
        self.assertTrue(all(not i['delivery_present'] for i in review['items']))
        summary = b.summarize(self.root, paths['review'], paths['mapping'])
        self.assertEqual(summary['groups']['C']['metrics']['facts']['pass'], 0)
        self.assertEqual(summary['groups']['C']['metrics']['facts']['unverified'], 10)
        self.assertEqual(summary['groups']['C']['deliveries_missing'], 10)

    def test_pass_requires_a_reason_and_supporting_source(self):
        self.frozen_formal()
        paths = b.review_export(self.root)
        review = b.read(paths['review'])
        review['items'][0]['checks']['facts']['verdict'] = 'pass'
        altered = self.root / 'edited-review.json'
        b.write_new(altered, review)
        with self.assertRaises(ValueError):
            b.summarize(self.root, altered, paths['mapping'])

    def test_source_snapshot_is_cached_and_hash_checked(self):
        from agent_v3 import public_sources
        cache, directory = self.root / 'snapshots', self.root / 'outputs'
        with patch.object(public_sources, 'read_public', return_value=(b'public page', 'https://example.test/article')) as original:
            with b.PublicArchive(cache, directory):
                first = public_sources.read_public('https://example.test/article', {'example.test'})
                second = public_sources.read_public('https://example.test/article', {'example.test'})
                self.assertEqual(first, second)
            self.assertEqual(original.call_count, 1)
        path = next(cache.glob('*.json'))
        value = b.read(path)
        value['body_base64'] = 'YnJva2Vu'
        # Deliberate mutation of this new fixture, not deletion of a user file.
        path.write_text(json.dumps(value), encoding='utf-8')
        with b.PublicArchive(cache, directory):
            with self.assertRaises(ValueError):
                public_sources.read_public('https://example.test/article', {'example.test'})

    def test_c_runs_actual_main_child_delivery_path_not_single_prompt(self):
        from agent_v3.pipeline import process
        fixture = main_fixtures.MainAgentTests()
        fixture.setUp()
        self.authorize()
        native = fixture.model('opportunity')

        class Client:
            def __init__(self, p, final_observer):
                self.observer = final_observer

            def run_task(self, *args, **kwargs):
                value = native.run_task(*args, **kwargs)
                value.update(api_model='deepseek-flash', usage={'prompt_tokens': 10, 'completion_tokens': 30})
                self.observer(json.dumps(value['result'], ensure_ascii=False), {k: v for k, v in value.items() if k != 'result'})
                return value

        try:
            model = b.RecordedModel(profile(), self.ledger, 'case-C', 'formal', self.root / 'outputs', Client)
            with patch('agent_v3.research.search_news', side_effect=AssertionError('offline fixture')):
                output = process(fixture.store, fixture.rid, topic_id=fixture.tid, model=model)
            self.assertEqual(output['status'], 'completed')
            self.assertEqual(native.calls, ['main_plan', 'research_plan', 'interpretation', 'intelligence', 'creative_plan', 'creative_production'])
            report = b.project_full(fixture.store, fixture.tid, output)
            self.assertEqual(report['decision'], 'create')
            self.assertTrue(report['creative'])
            self.assertIn('0–3秒', report['creative'])
            self.assertIn('{{TapTap承接链接}}', report['creative'])
            self.assertTrue(any('地图我来选' in m for m in report['materials']))
            self.assertEqual(self.ledger.summary()['calls'], 6)
            self.assertEqual(len(list((self.root / 'outputs').glob('*.final.json'))), 6)
        finally:
            fixture.tearDown()

    def test_offline_pilot_runs_all_six_trials_and_exports_auditable_results(self):
        from contextlib import redirect_stdout
        import io
        fixture = main_fixtures.MainAgentTests()
        fixture.setUp()
        self.authorize()
        native = fixture.model('opportunity')
        cases, selection = [], []
        for i in range(2):
            cid = f'case-integration-{i}'
            seed = {'title': f'玩家分享组队挑战玩法{i}', 'body': fixture.item['body'],
                    'url': f'https://example.test/fixture-{i}', 'platform': 'weibo', 'kind': 'ranking',
                    'published_at': now_iso(), 'observed_at': now_iso(), 'channel_id': 'baidu:game',
                    'rank': 12, 'metrics': {}}
            cases.append({'case_id': cid, 'seed': seed})
            selection.append({'case_id': cid, 'category': 'new_event', 'selection_reason': '离线集成样本',
                              'reference_facts': [{'text': '玩家分享组队玩法', 'quote': '玩家可分享地图并组队挑战。',
                                                   'url': seed['url']}]})
        b.write_new(self.root / 'pool.json', {'candidates': cases})
        b.write_new(self.root / 'selection.json', {'cases': selection})
        b.freeze(self.root, self.root / 'selection.json', phase='pilot')

        class Client:
            def __init__(self, p, final_observer):
                self.observer = final_observer

            def run_task(self, stage, packet, schema, system, **kwargs):
                if stage == 'baseline':
                    value = {'result': {'headline': packet['initial_information']['title'], 'one_line': '测试摘要',
                        'claims': [], 'unknowns': ['仅为离线测试'], 'decision': 'watch', 'decision_reason': '待核查',
                        'intelligence': [], 'materials': [], 'creative': None}, 'transport': 'fixture'}
                else:
                    value = native.run_task(stage, packet, schema, system, **kwargs)
                value.update(api_model='deepseek-flash', usage={'prompt_tokens': 10, 'completion_tokens': 30})
                self.observer(json.dumps(value['result'], ensure_ascii=False), {k: v for k, v in value.items() if k != 'result'})
                return value

        try:
            with patch('agent_v3.provider_adapter.ProfileModel', Client), patch('requests.post') as post, patch(
                    'agent_v3.tool_executor.ToolExecutor.execute', return_value={'calls': 0, 'status': 'fixture'}), redirect_stdout(io.StringIO()):
                result = b.run_phase(self.root, 'pilot')
                post.assert_not_called()
            self.assertEqual(result['calls'], 16)
            outputs = list((self.root / 'pilot').glob('*/result.json'))
            self.assertEqual(len(outputs), 6)
            self.assertTrue(all(b.read(p)['status'] == 'returned' for p in outputs))
            paths = b.review_export(self.root, 'pilot')
            summary = b.summarize(self.root, paths['review'], paths['mapping'], 'pilot')
            self.assertEqual(summary['groups']['C']['model_calls'], 12)
            self.assertEqual(summary['groups']['A']['metrics']['facts']['pass'], 0)
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
