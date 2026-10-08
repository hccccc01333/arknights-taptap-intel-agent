import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_v2.ingest import normalize
from agent_v2.store import dump, now_iso
from agent_v2.tests.test_core import ScriptedModel, final
from agent_v3.store import Store
from agent_v3.discovery import scan, queue, read_topic, title_key
from agent_v3.connectors import collect, CHANNELS
from agent_v3.tools import GrowthTools
from agent_v3.engine import run_agent
from agent_v3.reports import brief
from agent_v3.api import router


def source(title="下班后的松弛生活",platform="weibo",stamp=None):
    return normalize({"word":title,"title":title,"description":"玩家可分享地图并组队挑战。",
                      "observed_at":stamp or now_iso(),"rank":12,"url":"https://example.test/source"},platform,"test.csv")


def approve_test_topic(store,tid):
    # Explicit positive fixture precondition, never a production default.
    from agent_v3.risk import save
    t=read_topic(store,tid);e=t['evidence'][0]
    save(store,tid,t['fingerprint'],'fixture-risk',{'polarity':'neutral','level':'low',
        'reason':'测试来源为正常地图分享与组队玩法，无负面争议',
        'facts':[{'evidence_id':e['evidence_id'],'quote':e['body']}]},stage='safety_review')


def output(store,tid):
    topic=read_topic(store,tid);eid=topic["evidence"][0]["evidence_id"]
    result=final(eid);a=result["assessments"][0]
    a.update({"title":topic["title"],"topic_id":tid,"topic_fingerprint":topic["fingerprint"],
              "opportunity":{"audience_need":"工作后轻松参与", "spread_hook":"分享个人偏好", "taptap_bridge":"发现适合碎片时间的游戏", "why_now":"需先确认当前传播", "growth_goal":"引导潜在用户体验平台价值"}})
    c=a["creatives"][0]
    c.update({"growth_goal":"引导游戏发现", "hook":"你的下班放松方式是什么？", "distribution":"创作者内容入口", "resources":"一名运营，设计资源待确认", "journey":["看到话题内容","进入 TapTap 寻找感兴趣的游戏"],"reuse_material_ids":[],
              "deliverables":[{"kind":"copy","origin":"original","title":"传播文案","content":"下班后，给自己留十分钟轻松的游戏时间。","tags":["松弛","下班"],"evidence_ids":[eid],"rights_status":"原创草案，需核查措辞和产品承接"},
                              {"kind":"script","origin":"original","title":"短视频脚本","content":"0–3秒：展示下班场景；3–10秒：对比不同放松方式；10–15秒：邀请用户发现适合自己的游戏。","tags":["松弛"],"evidence_ids":[eid],"rights_status":"使用自行拍摄画面，制作前审核"}]})
    return result


