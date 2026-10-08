"""Automatic dispatch, research closure and product delivery boundaries."""
import copy,json,unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from agent_v2.store import dump,now_iso,stable_id
from agent_v3.store import Store
from agent_v3 import followups,retention
from agent_v3.service import scheduler_tick,unscreened_count,has_automatic_work
from agent_v3.main_agent import plan
from agent_v3.discovery import scan,queue,read_topic
from agent_v3.research_child import interpret
from agent_v3.presentation import classify_outputs,material_ready
from agent_v3.tests import test_main_agent as main_fixtures
from agent_v3.tests.test_v3 import source

class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=main_fixtures.MainAgentTests();self.fixture.setUp()
        self.s=self.fixture.store;self.tid=self.fixture.tid;self.rid=self.fixture.rid
        self.model=self.fixture.model()
    def tearDown(self):self.fixture.tearDown()
    def config(self,key,value):
        with self.s.conn:self.s.conn.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,dump(value)))
    def ready(self):return patch('agent_v3.service.gate',return_value={'status':'ready'})
    def test_product_profile_distinguishes_mobile_pc_and_resources(self):
        p=self.s.context()['product_profile']
        self.assertIn('mobile',p['platforms']);self.assertIn('pc',p['platforms'])
        self.assertTrue(any('Steam' in s for s in p['constraints']))
        self.assertTrue(any('诱导好评' in s for s in p['constraints']))
        self.assertNotIn('真实已上线专题',p['capabilities'])
    def test_screening_moves_window_without_deep_research(self):
        for i in range(45):
            item=source('新游戏玩家讨论关卡'+str(i));item['evidence_id']='screen_'+str(i);item['published_at']=now_iso()
            self.s.upsert_evidence(item)
        self.s.conn.commit();scan(self.s)
        before=unscreened_count(self.s)
        plan(self.s,self.rid,self.model,screen_limit=40,screen_only=True)
        middle=unscreened_count(self.s)
        plan(self.s,self.rid,self.model,screen_limit=40,screen_only=True)
        self.assertEqual(before-middle,40);self.assertEqual(unscreened_count(self.s),0)
        self.assertEqual(self.model.calls,['main_plan','main_plan'])
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM topic_interpretation').fetchone()[0],0)
    def test_rejected_candidate_not_revived_by_same_collection(self):
        self.model.route='unrelated';self.model.action='archive'
        plan(self.s,self.rid,self.model,self.tid);self.assertFalse(queue(self.s))
        scan(self.s);self.assertFalse(queue(self.s))
        self.assertTrue(self.s.evidence([self.fixture.item['evidence_id']]))
        self.s.upsert_evidence({**self.fixture.item,'body':self.fixture.item['body']+'新增游戏更新与玩家讨论。'})
        self.s.conn.commit();scan(self.s);self.assertTrue(queue(self.s))
    def test_scheduler_runs_collection_without_browser(self):
        self.config('schedule',{'enabled':True,'research':True,'interval_minutes':60,'next_run_at':'2000-01-01'})
        calls=[]
        self.assertEqual(scheduler_tick(self.s,cycle_dispatch=lambda **kw:calls.append(kw)), 'collection')
        self.assertEqual(calls,[{'research':True,'live':True}])
    def test_screening_independent_of_deep_cadence(self):
        self.config('schedule',{'enabled':True,'research':True,'interval_minutes':60,'next_run_at':'2099-01-01'})
        self.config('automatic_work',{'interval_minutes':3,'next_run_at':'2099-01-01'})
        calls=[]
        with self.ready():
            self.assertEqual(scheduler_tick(self.s,screen_dispatch=lambda:calls.append('screen')),'screening')
        self.assertEqual(calls,['screen'])
    def test_long_research_cannot_starve_screening(self):
        self.config('schedule',{'enabled':True,'research':True,'interval_minutes':60,'next_run_at':'2099-01-01'})
        self.config('automatic_work',{'interval_minutes':3,'next_run_at':'2000-01-01'})
        self.config('last_automatic_kind','research');calls=[]
        with self.ready():
            self.assertEqual(scheduler_tick(self.s,screen_dispatch=lambda:calls.append('screen')),'screening')
        self.assertEqual(calls,['screen'])
    def test_lease_and_provider_gate_prevent_parallel_or_futile_dispatch(self):
        self.config('schedule',{'enabled':True,'research':True,'interval_minutes':60,'next_run_at':'2099-01-01'})
        self.s.acquire(self.rid)
        with self.ready():self.assertEqual(scheduler_tick(self.s),'idle')
        self.s.release(self.rid)
        with patch('agent_v3.service.gate',return_value={'status':'deferred'}):self.assertEqual(scheduler_tick(self.s),'waiting_provider')
    def followup(self):
        fp=read_topic(self.s,self.tid)['fingerprint']
        ids=followups.enqueue(self.s,self.tid,fp,self.rid,[{'question':'实际玩家评论说了什么？','query':'游戏评论','tool':'sample_discussion'}])
        with self.s.conn:self.s.conn.execute("UPDATE research_followup SET retry_at='2000-01-01'")
        return ids
    def test_unknowns_become_durable_bounded_followups(self):
        ids=self.followup();self.followup()
        self.assertEqual(len(followups.read(self.s,self.tid)),1)
        mission=followups.missions(self.s,self.rid)[0]
        self.assertEqual(mission['followup_ids'],ids)
        followups.settle(self.s,mission,self.rid)
        f=followups.read(self.s,self.tid)[0]
        self.assertEqual(f['status'],'deferred');self.assertEqual(f['attempts'],1)
        self.assertFalse(followups.due(self.s))
        self.assertTrue(f['payload']['history'])
    def test_three_failures_wait_new_evidence_not_infinite_loop(self):
        self.followup()
        for _ in range(3):
            with self.s.conn:self.s.conn.execute("UPDATE research_followup SET retry_at='2000-01-01'")
            mission=followups.missions(self.s,self.rid)[0];followups.settle(self.s,mission,self.rid)
        self.assertEqual(followups.read(self.s,self.tid)[0]['status'],'awaiting_source')
        self.assertFalse(followups.due(self.s))
        self.s.upsert_evidence({**self.fixture.item,'body':self.fixture.item['body']+'后续补充实际细节。'})
        self.s.conn.commit();scan(self.s);followups.wake_changed(self.s)
        self.assertEqual(followups.read(self.s,self.tid)[0]['attempts'],0)
        self.assertTrue(followups.due(self.s))
    def test_statistical_limitations_are_not_search_tasks(self):
        fp=read_topic(self.s,self.tid)['fingerprint']
        followups.enqueue(self.s,self.tid,fp,self.rid,[],['未确认总体玩家情绪比例','尚未确认增长效果','授权与预算未确认'])
        self.assertFalse(followups.read(self.s,self.tid))
    def test_paraphrased_gaps_cannot_expand_queue_forever(self):
        fp=read_topic(self.s,self.tid)['fingerprint']
        for i in range(10):followups.enqueue(self.s,self.tid,fp,self.rid,[{'question':'来源具体情况待核查'+str(i),'query':'来源','tool':'search_web'}])
        self.assertEqual(len(followups.read(self.s,self.tid)),2)
    def test_pending_gaps_follow_new_version_without_spawning_more(self):
        self.followup()
        self.s.upsert_evidence({**self.fixture.item,'body':self.fixture.item['body']+'新的内容更新。'})
        self.s.conn.commit();scan(self.s);followups.wake_changed(self.s)
        fp=read_topic(self.s,self.tid)['fingerprint']
        followups.enqueue(self.s,self.tid,fp,self.rid,[{'question':'其他待查的时间问题','query':'日期','tool':'search_web'}])
        followups.enqueue(self.s,self.tid,fp,self.rid,[{'question':'第三种评论问题表述','query':'评论','tool':'sample_discussion'}])
        active=[r for r in followups.read(self.s,self.tid) if r['status'] not in ('resolved','superseded')]
        self.assertEqual(len(active),2);self.assertTrue(all(r['fingerprint']==fp for r in active))
    def test_comment_rewording_keeps_space_for_mechanism_question(self):
        self.followup();fp=read_topic(self.s,self.tid)['fingerprint']
        followups.enqueue(self.s,self.tid,fp,self.rid,[{'question':'评论中还讨论什么？','query':'评论','tool':'read_comments_visual'}])
        followups.enqueue(self.s,self.tid,fp,self.rid,[{'question':'组队机制有没有官方说明？','query':'官方组队机制','tool':'search_web'}])
        rows=followups.read(self.s,self.tid);self.assertEqual(len(rows),2)
        self.assertEqual({r['tool'] for r in rows},{'sample_discussion','search_web'})
    def test_research_answer_resolves_with_grounded_evidence(self):
        fp=read_topic(self.s,self.tid)['fingerprint']
        ids=followups.enqueue(self.s,self.tid,fp,self.rid,[{'question':'来源是否公开展示地图分享？','query':'地图分享','tool':'search_web'}])
        with self.s.conn:self.s.conn.execute("UPDATE research_followup SET retry_at='2000-01-01'")
        mission=followups.missions(self.s,self.rid)[0];base=self.model
        class Answer:
            model='test';last_session=None
            def run_task(inner,stage,packet,schema,system,**kw):
                response=base.run_task(stage,packet,schema,system,**kw)
                response['result']['followup_answers']=[{'followup_id':ids[0],'status':'resolved','reason':'直接来源明确说明地图分享和组队体验','basis_refs':[1]}]
                return response
        interpret(self.s,self.rid,Answer(),self.tid,mission)
        f=followups.read(self.s,self.tid)[0];self.assertEqual(f['status'],'resolved')
        self.assertTrue(f['payload']['resolution']['facts'])
        self.assertFalse(followups.due(self.s))
    def test_comment_task_cannot_be_resolved_by_article_quote(self):
        ids=self.followup();mission=followups.missions(self.s,self.rid)[0];base=self.model
        class Bad:
            model='test';last_session=None
            def run_task(inner,stage,packet,schema,system,**kw):
                response=base.run_task(stage,packet,schema,system,**kw)
                response['result']['followup_answers']=[{'followup_id':ids[0],'status':'resolved','reason':'错误用正文声称评论已核实','basis_refs':[1]}]
                return response
        with self.assertRaisesRegex(ValueError,'评论补查'):interpret(self.s,self.rid,Bad(),self.tid,mission)
        self.assertNotEqual(followups.read(self.s,self.tid)[0]['status'],'resolved')
    def test_compaction_keeps_latest_and_all_referenced_sources(self):
        eid=self.fixture.item['evidence_id'];self.model.route='unrelated';self.model.action='archive'
        plan(self.s,self.rid,self.model,self.tid)
        with self.s.conn:
            for day in ('2000-01-01','2000-01-02'):self.s.conn.execute('INSERT INTO channel_observation VALUES(?,?,?,?,?)',('baidu:game',eid,day,1,'{}'))
        r=retention.compact(self.s)
        self.assertEqual(r['redundant_observations_removed'],2)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM channel_observation').fetchone()[0],1)
        self.assertTrue(self.s.evidence([eid]));self.assertTrue(self.s.snapshot(eid))
    def test_legacy_classification_does_not_change_facts(self):
        data={'game_signals':[{'payload':{'title':'玩家分享农田布局','observed_change':'展示农田','game_context':'建造'},'risk_assessment':{'polarity':'positive'}}],
              'creatives':[{'payload':{'growth_goal':'引导社区参与','user_action':'评论分享','placement':'TapTap论坛'}}]}
        title=data['game_signals'][0]['payload']['title'];classify_outputs(data)
        self.assertEqual(data['game_signals'][0]['payload']['category'],'community_creation')
        self.assertEqual(data['creatives'][0]['payload']['category'],'community_participation')
        self.assertEqual(data['game_signals'][0]['payload']['title'],title)
    def test_internal_quote_not_counted_as_editable_material(self):
        self.assertFalse(material_ready({'kind':'source_quote','content':'研究摘录','application':{'usage_example':'内部研究引用'}}))
        self.assertTrue(material_ready({'kind':'script','content':'分镜与字幕内容'*20}))
    def test_new_event_assessment_does_not_reactivate_old_creative_basis(self):
        from agent_v3.pipeline import process
        process(self.s,self.rid,model=self.fixture.model('opportunity'),topic_id=self.tid)
        creative=self.s.creative_feed()[0];old_basis=self.s.creative_basis(creative)
        self.s.upsert_evidence({**self.fixture.item,'body':self.fixture.item['body']+'后续有新的内容。'})
        self.s.conn.commit();scan(self.s)
        fp=read_topic(self.s,self.tid)['fingerprint']
        updated={**old_basis,'topic_fingerprint':fp}
        with self.s.conn:self.s.conn.execute('UPDATE event SET assessment=? WHERE event_id=?',(dump(updated),creative['event_id']))
        self.assertEqual(self.s.creative_basis(creative)['topic_fingerprint'],old_basis['topic_fingerprint'])
        self.assertEqual(self.s.creative_feed(),[])
        self.assertEqual(len(self.s.creative_feed(held=True)),1)

if __name__=='__main__':unittest.main()
