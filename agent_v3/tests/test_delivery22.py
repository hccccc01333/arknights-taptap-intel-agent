"""Independent delivery, bounded repair, crash recovery and stale rejection."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from agent_v2.store import dump,now_iso
from agent_v3 import graph_ai
from agent_v3.pipeline import process
from agent_v3.research_child import interpret
from agent_v3.runtime_guard import LeaseLost,StaleBasis
from agent_v3.service import has_automatic_work
from agent_v3.tests import test_main_agent as fixtures


class Delivery22Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MainAgentTests();self.f.setUp();self.s=self.f.store;self.fp=self.s.conn.execute("SELECT fingerprint FROM topic WHERE topic_id=?",(self.f.tid,)).fetchone()[0]
    def tearDown(self):self.f.tearDown()

    def model(self,repair=False):
        m=self.f.model();original=m.run_task;self.graph_packets=[]
        def run(stage,packet,schema,system,**kwargs):
            result=original(stage,packet,schema,system,**kwargs)
            if stage=='graph_extraction':
                self.assertIsNotNone(self.s.interpretation(self.f.tid,self.fp))
                self.graph_packets.append(copy.deepcopy(packet))
                if not repair or len(self.graph_packets)==1:
                    result['result']['entities']=[{'surface':'未出现的实体','canonical_name':'未出现的实体','kind':'GAME','basis_refs':[1]}]
            return result
        m.run_task=run;return m

    def make_due(self,**changes):
        row=self.s.interpretation(self.f.tid,self.fp);p=row['payload']
        p['graph_extraction'].update(retry_at='2000-01-01T00:00:00+00:00',**changes)
        with self.s.delivery(self.f.rid):
            self.s.conn.execute('UPDATE topic_interpretation SET payload=? WHERE topic_id=?',(dump(p),self.f.tid))

    def test_invalid_graph_does_not_block_game_intelligence_or_fabricate_extraction(self):
        m=self.model();result=process(self.s,self.f.rid,model=m,topic_id=self.f.tid)
        self.assertEqual(result['status'],'intelligence_ready');self.assertEqual(len(self.s.game_signals()),1)
        p=self.s.interpretation(self.f.tid,self.fp)['payload']
        self.assertEqual(p['status'],'ready');self.assertEqual(p['graph_extraction']['status'],'deferred')
        self.assertNotIn('knowledge',p);self.assertEqual(m.calls.count('interpretation'),1)
        self.assertEqual(m.calls.count('graph_extraction'),2)
        self.assertEqual(self.s.conn.execute("SELECT COUNT(*) FROM kg_entity WHERE origin='proposed'").fetchone()[0],0)
        self.assertNotIn('未出现的实体',dump(p['graph_extraction']))

    def test_invalid_core_cannot_hide_behind_optional_graph_delivery(self):
        m=self.f.model();m.bad_view=True
        with self.assertRaises(ValueError):interpret(self.s,self.f.rid,m,self.f.tid)
        self.assertIsNone(self.s.interpretation(self.f.tid,self.fp));self.assertNotIn('graph_extraction',m.calls)

    def test_repair_only_calls_graph_and_never_rewrites_core_or_risk(self):
        m=self.model(repair=True);first=interpret(self.s,self.f.rid,m,self.f.tid)
        self.assertEqual(first['payload']['graph_extraction']['status'],'succeeded')
        again=interpret(self.s,self.f.rid,m,self.f.tid)
        self.assertEqual(first,again);self.assertEqual(m.calls,['interpretation','graph_extraction','graph_extraction'])
        self.assertEqual(graph_ai.due(self.s),[])

    def test_delayed_repair_has_fixed_ceiling_and_wakes_automatic_scheduler(self):
        m=self.model();process(self.s,self.f.rid,model=m,topic_id=self.f.tid)
        self.f.rid=self.s.create_run('next automatic repair cycle');self.assertTrue(self.s.acquire(self.f.rid))
        self.assertEqual(graph_ai.due(self.s),[]);self.make_due()
        self.assertEqual(graph_ai.due(self.s),[self.f.tid]);self.assertTrue(has_automatic_work(self.s))
        self.assertEqual(len(graph_ai.repair_due(self.s,self.f.rid,m)),1)
        self.assertEqual(self.s.interpretation(self.f.tid,self.fp)['payload']['graph_extraction']['status'],'exhausted')
        self.assertEqual(graph_ai.due(self.s),[])
        graph_ai.repair_due(self.s,self.f.rid,m);interpret(self.s,self.f.rid,m,self.f.tid)
        self.assertEqual(m.calls.count('graph_extraction'),4);self.assertEqual(m.calls.count('intelligence'),1)

    def test_delayed_repair_can_succeed_and_preserves_original_interpretation(self):
        bad=self.model();first=interpret(self.s,self.f.rid,bad,self.f.tid);self.make_due()
        good=self.f.model();graph_ai.repair_due(self.s,self.f.rid,good)
        saved=self.s.interpretation(self.f.tid,self.fp)
        self.assertEqual(good.calls,['graph_extraction']);self.assertEqual(saved['created_at'],first['created_at'])
        self.assertEqual(saved['payload']['core'],first['payload']['core'])
        self.assertEqual(saved['payload']['graph_extraction']['status'],'succeeded')
        self.assertNotIn('error',saved['payload']['graph_extraction'])

    def test_crash_after_reservation_keeps_core_and_consumes_round(self):
        m=self.f.model();original=m.run_task
        def crash(stage,*args,**kwargs):
            if stage=='graph_extraction':raise SystemExit('fixture interruption')
            return original(stage,*args,**kwargs)
        m.run_task=crash
        with self.assertRaises(SystemExit):interpret(self.s,self.f.rid,m,self.f.tid)
        p=self.s.interpretation(self.f.tid,self.fp)['payload']
        self.assertEqual(p['graph_extraction']['rounds'],1);self.assertEqual(p['status'],'ready')
        self.assertEqual(graph_ai.due(self.s),[]);self.make_due()
        graph_ai.repair_due(self.s,self.f.rid,self.f.model())
        self.assertEqual(self.s.interpretation(self.f.tid,self.fp)['payload']['graph_extraction']['rounds'],2)

    def test_last_round_crash_closes_without_another_model_call(self):
        interpret(self.s,self.f.rid,self.model(),self.f.tid)
        self.make_due(status='pending',rounds=2)
        m=self.f.model();graph_ai.repair_due(self.s,self.f.rid,m)
        self.assertEqual(m.calls,[])
        self.assertEqual(self.s.interpretation(self.f.tid,self.fp)['payload']['graph_extraction']['status'],'exhausted')

    def test_source_date_change_rejects_graph_result_and_preserves_core(self):
        m=self.f.model();original=m.run_task
        def change(stage,*args,**kwargs):
            r=original(stage,*args,**kwargs)
            if stage=='graph_extraction':
                self.s.conn.execute("UPDATE evidence SET published_at='2001-01-01T00:00:00+00:00'");self.s.conn.commit()
            return r
        m.run_task=change
        with self.assertRaises(StaleBasis):interpret(self.s,self.f.rid,m,self.f.tid)
        self.assertEqual(self.s.interpretation(self.f.tid,self.fp)['payload']['status'],'ready')
        self.assertNotIn('knowledge',self.s.interpretation(self.f.tid,self.fp)['payload']);self.assertEqual(graph_ai.due(self.s),[])

    def test_expired_worker_cannot_commit_graph_result(self):
        self.assertTrue(self.s.acquire(self.f.rid));m=self.f.model();original=m.run_task
        def expire(stage,*args,**kwargs):
            result=original(stage,*args,**kwargs)
            if stage=='graph_extraction':
                sqlite3.Connection.execute(self.s.conn,"UPDATE worker_lease SET expires_at='2000-01-01T00:00:00+00:00'")
                sqlite3.Connection.commit(self.s.conn)
            return result
        m.run_task=expire
        with self.assertRaises(LeaseLost):interpret(self.s,self.f.rid,m,self.f.tid)
        self.assertNotIn('knowledge',self.s.interpretation(self.f.tid,self.fp)['payload'])

    def test_competing_revision_is_not_overwritten(self):
        m=self.f.model();original=m.run_task
        def change(stage,*args,**kwargs):
            r=original(stage,*args,**kwargs)
            if stage=='graph_extraction':
                p=self.s.interpretation(self.f.tid,self.fp)['payload'];p['one_line']='新版解读已由另一交付更新，旧图谱不能覆盖。'
                with self.s.delivery(self.f.rid):self.s.conn.execute('UPDATE topic_interpretation SET payload=?',(dump(p),))
            return r
        m.run_task=change
        with self.assertRaises(StaleBasis):interpret(self.s,self.f.rid,m,self.f.tid)
        self.assertIn('新版解读',self.s.interpretation(self.f.tid,self.fp)['payload']['one_line'])

    def test_negative_interpretation_keeps_risk_gate_even_when_graph_fails(self):
        m=self.model();original=m.run_task
        def negative(stage,*args,**kwargs):
            r=original(stage,*args,**kwargs)
            if stage=='interpretation':r['result']['risk_assessment'].update(polarity='negative',level='medium')
            if stage=='intelligence':
                r['result']['patterns']=[];r['result']['opportunity']['prerequisites']=[]
                for key in ('audience_need','taptap_bridge','growth_goal','hypothesis','validation_plan'):r['result']['opportunity'][key]=''
            return r
        m.run_task=negative;result=process(self.s,self.f.rid,model=m,topic_id=self.f.tid)
        self.assertEqual(result['status'],'intelligence_ready');self.assertEqual(len(self.s.game_signals()),1)
        self.assertFalse(self.s.usable_materials());self.assertNotIn('creative_plan',m.calls)
        self.assertEqual(self.s.interpretation(self.f.tid,self.fp)['payload']['graph_extraction']['status'],'deferred')

    def test_public_projection_exposes_outcome_without_private_diagnostics(self):
        from agent_v3.public_site import interpretation
        saved=interpret(self.s,self.f.rid,self.model(),self.f.tid)
        projected=interpretation(saved)['payload']['graph_extraction']
        self.assertEqual(projected,{'status':'deferred','rounds':1})
        self.assertNotIn('knowledge',interpretation(saved)['payload'])


if __name__=='__main__':unittest.main()
