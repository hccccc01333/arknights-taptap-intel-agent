"""Go transport identity, reasoning isolation and finite free-model budgets."""
import copy
import json
from pathlib import Path
import unittest
import uuid
import time
from unittest.mock import patch, Mock

from agent_v3 import benchmark as b
from agent_v3.opencode_go import profile, MODELS, BASE_URL
from agent_v3.provider_adapter import ProfileModel, request_body
from agent_v3.tests.test_providers18 import reply, SCHEMA, PRIVATE
from agent_v3.tests.test_deepseek import response
from L4_intelligence.intelligence.llm import LLMUnavailable
from agent_v3.opencode_go import read_stream
from agent_v3.opencode_zen import StructuredDeliveryError


def sse(model, *, done=True, finish='stop', only_reasoning=False):
    events=[{'model':model,'id':'fixture-stream','choices':[{'index':0,'delta':{
        'reasoning':PRIVATE,'reasoning_details':[{'text':PRIVATE}],'reasoning_content':PRIVATE}}]},
        {'model':model,'choices':[{'index':0,'delta':{} if only_reasoning else {'content':'{"ready":true}'},'finish_reason':finish}]},
        {'model':model,'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':30,'total_tokens':40,
            'completion_tokens_details':{'reasoning_tokens':20}}}]
    raw=b''.join(b'data: '+json.dumps(e,ensure_ascii=False).encode()+b'\r\n\r\n' for e in events)
    if done:raw+=b'data: [DONE]\n\n'
    r=Mock(status_code=200,headers={'Content-Type':'text/event-stream'})
    r.iter_content.return_value=iter([raw[i:i+7] for i in range(0,len(raw),7)])
    r.__enter__=Mock(return_value=r);r.__exit__=Mock(return_value=False)
    return r


class GoTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('.toolchain/go-tests') / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.ledger = b.Ledger(self.root)

    def tearDown(self):
        self.ledger.close()

    def test_named_free_profiles_and_supported_reasoning_parameters(self):
        step = profile(MODELS[0]); cat = profile(MODELS[1])
        self.assertEqual(step['api_key_env'], 'OPENCODE_GO_API_KEY')
        self.assertEqual(request_body(step,'baseline',{},SCHEMA,'fixture')[0]['reasoning_effort'],'high')
        self.assertNotIn('reasoning_effort',request_body(cat,'baseline',{},SCHEMA,'fixture')[0])
        self.assertEqual(request_body(cat,'baseline',{},SCHEMA,'fixture')[0]['thinking'],{'type':'disabled'})
        with self.assertRaises(ValueError): profile('paid-model')

    def test_own_stable_session_and_user_agent_without_key_in_artifacts(self):
        p=profile(MODELS[0]); raw=reply(p)
        raw['choices'][0]['message']={'content':'{"ready":true}', 'reasoning':PRIVATE,
                                     'reasoning_details':[{'text':PRIVATE,'type':'reasoning.text'}]}
        observed=[]
        with patch('agent_v3.providers.credential',return_value='fixture-secret'),patch(
                'requests.post',side_effect=[response(raw),response(raw)]) as post:
            model=ProfileModel(p,final_observer=lambda content,meta:observed.append((content,meta)))
            first=model.run_task('baseline',{},SCHEMA,'fixture')
            second=model.run_task('baseline',{},SCHEMA,'fixture')
        self.assertTrue(first['reasoning_present'])
        self.assertEqual(first['session_id'],second['session_id'])
        for call in post.call_args_list:
            self.assertEqual(call.args[0],BASE_URL+'/chat/completions')
            self.assertEqual(call.kwargs['headers']['x-opencode-session'],first['session_id'])
            self.assertTrue(call.kwargs['headers']['User-Agent'].startswith('taptap-intel-agent/'))
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertEqual(call.kwargs['timeout'],(10,180))
        self.assertNotIn(PRIVATE,json.dumps(observed))
        self.assertNotIn('fixture-secret',json.dumps(observed))

    def test_free_does_not_remove_call_or_unknown_token_reservations(self):
        self.ledger.authorize_go(MODELS[0],'offline fixture',max_calls=2,pilot_calls=1,max_tokens=100)
        call=self.ledger.reserve('test-A','pilot','baseline',30,30)
        self.ledger.settle(call,{},1,'failed')
        with self.assertRaises(b.BudgetExceeded):self.ledger.reserve('test-B','pilot','baseline',1,1)
        with self.assertRaises(b.BudgetExceeded):self.ledger.reserve('test-C','formal','baseline',30,30)
        self.assertEqual(self.ledger.summary()['usage_unknown_calls'],1)
        self.ledger.reserve('test-C','formal','baseline',1,1)
        with self.assertRaises(b.BudgetExceeded):self.ledger.reserve('test-D','formal','baseline',1,1)

    def test_zero_marginal_fee_keeps_usage_and_rejects_configuration_drift(self):
        self.ledger.authorize_go(MODELS[1],'offline fixture')
        p=profile(MODELS[1]); raw=reply(p)
        with patch('agent_v3.providers.credential',return_value='fixture'),patch('requests.post',return_value=response(raw)):
            model=b.RecordedModel(p,self.ledger,'test-A','pilot',self.root/'outputs')
            model.run_task('baseline',{},SCHEMA,'fixture')
        summary=self.ledger.summary()
        self.assertEqual(summary['estimated_peak_cost_yuan'],0)
        self.assertEqual(summary['prompt_tokens'],10)
        self.assertEqual(summary['completion_tokens'],30)
        changed=copy.deepcopy(p);changed['base_url']='https://opencode.ai/zen/v1'
        with self.assertRaises(ValueError):b.RecordedModel(changed,self.ledger,'test-B','pilot',self.root)

    def test_wrong_returned_model_stops_instead_of_falling_back(self):
        self.ledger.authorize_go(MODELS[0],'offline fixture')
        p=profile(MODELS[0]);raw=reply(p);raw['model']='paid-replacement'
        with patch('agent_v3.providers.credential',return_value='fixture'),patch('requests.post',return_value=response(raw)):
            model=b.RecordedModel(p,self.ledger,'test-A','pilot',self.root/'outputs')
            with self.assertRaises(b.BudgetExceeded):model.run_task('baseline',{},SCHEMA,'fixture')
            self.assertTrue(model.stopped)

    def test_streamed_delivery_handles_split_frames_and_ignores_all_thinking_fields(self):
        p=profile(MODELS[0]);observed=[]
        with patch('agent_v3.providers.credential',return_value='fixture'),patch(
                'requests.post',return_value=sse(p['model_id'])) as post:
            r=ProfileModel(p,final_observer=lambda content,meta:observed.append((content,meta))).run_task(
                'baseline',{},SCHEMA,'fixture')
        self.assertEqual(r['result'],{'ready':True})
        self.assertEqual(r['usage']['completion_tokens'],30)
        self.assertTrue(r['reasoning_present'])
        self.assertNotIn(PRIVATE,json.dumps(observed))
        self.assertTrue(post.call_args.kwargs['json']['stream'])
        self.assertNotIn('stream_options',post.call_args.kwargs['json'])

    def test_stream_without_done_or_over_time_and_size_is_rejected(self):
        with self.assertRaises(LLMUnavailable):read_stream(sse(MODELS[0],done=False),time.monotonic(),180)
        with self.assertRaises(LLMUnavailable):read_stream(sse(MODELS[0]),time.monotonic()-181,180)
        r=Mock();r.iter_content.return_value=iter([b'x'*2000001])
        with self.assertRaises(LLMUnavailable):read_stream(r,time.monotonic(),180)

    def test_streamed_reasoning_alone_and_length_never_become_business_results(self):
        for changes in ({'only_reasoning':True},{'finish':'length'}):
            p=profile(MODELS[1])
            with patch('agent_v3.providers.credential',return_value='fixture'),patch(
                    'requests.post',return_value=sse(p['model_id'],**changes)):
                with self.assertRaises(StructuredDeliveryError):
                    ProfileModel(p).run_task('baseline',{},SCHEMA,'fixture')

    def test_schema_failure_preserves_paths_without_model_values(self):
        p=profile(MODELS[1]);raw=reply(p)
        raw['choices'][0]['message']['content']=json.dumps({'ready':PRIVATE})
        with patch('agent_v3.providers.credential',return_value='fixture'),patch('requests.post',return_value=response(raw)):
            with self.assertRaises(StructuredDeliveryError) as caught:
                ProfileModel(p).run_task('baseline',{},SCHEMA,'fixture')
        self.assertEqual(caught.exception.response['schema_errors'],[{'path':'ready','constraint':'const'}])
        self.assertNotIn(PRIVATE,json.dumps(caught.exception.response))

    def test_failure_artifact_records_http_code_without_provider_payload(self):
        self.ledger.authorize_go(MODELS[0],'offline fixture');p=profile(MODELS[0])
        rejected=response({});rejected.status_code=429
        with patch('agent_v3.providers.credential',return_value='fixture'),patch(
                'requests.post',return_value=rejected):
            model=b.RecordedModel(p,self.ledger,'test-C','pilot',self.root/'outputs')
            with self.assertRaises(LLMUnavailable):model.run_task('interpretation',{},SCHEMA,'fixture')
        delivery=json.loads(next((self.root/'outputs').glob('*.delivery.json')).read_text(encoding='utf-8'))
        self.assertEqual(delivery['failure']['category'],'http_429')
        self.assertTrue(model.stopped)
        self.assertNotIn('fixture',json.dumps(delivery))