class V3Tests(unittest.TestCase):
    def setUp(self):
        self.store=Store(":memory:");self.item=source();self.store.upsert_evidence(self.item);self.store.conn.commit();scan(self.store)
        self.tid=queue(self.store)[0]["topic_id"];self.eid=self.item["evidence_id"];approve_test_topic(self.store,self.tid)

    def tearDown(self):self.store.close()

    def test_non_game_hotspot_is_discovered(self):
        self.assertEqual(queue(self.store)[0]["title"],"下班后的松弛生活")

    def test_plain_search_and_old_post_are_background(self):
        item=source("普通检索标题","bilibili");item["kind"]="search"
        self.store.upsert_evidence(item);self.store.conn.commit();scan(self.store)
        self.assertNotIn("普通检索标题",[t["title"] for t in queue(self.store)])

    def test_second_scan_is_idempotent(self):
        before=self.store.conn.execute("SELECT COUNT(*) FROM topic_history").fetchone()[0]
        self.assertEqual(scan(self.store)["processed_evidence"],0)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM topic_history").fetchone()[0],before)

    def test_unchanged_poll_does_not_requeue_reviewed_topic(self):
        self.store.conn.execute("UPDATE topic SET reviewed_fingerprint=fingerprint");self.store.conn.commit()
        later={**self.item,"last_seen_at":(datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat(timespec='seconds')}
        self.store.upsert_evidence(later);self.store.conn.commit();scan(self.store)
        self.assertEqual(queue(self.store),[])

    def test_changed_text_requeues_and_invalidates_insight(self):
        self.store.conn.execute("UPDATE topic SET reviewed_fingerprint=fingerprint");self.store.conn.commit()
        later={**self.item,"body":"正文有新的表达内容","last_seen_at":(datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat(timespec='seconds')}
        self.store.upsert_evidence(later);self.store.conn.commit();scan(self.store)
        self.assertEqual(queue(self.store)[0]["state"],"updated")

    def test_future_observation_is_not_new_hotspot(self):
        item=source("未来热点",stamp=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
        self.store.upsert_evidence(item);self.store.conn.commit();scan(self.store)
        self.assertNotIn("未来热点",[t["title"] for t in queue(self.store)])

    def test_title_normalization_preserves_meaningful_punctuation(self):
        self.assertNotEqual(title_key("C++"),title_key("C#"))

    def test_same_title_cross_platform_is_candidate_signal(self):
        self.store.upsert_evidence(source(platform="baidu"));self.store.conn.commit();scan(self.store)
        topic=queue(self.store)[0]
        self.assertTrue(any(s["kind"]=="same_title_multiple_platforms" for s in topic["signals"]))
        self.assertEqual(self.store.overview()["counts"]["event"],0)

    def test_board_metrics_remain_scoped_to_channel(self):
        with patch('agent_v3.connectors.fetch',return_value=[{"word":"同一个话题","rank":3,"hot_score":10}]):
            a=collect(self.store,"baidu:realtime")
        with patch('agent_v3.connectors.fetch',return_value=[{"word":"同一个话题","rank":9,"hot_score":20}]):
            b=collect(self.store,"baidu:movie")
        self.assertEqual(a["evidence_ids"],b["evidence_ids"])
        points=list(self.store.conn.execute("SELECT channel_id,position FROM channel_observation"))
        self.assertEqual({r[0]:r[1] for r in points},{'baidu:realtime':3,'baidu:movie':9})
        for observation in self.store.evidence(a["evidence_ids"])[0]["observations"]:
            self.assertNotIn('rank',observation['metrics']);self.assertNotIn('hot_score',observation['metrics'])

    def test_rank_signal_requires_actual_half_hour_gap(self):
        start=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(timespec='seconds')
        for stamp,pos in ((start,12),(now_iso(),4)):
            self.store.conn.execute("INSERT INTO channel_observation VALUES(?,?,?,?,?)",('weibo:hot',self.eid,stamp,pos,'{}'))
        self.store.conn.commit();scan(self.store)
        self.assertTrue(any(s['kind']=='board_rank_rise' for s in queue(self.store)[0]['signals']))

    def test_unavailable_channel_backoff_is_visible(self):
        with patch('agent_v3.connectors.fetch',side_effect=ValueError('private token must not be logged')):
            first=collect(self.store,'baidu:movie');second=collect(self.store,'baidu:movie')
        self.assertEqual(first['status'],'failed');self.assertEqual(second['status'],'paused')
        self.assertNotIn('private token',first['error'])

    def test_delivery_requires_reading_topic(self):
        tools=GrowthTools(self.store);tools.call('read_evidence',{'evidence_ids':[self.eid]})
        with self.assertRaises(ValueError):tools.validate(output(self.store,self.tid))

    def test_empty_opportunity_is_rejected(self):
        tools=GrowthTools(self.store);tools.call('read_topic',{'topic_id':self.tid})
        value=output(self.store,self.tid);value['assessments'][0]['opportunity']['taptap_bridge']=''
        with self.assertRaises(ValueError):tools.validate(value)

    def test_creative_requires_actual_production_content(self):
        tools=GrowthTools(self.store);tools.call('read_topic',{'topic_id':self.tid})
        value=output(self.store,self.tid);value['assessments'][0]['creatives'][0]['deliverables']=[]
        with self.assertRaises(ValueError):tools.validate(value)

    def test_fabricated_source_quote_is_rejected(self):
        tools=GrowthTools(self.store);tools.call('read_topic',{'topic_id':self.tid})
        asset=output(self.store,self.tid)['assessments'][0]['creatives'][0]['deliverables'][0]
        asset.update(kind='source_quote',origin='source')
        with self.assertRaises(ValueError):tools.validate_asset(asset,{self.eid})

    def test_scripted_delivery_saves_reusable_assets_and_reviews_topic(self):
        value=output(self.store,self.tid);rid=self.store.create_run('测试跨领域机会')
        model=ScriptedModel([('read_topic',{'topic_id':self.tid}),('finish_research',value)])
        result=run_agent(self.store,rid,model)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(self.store.overview()['counts']['material'],2)
        self.assertEqual(queue(self.store),[])
        report=brief(self.store,1)
        self.assertIn('短视频脚本',report['markdown']);self.assertIn('用户路径',report['markdown'])

    def test_material_reuse_must_be_read_before_reference(self):
        tools=GrowthTools(self.store);tools.call('read_topic',{'topic_id':self.tid})
        value=output(self.store,self.tid);value['assessments'][0]['creatives'][0]['reuse_material_ids']=['invented']
        with self.assertRaises(ValueError):tools.validate(value)

    def test_update_reuses_event_and_identical_materials(self):
        value=output(self.store,self.tid);rid=self.store.create_run('first')
        run_agent(self.store,rid,ScriptedModel([('read_topic',{'topic_id':self.tid}),('finish_research',value)]))
        event_id=self.store.events()[0]['event_id'];second=output(self.store,self.tid)
        second['assessments'][0]['event_id']=event_id
        rid=self.store.create_run('update')
        result=run_agent(self.store,rid,ScriptedModel([('read_topic',{'topic_id':self.tid}),('finish_research',second)]))
        self.assertEqual(result['status'],'completed')
        self.assertEqual(self.store.overview()['counts']['event'],1)
        self.assertEqual(self.store.overview()['counts']['creative'],1)
        self.assertEqual(self.store.overview()['counts']['material'],2)
        self.assertEqual(len(self.store.get_event(event_id)['versions']),2)

    def test_same_title_new_occurrence_requires_reason(self):
        value=output(self.store,self.tid);rid=self.store.create_run('first')
        run_agent(self.store,rid,ScriptedModel([('read_topic',{'topic_id':self.tid}),('finish_research',value)]))
        tools=GrowthTools(self.store);tools.call('read_topic',{'topic_id':self.tid})
        second=output(self.store,self.tid)
        with self.assertRaises(ValueError):tools.validate(second)
        second['assessments'][0]['new_event_reason']='同名话题在不同日期由另一组参与者发起新的活动，需要另建事件。'
        self.assertTrue(tools.validate(second))

    def test_insight_cache_and_source_versions_survive_updates(self):
        tools=GrowthTools(self.store);tools.run_id='test-run';topic=tools.call('read_topic',{'topic_id':self.tid});s=topic['evidence'][0]
        packet={'evidence_id':self.eid,'content_hash':s['content_hash'],'summary':'分享和协作可能是参与动机','quotes':['玩家可分享地图并组队挑战。'],'emotions':['期待协作'],'needs':['寻找伙伴'],'spread_mechanics':['分享'],'patterns':[],'tags':['协作'],'unknowns':['没有读取评论']}
        self.assertEqual(tools.call('record_insight',packet)['status'],'saved')
        self.assertEqual(tools.call('record_insight',packet)['status'],'cached')
        self.store.upsert_evidence({**self.item,'body':'更新后的原文','last_seen_at':(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat()});self.store.conn.commit()
        changed=self.store.evidence([self.eid])[0]
        self.assertIsNone(self.store.insight(self.eid,changed['content_hash']))
        self.assertIsNotNone(self.store.insight(self.eid,s['content_hash']))

    def test_ai_failure_keeps_discovery_without_creatives(self):
        class Broken:
            model='unavailable'
            def decide(self,*_):raise RuntimeError('service unavailable')
        rid=self.store.create_run('测试服务失败');result=run_agent(self.store,rid,Broken())
        self.assertEqual(result['status'],'failed');self.assertTrue(queue(self.store))
        self.assertEqual(self.store.overview()['counts']['creative'],0)

    def test_channel_list_includes_broad_discovery(self):
        self.assertTrue({'baidu:realtime','baidu:movie','bilibili:popular'}.issubset({c['id'] for c in CHANNELS}))

    def test_viewer_cannot_write_or_start_cycles(self):
        app=FastAPI();app.include_router(router(lambda:{'actor':'观察E','role':'viewer'}))
        client=TestClient(app)
        for path in ('/api/v3/cycles','/api/v3/settings/context','/api/v3/settings/schedule','/api/v3/settings/model','/api/v3/settings/zen_quota','/api/v3/feedback'):
            self.assertEqual(client.post(path,json={}).status_code,403,path)

    def test_invalid_topic_is_not_silently_empty(self):
        with self.assertRaises(ValueError):read_topic(self.store,'missing')


if __name__=='__main__':unittest.main()
