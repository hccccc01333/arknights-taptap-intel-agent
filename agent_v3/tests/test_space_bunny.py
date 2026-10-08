"""Credential isolation, JSON task delivery and honest cost/transport reporting."""
import json
import unittest
from unittest.mock import Mock,patch

from L4_intelligence.intelligence.llm import LLMUnavailable
from agent_v3.space_bunny import SpaceBunnyModel,MODEL,API_MODEL,ENDPOINT,api_key
from agent_v3.opencode_zen import StructuredDeliveryError
from agent_v3.store import Store
from agent_v3.model import GatedModel,gate,failure,set_zen_quota,task_metadata
from agent_v3.tests import test_delivery
from agent_v3.pipeline import process

SCHEMA={"type":"object","properties":{"ready":{"const":True}},"required":["ready"],"additionalProperties":False}


def reply():
    return {"id":"chatcmpl_test","model":API_MODEL,"choices":[{"finish_reason":"stop",
        "message":{"content":'{"ready":true}'}}],
        "usage":{"prompt_tokens":12,"completion_tokens":20,"total_tokens":32,
                 "completion_tokens_details":{"reasoning_tokens":15}}}


class BunnyTests(unittest.TestCase):
    def invoke(self,value=None,*,code=200,effort="low",clock=None):
        response=Mock(status_code=code)
        response.iter_content.return_value=iter([json.dumps(reply() if value is None else value).encode()])
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch('agent_v3.space_bunny.api_key',return_value='private-test-key'),patch('agent_v3.space_bunny.requests.post',return_value=response) as post:
            model=SpaceBunnyModel(reasoning_effort=effort)
            if clock:
                with patch('agent_v3.space_bunny.time.monotonic',side_effect=clock):result=model.run_task('test',{},SCHEMA,'check')
            else:result=model.run_task('test',{},SCHEMA,'check')
            return result,post.call_args

    def test_reads_process_key_before_any_registry_fallback(self):
        with patch.dict('os.environ',{'space_bunney_free_api_key':'private-process-key'}):
            self.assertEqual(api_key(),'private-process-key')

    def test_fixed_endpoint_model_json_mode_and_no_paid_fallback(self):
        result,call=self.invoke()
        self.assertEqual(call.args[0],ENDPOINT)
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertEqual(call.kwargs['json']['model'],API_MODEL)
        self.assertEqual(call.kwargs['json']['response_format'],{'type':'json_object'})
        self.assertEqual(call.kwargs['json']['reasoning'],{'effort':'low'})
        self.assertNotIn('private-test-key',json.dumps(call.kwargs['json']))
        self.assertEqual(result['transport'],'space-bunny-api');self.assertIsNone(result['session_id'])
        self.assertEqual(result['request_id'],'chatcmpl_test')
        self.assertEqual(result['usage']['total_tokens'],32)
        self.assertEqual(result['cost_status'],'unknown');self.assertIsNone(result['reported_cost'])

    def test_reasoning_default_and_max_use_documented_options(self):
        _,call=self.invoke(effort='default');self.assertNotIn('reasoning',call.kwargs['json'])
        _,call=self.invoke(effort='max');self.assertEqual(call.kwargs['json']['reasoning'],{'effort':'max'})

    def test_invalid_json_and_schema_are_delivery_failures(self):
        for content in ('not JSON','[]','{"wrong":true}'):
            value=reply();value['choices'][0]['message']['content']=content
            with self.subTest(content=content),self.assertRaises(StructuredDeliveryError):self.invoke(value)

    def test_wrong_model_truncation_and_nonzero_cost_stop_delivery(self):
        for field,value in (('model','different-model'),('finish','length'),('cost',1),('cost','unknown')):
            data=reply()
            if field=='model':data['model']=value
            elif field=='finish':data['choices'][0]['finish_reason']=value
            else:data['usage']['cost']=value
            with self.subTest(field=field,value=value),self.assertRaises(LLMUnavailable):self.invoke(data)
        data=reply();data['usage']['cost']=0
        self.assertEqual(self.invoke(data)[0]['cost_status'],'reported_zero')

    def test_gateway_failure_is_sanitized_and_not_retried_inside_adapter(self):
        with self.assertRaisesRegex(LLMUnavailable,'HTTP 502') as caught:self.invoke({'error':'private-test-key'},code=502)
        self.assertNotIn('private-test-key',str(caught.exception))
        with self.assertRaisesRegex(LLMUnavailable,'时间预算'):self.invoke(clock=[0,181])

    def test_bunny_credentials_reasoning_and_gate_are_independent_of_zen(self):
        s=Store(':memory:')
        try:
            with patch('agent_v3.space_bunny.api_key',return_value='private-test-key'):s.set_model(MODEL)
            s.set_reasoning('xhigh');set_zen_quota(s,True)
            self.assertEqual(gate(s,MODEL)['status'],'ready')
            with patch('agent_v3.model.SpaceBunnyModel') as bunny,patch('agent_v3.model.OpenCodeModel') as zen:
                bunny.return_value.model=MODEL;GatedModel(s)
                bunny.assert_called_once_with(reasoning_effort='xhigh');zen.assert_not_called()
            failure(s,MODEL,'HTTP 429')
            self.assertEqual(gate(s,MODEL)['status'],'deferred')
            self.assertEqual(gate(s,'deepseek-chat')['status'],'ready')
            with patch('agent_v3.space_bunny.api_key',return_value=None),self.assertRaises(ValueError):s.set_model(MODEL)
            with self.assertRaises(ValueError):s.set_model('spacebunny/paid-model')
        finally:s.close()

    def test_whole_growth_pipeline_records_actual_provider_and_cost_status(self):
        fixture=test_delivery.DeliveryTests();fixture.setUp()
        try:
            model=fixture.model();model.model=MODEL;model.transport='space-bunny-api'
            original=model.run_task
            def task(*args,**kwargs):
                r=original(*args,**kwargs);r.update(transport=model.transport,session_id=None,
                    request_id='chatcmpl_test',reported_cost=None,cost_status='unknown');return r
            model.run_task=task
            result=process(fixture.store,fixture.rid,model=model)
            self.assertEqual(result['status'],'completed')
            saved=fixture.store.get_run(fixture.rid)
            self.assertEqual(saved['result']['transport'],'space-bunny-api')
            steps=[x for x in saved['steps'] if x['kind']=='model_task']
            self.assertEqual(len(steps),3)
            self.assertTrue(all(x['payload']['cost_status']=='unknown' and x['payload']['session_id'] is None for x in steps))
            self.assertNotIn('result',task_metadata({'result':{'private':'large output'}}))
        finally:fixture.tearDown()


if __name__=='__main__':unittest.main()
