"""Rank stability, time gates, original provenance and quota-safe consumption."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_v2.store import dump, now_iso
from agent_v3.api import router
from agent_v3.discovery import scan, read_topic
from agent_v3.model import gate, set_provider_hold, GatedModel, failure
from agent_v3.pipeline import process
from agent_v3 import work, public_site
from agent_v3.tests import test_main_agent as fixtures


class Library15Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MainAgentTests(); self.fixture.setUp()
        self.s = self.fixture.store; self.tid = self.fixture.tid
        self.eid = self.fixture.item['evidence_id']; self.rid = self.fixture.rid
    def tearDown(self): self.fixture.tearDown()
    def deliver(self):
        process(self.s, self.rid, model=self.fixture.model('opportunity'), topic_id=self.tid)
        return self.s.creative_feed()[0]

    def test_rank_rise_retains_business_version_and_completed_work(self):
        self.s.conn.execute('UPDATE channel_observation SET position=20,observed_at=?',
            ((datetime.now(timezone.utc)-timedelta(minutes=40)).isoformat(timespec='seconds'),))
        self.s.conn.commit(); scan(self.s)
        self.deliver(); before=read_topic(self.s,self.tid)['fingerprint']
        count=self.s.conn.execute('SELECT COUNT(*) FROM work_item').fetchone()[0]
        self.s.conn.execute('INSERT INTO channel_observation VALUES(?,?,?,?,?)',('baidu:game',self.eid,now_iso(),2,'{}'))
        self.s.conn.commit(); scan(self.s); work.enqueue(self.s,topic_ids=[self.tid])
        self.assertEqual(read_topic(self.s,self.tid)['fingerprint'],before)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM work_item').fetchone()[0],count)
        self.assertEqual(len(self.s.creative_feed()),1)
        self.assertGreaterEqual(self.s.conn.execute('SELECT COUNT(*) FROM topic_observation_revision').fetchone()[0],2)
        self.assertTrue(any(s['kind']=='board_rank_rise' for s in read_topic(self.s,self.tid)['signals']))

    def test_new_channel_updates_heat_without_invalidating_same_content(self):
        creative=self.deliver(); before=self.s.creative_basis(creative)['topic_fingerprint']
        self.s.conn.execute('INSERT INTO channel_observation VALUES(?,?,?,?,?)',('weibo:hot',self.eid,now_iso(),2,'{}'))
        self.s.conn.commit(); scan(self.s)
        self.assertEqual(read_topic(self.s,self.tid)['fingerprint'],before)
        self.assertEqual(len(self.s.creative_feed()),1)

    def test_body_change_invalidates_results_and_retains_history(self):
        creative=self.deliver(); old=self.s.creative_basis(creative)['topic_fingerprint']
        self.s.upsert_evidence({**self.fixture.item,'body':self.fixture.item['body']+'后续新增地图规则。'})
        self.s.conn.commit(); scan(self.s)
        self.assertNotEqual(read_topic(self.s,self.tid)['fingerprint'],old)
        self.assertEqual(self.s.creative_feed(),[])
        self.assertEqual(self.s.creative_feed(held=True)[0]['creative_id'],creative['creative_id'])
        self.assertTrue(self.s.conn.execute('SELECT 1 FROM topic_history WHERE fingerprint=?',(old,)).fetchone())

    def test_publication_date_is_content_metadata_not_observation_time(self):
        before=read_topic(self.s,self.tid)['fingerprint']
        self.s.upsert_evidence({**self.fixture.item,'published_at':(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()})
        self.s.conn.commit(); scan(self.s)
        self.assertNotEqual(read_topic(self.s,self.tid)['fingerprint'],before)

    def test_expired_results_leave_current_libraries_but_stay_in_database(self):
        self.deliver(); self.assertTrue(self.s.game_signals()); self.assertTrue(self.s.usable_materials())
        actual=datetime.now(timezone.utc)
        with patch('agent_v3.freshness.datetime') as clock:
            clock.now.return_value=actual+timedelta(days=8);clock.fromisoformat.side_effect=datetime.fromisoformat
            self.assertEqual(self.s.game_signals(),[])
            self.assertEqual(self.s.usable_materials(),[])
            self.assertEqual(self.s.creative_feed(),[])
            self.assertEqual(len(self.s.creative_feed(held=True)),1)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM creative').fetchone()[0],1)

    def test_expired_native_growth_never_calls_model(self):
        self.deliver(); actual=datetime.now(timezone.utc); model=self.fixture.model('opportunity')
        from agent_v3.native_growth import run
        with patch('agent_v3.freshness.datetime') as clock:
            clock.now.return_value=actual+timedelta(days=8);clock.fromisoformat.side_effect=datetime.fromisoformat
            with self.assertRaisesRegex(ValueError,'时机已过期'):run(self.s,self.rid,model,self.tid)
        self.assertEqual(model.calls,[])

    def test_creative_sources_use_delivery_version_after_event_and_source_update(self):
        creative=self.deliver(); basis=self.s.creative_basis(creative)
        original=self.s.creative_event(creative['creative_id'])['source_snapshots'][0]['body']
        self.s.upsert_evidence({**self.fixture.item,'body':'全新内容，与交付时的正文不同。'})
        self.s.conn.commit(); scan(self.s)
        updated={**basis,'summary':'这是一份后来更新的机会判断。','topic_fingerprint':read_topic(self.s,self.tid)['fingerprint']}
        with self.s.conn:self.s.conn.execute('UPDATE event SET assessment=? WHERE event_id=?',(dump(updated),creative['event_id']))
        trace=self.s.creative_event(creative['creative_id'])
        self.assertEqual(trace['assessment']['summary'],basis['summary'])
        self.assertEqual(trace['source_snapshots'][0]['body'],original)
        self.assertFalse(trace['risk_assessment']['growth_allowed'])

    def test_quota_hold_survives_expired_backoff_and_model_alias_switch(self):
        failure(self.s,'deepseek-flash','HTTP 402',observed_at='2000-01-01T00:00:00+00:00')
        set_provider_hold(self.s,'deepseek-flash',True)
        for name in ('deepseek-flash','deepseek-v4-flash'):
            self.assertEqual(gate(self.s,name)['status'],'deferred')
            self.assertIsNone(gate(self.s,name)['retry_at'])
        self.assertEqual(gate(self.s,'other-provider')['status'],'ready')

    def test_missing_original_version_cannot_use_current_event_as_its_basis(self):
        creative=self.deliver()
        with self.s.conn:self.s.conn.execute('UPDATE event_version SET run_id=? WHERE event_id=? AND run_id=?',
            ('unrelated-run',creative['event_id'],creative['run_id']))
        self.assertEqual(self.s.creative_basis(creative),{})
        self.assertEqual(self.s.creative_feed(),[])
        trace=self.s.creative_event(creative['creative_id'])
        self.assertEqual(trace['title'],'原始生成依据版本缺失')
        self.assertEqual(trace['source_snapshots'],[])
        self.assertFalse(trace['risk_assessment']['growth_allowed'])

    def test_hold_blocks_an_already_constructed_model_without_calling_client(self):
        model=GatedModel.__new__(GatedModel);model.store=self.s;model.model='deepseek-flash';model.client=Mock()
        set_provider_hold(self.s,model.model,True)
        from L4_intelligence.intelligence.llm import LLMUnavailable
        with self.assertRaises(LLMUnavailable):model.decide([],[])
        with self.assertRaises(LLMUnavailable):model.run_task('test',{}, {},'')
        model.client.decide.assert_not_called();model.client.run_task.assert_not_called()

    def test_releasing_hold_does_not_erase_existing_provider_backoff(self):
        failure(self.s,'deepseek-flash','HTTP 402');set_provider_hold(self.s,'deepseek-flash',True)
        set_provider_hold(self.s,'deepseek-flash',False)
        self.assertEqual(gate(self.s,'deepseek-flash')['status'],'deferred')
        self.assertIsNotNone(gate(self.s,'deepseek-flash')['retry_at'])
        with self.assertRaises(ValueError):set_provider_hold(self.s,'deepseek-flash','false')

    def test_public_snapshot_has_readable_references_and_honest_quota_status(self):
        creative=self.deliver();set_provider_hold(self.s,'deepseek-flash',True);self.s.set_model('deepseek-flash')
        before=self.s.conn.total_changes;data=public_site.snapshot(self.s)
        self.assertEqual(self.s.conn.total_changes,before)
        self.assertEqual(data['overview']['publication']['automation_status']['ai'],'waiting_quota')
        self.assertEqual(data['overview']['creatives'][0]['delivery_meta']['event_title'],'创作者展示地图分享与组队挑战玩法')
        self.assertIn(creative['creative_id'],data['creative_events'])
        self.assertIn(self.eid,data['sources']);self.assertNotIn('body',data['sources'][self.eid])
        self.assertNotIn('source_path',data['sources'][self.eid])

    def test_source_api_rejects_unbounded_and_invalid_requests(self):
        app=FastAPI();app.include_router(router(lambda:{'actor':'test','role':'observer'}))
        client=TestClient(app)
        with patch('agent_v3.service.read',return_value={'sources':self.s.evidence([self.eid])}) as read:
            self.assertEqual(client.get('/api/v3/sources',params={'ids':self.eid}).json()['sources'][0]['evidence_id'],self.eid)
            read.assert_called_once_with('sources',[self.eid])
            self.assertEqual(client.get('/api/v3/sources',params={'ids':','.join('e'+str(i) for i in range(21))}).status_code,400)
            self.assertEqual(client.get('/api/v3/sources',params={'ids':'../../private'}).status_code,400)
            self.assertEqual(read.call_count,1)
        with patch('agent_v3.service.read',return_value=None):
            self.assertEqual(client.get('/api/v3/creative-sources/missing').status_code,404)

    def test_public_references_include_indirect_fact_sources(self):
        from agent_v3.library_context import evidence_ids
        ids=evidence_ids({'facts':[{'evidence_id':'background'}],'nested':{'source_versions':{'original':'hash'}},'evidence_ids':['comment']})
        self.assertEqual(ids,{'background','original','comment'})


if __name__=='__main__':unittest.main()
