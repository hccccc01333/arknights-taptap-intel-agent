"""Regression cases from the failed live study, without claiming model quality."""
import json
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch

from bs4 import BeautifulSoup
from agent_v2.ingest import normalize
from agent_v2.store import now_iso,dump
from agent_v3.store import Store
from agent_v3.discovery import scan,queue,read_topic
from agent_v3.freshness import assess
from agent_v3.public_sources import publication_date,read_source
from agent_v3.contracts import schema_feedback
from agent_v3 import research
from agent_v3.main_agent import plan,PLAN_VERSION
from agent_v3.research_child import schema
from agent_v3.pipeline import process
from agent_v3.tests import test_main_agent as fixtures
from agent_v3.task_packets import topic_packet,object_schema


class Delivery21Tests(unittest.TestCase):
    def setUp(self):self.store=Store(':memory:')
    def tearDown(self):self.store.close()

    def news(self,date=None,observation=True):
        e=normalize({'title':'测试游戏发布更新公告','url':'https://www.3dmgame.com/news/202610/fixture.html',
            'description':'来源描述游戏更新的具体变化。'*10,'observed_at':now_iso(),'published_at':date},'gamemedia','v3:live:fixture')
        self.store.upsert_evidence(e)
        if observation:self.store.conn.execute('INSERT OR IGNORE INTO channel_observation VALUES(?,?,?,?,?)',('gamemedia:news',e['evidence_id'],now_iso(),None,'{}'))
        self.store.conn.commit();scan(self.store)
        return e

    def test_missing_date_admitted_only_with_recent_channel_observation(self):
        e=self.news(observation=False);self.assertEqual(queue(self.store),[])
        self.store.conn.execute('INSERT INTO channel_observation VALUES(?,?,?,?,?)',('gamemedia:news',e['evidence_id'],now_iso(),None,'{}'))
        self.store.conn.commit();scan(self.store)
        t=read_topic(self.store,queue(self.store)[0]['topic_id'])
        self.assertEqual(assess(self.store,t)['status'],'date_unknown')
        self.assertFalse(assess(self.store,t)['business_eligible']);self.assertFalse(self.store.hotspot_feed())
        self.assertTrue(any(s['kind']=='publication_date_pending' for s in t['signals']))

    def test_unknown_date_does_not_override_an_old_or_future_date(self):
        for delta in (-9,1):
            self.news((datetime.now(timezone.utc)+timedelta(days=delta)).isoformat())
            self.assertEqual(queue(self.store),[])

    def page(self,stamp):
        return ('<div class="news_warp_top"><h1>游戏更新</h1><div class="time"><span>'+stamp+
            '</span></div></div><div class="news_warp_center"><p>'+('来源描述已公布的具体游戏变化。'*10)+'</p></div>').encode()

    def test_read_detail_recovers_publisher_date_and_records_its_basis(self):
        e=self.news();stamp=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(timespec='seconds')
        with patch('agent_v3.public_sources.read_public',return_value=(self.page(stamp),e['url'])):
            result=read_source(self.store,e['evidence_id'])
        publication=result['metadata']['publication']
        self.assertEqual(publication['published_at'],stamp);self.assertEqual(publication['selector'],'3dm.article.time')
        scan(self.store);t=read_topic(self.store,queue(self.store)[0]['topic_id'])
        self.assertTrue(assess(self.store,t)['business_eligible']);self.assertFalse(self.store.hotspot_feed())
        self.assertEqual(t['evidence'][0]['content_scope'],'article_excerpt')

    def test_recovered_old_date_expires_candidate_instead_of_making_new_hotspot(self):
        e=self.news();stamp=(datetime.now(timezone.utc)-timedelta(days=500)).isoformat()
        tid=queue(self.store)[0]['topic_id']
        with patch('agent_v3.public_sources.read_public',return_value=(self.page(stamp),e['url'])):read_source(self.store,e['evidence_id'])
        scan(self.store);self.assertEqual(queue(self.store),[])
        self.assertEqual(assess(self.store,read_topic(self.store,tid))['status'],'historical_only')

    def test_modified_date_body_date_and_url_do_not_prove_publication(self):
        html='<meta property="article:modified_time" content="2026-10-10"><p>2026-10-10更新</p>'
        self.assertEqual(publication_date(BeautifulSoup(html,'html.parser'),'www.3dmgame.com')['status'],'missing')

    def test_conflicting_publication_dates_are_unknown_and_future_is_rejected(self):
        html='<meta property="article:published_time" content="2025-01-01"><time itemprop="datePublished" datetime="2025-01-02"></time>'
        self.assertEqual(publication_date(BeautifulSoup(html,'html.parser'),'www.gcores.com')['status'],'conflicting')
        html='<meta property="article:published_time" content="2099-01-01">'
        self.assertEqual(publication_date(BeautifulSoup(html,'html.parser'),'www.gcores.com')['status'],'missing')

    def test_empty_model_tool_plan_cannot_skip_required_date_read(self):
        self.news();t=read_topic(self.store,queue(self.store)[0]['topic_id'])
        class Empty:
            supports_tasks=True
            def run_task(self,*args,**kwargs):return {'result':{'reason':'正文已存在无需操作','actions':[]}}
        p=research.plan(self.store,t,Empty())
        self.assertEqual(p['actions'],[{'tool':'read_detail','source_ref':0,'query':''}])
        self.assertEqual(p['gaps_before']['publication_date']['status'],'missing')

    def fixture(self):
        f=fixtures.MainAgentTests();f.setUp();self.addCleanup(f.tearDown);return f

    def test_game_research_goal_survives_watch_without_promotional_bridge(self):
        f=self.fixture();f.store.conn.execute('DELETE FROM channel_observation');f.store.conn.commit();scan(f.store)
        m=f.model();native=m.run_task
        def run(stage,*args,**kwargs):
            r=native(stage,*args,**kwargs)
            if stage=='main_plan':
                for d in r['result']['decisions']:
                    d.update(action='watch',research_goal='game_change',reason='游戏有具体变化，缺少热度和TapTap承接证据')
                    d.pop('questions');d.pop('query')
            return r
        m.run_task=run
        result=process(f.store,f.rid,model=m,topic_id=f.tid)
        self.assertEqual(result['status'],'intelligence_ready')
        self.assertEqual(len(f.store.game_signals()),1);self.assertFalse(f.store.hotspot_feed())
        self.assertEqual(f.store.overview()['counts']['creative'],0)
        row=json.loads(f.store.conn.execute('SELECT payload FROM main_decision').fetchone()[0])
        self.assertEqual(row['model_action'],'watch');self.assertEqual(row['action'],'delegate_research')

    def test_no_research_goal_retains_watch_for_ordinary_game_post(self):
        f=self.fixture();m=f.model();m.action='watch'
        self.assertEqual(process(f.store,f.rid,model=m,topic_id=f.tid)['status'],'no_selected_work')
        self.assertEqual(m.calls,['main_plan'])

    def test_old_plan_version_is_reconsidered_instead_of_reusing_old_watch(self):
        f=self.fixture();m=f.model();m.action='watch';plan(f.store,f.rid,m,f.tid)
        row=f.store.conn.execute('SELECT payload FROM main_decision').fetchone();p=json.loads(row[0]);p['decision_version']='main-plan-v3.10'
        f.store.conn.execute('UPDATE main_decision SET payload=?',(dump(p),));f.store.conn.commit();m.action='delegate_research'
        from agent_v3.service import has_automatic_work,unscreened_count
        self.assertTrue(has_automatic_work(f.store));self.assertEqual(unscreened_count(f.store),1)
        self.assertEqual(len(plan(f.store,f.rid,m)),1);self.assertEqual(m.calls.count('main_plan'),2)
        self.assertEqual(json.loads(f.store.conn.execute('SELECT payload FROM main_decision').fetchone()[0])['decision_version'],PLAN_VERSION)

    def test_no_followups_or_pending_relations_means_no_placeholder_fields(self):
        f=self.fixture();p=topic_packet(read_topic(f.store,f.tid),f.store.context());p['mission']={}
        s=schema(p)
        self.assertNotIn('followup_answers',s['properties']);self.assertNotIn('relation_reviews',s['properties']['knowledge']['properties'])
        p['mission']={'followup_ids':['followup_fixture']}
        p['graph_context']={'pending_relations':[{'relation_id':'relation_fixture','status':'proposed'}]}
        s=schema(p);self.assertIn('followup_answers',s['properties']);self.assertIn('relation_reviews',s['properties']['knowledge']['properties'])

    def test_missing_field_feedback_names_trusted_paths_not_model_values(self):
        s=object_schema({'knowledge':object_schema({'entities':{'type':'array'},'relations':{'type':'array'}})})
        errors=schema_feedback(s,{'knowledge':{'entities':[]},'secret_value':'private fixture text'})
        self.assertIn({'path':'knowledge/relations','constraint':'required'},errors)
        self.assertNotIn('private fixture text',json.dumps(errors));self.assertNotIn('secret_value',json.dumps(errors))

    def test_isolated_profile_registry_preserves_original_business_failure(self):
        from agent_v3.providers import save
        from agent_v3.opencode_go import profile
        f=self.fixture();p=profile('longcat-2.5-preview-free');save(f.store,p)
        f.store.conn.execute('UPDATE evidence SET published_at=NULL');f.store.conn.commit();scan(f.store)
        m=f.model();m.model='profile/'+p['id']
        with patch('agent_v3.research.run',return_value={'calls':0}):
            result=process(f.store,f.rid,model=m,topic_id=f.tid)
        self.assertEqual(result['status'],'ai_deferred')
        self.assertIn('只处理近一周事件',result['error'])
        self.assertEqual(f.store.overview()['counts']['creative'],0)


if __name__=='__main__':unittest.main()
