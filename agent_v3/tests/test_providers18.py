"""Offline provider switching, final-answer isolation and identical evidence contracts."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import requests
from agent_v3 import providers
from agent_v3.provider_adapter import ProfileModel, request_body
from agent_v3.store import Store
from agent_v3.model import GatedModel, gate, failure, set_provider_hold, model_options
from agent_v3.contracts import run_task
from agent_v3.opencode_zen import StructuredDeliveryError
from L4_intelligence.intelligence.llm import LLMUnavailable
from agent_v3.tests.test_deepseek import response

PRIVATE = 'PRIVATE_THINKING_IS_NEVER_A_DELIVERABLE'
SCHEMA = {'type':'object','properties':{'ready':{'const':True}},'required':['ready'],'additionalProperties':False}


def profile(pid='openai', **changes):
    p = copy.deepcopy(next(p for p in providers.PRESETS if p['id']==pid))
    if not p['model_id']:
        p['model_id']='ep-offline-fixture'
    p.update(changes)
    return providers.validate(p)


def reply(p, value=None, *, finish=None):
    text=json.dumps({'ready':True} if value is None else value)
    if p['protocol']=='openai_responses':
        return {'id':'offline-request','model':p['model_id'],'status':finish or 'completed',
                'output':[{'type':'reasoning','summary':[{'text':PRIVATE}],'encrypted_content':PRIVATE},
                          {'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':text}]}],
                'usage':{'input_tokens':10,'output_tokens':30,'total_tokens':40,'output_tokens_details':{'reasoning_tokens':20}}}
    if p['protocol']=='anthropic_messages':
        return {'id':'offline-request','type':'message','model':p['model_id'],'stop_reason':finish or 'end_turn',
                'content':[{'type':'thinking','thinking':PRIVATE,'signature':PRIVATE},{'type':'text','text':text}],
                'usage':{'input_tokens':10,'output_tokens':30,'cache_read_input_tokens':4,'cache_creation_input_tokens':2}}
    return {'id':'offline-request','model':p['model_id'],'choices':[{'finish_reason':finish or 'stop',
            'message':{'content':text,'reasoning_content':PRIVATE}}],
            'usage':{'prompt_tokens':10,'completion_tokens':30,'total_tokens':40,
                     'completion_tokens_details':{'reasoning_tokens':20}}}


class ConfigurationTests(unittest.TestCase):
    def setUp(self):self.s=Store(':memory:')
    def tearDown(self):self.s.close()

    def test_thirteen_presets_validate_without_any_network_or_secret_storage(self):
        with patch('agent_v3.providers.credential',return_value='test-secret'),patch('requests.post') as post:
            for item in providers.PRESETS:
                p=profile(item['id']);saved=providers.save(self.s,p)
                self.assertEqual(saved['model'],'profile/'+p['id'])
                self.assertEqual(saved['label'],p['label'])
            data=providers.listing(self.s)
            self.assertEqual(data['profiles'][0]['label'],providers.PRESETS[0]['label'])
            self.assertEqual(len(data['profiles']),13);post.assert_not_called()
            self.assertNotIn('test-secret',json.dumps(data))
            self.assertNotIn('test-secret',self.s.conn.execute('SELECT value FROM settings WHERE key=?',(providers.SETTING,)).fetchone()[0])
            self.assertIsNone(self.s.model_setting())

    def test_custom_native_profiles_persist_and_have_separate_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'isolated.sqlite3';s=Store(path)
            try:
                providers.save(s,profile('ollama',id='custom_local',model_id='my-model',output_limit=9000))
                s.set_model('profile/custom_local');self.assertEqual(s.output_setting(),9000)
                s.set_output(1200);self.assertEqual(s.reasoning_setting(),'default')
            finally:s.close()
            s=Store(path)
            try:self.assertEqual(s.model_setting(),'profile/custom_local');self.assertEqual(s.output_setting(),1200)
            finally:s.close()

    def test_endpoints_normalize_and_invalid_or_credential_fields_are_rejected(self):
        self.assertEqual(profile(base_url='https://example.test/v1/chat/completions/')['base_url'],'https://example.test/v1')
        self.assertEqual(profile('anthropic',base_url='https://example.test/v1/messages')['base_url'],'https://example.test/v1')
        for changes in ({'api_key':'secret'},{'headers':{'Authorization':'secret'}},
                        {'base_url':'https://user:secret@example.test/v1'}, {'base_url':'https://example.test/v1?key=secret'},
                        {'base_url':'http://example.test/v1'}, {'base_url':'http://169.254.169.254/v1','allow_http':True},
                        {'base_url':'https://example.test/\nkey'}, {'api_key_env':'sk-private-key'},
                        {'protocol':{}}, {'reasoning_mode':[]}, {'output_limit':True},{'thinking_budget':0},
                        {'reasoning_mode':'omit','reasoning_effort':'high'},{'budget_scope':'separate'},
                        {'protocol':'anthropic_messages','reasoning_mode':'reasoning_effort'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):profile(**changes)

    def test_edit_active_worker_configuration_is_rejected_without_partial_write(self):
        p=profile('ollama');providers.save(self.s,p);self.s.set_model('profile/ollama')
        rid=self.s.create_run('fixture');self.assertTrue(self.s.acquire(rid))
        before=providers.fingerprint(providers.get(self.s,'profile/ollama'))
        with self.assertRaisesRegex(ValueError,'自动运行'):providers.save(self.s,{**p,'output_limit':1000})
        self.assertEqual(before,providers.fingerprint(providers.get(self.s,'profile/ollama')))

    def test_missing_key_does_not_select_profile_or_probe_remote(self):
        providers.save(self.s,profile())
        with patch('agent_v3.providers.credential',return_value=None),patch('requests.post') as post:
            with self.assertRaises(ValueError):self.s.set_model('profile/openai')
            with self.assertRaises(LLMUnavailable):ProfileModel(profile())
            self.assertFalse(model_options(self.s)[0]['configured']);post.assert_not_called()

    def test_deepseek_profile_keeps_legacy_quota_hold_and_other_providers_are_independent(self):
        providers.save(self.s,profile('deepseek'));providers.save(self.s,profile('ollama'))
        set_provider_hold(self.s,'deepseek-flash',True)
        with patch('agent_v3.providers.credential',return_value='test'):
            self.s.set_model('profile/deepseek')
            self.assertTrue(gate(self.s)['manual_resume'])
            with self.assertRaises(LLMUnavailable):GatedModel(self.s)
        self.assertEqual(gate(self.s,'profile/ollama')['status'],'ready')
        failure(self.s,'profile/ollama','HTTP 401: credential-invalid')
        self.assertEqual(gate(self.s,'profile/ollama')['status'],'deferred')
        self.assertTrue(gate(self.s,'deepseek-flash')['manual_resume'])

    def test_alias_profiles_share_backoff_but_endpoints_are_isolated(self):
        p=profile('ollama');providers.save(self.s,p)
        providers.save(self.s,{**p,'id':'same_endpoint'});providers.save(self.s,{**p,'id':'other_endpoint','base_url':'http://localhost:1234/v1'})
        failure(self.s,'profile/ollama','HTTP 429')
        self.assertEqual(gate(self.s,'profile/same_endpoint')['status'],'deferred')
        self.assertEqual(gate(self.s,'profile/other_endpoint')['status'],'ready')

    def test_gated_client_rejects_profile_changed_before_call(self):
        p=profile('ollama');providers.save(self.s,p);self.s.set_model('profile/ollama');model=GatedModel(self.s)
        providers.save(self.s,{**p,'model_id':'new-model'})
        with patch('requests.post') as post,self.assertRaises(LLMUnavailable):model.run_task('readiness',{},SCHEMA,'fixture')
        post.assert_not_called()

    def test_gated_client_rejects_changed_profile_after_call_and_preserves_usage(self):
        p=profile('ollama');providers.save(self.s,p);self.s.set_model('profile/ollama');model=GatedModel(self.s)
        def changed(*args,**kwargs):
            providers.save(self.s,{**p,'output_limit':1000})
            return {'result':{'ready':True},'usage':{'total_tokens':40}}
        with patch.object(model.client,'run_task',side_effect=changed),self.assertRaises(StructuredDeliveryError) as caught:
            model.run_task('readiness',{},SCHEMA,'fixture')
        self.assertIsNone(caught.exception.response['result']);self.assertEqual(caught.exception.response['usage']['total_tokens'],40)

    def test_delivery_checks_profile_version_after_model_return(self):
        from agent_v3.tests import test_main_agent as fixtures
        from agent_v3.runtime_guard import basis,StaleBasis
        f=fixtures.MainAgentTests();f.setUp()
        try:
            p=profile('ollama');providers.save(f.store,p);f.store.set_model('profile/ollama')
            expected=basis(f.store,f.tid);providers.save(f.store,{**p,'output_limit':1000})
            with self.assertRaises(StaleBasis):f.store.check_basis(expected)
        finally:f.tearDown()


class TransportTests(unittest.TestCase):
    def invoke(self,p,raw=None,*,code=200):
        with patch('agent_v3.providers.credential',return_value='isolated-private-key'),patch('agent_v3.provider_adapter.requests.post',return_value=response(raw if raw is not None else reply(p),code)) as post:
            return ProfileModel(p).run_task('intelligence',{},SCHEMA,'fixture'),post.call_args

    def test_all_presets_read_final_only_and_return_actual_model_and_safe_metadata(self):
        for item in providers.PRESETS:
            with self.subTest(provider=item['id']):
                p=profile(item['id']);result,call=self.invoke(p)
                self.assertEqual(result['result'],{'ready':True});self.assertNotIn(PRIVATE,json.dumps(result))
                self.assertEqual(result['api_model'],p['model_id']);self.assertIsNone(result['reported_cost'])
                self.assertFalse(call.kwargs['allow_redirects']);self.assertEqual(result['profile_version'],providers.fingerprint(p))
                self.assertNotIn('isolated-private-key',json.dumps(call.kwargs['json']))
                if p['protocol']=='anthropic_messages':
                    self.assertIn('x-api-key',call.kwargs['headers']);self.assertEqual(result['usage']['total_tokens'],46)
                else:self.assertEqual(result['usage']['reasoning_tokens'],20)

    def test_native_reasoning_fields_and_bounded_total_budgets(self):
        p=profile('openai');b,budget=request_body(p,'readiness',{},SCHEMA,'fixture')
        self.assertEqual(b['max_completion_tokens'],512);self.assertEqual(b['reasoning_effort'],'low');self.assertNotIn('thinking',b)
        p=profile('anthropic');b,_=request_body(p,'readiness',{},SCHEMA,'fixture');self.assertEqual(b['output_config']['effort'],'low');self.assertNotIn('response_format',b)
        p=profile('deepseek',reasoning_effort='none');b,_=request_body(p,'readiness',{},SCHEMA,'fixture');self.assertEqual(b['thinking'],{'type':'disabled'});self.assertNotIn('reasoning_effort',b)
        p=profile('siliconflow',reasoning_effort='enabled',output_limit=700,thinking_budget=4000)
        b,budget=request_body(p,'intelligence',{},SCHEMA,'fixture');self.assertEqual(budget,700)
        self.assertEqual(b['max_tokens']+b['thinking_budget'],700)
        self.assertGreaterEqual(b['max_tokens'],256)
        p=profile('minimax');b,_=request_body(p,'readiness',{},SCHEMA,'fixture');self.assertTrue(b['reasoning_split'])

    def test_prompt_and_native_schema_modes_keep_same_local_validation(self):
        for pid in ('openai','anthropic'):
            p=profile(pid,output_mode='json_schema');result,call=self.invoke(p)
            body=call.kwargs['json']
            actual=body['output_config']['format']['schema'] if pid=='anthropic' else body['response_format']['json_schema']['schema']
            self.assertEqual(actual,SCHEMA)
            with self.assertRaises(StructuredDeliveryError):self.invoke(p,reply(p,{'ready':False}))

    def test_missing_final_text_never_uses_thinking_or_context(self):
        for pid in ('openai','anthropic'):
            p=profile(pid);raw=reply(p)
            if pid=='anthropic':raw['content']=[{'type':'thinking','thinking':'{"ready":true}'}]
            else:raw['choices'][0]['message'].update(content=None,context='{"ready":true}',reasoning_content='{"ready":true}')
            with self.subTest(provider=pid),self.assertRaises(StructuredDeliveryError) as caught:self.invoke(p,raw)
            self.assertIsNone(caught.exception.response['result']);self.assertNotIn(PRIVATE,json.dumps(caught.exception.response))

    def test_refusals_truncation_tools_and_invalid_json_are_rejected_with_usage(self):
        for pid,finishes in (('openai',('length','tool_calls','content_filter','aborted')),('anthropic',('max_tokens','tool_use','refusal','pause_turn'))):
            p=profile(pid)
            for finish in finishes:
                with self.subTest(provider=pid,finish=finish),self.assertRaises(StructuredDeliveryError) as caught:self.invoke(p,reply(p,finish=finish))
                self.assertTrue(caught.exception.response['usage']);self.assertIsNone(caught.exception.response['result'])
        p=profile();raw=reply(p);raw['choices'][0]['message']['content']='<think>'+PRIVATE+'</think>{"ready":true}'
        with self.assertRaises(StructuredDeliveryError):self.invoke(p,raw)

    def test_http_errors_never_echo_provider_bodies_or_retry_another_provider(self):
        for code in (400,401,402,403,404,429,500,302):
            with self.subTest(code=code),self.assertRaises(LLMUnavailable) as caught:self.invoke(profile(),{'error':PRIVATE},code=code)
            self.assertNotIn(PRIVATE,str(caught.exception));self.assertIn('HTTP '+str(code),str(caught.exception))
        with patch('agent_v3.providers.credential',return_value='test'),patch('requests.post',side_effect=requests.Timeout('private-key')) as post,self.assertRaises(LLMUnavailable) as caught:
            ProfileModel(profile()).run_task('readiness',{},SCHEMA,'fixture')
        self.assertNotIn('private-key',str(caught.exception));self.assertEqual(post.call_count,1)

    def test_clock_input_and_response_size_limits(self):
        with patch('agent_v3.providers.credential',return_value='test'),patch('requests.post',return_value=response(reply(profile()))),patch('agent_v3.provider_adapter.time.monotonic',side_effect=[0,181]),self.assertRaises(LLMUnavailable):
            ProfileModel(profile()).run_task('readiness',{},SCHEMA,'fixture')
        with patch('agent_v3.providers.credential',return_value='test'),patch('requests.post') as post,self.assertRaises(ValueError):
            ProfileModel(profile()).run_task('readiness',{'huge':'x'*500001},SCHEMA,'fixture')
        post.assert_not_called()

    def test_responses_reads_only_output_text_and_rejects_incomplete_or_tools(self):
        p=profile('xai',reasoning_mode='reasoning_effort',reasoning_effort='low')
        result,call=self.invoke(p);b=call.kwargs['json']
        self.assertFalse(b['store']);self.assertEqual(b['reasoning'],{'effort':'low'})
        self.assertEqual(b['max_output_tokens'],8192);self.assertNotIn('messages',b)
        self.assertNotIn(PRIVATE,json.dumps(result));self.assertEqual(result['usage']['total_tokens'],40)
        for kind in ('incomplete','failed','in_progress','cancelled'):
            with self.subTest(kind=kind),self.assertRaises(StructuredDeliveryError):self.invoke(p,reply(p,finish=kind))
        raw=reply(p);raw['output'].append({'type':'function_call','arguments':'{}'})
        with self.assertRaises(StructuredDeliveryError):self.invoke(p,raw)
        raw=reply(p);raw['output']=raw['output'][:1]
        with self.assertRaises(StructuredDeliveryError):self.invoke(p,raw)


class SharedContractTests(unittest.TestCase):
    def test_all_presets_pass_identical_main_and_research_evidence_contracts(self):
        from agent_v3.tests import test_main_agent as fixtures
        from agent_v3.discovery import read_topic
        from agent_v3.task_packets import topic_packet
        from agent_v3.main_agent import business_schema
        from agent_v3.research_child import schema as research_schema
        from agent_v3.graph_ai import extraction_schema
        fixture=fixtures.MainAgentTests();fixture.setUp()
        try:
            packet=topic_packet(read_topic(fixture.store,fixture.tid),fixture.store.context());fingerprints=[]
            for stage,schema in (('intelligence',business_schema(packet)),('interpretation',research_schema(packet)),('graph_extraction',extraction_schema(packet))):
                good=fixture.model().run_task(stage,packet,schema,'fixture')['result']
                for item in providers.PRESETS:
                    p=profile(item['id'])
                    with self.subTest(stage=stage,provider=p['id']),patch('agent_v3.providers.credential',return_value='test'),patch('requests.post',return_value=response(reply(p,good))):
                        result=run_task(ProfileModel(p),stage,packet,schema,'fixture')
                        self.assertEqual(result['result'],good);self.assertNotIn(PRIVATE,json.dumps(result))
                        self.assertEqual(result['agent_role'],'business_main' if stage=='intelligence' else 'research_child')
                        fingerprints.append((stage,result['input_fingerprint']))
                    bad=copy.deepcopy(good)
                    if stage=='intelligence':bad['fact_refs']=[999]
                    elif stage=='interpretation':bad['core'][0]['basis_refs']=[999]
                    else:bad['entities']=[{'surface':'玩家','canonical_name':'玩家','kind':'CREATOR','basis_refs':[999]}]
                    with self.subTest(bad_ref=p['id'],stage=stage),patch('agent_v3.providers.credential',return_value='test'),patch('requests.post',return_value=response(reply(p,bad))),self.assertRaises(StructuredDeliveryError):
                        run_task(ProfileModel(p),stage,packet,schema,'fixture')
            for stage in ('intelligence','interpretation','graph_extraction'):self.assertEqual(len({fp for st,fp in fingerprints if st==stage}),1)
        finally:fixture.tearDown()

    def test_settings_api_is_role_protected_and_saving_is_not_activation(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agent_v3.api import router
        s=Store(':memory:');original=s.conn;s.conn=sqlite3.connect(':memory:',check_same_thread=False)
        original.backup(s.conn);s.conn.row_factory=sqlite3.Row;original.close()
        actor={'role':'observer','actor':'fixture'};app=FastAPI();app.include_router(router(lambda:actor))
        try:
            with TestClient(app) as client,patch('agent_v3.store.Store',return_value=s),patch('agent_v3.service.Store',return_value=s),patch.object(s,'close'),patch('requests.post') as post:
                self.assertEqual(client.get('/api/v3/models').status_code,403)
                self.assertEqual(client.post('/api/v3/settings/provider',json=profile('ollama')).status_code,403)
                actor['role']='operator';catalog=client.get('/api/v3/models')
                self.assertEqual(catalog.status_code,200)
                self.assertIn('model_gate',catalog.json());self.assertIn('model_options',catalog.json())
                self.assertIn('active_run',catalog.json());self.assertIn('model',catalog.json())
                self.assertEqual(client.post('/api/v3/settings/provider',json=profile('ollama')).status_code,200)
                self.assertIsNone(s.model_setting());post.assert_not_called()
                self.assertEqual(client.post('/api/v3/settings/provider',json={'api_key':'private-key'}).status_code,400)
                rid=s.create_run('fixture');self.assertTrue(s.acquire(rid))
                self.assertEqual(client.post('/api/v3/settings/provider',json=profile('ollama')).status_code,400)
        finally:s.close()
