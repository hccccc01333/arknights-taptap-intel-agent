"""Verify non-game discovery, actual read provenance and fair independent enrichment."""
import json
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch

import requests
from agent_v2.ingest import normalize
from agent_v2.store import now_iso,dump
from agent_v3 import public_sources as public,enrichment,work
from agent_v3.connectors import collect,coverage
from agent_v3.discovery import scan,queue,read_topic
from agent_v3.store import Store
from agent_v3.task_packets import topic_packet
from agent_v3.tools import GrowthTools


class PublicSourceTests(unittest.TestCase):
    def setUp(self):self.store=Store(':memory:')
    def tearDown(self):self.store.close()

    def news(self,channel='chinanews:society',title='假期返程生活变化',published=None):
        row={'title':title,'description':'真实来源的摘要，用于验证新闻线索与正文读取范围。',
             'url':'https://www.chinanews.com.cn/sh/2026/10-07/'+title+'.shtml',
             'published_at':published or now_iso()}
        with patch('agent_v3.connectors.fetch',return_value=[row]):result=collect(self.store,channel)
        scan(self.store)
        return result['evidence_ids'][0]

    def test_feed_preserves_publication_and_does_not_invent_popularity(self):
        content=b'''<rss><channel><item><title>Public story</title><link>https://www.chinanews.com.cn/sh/a.shtml</link>
          <description>Real summary</description><pubDate>Wed, 7 Oct 2026 13:00:00 +0800</pubDate></item></channel></rss>'''
        row=public.parse_feed(content)[0]
        self.assertEqual(row['published_at'],'2026-10-07T05:00:00+00:00')
        self.assertNotIn('rank',row);self.assertNotIn('hot_score',row)

    def test_feed_rejects_entities_and_non_source_links(self):
        with self.assertRaises(ValueError):public.parse_feed(b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///private">]><rss/>')
        self.assertEqual(public.parse_feed(b'<rss><channel><item><title>X</title><link>https://localhost/x</link></item></channel></rss>'),[])
        with self.assertRaises(ValueError):public.parse_feed(b'<html/>')

    def test_reader_validates_credentials_scheme_host_port(self):
        for url in ('http://www.chinanews.com.cn/a','https://www.chinanews.com.cn.evil.test/a',
                    'https://user:pass@www.chinanews.com.cn/a','https://127.0.0.1/a','https://www.chinanews.com.cn:8443/a'):
            with self.subTest(url=url),patch('agent_v3.public_sources.requests.get') as get:
                with self.assertRaises(ValueError):public.read_public(url,{'www.chinanews.com.cn'})
                get.assert_not_called()

    def test_offsite_redirect_is_never_followed(self):
        class Reply:
            is_redirect=True;headers={'Location':'https://127.0.0.1/private'}
            def __enter__(self):return self
            def __exit__(self,*args):pass
        with patch('agent_v3.public_sources.requests.get',return_value=Reply()) as get:
            with self.assertRaises(ValueError):public.read_public('https://www.chinanews.com.cn/rss/society.xml',{'www.chinanews.com.cn'})
            self.assertEqual(get.call_count,1)
            self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_page_size_and_total_time_are_bounded(self):
        class Reply:
            is_redirect=False
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def raise_for_status(self):pass
            def iter_content(self,size):yield b'123456'
        with patch('agent_v3.public_sources.requests.get',return_value=Reply()):
            with self.assertRaises(ValueError):public.read_public('https://www.chinanews.com.cn/a',{'www.chinanews.com.cn'},max_bytes=5)
            with patch('agent_v3.public_sources.time.monotonic',side_effect=[0,0,20]):
                with self.assertRaises(TimeoutError):public.read_public('https://www.chinanews.com.cn/a',{'www.chinanews.com.cn'})

    def test_article_and_editorial_topic_description_have_distinct_scopes(self):
        article=public.parse_detail(('<nav>noise</nav><div class="left_zw"><p>'+('实际正文。'*1300)+'</p><script>private noise</script></div>').encode(),'chinanews')
        self.assertEqual(article['scope'],'article_excerpt');self.assertTrue(article['content_truncated'])
        self.assertEqual(len(article['body']),6000);self.assertNotIn('noise',article['body'])
        topic=public.parse_detail(('<p class="topic-desc">'+('平台编辑话题介绍。'*4)+'</p>').encode(),'tieba')
        self.assertEqual(topic['scope'],'topic_description');self.assertFalse(topic['comments_read'])
        self.assertIsNone(public.parse_detail(b'<html>captcha</html>','tieba'))

    def test_news_order_cannot_produce_board_rank_rise(self):
        eid=self.news()
        self.assertIsNone(self.store.conn.execute('SELECT position FROM channel_observation WHERE evidence_id=?',(eid,)).fetchone()[0])
        topic=queue(self.store)[0]
        self.assertEqual(topic['candidate_type'],'news')
        self.assertTrue(any(s['kind']=='news_publication' for s in topic['signals']))
        self.assertFalse(any(s['kind']=='board_rank_rise' for s in topic['signals']))

    def test_old_and_future_excluded_but_undated_news_can_be_checked(self):
        for title,stamp in [('old',(datetime.now(timezone.utc)-timedelta(days=9)).isoformat()),
                            ('future',(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()),('undated','invalid')]:
            self.news(title=title,published=stamp)
        pending=queue(self.store)
        self.assertEqual([p['title'] for p in pending],['undated'])
        from agent_v3.freshness import assess
        self.assertFalse(assess(self.store,read_topic(self.store,pending[0]['topic_id']))['business_eligible'])
        self.assertFalse(self.store.hotspot_feed())

    def test_an_expired_candidate_is_not_refreshed_by_polling_an_old_article(self):
        eid=self.news();self.assertEqual(len(queue(self.store)),1)
        self.store.conn.execute('UPDATE evidence SET published_at=? WHERE evidence_id=?',
          ((datetime.now(timezone.utc)-timedelta(days=9)).isoformat(),eid));self.store.conn.commit();scan(self.store)
        self.assertEqual(queue(self.store),[])

    def test_domain_filters_are_channel_backed(self):
        self.news(title='社会故事');self.news('chinanews:culture','文娱故事');self.news('chinanews:life','生活故事')
        self.assertEqual([t['title'] for t in queue(self.store,domain='社会')],['社会故事'])
        self.assertEqual([t['title'] for t in queue(self.store,domain='娱乐')],['文娱故事'])
        self.assertEqual([t['title'] for t in queue(self.store,domain='生活方式')],['生活故事'])
        self.assertEqual(queue(self.store,domain='missing'),[])
        self.assertEqual(len(coverage(self.store)),13)

    def test_real_body_changes_topic_version_and_retains_metadata_through_feed_refresh(self):
        eid=self.news();tid=queue(self.store)[0]['topic_id'];old=read_topic(self.store,tid)['fingerprint']
        body='新闻原文对生活方式的真实背景描述。'*8
        with patch('agent_v3.public_sources.read_public',return_value=(('<div class="left_zw"><p>'+body+'</p></div>').encode(),'https://www.chinanews.com.cn/sh/article.shtml')):
            result=enrichment.prepare(self.store,topic_id=tid)
        self.assertEqual(result['results'][0]['status'],'ok');scan(self.store);work.enqueue(self.store)
        topic=read_topic(self.store,tid);self.assertNotEqual(topic['fingerprint'],old)
        source=topic['evidence'][0];self.assertEqual(source['body'],body)
        self.assertEqual(source['content_scope'],'article_excerpt')
        self.assertFalse(topic_packet(topic,self.store.context())['evidence'][0]['reading_metadata']['comments_read'])
        with patch('agent_v3.enrichment.read_source') as reader:enrichment.prepare(self.store,topic_id=tid);reader.assert_not_called()
        with patch('agent_v3.connectors.fetch',return_value=[{'title':source['title'],'url':source['url'],'description':'摘要','published_at':source['published_at']}]):collect(self.store,'chinanews:society')
        self.assertEqual(self.store.evidence([eid])[0]['body'],body)
        self.assertTrue(any(a['scope']=='article_excerpt' for a in self.store.source_assets(evidence_ids=[eid])))

    def test_enrichment_rotates_persistently_and_is_not_limited_to_top_game_topics(self):
        for i in range(35):self.news(title='社会'+str(i))
        self.news('chinanews:culture','文娱');self.news('chinanews:life','生活')
        with patch('agent_v3.enrichment.read_source',return_value={'status':'unavailable'}) as reader:
            a=enrichment.prepare(self.store,max_calls=1);b=enrichment.prepare(self.store,max_calls=1);c=enrichment.prepare(self.store,max_calls=1)
        self.assertEqual({a['results'][0]['bucket'],b['results'][0]['bucket'],c['results'][0]['bucket']},
                         {'chinanews:society','chinanews:culture','chinanews:life'})
        self.assertEqual(reader.call_count,3)

    def test_restricted_source_stops_same_source_reads_within_cycle(self):
        for i in range(3):
            item=normalize({'title':'视频'+str(i),'bvid':'BV'+str(i),'word':'视频'+str(i),'observed_at':now_iso()},'bilibili','test')
            self.store.upsert_evidence(item)
        self.store.conn.commit();scan(self.store)
        response=requests.Response();response.status_code=412
        with patch('agent_v3.enrichment.read_source',side_effect=requests.HTTPError('private server output',response=response)) as reader:
            result=enrichment.prepare(self.store)
        self.assertEqual(reader.call_count,1);self.assertIn('412',result['results'][0]['error'])
        self.assertNotIn('private server output',result['results'][0]['error'])

    def test_tool_reads_only_requested_source_and_reuses_backoff(self):
        eid=self.news()
        tools=GrowthTools(self.store,network_budget=2)
        with patch('agent_v3.enrichment.read_source',return_value={'status':'unavailable'}) as reader:
            result=tools.call('read_source',{'evidence_id':eid});tools.call('read_source',{'evidence_id':eid})
        self.assertEqual(reader.call_count,1);self.assertEqual(result['evidence'][0]['evidence_id'],eid)
        self.assertIn(eid,tools.read_ids)

    def test_url_change_invalidates_read_metadata_and_requires_read_even_if_text_is_identical(self):
        eid=self.news();tid=queue(self.store)[0]['topic_id']
        body='已经读取的真实新闻原文背景。'*8
        with patch('agent_v3.public_sources.read_public',return_value=(('<div class="left_zw"><p>'+body+'</p></div>').encode(),'https://www.chinanews.com.cn/sh/old.shtml')):
            enrichment.prepare(self.store,topic_id=tid)
        old=self.store.evidence([eid])[0]
        self.store.conn.execute('UPDATE evidence SET url=url||? WHERE evidence_id=?',('?updated=1',eid));self.store.conn.commit()
        self.assertIsNone(self.store.evidence([eid])[0]['reading_metadata'])
        with patch('agent_v3.public_sources.read_public',return_value=(('<div class="left_zw"><p>'+body+'</p></div>').encode(),'https://www.chinanews.com.cn/sh/new.shtml')) as reader:
            enrichment.prepare(self.store,topic_id=tid)
        self.assertEqual(reader.call_count,1)
        current=self.store.evidence([eid])[0]
        self.assertEqual(old['content_hash'],current['content_hash'])
        self.assertEqual(current['reading_metadata']['requested_url'],current['url'])
        self.assertTrue(current['reading_metadata']['resolved_url'].endswith('new.shtml'))
        self.store.conn.execute('UPDATE evidence SET url=url||? WHERE evidence_id=?',('&second=1',eid));self.store.conn.commit()
        with patch('agent_v3.enrichment.read_source',side_effect=RuntimeError('private response')) as reader:
            enrichment.prepare(self.store,topic_id=tid)
            self.assertEqual(enrichment.prepare(self.store,topic_id=tid)['calls'],0)
            self.assertEqual(reader.call_count,1)


if __name__=='__main__':unittest.main()
