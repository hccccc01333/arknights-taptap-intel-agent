import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from agent_v2.store import now_iso
from agent_v2.tests.test_core import ScriptedModel
from agent_v3.store import Store
from agent_v3.connectors import collect
from agent_v3.discovery import scan, queue
from agent_v3.materials import capture, public_url
from agent_v3 import work, intelligence
from agent_v3.model import failure, gate, import_failure_history
from agent_v3.pipeline import process
from agent_v3.tools import GrowthTools
from agent_v3.tests.test_v3 import source, output, approve_test_topic


def analysis(job,eid,decision="archive"):
    return {"topic_id":job["topic_id"],"topic_fingerprint":job["fingerprint"],
            "summary":"原文提到玩家分享地图和组队，传播规模尚未验证。",
            "facts":[{"evidence_id":eid,"quote":"玩家可分享地图并组队挑战。"}],
            "emotions":["合作期待是分析假设"],"needs":["寻找伙伴可能是需求"],
            "spread_mechanics":["地图分享可能推动参与"],"content_forms":["玩法介绍"],
            "reusable_angles":["邀请伙伴协作"],"unknowns":["未读取评论或视频正文"],
            "source_asset_ids":[],"patterns":[],"opportunity":{
                "decision":decision,"reason":"目前缺乏业务承接证据，先保留情报。",
                "audience_need":"寻找伙伴" if decision=="opportunity" else "",
                "taptap_bridge":"实际社区交流入口需核查" if decision=="opportunity" else "",
                "growth_goal":"引导参与交流" if decision=="opportunity" else ""}}


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(":memory:");self.item=source();self.eid=self.item["evidence_id"]
        self.store.upsert_evidence(self.item);self.store.conn.commit();scan(self.store)
        self.tid=queue(self.store)[0]["topic_id"];approve_test_topic(self.store,self.tid)
        work.enqueue(self.store);self.rid=self.store.create_run("独立情报测试")

    def tearDown(self):self.store.close()

    def job(self):return work.claim(self.store,self.rid,"intelligence",topic_id=self.tid)

    def test_collection_saves_text_and_media_without_model_or_creative(self):
        raw={"bvid":"BVreference","title":"来源视频引用", "description":"这是实际视频简介，包含完整表达，但不是视频转录文字。",
             "pic":"https://i0.hdslb.com/bfs/archive/actual.jpg","url":"https://www.bilibili.com/video/BVreference"}
        with patch("agent_v3.connectors.fetch",return_value=[raw]):collect(self.store,"bilibili:popular")
        kinds={m["kind"] for m in self.store.source_assets()}
        self.assertEqual(kinds,{"source_text","video_reference","image_reference"})
        self.assertEqual(self.store.overview()["counts"]["creative"],0)
        self.assertTrue(all(m["source_version"] for m in self.store.source_assets()))

    def test_hotword_alone_is_not_counted_as_material(self):
        with patch("agent_v3.connectors.fetch",return_value=[{"word":"仅有热搜词"}]):collect(self.store,"baidu:realtime")
        self.assertEqual(self.store.source_assets(),[])

    def test_source_inventory_is_idempotent_and_versioned(self):
        self.store.upsert_evidence({**self.item,"body":"足够长的真实来源内容，用于核查来源版本与重复采集保存行为。"});self.store.conn.commit()
        with self.store.conn:first=capture(self.store,self.eid)
        with self.store.conn:self.assertEqual(first,capture(self.store,self.eid))
        later={**self.item,"body":"新的来源表达内容，这次修改正文，旧素材版本也应继续保留。","last_seen_at":(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(timespec="seconds")}
        self.store.upsert_evidence(later);self.store.conn.commit()
        with self.store.conn:second=capture(self.store,self.eid)
        self.assertNotEqual(first,second);self.assertEqual(len(self.store.source_assets()),2)

    def test_material_url_rejects_local_and_non_http_targets(self):
        for value in ("javascript:alert(1)","http://127.0.0.1/image","https://localhost/x","https://u:p@example.com/x"):
            self.assertIsNone(public_url(value))
        self.assertEqual(public_url("//i0.hdslb.com/test.jpg"),"https://i0.hdslb.com/test.jpg")

    def test_work_is_unique_for_same_topic_version(self):
        self.assertEqual(work.enqueue(self.store)["intelligence"],0)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM work_item").fetchone()[0],1)

    def test_changed_topic_retains_and_supersedes_old_work(self):
        old=self.store.conn.execute("SELECT job_id FROM work_item").fetchone()[0]
        self.store.upsert_evidence({**self.item,"body":"更新了正文表达，应该形成新版本情报任务。"});self.store.conn.commit();scan(self.store);work.enqueue(self.store)
        self.assertEqual(self.store.conn.execute("SELECT status FROM work_item WHERE job_id=?",(old,)).fetchone()[0],"superseded")
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM work_item").fetchone()[0],2)

    def test_interrupted_stage_resumes_after_lease_expiry(self):
        job=self.job()
        self.store.conn.execute("UPDATE work_item SET lease_until=?",("2000-01-01T00:00:00+00:00",));self.store.conn.commit()
        resumed=work.claim(self.store,self.rid,"intelligence")
        self.assertEqual(resumed["job_id"],job["job_id"])
        self.assertEqual(self.store.conn.execute("SELECT attempts FROM work_item").fetchone()[0],2)

    def test_archive_still_accumulates_intelligence_without_creative(self):
        job=self.job();payload=analysis(job,self.eid)
        result=intelligence.run(self.store,self.rid,job,ScriptedModel([("finish_intelligence",payload)]))
        work.finish(self.store,job["job_id"],"succeeded",result=result);work.enqueue(self.store)
        self.assertEqual(self.store.overview()["counts"]["topic_intelligence"],1)
        self.assertEqual(self.store.overview()["counts"]["creative"],0)
        self.assertIsNone(work.claim(self.store,self.rid,"creative"))

    def test_watch_analysis_can_save_reusable_expression(self):
        job=self.job();payload=analysis(job,self.eid,"watch")
        payload["patterns"]=[{"kind":"expression_pattern","origin":"inferred","title":"协作邀请表达",
                             "content":"通过邀请伙伴共同完成挑战建立参与理由。","tags":["协作"],"evidence_ids":[self.eid],"rights_status":"分析推断，使用前核查"}]
        intelligence.run(self.store,self.rid,job,ScriptedModel([("finish_intelligence",payload)]))
        self.assertEqual(len(self.store.assets()),1)
        self.assertEqual(self.store.overview()["counts"]["creative"],0)

    def test_opportunity_routes_a_separate_creative_job(self):
        job=self.job();payload=analysis(job,self.eid,"opportunity")
        result=intelligence.run(self.store,self.rid,job,ScriptedModel([("finish_intelligence",payload)]))
        work.finish(self.store,job["job_id"],"succeeded",result=result)
        self.assertEqual(work.enqueue(self.store)["creative"],1)
        creative=work.claim(self.store,self.rid,"creative")
        self.assertEqual(creative["topic_id"],self.tid)
        self.assertIn("context_",creative["prompt_version"])

    def test_empty_value_bridge_cannot_route_a_creative(self):
        job=self.job();payload=analysis(job,self.eid,"opportunity");payload["opportunity"]["taptap_bridge"]=""
        tools=GrowthTools(self.store);tools.call("read_topic",{"topic_id":self.tid})
        with self.assertRaises(ValueError):intelligence.validate(self.store,tools,job,payload,set())

    def test_unknown_or_fabricated_intelligence_sources_are_rejected(self):
        job=self.job();tools=GrowthTools(self.store);tools.call("read_topic",{"topic_id":self.tid})
        payload=analysis(job,self.eid);payload["facts"][0]["quote"]="这句话来源没有说过"
        with self.assertRaises(ValueError):intelligence.validate(self.store,tools,job,payload,set())
        payload=analysis(job,self.eid);payload["source_asset_ids"]=["fabricated"]
        with self.assertRaises(ValueError):intelligence.validate(self.store,tools,job,payload,set())

    def test_daily_free_quota_is_shared_and_collection_survives(self):
        failure(self.store,"openrouter/free","HTTP 429: 免费模型当天调用额度已用尽")
        self.assertEqual(gate(self.store,"nvidia/anything:free")["status"],"deferred")
        with patch("agent_v3.pipeline.GatedModel") as model:
            result=process(self.store,self.rid)
        model.assert_not_called();self.assertEqual(result["status"],"ai_deferred")
        self.assertEqual(self.store.conn.execute("SELECT attempts FROM work_item").fetchone()[0],0)
        with patch("agent_v3.connectors.fetch",return_value=[{"word":"限流期间新发现"}]):
            self.assertEqual(collect(self.store,"weibo:hot")["status"],"ok")

    def test_backoff_does_not_apply_to_a_different_provider(self):
        failure(self.store,"openrouter/free","HTTP 429: free-models-per-day")
        self.assertEqual(gate(self.store,"deepseek-chat")["status"],"ready")

    def test_switching_provider_can_wake_model_blocked_jobs(self):
        cooldown=failure(self.store,"openrouter/free","HTTP 429: free-models-per-day")
        work.defer_due(self.store,cooldown)
        self.assertIsNone(self.job())
        work.wake_provider_work(self.store)
        self.assertIsNotNone(self.job())

    def test_business_context_change_versions_creative_work(self):
        job=self.job();payload=analysis(job,self.eid,"opportunity")
        result=intelligence.run(self.store,self.rid,job,ScriptedModel([("finish_intelligence",payload)]))
        work.finish(self.store,job["job_id"],"succeeded",result=result);work.enqueue(self.store)
        old=self.store.conn.execute("SELECT job_id FROM work_item WHERE stage='creative'").fetchone()[0]
        context=self.store.context();context["goal"]="核查新的承接目标";self.store.set_context(context)
        self.assertEqual(work.enqueue(self.store)["creative"],1)
        self.assertEqual(self.store.conn.execute("SELECT status FROM work_item WHERE job_id=?",(old,)).fetchone()[0],"superseded")

    def test_stage_selection_rotates_channels_after_success(self):
        second=source("不同渠道的文化话题","bilibili")
        self.store.upsert_evidence(second)
        self.store.conn.execute("INSERT INTO channel_observation VALUES(?,?,?,?,?)",("weibo:hot",self.eid,now_iso(),1,"{}"))
        self.store.conn.execute("INSERT INTO channel_observation VALUES(?,?,?,?,?)",("bilibili:popular",second["evidence_id"],now_iso(),1,"{}"))
        self.store.conn.commit();scan(self.store);work.enqueue(self.store)
        self.store.conn.execute("UPDATE work_item SET bucket='weibo:hot' WHERE topic_id=?",(self.tid,));self.store.conn.commit()
        first=self.job();work.finish(self.store,first["job_id"],"succeeded",result={})
        next_job=work.claim(self.store,self.rid,"intelligence")
        self.assertEqual(next_job["bucket"],"bilibili:popular")

    def test_migrated_failure_does_not_extend_cooldown_every_start(self):
        self.store.finish(self.rid,"failed",error="HTTP 429: 免费模型当天调用额度已用尽",model="openrouter/free")
        import_failure_history(self.store);first=gate(self.store,"openrouter/free")["retry_at"]
        import_failure_history(self.store)
        self.assertEqual(gate(self.store,"openrouter/free")["retry_at"],first)

    def test_pipeline_intelligence_does_not_require_a_creative_result(self):
        row=dict(self.store.conn.execute("SELECT * FROM work_item").fetchone())
        model=ScriptedModel([("finish_intelligence",analysis(row,self.eid))])
        result=process(self.store,self.rid,model=model)
        self.assertEqual(result["status"],"intelligence_ready")
        self.assertEqual(len(result["intelligence"]),1)
        self.assertEqual(result["creative"],[])
        self.assertEqual(result["usage"]["total_tokens"],10)

    def test_pipeline_connects_intelligence_to_targeted_creative(self):
        row=dict(self.store.conn.execute("SELECT * FROM work_item").fetchone())
        value=output(self.store,self.tid)
        result=process(self.store,self.rid,model=ScriptedModel([("finish_intelligence",analysis(row,self.eid,"opportunity")),
                       ("read_topic",{"topic_id":self.tid}),("finish_research",value)]))
        self.assertEqual(result["status"],"completed")
        self.assertEqual(self.store.overview()["counts"]["topic_intelligence"],1)
        self.assertEqual(self.store.overview()["counts"]["creative"],1)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM work_item WHERE status='succeeded'").fetchone()[0],2)

    def test_independent_model_failure_preserves_work_and_no_fake_analysis(self):
        class Broken:
            model="test-model"
            def decide(self,*_):raise RuntimeError("service unavailable")
        result=process(self.store,self.rid,model=Broken())
        self.assertEqual(result["status"],"ai_deferred")
        self.assertEqual(self.store.overview()["counts"]["topic_intelligence"],0)
        self.assertEqual(self.store.conn.execute("SELECT status FROM work_item").fetchone()[0],"deferred")

    def test_source_reuse_requires_reading_and_persists_relation(self):
        long={**self.item,"body":self.item["body"]+" 这里补充来源内容，保证具有可以保存的完整说明。"}
        self.store.upsert_evidence(long);self.store.conn.commit()
        with self.store.conn:sids=capture(self.store,self.eid)
        scan(self.store)
        approve_test_topic(self.store,self.tid)
        value=output(self.store,self.tid);value["assessments"][0]["creatives"][0]["reuse_source_asset_ids"]=sids
        tools=GrowthTools(self.store);tools.call("read_topic",{"topic_id":self.tid})
        assessments=tools.validate(value);self.store.save_assessments(self.rid,assessments)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM source_asset_use").fetchone()[0],len(sids))

if __name__=="__main__":unittest.main()
