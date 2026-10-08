"""Meaningful fallback, provenance, denied access and freshness boundary checks."""
import json
import unittest
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from agent_v3 import browser_tools,research
from agent_v3.discovery import scan,read_topic
from agent_v3.freshness import assess,delivery_current
from agent_v3.tests import test_main_agent as fixtures


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MainAgentTests();self.f.setUp();self.s=self.f.store;self.tid=self.f.tid;self.eid=self.f.item['evidence_id']
    def tearDown(self):self.f.tearDown()
    def capture(self,**extra):
        return {'capture_id':'capture_'+'0'*32,'status':'ok','body':'','ocr_text':'截图中的地图分享玩法介绍。'*8,
            'requested_url':self.f.item['url'],'resolved_url':self.f.item['url'],'captures':[{'file':'frame-0.png','sha256':'a'*64,'ocr':{'status':'ok','text':'地图分享'},'scroll_y':0}],
            'ocr_status':'ok','captured_at':'now',**extra}

    def test_image_only_text_is_saved_with_ocr_scope_and_original_identity(self):
        with patch.object(browser_tools,'browse',return_value=self.capture()):
            value=browser_tools.read(self.s,self.tid,self.eid,visual=True)
        self.assertEqual(value['status'],'ok');self.assertEqual(value['captures'],1)
        e=self.s.evidence([self.eid])[0];self.assertIn('截图中的地图',e['body'])
        self.assertEqual(e['content_scope'],'browser_ocr_excerpt');self.assertFalse(e['reading_metadata']['comments_read'])
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0],1)
        self.assertEqual(len(read_topic(self.s,self.tid)['research_captures']),1)

    def test_captcha_screenshot_is_not_used_as_event_body(self):
        before=self.s.evidence([self.eid])[0]['body']
        with patch.object(browser_tools,'browse',return_value=self.capture(status='blocked',ocr_text='请通过验证码')):
            value=browser_tools.read(self.s,self.tid,self.eid,visual=True)
        self.assertEqual(value['status'],'blocked');self.assertEqual(self.s.evidence([self.eid])[0]['body'],before)
        self.assertEqual(self.s.conn.execute('SELECT status FROM research_capture').fetchone()[0],'blocked')

    def test_read_cache_does_not_repeat_browser(self):
        with patch.object(browser_tools,'browse',return_value=self.capture()) as tool:
            browser_tools.read(self.s,self.tid,self.eid,visual=True);r=browser_tools.read(self.s,self.tid,self.eid,visual=True)
        self.assertEqual(r['calls'],0);self.assertEqual(tool.call_count,1)

    def test_empty_page_and_unavailable_ocr_not_counted_as_read(self):
        with patch.object(browser_tools,'browse',return_value=self.capture(ocr_text='',ocr_status='unavailable')):
            r=browser_tools.read(self.s,self.tid,self.eid,visual=True)
        self.assertEqual(r['status'],'insufficient');self.assertIsNone(self.s.evidence([self.eid])[0]['reading'])

    def test_private_address_and_non_https_are_rejected(self):
        with patch('agent_v3.browser_tools.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            for url in ('http://news.test/','https://news.test/','https://user:password@news.test/'):
                with self.assertRaises(ValueError):browser_tools.public_url(url)

    def test_http_failure_escalates_to_browser_then_ocr_within_budget(self):
        model=self.f.model();run_task=model.run_task
        def run(stage,*args,**kwargs):
            if stage=='research_plan':return {'result':{'reason':'先尝试爬虫补读正文','actions':[{'tool':'read_detail','source_ref':0,'query':''}]},'usage':{},'transport':'fixture'}
            return run_task(stage,*args,**kwargs)
        model.run_task=run
        self.s.conn.execute("UPDATE evidence SET platform='bilibili',body=''");self.s.conn.commit()
        with patch('agent_v3.enrichment.prepare',return_value={'calls':1,'results':[{'status':'failed'}]}),patch('agent_v3.browser_tools.read',side_effect=[
            {'calls':1,'status':'insufficient'},{'calls':1,'status':'ok','ocr_characters':100}]) as tool:
            r=research.run(self.s,delegations=[{'topic_id':self.tid,'query':''}],model=model,run_id=self.f.rid,max_calls=3)
        self.assertEqual(r['calls'],3);self.assertEqual(tool.call_count,2)
        self.assertFalse(tool.call_args_list[0].kwargs['visual']);self.assertTrue(tool.call_args_list[1].kwargs['visual'])

    def test_old_content_board_presence_is_not_a_revival(self):
        stamp=(datetime.now(timezone.utc)-timedelta(days=400)).isoformat(timespec='seconds')
        self.s.conn.execute('UPDATE evidence SET published_at=?',(stamp,));self.s.conn.commit();scan(self.s)
        verdict=assess(self.s,read_topic(self.s,self.tid))
        self.assertEqual(verdict['status'],'historical_only');self.assertFalse(verdict['business_eligible'])
        self.assertTrue(verdict['heat_evidence'])

    def test_old_content_with_actual_current_rank_rise_can_be_revival(self):
        stamp=(datetime.now(timezone.utc)-timedelta(days=400)).isoformat(timespec='seconds')
        self.s.conn.execute('UPDATE evidence SET published_at=?',(stamp,));self.s.conn.commit();scan(self.s)
        topic=read_topic(self.s,self.tid);topic['signals'].append({'kind':'board_rank_rise','channel_id':'baidu:game','to_at':datetime.now(timezone.utc).isoformat(timespec='seconds')})
        verdict=assess(self.s,topic);self.assertEqual(verdict['status'],'verified_revival');self.assertTrue(verdict['business_eligible'])

    def test_recent_article_reporting_old_event_is_not_recent_hotspot(self):
        v=delivery_current(self.s,read_topic(self.s,self.tid),{'recency':{'kind':'historical'}})
        self.assertFalse(v['business_eligible'])

    def test_unattended_queue_exists_without_clicking_topic(self):
        from agent_v3.service import has_automatic_work
        self.assertTrue(has_automatic_work(self.s))

    def test_observation_time_cannot_validate_an_event_date(self):
        from agent_v3.freshness import validate_event_date,publication_time_matches
        sources={self.eid:{'published_at':'2025-03-31T12:00:00+00:00'}}
        recency={'date_iso':'2026-10-08','time_text':'2026-10-08','facts':[{'evidence_id':self.eid,'quote':'旧动画内容'}]}
        self.assertFalse(validate_event_date(recency,sources));self.assertFalse(publication_time_matches(recency,sources))

    def test_publication_metadata_is_valid_but_not_quoted_event_time(self):
        from agent_v3.freshness import validate_event_date,publication_time_matches
        sources={self.eid:{'published_at':'2026-10-01T10:53:56+00:00'}}
        recency={'date_iso':'2026-10-01','time_text':'2026-10-01','facts':[{'evidence_id':self.eid,'quote':'装修作品介绍'}]}
        self.assertTrue(publication_time_matches(recency,sources));self.assertFalse(validate_event_date(recency,sources))


if __name__=='__main__':unittest.main()
