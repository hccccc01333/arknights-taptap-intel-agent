"""Final JSON isolation, usage, budgets, and private tool continuation."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from L4_intelligence.intelligence.llm import LLMUnavailable, provider_key
from L4_intelligence.intelligence.chat_response import assistant_history
from agent_v2.model import LiveModel
from agent_v2.engine import run_agent
from agent_v2.store import Store as V2Store
from agent_v2.tests.test_core import sample, final
from agent_v3.deepseek import DeepSeekModel, MODEL, ENDPOINT
from agent_v3.store import Store
from agent_v3.model import GatedModel, gate, failure, set_zen_quota, task_metadata
from agent_v3.opencode_zen import StructuredDeliveryError
from agent_v3.tests import test_delivery
from agent_v3.pipeline import process

SCHEMA={'type':'object','properties':{'ready':{'const':True}},'required':['ready'],'additionalProperties':False}
PRIVATE='PRIVATE_THINKING_MUST_NEVER_BECOME_BUSINESS_DATA'


def reply(content='{"ready":true}', finish='stop'):
    return {'id':'request-test','model':MODEL,'choices':[{'finish_reason':finish,'message':{
        'role':'assistant','content':content,'reasoning_content':PRIVATE}}],
        'usage':{'prompt_tokens':10,'completion_tokens':50,'total_tokens':60,
                 'completion_tokens_details':{'reasoning_tokens':40}}}


def response(data, code=200):
    r=Mock(status_code=code)
    r.iter_content.return_value=iter([json.dumps(data).encode()])
    r.__enter__=Mock(return_value=r);r.__exit__=Mock(return_value=False)
    return r


class DeepSeekTests(unittest.TestCase):
    def invoke(self, data=None, *, effort='low', limit=16384, stage='intelligence', code=200, clock=None):
        with patch('agent_v3.deepseek.api_key',return_value='private-test-key'),patch('agent_v3.deepseek.requests.post',return_value=response(reply() if data is None else data,code)) as post:
            model=DeepSeekModel(reasoning_effort=effort,output_limit=limit)
            if clock:
                with patch('agent_v3.deepseek.time.monotonic',side_effect=clock):result=model.run_task(stage,{},SCHEMA,'read inputs')
            else:result=model.run_task(stage,{},SCHEMA,'read inputs')
            return result,post.call_args

    def test_final_content_only_and_reasoning_is_counted_once(self):
        result,call=self.invoke()
        self.assertEqual(result['result'],{'ready':True})
        self.assertNotIn(PRIVATE,json.dumps(result))
        self.assertEqual(result['usage']['total_tokens'],60)
        self.assertEqual(result['usage']['reasoning_tokens'],40)
        self.assertTrue(result['reasoning_present'])
        self.assertEqual(result['cost_status'],'provider_billed_not_reported')
        self.assertIsNone(result['reported_cost'])
        self.assertEqual(call.args[0],ENDPOINT)
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertNotIn('private-test-key',json.dumps(call.kwargs['json']))

    def test_documented_efforts_and_disabled_mode(self):
        for effort in ('none','low','high','max','default'):
            with self.subTest(effort=effort):
                result,call=self.invoke(effort=effort)
                payload=call.kwargs['json']
                self.assertEqual(payload['thinking']['type'],'disabled' if effort=='none' else 'enabled')
                self.assertEqual(payload.get('reasoning_effort'),None if effort in ('none','default') else effort)
                self.assertNotIn('reasoning',payload);self.assertNotIn('temperature',payload)
                self.assertNotIn('tools',payload);self.assertNotIn('tool_choice',payload)
                self.assertEqual(payload['response_format'],{'type':'json_object'})
                self.assertEqual(result['output_limit'],payload['max_tokens'])

    def test_stage_budgets_never_exceed_user_cap(self):
        for stage,expected in (('main_plan',8192),('research_plan',4096),('interpretation',8192),('event_relation',4096),('intelligence',8192),('creative_production',12288)):
            self.assertEqual(self.invoke(stage=stage)[0]['output_limit'],expected)
        self.assertEqual(self.invoke(effort='max',stage='creative_production',limit=6000)[0]['output_limit'],6000)
        self.assertEqual(self.invoke(effort='high',stage='event_relation')[0]['output_limit'],8192)

    def test_empty_final_cannot_be_rescued_by_valid_json_in_reasoning(self):
        data=reply(None);data['choices'][0]['message']['reasoning_content']='{"ready":true}'
        with self.assertRaisesRegex(StructuredDeliveryError,'content 为空') as caught:self.invoke(data)
        self.assertIsNone(caught.exception.response['result'])
        self.assertNotIn('reasoning_content',caught.exception.response)

    def test_context_is_not_a_response_content_alias(self):
        data=reply(None);data['choices'][0]['message']['context']='{"ready":true}'
        with self.assertRaises(StructuredDeliveryError):self.invoke(data)

    def test_truncation_refusal_and_interruption_never_finish_business(self):
        for finish in ('length','content_filter','aborted','insufficient_system_resource',None):
            with self.subTest(finish=finish),self.assertRaises(StructuredDeliveryError) as caught:self.invoke(reply(finish=finish))
            self.assertIsNone(caught.exception.response['result'])
            self.assertEqual(caught.exception.response['usage']['total_tokens'],60)
            self.assertNotIn(PRIVATE,json.dumps(caught.exception.response))

    def test_wrong_json_and_schema_do_not_use_thinking_fallback(self):
        for content in ('[]','not json','```json\n{"ready":true}\n```','{"wrong":true}','{"ready":false}'):
            with self.subTest(content=content),self.assertRaises(StructuredDeliveryError):self.invoke(reply(content))

    def test_malformed_envelopes_and_wrong_models_stop_safely(self):
        for data in ([],{}, {'choices':[None]}, {'choices':[{'message':None}]},reply({'ready':True})):
            with self.subTest(data=data),self.assertRaises(LLMUnavailable):self.invoke(data)
        data=reply();data['model']='deepseek-v4-pro'
        with self.assertRaises(LLMUnavailable):self.invoke(data)

    def test_gateway_error_does_not_echo_keys_or_provider_thinking(self):
        for code in (400,401,402,429,502):
            with self.subTest(code=code),self.assertRaisesRegex(LLMUnavailable,f'HTTP {code}') as caught:self.invoke({'error':PRIVATE+'private-test-key'},code=code)
            self.assertNotIn(PRIVATE,str(caught.exception));self.assertNotIn('private-test-key',str(caught.exception))

    def test_heartbeat_is_bounded(self):
        with self.assertRaisesRegex(LLMUnavailable,'时间预算'):self.invoke(clock=[0,181])

    def test_settings_and_backoff_are_independent_of_zen(self):
        s=Store(':memory:')
        try:
            with patch('agent_v3.deepseek.api_key',return_value='test'):
                s.set_model(MODEL);s.set_reasoning('max');s.set_output(9000);set_zen_quota(s,True)
                self.assertEqual(gate(s)['status'],'ready')
                with patch('agent_v3.model.DeepSeekModel') as deep,patch('agent_v3.model.OpenCodeModel') as zen:
                    deep.return_value.model=MODEL;GatedModel(s)
                    deep.assert_called_once_with(MODEL,reasoning_effort='max',output_limit=9000);zen.assert_not_called()
                failure(s,MODEL,'HTTP 429');self.assertEqual(gate(s,'deepseek-v4-flash')['status'],'deferred')
                self.assertEqual(gate(s,'opencode/big-pickle')['reason'],'已确认 Zen 免费额度耗尽，AI 等待恢复')
                with self.assertRaises(ValueError):s.set_reasoning('medium')
                for invalid in (True,255,32769,'8192'):
                    with self.assertRaises(ValueError):s.set_output(invalid)
            with patch('agent_v3.deepseek.api_key',return_value=None),self.assertRaises(ValueError):s.set_model(MODEL)
        finally:s.close()

    def test_output_settings_endpoint_enforces_role_type_and_active_run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agent_v3.api import router
        import sqlite3
        s=Store(':memory:');actor={'role':'observer','actor':'test'}
        # The request handler uses a worker thread; this isolated in-memory
        # fixture explicitly permits that thread, unlike production connections.
        original=s.conn;s.conn=sqlite3.connect(':memory:',check_same_thread=False)
        original.backup(s.conn);s.conn.row_factory=sqlite3.Row;original.close()
        app=FastAPI();app.include_router(router(lambda:actor))
        try:
            with TestClient(app) as client,patch('agent_v3.service.Store',return_value=s),patch.object(s,'close'):
                self.assertEqual(client.post('/api/v3/settings/output',json={'max_tokens':9000}).status_code,403)
                actor['role']='operator'
                self.assertEqual(client.post('/api/v3/settings/output',json={'max_tokens':True}).status_code,400)
                self.assertEqual(s.output_setting(),16384)
                self.assertEqual(client.post('/api/v3/settings/output',json={'max_tokens':9000}).status_code,200)
                self.assertEqual(s.output_setting(),9000)
                rid=s.create_run('test');self.assertTrue(s.acquire(rid))
                self.assertEqual(client.post('/api/v3/settings/output',json={'max_tokens':10000}).status_code,400)
                self.assertEqual(s.output_setting(),9000)
        finally:s.close()

    def test_metadata_has_only_diagnostics_and_not_raw_response(self):
        result,_=self.invoke();result.update(reasoning_content=PRIVATE,_continuation={'reasoning_content':PRIVATE})
        meta=task_metadata(result)
        self.assertNotIn(PRIVATE,json.dumps(meta));self.assertNotIn('result',meta)
        self.assertEqual(meta['finish_reason'],'stop');self.assertEqual(meta['output_limit'],8192)

    def test_native_pipeline_uses_actual_provider_and_saves_no_reasoning(self):
        fixture=test_delivery.DeliveryTests();fixture.setUp()
        try:
            model=fixture.model();model.model=MODEL;model.transport='deepseek-api'
            original=model.run_task
            def task(*args,**kwargs):
                r=original(*args,**kwargs);r.update(transport=model.transport,session_id=None,reasoning_present=True,reasoning_effort='low',finish_reason='stop',output_limit=8192)
                return r
            model.run_task=task
            result=process(fixture.store,fixture.rid,model=model)
            self.assertEqual(result['status'],'completed')
            saved=fixture.store.get_run(fixture.rid)
            self.assertEqual(saved['result']['transport'],'deepseek-api')
            steps=[s for s in saved['steps'] if s['kind']=='model_task']
            self.assertEqual(len(steps),3);self.assertTrue(all(s['payload']['finish_reason']=='stop' for s in steps))
        finally:fixture.tearDown()

    def test_invalid_delivery_usage_is_retained_without_saving_intelligence(self):
        fixture=test_delivery.DeliveryTests();fixture.setUp()
        try:
            model=fixture.model();model.model=MODEL;model.transport='deepseek-api'
            def task(*args,**kwargs):
                raise StructuredDeliveryError('输出总预算耗尽（finish_reason=length）',
                    {'model':MODEL,'result':None,'usage':{'total_tokens':60,'reasoning_tokens':40},
                     'seconds':0.1,'transport':'deepseek-api','session_id':None,'finish_reason':'length'})
            model.run_task=task
            result=process(fixture.store,fixture.rid,model=model)
            self.assertEqual(result['status'],'ai_deferred')
            self.assertEqual(result['usage'],{'total_tokens':120,'reasoning_tokens':80})
            self.assertEqual(fixture.store.conn.execute('SELECT COUNT(*) FROM topic_intelligence').fetchone()[0],0)
        finally:fixture.tearDown()


class ContinuationTests(unittest.TestCase):
    def test_legacy_tool_response_preserves_private_reasoning_separately(self):
        data=reply(None,finish='tool_calls')
        data['choices'][0]['message']['tool_calls']=[{'id':'call1','type':'function','function':{'name':'read_evidence','arguments':'{"evidence_ids":[]}'}}]
        model=object.__new__(LiveModel);model.model=MODEL;model.router=SimpleNamespace(reasoning_effort='low')
        with patch('agent_v2.model.resolve_provider',return_value={'base_url':ENDPOINT}),patch('agent_v2.model.provider_key',return_value='test'),patch('agent_v2.model.requests.post',return_value=response(data)) as post:
            r=model._chat([], [{'type':'function'}])
        self.assertEqual(r['content'],'');self.assertEqual(r['tool_calls'][0]['name'],'read_evidence')
        self.assertEqual(assistant_history(r)['reasoning_content'],PRIVATE)
        self.assertNotIn('tool_choice',post.call_args.kwargs['json'])
        self.assertNotIn(PRIVATE,json.dumps(task_metadata(r)))

    def test_engine_only_replays_private_reasoning_and_never_persists_it(self):
        s=V2Store(':memory:');item=sample();s.upsert_evidence(item);s.conn.commit();eid=item['evidence_id'];rid=s.create_run('test')
        class Model:
            model=MODEL;calls=0
            def decide(self,messages,definitions):
                self.calls+=1
                if self.calls==2:
                    assert messages[-2]['reasoning_content']==PRIVATE
                    name,args='finish_research',final(eid)
                else:name,args='read_evidence',{'evidence_ids':[eid]}
                return {'model':MODEL,'content':'','tool_calls':[{'id':'call'+str(self.calls),'name':name,'arguments':args}],
                        '_continuation':{'reasoning_content':PRIVATE},'usage':{'total_tokens':10}}
        try:
            run_agent(s,rid,Model())
            self.assertEqual(s.get_run(rid)['status'],'completed')
            self.assertNotIn(PRIVATE,json.dumps(s.get_run(rid)))
        finally:s.close()


if __name__=='__main__':unittest.main()