class GroundingRetryTests(unittest.TestCase):
    def test_invalid_entity_is_repaired_before_any_business_write(self):
        self.exercise(repair=True)

    def test_repeated_invalid_entity_fails_closed_without_partial_writes(self):
        self.exercise(repair=False)

    def exercise(self,repair):
        from agent_v3.tests.test_main_agent import MainAgentTests
        from agent_v3.research_child import interpret
        from agent_v3.discovery import read_topic
        from agent_v3.main_agent import SemanticDeliveryError
        fixture=MainAgentTests();fixture.setUp()
        try:
            model=fixture.model();original=model.run_task;packets=[]
            def run(stage,packet,schema,system,**kwargs):
                self.assertEqual(fixture.store.conn.execute('SELECT COUNT(*) FROM topic_interpretation').fetchone()[0],0)
                packets.append(copy.deepcopy(packet));result=original(stage,packet,schema,system,**kwargs)
                if not repair or len(packets)==1:
                    result['result']['knowledge']['entities']=[{'surface':'完全没有出现的实体','canonical_name':'完全没有出现的实体',
                                                              'kind':'GAME','basis_refs':[1]}]
                return result
            model.run_task=run
            if repair:
                saved=interpret(fixture.store,fixture.rid,model,fixture.tid)
                self.assertTrue(saved);self.assertEqual(saved['payload']['knowledge']['entities'],[])
            else:
                with self.assertRaises(SemanticDeliveryError):interpret(fixture.store,fixture.rid,model,fixture.tid)
                self.assertIsNone(fixture.store.interpretation(fixture.tid,read_topic(fixture.store,fixture.tid)['fingerprint']))
            self.assertEqual(len(packets),2)
            feedback=packets[1]['validation_errors'][0]
            self.assertIn('knowledge/entities/0/basis_refs',feedback)
            self.assertNotIn('完全没有出现的实体',feedback)
            self.assertEqual(fixture.store.conn.execute("SELECT COUNT(*) FROM kg_entity WHERE origin='proposed'").fetchone()[0],0)
        finally:fixture.tearDown()


if __name__=='__main__':unittest.main()
