"""Gap-directed research and reversible identity tests; fixtures are not production evidence."""
import json,unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import requests

from agent_v2.store import now_iso,dump
from agent_v3.store import Store
from agent_v3.connectors import collect
from agent_v3.discovery import scan,queue,read_topic
from agent_v3 import discussion,research,tracking,work,semantic
from agent_v3.task_packets import topic_packet,intelligence_result
from agent_v3.tools import GrowthTools
from agent_v3.public_sources import search_news


class ResearchTrackingTests(unittest.TestCase):
    def setUp(self):self.store=Store(':memory:')
    def tearDown(self):self.store.close()

    def source(self,title='假期返程生活方式',platform='chinanews',identifier='one',body='只有摘要',url=None):
        channel={'taptap':'taptap:discovery','bilibili':'bilibili:popular','chinanews':'chinanews:society','weibo':'weibo:hot'}[platform]
        # Channel IDs come from the live registry; no implicit game classifier.
        from agent_v3.connectors import CHANNELS
        if channel not in {c['id'] for c in CHANNELS}:channel=next(c['id'] for c in CHANNELS if c['platform']==platform)
        raw={'title':title,'description':body,'item_id':identifier,'url':url or f'https://www.chinanews.com.cn/sh/{identifier}.shtml','published_at':now_iso()}
        if platform=='taptap':raw.update(moment_id=identifier,url=f'https://www.taptap.cn/moment/{identifier}')
        if platform=='bilibili':raw.update(bvid=identifier,aid='123',url=f'https://www.bilibili.com/video/{identifier}')
        if platform=='weibo':raw.update(word=title,url=url or f'https://s.weibo.com/weibo?q={identifier}')
        with patch('agent_v3.connectors.fetch',return_value=[raw]):eid=collect(self.store,channel)['evidence_ids'][0]
        scan(self.store)
        tid=self.store.conn.execute('SELECT topic_id FROM topic_member WHERE evidence_id=? AND active=1',(eid,)).fetchone()[0]
        return eid,tid

    def parent(self):
        eid,tid=self.source('公开帖子：城市散步分享','taptap','123','真实公开帖子中描述城市散步方式与路线，以下为测试夹具。'*3)
        return self.store.evidence([eid])[0],tid

    def pair(self):
        left,lt=self.source(identifier='left');right,rt=self.source('交通返程安排更新','weibo','right')
        tracking.observe(self.store)
        with patch('agent_v3.tracking.semantic.pairs',return_value=([(0.91,0,1)],{'method':'fixture','available':True})):
            tracking.recall(self.store)
        row=self.store.conn.execute('SELECT * FROM event_relation').fetchone()
        return dict(row),left,right,lt,rt

    def test_public_post_is_limited_context_not_comments(self):
        parent,tid=self.parent();self.assertEqual(parent['content_scope'],'post_excerpt')
        gaps=research.inspect(self.store,read_topic(self.store,tid))
        self.assertEqual(gaps['context']['status'],'present');self.assertEqual(gaps['discussion']['status'],'missing')

    def test_hot_recent_overlap_deduplicates_and_retains_both_methods(self):
        parent,tid=self.parent();row={'id':'1','text':'想找一个能一起散步的伙伴','author_hash':'hash-only','published_at':now_iso()}
        with patch('agent_v3.discussion.fetch_page',return_value=([row],100)):
            first=discussion.sample(self.store,tid,parent['evidence_id']);again=discussion.sample(self.store,tid,parent['evidence_id'])
        self.assertEqual(first['calls'],2);self.assertEqual(again['calls'],0)
        samples=discussion.read_samples(self.store,tid);self.assertEqual(len(samples),1)
        self.assertEqual(set(samples[0]['sample']['methods']),{'hot','recent'})
        self.assertEqual(samples[0]['url'],parent['url']);self.assertEqual(samples[0]['content_scope'],'comment_sample')
        self.assertEqual(len(queue(self.store)),1)

    def test_spam_short_and_duplicate_samples_not_used_for_needs(self):
        parent,tid=self.parent()
        rows=[{'id':'1','text':'互赞有赞必回','author_hash':'h1'},{'id':'2','text':'好'},{'id':'3','text':'寻找一起散步的朋友','author_hash':'h2'},
              {'id':'4','text':'寻找一起散步的朋友','author_hash':'h3'}]
        discussion.save_samples(self.store,tid,parent,rows,'hot');discussion.save_samples(self.store,tid,parent,rows,'recent')
        gaps=research.inspect(self.store,read_topic(self.store,tid))['discussion']
        self.assertEqual(gaps['raw_samples'],4);self.assertEqual(gaps['informative_samples'],1);self.assertEqual(gaps['known_authors'],1)

    def test_unknown_authors_not_counted_as_distinct_people(self):
        parent,tid=self.parent();discussion.save_samples(self.store,tid,parent,[{'id':'1','text':'期待一起探索城市不同路线'}],'hot')
        self.assertEqual(research.inspect(self.store,read_topic(self.store,tid))['discussion']['known_authors'],0)

    def test_invalid_comment_response_is_failure_not_zero_success(self):
        parent,tid=self.parent()
        from types import SimpleNamespace
        client=SimpleNamespace(load_xua=lambda:'fixture-public-client-metadata',COMMENT_URL='https://www.taptap.cn/webapiv2/moment-comment/v1/by-moment')
        with patch('agent_v3.discussion.legacy',return_value=client),patch('agent_v3.discussion.read_public',return_value=(b'{"success":false,"data":{}}','https://www.taptap.cn')):
            result=discussion.sample(self.store,tid,parent['evidence_id'])
        self.assertEqual(result['status'],'partial');self.assertEqual(result['calls'],1)
        self.assertEqual(discussion.read_samples(self.store,tid),[])

    def test_provider_failure_stops_other_method_and_next_post(self):
        parent,tid=self.parent();reply=requests.Response();reply.status_code=412
        with patch('agent_v3.discussion.fetch_page',side_effect=requests.HTTPError(response=reply)) as fetch:
            a=discussion.sample(self.store,tid,parent['evidence_id']);b=discussion.sample(self.store,tid,parent['evidence_id'])
        self.assertEqual(fetch.call_count,1);self.assertEqual(a['calls'],1);self.assertEqual(b['calls'],0)
        self.assertIn('HTTP 412',b['reason'])

    def test_bilibili_without_verified_locator_never_sends_request(self):
        eid,_=self.source(platform='bilibili',identifier='BVtest');parent=self.store.evidence([eid])[0]
        self.store.conn.execute("UPDATE source_locator SET payload='{}' WHERE evidence_id=?",(eid,));self.store.conn.commit()
        with patch('agent_v3.discussion.read_public') as fetch:
            with self.assertRaises(ValueError):discussion.fetch_page(self.store,parent,'hot')
        fetch.assert_not_called()

    def test_comment_sources_enter_ai_packet_with_sampling_limits(self):
        parent,tid=self.parent();discussion.save_samples(self.store,tid,parent,[{'id':'1','text':'互赞有赞必回'}],'hot')
        topic=read_topic(self.store,tid);packet=topic_packet(topic,self.store.context())
        sample=next(e for e in packet['evidence'] if e['role']=='discussion')
        self.assertIn('engagement_exchange',sample['sample']['flags'])
        tools=GrowthTools(self.store);tools.call('read_topic',{'topic_id':tid});self.assertIn(sample['evidence_id'],tools.read_ids)

    def test_new_comment_changes_analysis_version_but_repeat_does_not(self):
        parent,tid=self.parent();first=read_topic(self.store,tid)['fingerprint']
        row={'id':'1','text':'想了解附近适合散步的路线'}
        discussion.save_samples(self.store,tid,parent,[row],'hot');scan(self.store);second=read_topic(self.store,tid)['fingerprint']
        self.assertNotEqual(first,second)
        discussion.save_samples(self.store,tid,parent,[row],'recent');scan(self.store)
        self.assertEqual(second,read_topic(self.store,tid)['fingerprint'])

    def test_official_search_form_is_post_parsed_as_unverified_background(self):
        html='<script>var docArr = '+json.dumps([{'title':'<em>返程</em>安排','url':'http://www.chinanews.com.cn/sh/actual.shtml',
            'content_without_tag':'这是查询命中的来源摘要。','pubtime':'2026-10-07 13:00:00'}])+';</script>'
        with patch('agent_v3.public_sources.read_public',return_value=(html.encode(),'https://sou.chinanews.com.cn/search/news')) as reader:
            rows=search_news('返程')
        self.assertEqual(reader.call_args.kwargs['method'],'POST');self.assertEqual(rows[0]['title'],'返程安排')
        self.assertEqual(rows[0]['source'],'search:返程')

    def test_background_cache_relinks_without_promoting_search_to_hotspot(self):
        _,tid=self.source();_,other=self.source('另一个待研究线索',identifier='other');before=len(queue(self.store))
        rows=[{'title':'搜索命中背景','url':'https://www.chinanews.com.cn/sh/background.shtml','source':'search:背景'}]
        with patch('agent_v3.research.search_news',return_value=rows) as search:
            first=research.background(self.store,tid,'背景');second=research.background(self.store,other,'背景')
        self.assertEqual(search.call_count,1);self.assertEqual(second['calls'],0);self.assertEqual(first['evidence_ids'],second['evidence_ids'])
        self.assertEqual(read_topic(self.store,other)['research_sources'][0]['research_state'],'unverified')
        scan(self.store);self.assertEqual(len(queue(self.store)),before)

    def test_gap_policy_prioritizes_real_discussion_on_public_post(self):
        _,tid=self.parent();p=research.plan(self.store,read_topic(self.store,tid))
        self.assertEqual(p['planner'],'policy_fallback');self.assertEqual(p['actions'][0]['tool'],'sample_discussion')

    def test_research_budget_saves_incomplete_gaps_and_real_tool_results(self):
        parent,tid=self.parent()
        with patch('agent_v3.discussion.fetch_page',return_value=([{'id':'1','text':'互赞有赞必回'}],1)) as fetch:
            result=research.run(self.store,topic_id=tid,max_calls=1)
        self.assertEqual(result['calls'],1);self.assertEqual(fetch.call_count,1)
        self.assertEqual(result['research_tasks'][0]['status'],'evidence_gaps_remaining')
        task=read_topic(self.store,tid)['research'];self.assertEqual(task['payload']['actions_executed'][0]['result']['results'][0]['saved'],1)

    def test_invalid_model_plan_falls_back_without_invented_tool_execution(self):
        _,tid=self.parent()
        class Model:
            supports_tasks=True
            def run_task(self,*args,**kwargs):return {'result':{'reason':'错误引用来源编号','actions':[{'tool':'sample_discussion','source_ref':11,'query':''}]}}
        with patch('agent_v3.discussion.fetch_page',return_value=([],0)):
            result=research.run(self.store,topic_id=tid,max_calls=1,model=Model())
        self.assertEqual(result['research_tasks'][0]['planner'],'policy_fallback')
        self.assertEqual(read_topic(self.store,tid)['research']['payload']['planner_error'],'ValueError')

    def test_stable_event_id_survives_source_title_update(self):
        eid,_=self.source();tracking.observe(self.store);tid=self.store.conn.execute('SELECT tracked_id FROM tracked_member').fetchone()[0]
        e=self.store.evidence([eid])[0];self.store.upsert_evidence({**e,'title':'更正后的事件标题','metrics':{}});self.store.conn.commit()
        scan(self.store);tracking.observe(self.store)
        self.assertEqual(self.store.conn.execute('SELECT tracked_id FROM tracked_member').fetchone()[0],tid)
        self.assertEqual(tracking.read_event(self.store,tid)['title'],'更正后的事件标题')

    def test_repeat_observation_does_not_create_fake_development(self):
        self.source();tracking.observe(self.store);before=self.store.conn.execute('SELECT COUNT(*) FROM tracked_change').fetchone()[0]
        tracking.observe(self.store);self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM tracked_change').fetchone()[0],before)

    def test_cross_domain_similarity_only_creates_pending_relation(self):
        pair,*_=self.pair();self.assertEqual(pair['status'],'pending')
        self.assertEqual(tracking.overview(self.store)['counts']['events'],2)
        self.assertEqual(tracking.overview(self.store)['counts']['confirmed_relations'],0)

    def test_fabricated_event_quotes_cannot_confirm_identity(self):
        pair,*_=self.pair()
        with self.assertRaises(ValueError):tracking.decide(self.store,pair['pair_id'],'same','双方是同一事件的报道','fixture',quotes=['来源没有说这句话']*2)
        self.assertEqual(tracking.overview(self.store)['counts']['events'],2)

    def test_stale_source_version_cannot_be_merged(self):
        pair,left,*_=self.pair();e=self.store.evidence([left])[0]
        self.store.upsert_evidence({**e,'body':'更新后的真实来源内容，身份判断必须重做。','metrics':{}});self.store.conn.commit();tracking.observe(self.store)
        with self.assertRaises(ValueError):tracking.decide(self.store,pair['pair_id'],'same','事件发生内容一致','fixture')

    def test_related_and_insufficient_preserve_distinct_events(self):
        for status in ('related','insufficient'):
            with self.subTest(status=status):
                if status=='insufficient':
                    self.store.close();self.store=Store(':memory:')
                pair,*_=self.pair();tracking.decide(self.store,pair['pair_id'],status,'来源没有证明同一次发生','fixture')
                self.assertEqual(tracking.overview(self.store)['counts']['events'],2)

    def test_merge_retains_all_sources_timeline_and_is_reversible(self):
        pair,left,right,*_=self.pair();before=self.store.conn.execute('SELECT COUNT(*) FROM tracked_change').fetchone()[0]
        tracking.decide(self.store,pair['pair_id'],'development','返程事件的后续回应，测试夹具','fixture')
        event=tracking.read_event(self.store,pair['left_id']);self.assertEqual({e['evidence_id'] for e in event['evidence']},{left,right})
        self.assertEqual(len(event['timeline']),before+1)
        tracking.withdraw(self.store,pair['pair_id'],'核对后需要分别研究，测试夹具','fixture')
        self.assertEqual(tracking.overview(self.store)['counts']['events'],2)
        self.assertNotEqual(tracking.root_id(self.store,pair['left_id']),tracking.root_id(self.store,pair['right_id']))
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM relation_history').fetchone()[0],2)
        self.assertGreater(self.store.conn.execute('SELECT COUNT(*) FROM tracked_change').fetchone()[0],before)

    def test_confirmed_identity_shares_sources_and_suppresses_duplicate_work(self):
        pair,left,right,lt,rt=self.pair();work.enqueue(self.store)
        tracking.decide(self.store,pair['pair_id'],'same','来源描述同一次发生，测试夹具','fixture');scan(self.store);work.enqueue(self.store)
        topic=read_topic(self.store,lt);self.assertEqual({e['evidence_id'] for e in topic['evidence']},{left,right})
        active=self.store.conn.execute("SELECT COUNT(*) FROM work_item WHERE status='pending'").fetchone()[0]
        self.assertEqual(active,1)
        tracking.withdraw(self.store,pair['pair_id'],'恢复两次独立研究，测试夹具','fixture');scan(self.store);work.enqueue(self.store)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM work_item WHERE status='pending'").fetchone()[0],2)

    def test_observation_time_is_separate_from_publication(self):
        eid,_=self.source();self.store.conn.execute("UPDATE evidence SET published_at='2026-10-07T01:00:00+00:00' WHERE evidence_id=?",(eid,));self.store.conn.commit()
        tracking.observe(self.store);event=tracking.read_event(self.store,self.store.conn.execute('SELECT tracked_id FROM tracked_member').fetchone()[0])
        self.assertNotEqual(event['first_observed_at'],event['evidence'][0]['published_at'])
        self.assertIn('系统观察顺序',event['note'])

    def test_offline_vectors_use_versioned_cache_without_loading_encoder(self):
        from agent_v2.store import stable_id
        key=stable_id('embedding_','城市散步')
        self.store.conn.execute('INSERT INTO semantic_embedding VALUES(?,?,?)',(key,semantic.VERSION,dump([1.0]+[0.0]*511)));self.store.conn.commit()
        with patch.object(semantic,'_failure','offline unavailable'):
            vectors,state=semantic.encode(self.store,['城市散步'])
        self.assertEqual(len(vectors[0]),512);self.assertEqual(state['cached'],1)

    def test_missing_local_model_fallback_still_never_merges(self):
        self.source();self.source('假期返程生活方式新安排',identifier='two');tracking.observe(self.store)
        with patch.object(semantic,'_failure','local model not installed'):
            result=tracking.recall(self.store)
        self.assertEqual(result['semantic']['method'],'character_overlap')
        self.assertEqual(tracking.overview(self.store)['counts']['events'],2)

    def test_sort_failure_does_not_block_working_comment_mode(self):
        parent,tid=self.parent();reply=requests.Response();reply.status_code=400
        with patch('agent_v3.discussion.fetch_page',side_effect=[([],0),requests.HTTPError(response=reply)]):
            result=discussion.sample(self.store,tid,parent['evidence_id'])
        self.assertEqual(result['status'],'partial');self.assertIsNone(discussion.provider_blocked(self.store,'taptap'))
        self.assertEqual(discussion.capability(self.store,'discussion:taptap:recent')['status'],'failed')

    def test_background_article_can_be_read_without_becoming_direct_evidence(self):
        _,tid=self.source();row={'title':'背景报道','description':'背景摘要','url':'https://www.chinanews.com.cn/sh/background.shtml','source':'search:背景'}
        with patch('agent_v3.research.search_news',return_value=[row]):ids=research.background(self.store,tid,'背景')['evidence_ids']
        p=research.plan(self.store,read_topic(self.store,tid));self.assertIn(ids[0],p['source_ids'])
        self.assertTrue(any(a['tool']=='read_detail' and p['source_ids'][a['source_ref']]==ids[0] for a in p['actions']))
        self.assertNotIn(ids[0],{e['evidence_id'] for e in read_topic(self.store,tid)['evidence']})

    def test_nested_merges_require_withdrawing_later_effect_first(self):
        pair,*_=self.pair();tracking.decide(self.store,pair['pair_id'],'same','同一事件，测试夹具','fixture')
        self.source('第三条后续报道',identifier='third');tracking.observe(self.store)
        with patch('agent_v3.tracking.semantic.pairs',return_value=([(0.92,0,1)],{'method':'fixture'})):tracking.recall(self.store)
        second=self.store.conn.execute("SELECT pair_id FROM event_relation WHERE status='pending'").fetchone()[0]
        tracking.decide(self.store,second,'development','该事件后续报道，测试夹具','fixture')
        with self.assertRaises(ValueError):tracking.withdraw(self.store,pair['pair_id'],'撤回早期合并判断','fixture')
        tracking.withdraw(self.store,second,'先撤回后续合并判断','fixture');tracking.withdraw(self.store,pair['pair_id'],'再撤回早期合并判断','fixture')
        self.assertEqual(tracking.overview(self.store)['counts']['events'],3)

    def test_observer_cannot_decide_or_withdraw_event_relations(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agent_v3.api import router
        app=FastAPI();app.include_router(router(lambda:{'actor':'fixture observer','role':'observer'}))
        with TestClient(app) as client,patch('agent_v3.service.relation_decision') as mutate:
            for action in ('decide','withdraw'):
                self.assertEqual(client.post('/api/v3/event-relations/test/'+action,json={'status':'same','reason':'不能修改的观察角色'}).status_code,403)
        mutate.assert_not_called()

    def test_model_identity_decision_rejects_title_only_merge(self):
        pair,*_=self.pair();rid=self.store.create_run('测试标题不可直接模型合并')
        value=tracking.read_relation(self.store,pair['pair_id'])
        class Model:
            model='fixture'
            def run_task(self,*args,**kwargs):return {'result':{'relation':'same','reason':'仅看标题推断同一事件','left_quote':value['left']['title'],'right_quote':value['right']['title']}}
        with self.assertRaises(ValueError):tracking.review_model(self.store,Model(),rid)
        self.assertEqual(tracking.overview(self.store)['counts']['events'],2)

    def test_sampling_refreshes_after_cache_expiry_even_with_existing_samples(self):
        parent,tid=self.parent()
        with patch('agent_v3.discussion.fetch_page',return_value=([{'id':'1','text':'期待寻找一起散步的朋友'}],1)):
            discussion.sample(self.store,tid,parent['evidence_id'])
        self.assertFalse(discussion.needs_sampling(self.store,parent['evidence_id'],'taptap'))
        self.store.conn.execute("UPDATE research_capability SET retry_at='2000-01-01T00:00:00+00:00'");self.store.conn.commit()
        p=research.plan(self.store,read_topic(self.store,tid));self.assertEqual(p['actions'][0]['tool'],'sample_discussion')

    def test_one_call_budget_can_read_recent_when_hot_is_cached(self):
        parent,tid=self.parent()
        with patch('agent_v3.discussion.fetch_page',return_value=([],0)):
            discussion.sample(self.store,tid,parent['evidence_id'],max_calls=1)
        with patch('agent_v3.discussion.fetch_page',return_value=([],0)) as fetch:
            result=discussion.sample(self.store,tid,parent['evidence_id'],max_calls=1)
        self.assertEqual(result['calls'],1);self.assertEqual(fetch.call_args.args[-1],'recent')

    def test_codes_and_referrals_are_preserved_without_inventing_user_needs(self):
        parent,tid=self.parent();rows=[{'id':'1','text':'KWIOBuPj12I'},{'id':'2','text':'9WxFOtreJZp，邀请帮就差一个人了，来个兄弟帮一下'},
                                    {'id':'3','text':'想找朋友一起探索城市散步的新路线'}]
        discussion.save_samples(self.store,tid,parent,rows,'hot');samples=discussion.read_samples(self.store,tid)
        flags={e['sample']['comment_id']:e['sample']['flags'] for e in samples}
        self.assertIn('code_or_link_only',flags['1']);self.assertIn('referral_solicitation',flags['2'])
        self.assertEqual(flags['3'],[]);self.assertEqual(research.inspect(self.store,read_topic(self.store,tid))['discussion']['informative_samples'],1)

    def test_historical_comment_is_not_counted_as_recent_demand(self):
        parent,tid=self.parent();discussion.save_samples(self.store,tid,parent,[{'id':'1','text':'想找伙伴一起体验散步的新路线',
            'published_at':(datetime.now(timezone.utc)-timedelta(days=20)).isoformat(timespec='seconds')}],'hot')
        gaps=research.inspect(self.store,read_topic(self.store,tid))['discussion']
        self.assertEqual(gaps['recent_72h_samples'],0);self.assertEqual(gaps['raw_samples'],1)

    def test_background_to_organic_news_uses_publication_eligibility(self):
        _,tid=self.source();old=(datetime.now(timezone.utc)-timedelta(days=10)).isoformat(timespec='seconds')
        raw={'title':'历史背景报道','url':'https://www.chinanews.com.cn/sh/history.shtml','description':'旧闻语境',
             'published_at':old,'source':'search:历史'}
        with patch('agent_v3.research.search_news',return_value=[raw]):eid=research.background(self.store,tid,'历史')['evidence_ids'][0]
        with patch('agent_v3.connectors.fetch',return_value=[raw|{'source':''}]):collect(self.store,'chinanews:society')
        self.assertEqual(self.store.evidence([eid])[0]['kind'],'news');scan(self.store)
        self.assertNotIn('历史背景报道',{t['title'] for t in queue(self.store)})

    def test_missing_client_configuration_is_retained_failure_not_process_exit(self):
        from unittest.mock import Mock
        parent,tid=self.parent();client=Mock();client.load_xua.side_effect=SystemExit(2)
        with patch('agent_v3.discussion.legacy',return_value=client),patch('agent_v3.discussion.read_public') as read:
            result=discussion.sample(self.store,tid,parent['evidence_id'])
        self.assertEqual(result['status'],'partial');self.assertIn('未配置',result['results'][0]['error']);read.assert_not_called()


if __name__=='__main__':unittest.main()
