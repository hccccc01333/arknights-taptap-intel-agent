import unittest
from unittest.mock import patch
from agent_v3 import web_research,research
from agent_v3.tests import test_main_agent


class WebResearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_main_agent.MainAgentTests();self.fixture.setUp()
        self.store=self.fixture.store;self.tid=self.fixture.tid;self.rid=self.fixture.rid

    def tearDown(self):self.fixture.tearDown()

    def model(self):return self.fixture.model()
    def test_search_rss_uses_real_query_and_does_not_invent_publication_date(self):
        raw=b'<rss><channel><item><title>Game news</title><link>https://www.gamersky.com/news/item.shtml</link><description>Actual search excerpt</description><pubDate>today</pubDate></item></channel></rss>'
        with patch('agent_v3.web_research.read_public',return_value=(raw,'https://www.bing.com/search')) as reader:
            result=web_research.search('game update')
        self.assertIn('q=game+update',reader.call_args.args[0]);self.assertIsNone(result[0]['published_at'])

    def test_unknown_host_not_read_as_article(self):
        with patch('agent_v3.web_research.read_public') as reader:
            with self.assertRaises(ValueError):web_research.article('https://unknown.test/page')
        reader.assert_not_called()

    def test_search_rejects_external_xml_entities(self):
        with patch('agent_v3.web_research.read_public',return_value=(b'<!DOCTYPE x><rss/>','https://www.bing.com')):
            with self.assertRaises(ValueError):web_research.search('game')

    def test_search_and_article_reads_share_total_budget_and_cache(self):
        rows=[{'title':'相关报道','url':'https://www.gamersky.com/news/'+str(i)+'.shtml','description':'实际搜索摘要','source':'search:bing'} for i in range(3)]
        detail={'body':'这是一段正文。'*30,'resolved_url':rows[0]['url'],'content_truncated':False,'scope':'article_excerpt','comments_read':False}
        with patch('agent_v3.web_research.search',return_value=rows) as search,patch('agent_v3.web_research.article',return_value=detail) as reader:
            first=web_research.background(self.store,self.tid,'相关报道',max_calls=2)
            second=web_research.background(self.store,self.tid,'相关报道',max_calls=2)
        self.assertEqual(first['calls'],2);self.assertEqual(second['calls'],0);self.assertEqual(reader.call_count,1);search.assert_called_once()
        self.assertTrue(all(e['kind']=='search' for e in self.store.evidence(first['evidence_ids'])))

    def test_failed_search_retains_actual_call_and_defers_retry(self):
        with patch('agent_v3.web_research.search',side_effect=TimeoutError):
            first=web_research.background(self.store,self.tid,'失败查询')
            second=web_research.background(self.store,self.tid,'失败查询')
        self.assertEqual(first['calls'],1);self.assertEqual(first['status'],'failed');self.assertEqual(second['calls'],0)

    def test_no_main_delegation_does_not_spend_leftover_read_budget(self):
        with patch('agent_v3.enrichment.prepare') as reader:
            value=research.run(self.store,delegations=[],model=self.model(),run_id=self.rid)
        self.assertEqual(value['calls'],0);reader.assert_not_called()


if __name__=='__main__':unittest.main()
