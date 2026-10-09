"""Actual SQLite concurrency/crash tests and identical offline provider contracts."""
import copy
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from agent_v2.store import now_iso
from agent_v3.store import Store
from agent_v3 import work
from agent_v3.discovery import scan,queue,read_topic
from agent_v3.runtime_guard import LeaseLost,StaleBasis,basis
from agent_v3.contracts import run_task,AgentContract
from agent_v3.task_packets import topic_packet
from agent_v3.main_agent import business_schema
from agent_v3.research_child import schema as research_schema
from agent_v3.tool_executor import ToolExecutor,history
from agent_v3.tests.test_v3 import source,approve_test_topic
from agent_v3.tests.test_deepseek import response as http_response,reply as deepseek_reply,PRIVATE
from agent_v3.tests.test_space_bunny import reply as bunny_reply
from agent_v3.tests.test_zen import Transport,response as zen_reply
from agent_v3.tests import test_main_agent as fixtures


def crash_worker(path,tid,after_commit):
    store=Store(path);rid=store.create_run('crash fixture');store.acquire(rid)
    job=work.claim(store,rid,'intelligence',topic_id=tid)
    expected=basis(store,tid,job['fingerprint'])
    with store.delivery(rid,expected,job=job,result={'topic_id':tid}):
        store.save_intelligence(tid,job['fingerprint'],job['prompt_version'],rid,{'summary':'isolated crash test'})
        store.conn.commit()  # must not commit half of the outer delivery
        if not after_commit:os._exit(19)
    os._exit(19)


def crash_tool_worker(path):
    store=Store(path);rid=store.create_run('crash tool fixture');store.acquire(rid)
    ToolExecutor(store,rid,limit=1).invoke('browse_page',{},lambda _:os._exit(19),maximum=1)


class RuntimeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=str(Path(self.temp.name)/'agent.sqlite3')
        self.s=Store(self.path);self.item=source();self.eid=self.item['evidence_id']
        self.s.upsert_evidence(self.item);self.s.conn.commit();scan(self.s)
        self.tid=queue(self.s)[0]['topic_id'];approve_test_topic(self.s,self.tid)
        work.enqueue(self.s);self.rid=self.s.create_run('runtime safety fixture')

    def tearDown(self):self.s.close();self.temp.cleanup()

    def claim(self):return work.claim(self.s,self.rid,'intelligence',topic_id=self.tid)

    def expire(self):
        with self.s.conn:
            self.s.conn.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00+00:00'")
            self.s.conn.execute("UPDATE work_item SET lease_until='2000-01-01T00:00:00+00:00' WHERE status='running'")

    def test_two_real_connections_only_one_can_claim(self):
        barrier=threading.Barrier(2)
        def worker():
            other=Store(self.path)
            try:
                rid=other.create_run('concurrent fixture');barrier.wait(timeout=10)
                return work.claim(other,rid,'intelligence',topic_id=self.tid)
            finally:other.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:worker(),range(2)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertTrue(next(r for r in results if r)['lease_token'])

    def test_expired_worker_cannot_reacquire_same_run_or_write(self):
        self.assertTrue(self.s.acquire(self.rid))
        other=Store(self.path)
        try:
            with other.conn:other.conn.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00+00:00'")
            self.assertFalse(self.s.acquire(self.rid))
            next_run=other.create_run('recovery');self.assertTrue(other.acquire(next_run))
            with self.assertRaises(LeaseLost):
                self.s.conn.execute("UPDATE evidence SET body='stale' WHERE evidence_id=?",(self.eid,))
            self.s.release(self.rid)
            self.assertEqual(other.active_owner(),next_run)
            self.assertNotEqual(other.evidence([self.eid])[0]['body'],'stale')
        finally:other.close()

    def test_reclaimed_job_rejects_old_completion_and_old_failure(self):
        first=self.claim();self.expire()
        other=Store(self.path)
        try:
            rid=other.create_run('replacement');second=work.claim(other,rid,'intelligence',topic_id=self.tid)
            self.assertNotEqual(first['lease_token'],second['lease_token'])
            for status in ('succeeded','deferred','failed','superseded'):
                with self.subTest(status=status),self.assertRaises(LeaseLost):work.finish(self.s,first,status)
            self.assertEqual(other.conn.execute('SELECT status FROM work_item WHERE job_id=?',(first['job_id'],)).fetchone()[0],'running')
        finally:other.close()

    def test_expired_claim_cannot_be_renewed(self):
        job=self.claim();self.expire()
        with self.assertRaises(LeaseLost):work.renew(self.s,job)

    def test_source_url_date_and_text_changes_block_delivery(self):
        for field,value in (('body','changed source text'),('url','https://example.test/changed'),('published_at','2000-01-01T00:00:00+00:00')):
            with self.subTest(field=field):
                expected=basis(self.s,self.tid,packet={'evidence':self.s.evidence([self.eid])});self.s.conn.commit()
                original=self.s.conn.execute('SELECT '+field+' FROM evidence WHERE evidence_id=?',(self.eid,)).fetchone()[0]
                with self.s.conn:self.s.conn.execute('UPDATE evidence SET '+field+'=? WHERE evidence_id=?',(value,self.eid))
                with self.assertRaises(StaleBasis):
                    with self.s.delivery(self.rid,expected):self.fail('stale delivery entered')
                with self.s.conn:self.s.conn.execute('UPDATE evidence SET '+field+'=? WHERE evidence_id=?',(original,self.eid))

    def test_changed_context_and_topic_revision_block_delivery(self):
        job=self.claim();expected=basis(self.s,self.tid)
        self.s.set_context({**self.s.context(),'goal':'different fixture business goal'})
        with self.assertRaises(StaleBasis):
            with self.s.delivery(self.rid,expected,job=job):pass
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM topic_intelligence').fetchone()[0],0)
        with self.s.conn:self.s.conn.execute("UPDATE topic SET fingerprint='new-version' WHERE topic_id=?",(self.tid,))
        with self.assertRaises(StaleBasis):work.assert_claim(self.s,job)

    def test_nested_commit_failure_rolls_back_all_artifacts_and_completion(self):
        job=self.claim();expected=basis(self.s,self.tid)
        with self.assertRaisesRegex(RuntimeError,'fixture failure'):
            with self.s.delivery(self.rid,expected,job=job,result={'topic_id':self.tid}):
                self.s.save_intelligence(self.tid,job['fingerprint'],job['prompt_version'],self.rid,{'summary':'must roll back'})
                with self.s.conn:self.s.conn.commit()
                raise RuntimeError('fixture failure')
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM topic_intelligence').fetchone()[0],0)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM delivery_receipt').fetchone()[0],0)
        self.assertEqual(self.s.conn.execute('SELECT status FROM work_item WHERE job_id=?',(job['job_id'],)).fetchone()[0],'running')

    def test_atomic_completion_is_idempotent(self):
        job=self.claim();value={'topic_id':self.tid}
        with self.s.delivery(self.rid,basis(self.s,self.tid),job=job,result=value):
            self.s.save_intelligence(self.tid,job['fingerprint'],job['prompt_version'],self.rid,{'summary':'one delivery'})
        self.assertFalse(work.finish(self.s,job,'succeeded',result=value))
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM delivery_receipt').fetchone()[0],1)
        self.assertIsNone(work.claim(self.s,self.rid,'intelligence',topic_id=self.tid))

    def test_expiry_during_delivery_rolls_back_before_commit(self):
        self.assertTrue(self.s.acquire(self.rid));job=self.claim()
        with patch('agent_v3.runtime_guard.now_iso',side_effect=[now_iso(),'9999-01-01T00:00:00+00:00']):
            with self.assertRaises(LeaseLost):
                with self.s.delivery(self.rid,basis(self.s,self.tid),job=job,result={}):
                    # Raw write simulates a nested helper. The commit fence must
                    # still reject it after the worker deadline passes.
                    import sqlite3
                    sqlite3.Connection.execute(self.s.conn,'INSERT INTO topic_intelligence VALUES(?,?,?,?,?,?)',
                        (self.tid,job['fingerprint'],job['prompt_version'],self.rid,now_iso(),'{}'))
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM topic_intelligence').fetchone()[0],0)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM delivery_receipt').fetchone()[0],0)

    def test_automatic_followup_refresh_keeps_both_receipts_and_old_replay_is_harmless(self):
        first=self.claim()
        with self.s.delivery(self.rid,basis(self.s,self.tid),job=first,result={}):
            self.s.save_intelligence(self.tid,first['fingerprint'],first['prompt_version'],self.rid,{'summary':'first'})
        # The existing automatic followup path deliberately refreshes a completed
        # judgment. It must preserve the receipt, and receive a fresh claim token.
        with self.s.conn:self.s.conn.execute("UPDATE work_item SET status='pending' WHERE job_id=?",(first['job_id'],))
        second=self.claim();self.assertNotEqual(first['lease_token'],second['lease_token'])
        self.assertFalse(work.finish(self.s,first,'succeeded',result={'stale':True}))
        with self.s.delivery(self.rid,basis(self.s,self.tid),job=second,result={}):
            self.s.save_intelligence(self.tid,second['fingerprint'],second['prompt_version'],self.rid,{'summary':'refreshed'})
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM delivery_receipt').fetchone()[0],2)
        self.assertEqual(self.s.intelligence(self.tid,second['fingerprint'])['payload']['summary'],'refreshed')

    def test_expired_and_changed_context_jobs_are_not_claimed(self):
        self.s.set_context({**self.s.context(),'goal':'changed business'})
        self.assertIsNone(self.claim())
        work.enqueue(self.s)
        with self.s.conn:self.s.conn.execute("UPDATE topic SET last_seen_at='2000-01-01T00:00:00+00:00'")
        self.assertIsNone(self.claim())

    def test_two_connections_share_reserved_tool_budget(self):
        barrier=threading.Barrier(2)
        def worker():
            other=Store(self.path)
            try:
                executor=ToolExecutor(other,self.rid,limit=1);barrier.wait(timeout=10)
                return executor.invoke('search_web',{},lambda units:{'calls':units,'status':'ok'},maximum=1)
            finally:other.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:worker(),range(2)))
        self.assertEqual(sum(r['calls'] for r in results),1)
        row=self.s.conn.execute('SELECT * FROM tool_budget WHERE run_id=?',(self.rid,)).fetchone()
        self.assertEqual((row['spent'],row['reserved']),(1,0))

    def test_crash_during_tool_leaves_reservation_and_interruption_trace(self):
        child=multiprocessing.get_context('spawn').Process(target=crash_tool_worker,args=(self.path,))
        child.start();child.join(timeout=20)
        if child.is_alive():child.terminate();child.join();self.fail('tool crash fixture did not exit')
        self.assertEqual(child.exitcode,19)
        row=self.s.conn.execute('SELECT * FROM tool_execution').fetchone()
        self.assertEqual(row['status'],'running')
        self.expire();self.assertTrue(self.s.acquire(self.rid))
        trace=history(self.s,row['run_id'])[0]
        self.assertEqual(trace['status'],'interrupted')
        self.assertEqual(trace['cost_status'],'unknown_after_interruption')
        self.assertEqual(ToolExecutor(self.s,row['run_id'],limit=100).remaining,0)

    def test_process_crash_before_commit_can_recover_without_half_delivery(self):
        self.crash(False)

    def test_process_crash_after_commit_does_not_repeat_delivery(self):
        self.crash(True)

    def crash(self,after):
        child=multiprocessing.get_context('spawn').Process(target=crash_worker,args=(self.path,self.tid,after))
        child.start();child.join(timeout=20)
        if child.is_alive():child.terminate();child.join();self.fail('crash fixture did not exit')
        self.assertEqual(child.exitcode,19)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM topic_intelligence').fetchone()[0],int(after))
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM delivery_receipt').fetchone()[0],int(after))
        self.expire();rid=self.s.create_run('automatic recovery');self.assertTrue(self.s.acquire(rid))
        job=work.claim(self.s,rid,'intelligence',topic_id=self.tid)
        if after:self.assertIsNone(job)
        else:
            with self.s.delivery(rid,basis(self.s,self.tid),job=job,result={'topic_id':self.tid}):
                self.s.save_intelligence(self.tid,job['fingerprint'],job['prompt_version'],rid,{'summary':'recovered once'})
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM topic_intelligence').fetchone()[0],1)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM delivery_receipt').fetchone()[0],1)


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MainAgentTests();self.f.setUp()
        self.packet=topic_packet(read_topic(self.f.store,self.f.tid),self.f.store.context())

    def tearDown(self):self.f.tearDown()

    def invoke(self,provider,stage,value,schema):
        if provider=='zen':
            from agent_v3.opencode_zen import OpenCodeModel
            reply=zen_reply();reply['info']['structured']=value
            model=OpenCodeModel(runtime=Transport(reply=reply))
            return run_task(model,stage,self.packet,schema,'offline contract fixture')
        if provider=='deepseek':
            from agent_v3.deepseek import DeepSeekModel
            data=deepseek_reply(json.dumps(value,ensure_ascii=False))
            key='agent_v3.deepseek'
            constructor=DeepSeekModel
        else:
            from agent_v3.space_bunny import SpaceBunnyModel
            data=bunny_reply();data['choices'][0]['message'].update(content=json.dumps(value,ensure_ascii=False),reasoning_content=PRIVATE)
            key='agent_v3.space_bunny';constructor=SpaceBunnyModel
        with patch(key+'.api_key',return_value='isolated-test-key'),patch(key+'.requests.post',return_value=http_response(data)):
            return run_task(constructor(),stage,self.packet,schema,'offline contract fixture')

    def test_three_real_adapters_pass_the_same_main_and_child_contracts(self):
        model=self.f.model()
        for stage,schema in (('intelligence',business_schema(self.packet)),('interpretation',research_schema(self.packet))):
            value=model.run_task(stage,self.packet,schema,'fixture')['result']
            fingerprints=[]
            for provider in ('deepseek','bunny','zen'):
                with self.subTest(stage=stage,provider=provider):
                    result=self.invoke(provider,stage,value,schema)
                    self.assertEqual(result['result'],value)
                    self.assertEqual(result['agent_role'],'research_child' if stage=='interpretation' else 'business_main')
                    self.assertNotIn(PRIVATE,json.dumps(result))
                    fingerprints.append(result['input_fingerprint'])
            self.assertEqual(len(set(fingerprints)),1)

    def test_all_adapters_reject_missing_fields_and_invented_evidence_refs(self):
        schema=business_schema(self.packet);good=self.f.model().run_task('intelligence',self.packet,schema,'fixture')['result']
        from agent_v3.opencode_zen import StructuredDeliveryError
        for provider in ('deepseek','bunny','zen'):
            for change in ('missing','bad_ref'):
                value=copy.deepcopy(good)
                if change=='missing':value.pop('opportunity')
                else:value['fact_refs']=[9999]
                with self.subTest(provider=provider,change=change),self.assertRaises(StructuredDeliveryError):self.invoke(provider,'intelligence',value,schema)

    def test_chat_adapter_uses_only_final_content_and_same_contract(self):
        value={'ready':True};schema={'type':'object','properties':{'ready':{'const':True}},'required':['ready'],'additionalProperties':False}
        class Chat:
            model='test-chat'
            def decide(self,messages,tools):
                return {'content':json.dumps(value),'reasoning_content':PRIVATE,'_continuation':{'reasoning_content':PRIVATE},'usage':{'total_tokens':3},'finish_reason':'stop'}
        result=run_task(Chat(),'readiness',{},schema,'fixture')
        self.assertEqual(result['result'],value);self.assertNotIn(PRIVATE,json.dumps(result))
        with self.assertRaises(ValueError):AgentContract.create('unregistered',{},schema)


class ToolExecutorTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');item=source();self.eid=item['evidence_id'];self.s.upsert_evidence(item);self.s.conn.commit()
        self.rid=self.s.create_run('tool ledger fixture')

    def tearDown(self):self.s.close()

    def test_budget_is_reserved_before_network_and_persisted_between_executors(self):
        executor=ToolExecutor(self.s,self.rid,limit=2)
        def call(units):
            row=self.s.conn.execute('SELECT reserved FROM tool_budget').fetchone()
            self.assertEqual(row[0],2)
            return {'calls':units,'status':'ok','evidence_ids':[self.eid]}
        result=executor.invoke('search_web',{'evidence_id':self.eid},call,maximum=3)
        self.assertEqual(result['calls'],2)
        second=ToolExecutor(self.s,self.rid,limit=100)
        self.assertEqual(second.remaining,0)
        blocked=second.invoke('search_web',{},lambda units:self.fail('network called after exhaustion'),maximum=1)
        self.assertEqual(blocked['status'],'budget_exhausted')
        trace=history(self.s,self.rid)[0]
        self.assertEqual(trace['charged_units'],2);self.assertEqual(trace['sources'][0]['url'],'https://example.test/source')
        self.assertIsNone(trace['monetary_cost']);self.assertEqual(trace['status'],'completed')

    def test_cached_tool_releases_unused_reservation(self):
        e=ToolExecutor(self.s,self.rid,limit=2)
        e.invoke('browse_page',{},lambda _: {'calls':0,'status':'cached'},maximum=1)
        self.assertEqual(e.remaining,2)

    def test_exception_is_traced_and_charged_conservatively(self):
        e=ToolExecutor(self.s,self.rid,limit=2)
        def fail(_):raise TimeoutError('private provider payload must not be logged')
        result=e.invoke('browse_page',{},fail,maximum=1)
        self.assertEqual(result['status'],'failed');self.assertEqual(e.remaining,1)
        trace=history(self.s,self.rid)[0]
        self.assertEqual(trace['error'],'TimeoutError');self.assertEqual(trace['charged_units'],1)
        self.assertNotIn('private provider payload',json.dumps(trace))

    def test_reported_failure_and_malformed_source_results_are_audited(self):
        e=ToolExecutor(self.s,self.rid,limit=3)
        e.invoke('browse_page',{},lambda _: {'calls':1,'status':'failed','error':'private response'},maximum=1)
        self.assertEqual(history(self.s,self.rid)[0]['status'],'failed')
        e.invoke('search_web',{},lambda _: {'calls':1,'evidence_ids':{'invalid':'shape'}},maximum=1)
        self.assertEqual(e.remaining,1)
        self.assertEqual(history(self.s,self.rid)[1]['error'],'ValueError')

    def test_tool_cannot_run_network_inside_delivery_transaction(self):
        e=ToolExecutor(self.s,self.rid,limit=1)
        with self.s.delivery(self.rid),self.assertRaises(RuntimeError):
            e.invoke('browse_page',{},lambda _: self.fail('network under SQLite lock'),maximum=1)
        self.assertEqual(e.remaining,1)

    def test_stale_tool_result_is_audited_but_cannot_write_evidence(self):
        self.assertTrue(self.s.acquire(self.rid));e=ToolExecutor(self.s,self.rid,limit=1)
        def tool(_):
            # Simulate a timeout before the tool starts committing its result.
            import sqlite3
            sqlite3.Connection.execute(self.s.conn,"UPDATE worker_lease SET expires_at='2000-01-01T00:00:00+00:00'")
            sqlite3.Connection.commit(self.s.conn)
            self.s.conn.execute("UPDATE evidence SET body='stale tool response' WHERE evidence_id=?",(self.eid,))
        with self.assertRaises(LeaseLost):e.invoke('browse_page',{'evidence_id':self.eid},tool,maximum=1)
        self.assertNotEqual(self.s.evidence([self.eid])[0]['body'],'stale tool response')
        self.assertEqual(history(self.s,self.rid)[0]['status'],'stale')
        self.assertEqual(e.remaining,0)

    def test_local_tools_have_trace_without_network_cost(self):
        e=ToolExecutor(self.s,self.rid,limit=0)
        e.invoke('read_evidence',{'evidence_id':self.eid},lambda _: {'evidence_ids':[self.eid]})
        trace=self.s.get_run(self.rid)['tool_calls'][0]
        self.assertEqual(trace['charged_units'],0);self.assertTrue(trace['sources'])

    def test_unknown_research_tool_cannot_dispatch(self):
        with self.assertRaises(ValueError):ToolExecutor(self.s,self.rid).execute('arbitrary_shell',{'topic_id':'fixture'})
