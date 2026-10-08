import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from agent_v2.connectors import collect
from agent_v2.ingest import normalize
from agent_v2.reports import brief
from agent_v2.store import Store, now_iso
from agent_v2.tracking import compare_observations
from agent_v2.tests.test_core import sample, final


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(":memory:")
        self.item=sample(observed=now_iso())
        self.store.upsert_evidence(self.item)
        self.store.conn.commit()
        self.eid=self.item["evidence_id"]

    def tearDown(self):
        self.store.close()

    def save(self):
        run=self.store.create_run("测试研究")
        event=self.store.save_assessments(run,[final(self.eid)["assessments"][0]])[0]
        return run,event

    def test_weekly_brief_has_actions_sources_and_incomplete_history_disclosure(self):
        self.save()
        report=brief(self.store,7)
        self.assertEqual(report["counts"]["new_events"],1)
        self.assertEqual(len(report["actions"]),1)
        self.assertIn(self.item["url"],report["markdown"])
        self.assertTrue(any("历史尚未覆盖" in s for s in report["limitations"]))
        self.assertIn("玩家地图交流帖",report["markdown"])

    def test_empty_week_cannot_manufacture_recommendations(self):
        report=brief(self.store)
        self.assertEqual(report["actions"],[])
        self.assertIn("没有经研究形成的可行动提案",report["markdown"])

    def test_feedback_is_a_record_not_a_growth_claim(self):
        self.save()
        cid=self.store.overview()["creatives"][0]["creative_id"]
        self.store.add_feedback(cid,"运营测试","accepted","等待素材核查")
        report=brief(self.store)
        self.assertEqual(report["actions"][0]["decision"],"accepted")
        self.assertEqual(report["feedback"][0]["outcome"],{})

    def test_unknown_creative_cannot_receive_feedback(self):
        with self.assertRaises(ValueError):
            self.store.add_feedback("missing","operator","accepted","原因")

    def test_identical_proposal_is_not_delivered_twice_on_event_update(self):
        _,event=self.save()
        run=self.store.create_run("更新")
        update=final(self.eid)["assessments"][0];update["event_id"]=event
        self.store.save_assessments(run,[update])
        self.assertEqual(self.store.overview()["counts"]["creative"],1)
        self.assertEqual(len(brief(self.store)["actions"]),1)
        self.assertEqual(len(self.store.get_event(event)["changes"]),1)

    def test_shared_evidence_does_not_merge_distinct_events(self):
        _,first=self.save()
        run=self.store.create_run("另一事件")
        second=self.store.save_assessments(run,[final(self.eid)["assessments"][0]])[0]
        self.assertNotEqual(first,second)

    def test_delivery_is_atomic_if_second_event_cannot_be_saved(self):
        run=self.store.create_run("批次")
        invalid=final("not_in_store")["assessments"][0]
        with self.assertRaises(ValueError):
            self.store.save_assessments(run,[final(self.eid)["assessments"][0],invalid])
        self.assertEqual(self.store.overview()["counts"]["event"],0)
        self.assertEqual(self.store.overview()["counts"]["creative"],0)

    def test_quote_snapshot_survives_source_text_change(self):
        _,event=self.save()
        new=sample(observed=(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat())
        new["body"]="正文已经修改。"
        self.store.upsert_evidence(new);self.store.conn.commit()
        detail=self.store.get_event(event)
        self.assertEqual(detail["evidence"][0]["body"],"正文已经修改。")
        self.assertIn("玩家可分享地图",detail["source_snapshots"][0]["body"])

    def test_two_points_use_actual_interval_and_not_growth_acceleration(self):
        before=sample(observed=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat())
        before["metrics"]["views"]=50
        self.store.upsert_evidence(before);self.store.conn.commit()
        comparison=compare_observations(self.store,[self.eid])["comparisons"][0]["metrics"]["views"]
        self.assertEqual(comparison["status"],"comparable")
        self.assertEqual(comparison["delta"],50)
        self.assertAlmostEqual(comparison["per_hour"],25,delta=0.02)
        self.assertNotIn("acceleration",comparison)

    def test_counter_decrease_is_not_published_as_negative_growth(self):
        before=sample(observed=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat())
        before["metrics"]["views"]=200
        self.store.upsert_evidence(before);self.store.conn.commit()
        metric=compare_observations(self.store,[self.eid])["comparisons"][0]["metrics"]["views"]
        self.assertEqual(metric["status"],"counter_decreased_or_reset")
        self.assertNotIn("per_hour",metric)

    def test_observation_uses_last_real_seen_not_original_crawl_time(self):
        row=normalize({"title":"帖子","moment_id":"1","crawled_at":"2026-10-01T10:00:00+08:00","last_seen_at":"2026-10-07T10:00:00+08:00","first_seen_at":"2026-10-01T10:00:00+08:00"},"taptap","posts.csv")
        self.assertEqual(row["last_seen_at"],"2026-10-07T02:00:00+00:00")
        self.assertEqual(row["first_seen_at"],"2026-10-01T02:00:00+00:00")

    def test_connector_failure_is_persisted_and_backs_off(self):
        with patch("agent_v2.connectors.fetch",side_effect=ValueError("empty")) as fetch:
            first=collect(self.store,"bilibili")
            second=collect(self.store,"bilibili")
        self.assertEqual(first["status"],"failed")
        self.assertEqual(second["status"],"paused")
        self.assertEqual(fetch.call_count,1)

    def test_lease_rejects_concurrent_work_and_recovers_expired_run(self):
        old=self.store.create_run("中断研究")
        self.assertTrue(self.store.acquire(old))
        self.assertFalse(self.store.acquire("different"))
        self.store.conn.execute("UPDATE worker_lease SET expires_at='2020-01-01T00:00:00+00:00'");self.store.conn.commit()
        self.assertTrue(self.store.acquire("new_owner"))
        self.assertEqual(self.store.get_run(old)["status"],"interrupted")

    def test_invalid_reporting_period_is_rejected(self):
        with self.assertRaises(ValueError):brief(self.store,30)


class PermissionTests(unittest.TestCase):
    def test_viewer_cannot_collect_research_feedback_or_change_settings(self):
        from fastapi.testclient import TestClient
        from webapp.main import app,current_user
        prior=dict(app.dependency_overrides)
        app.dependency_overrides[current_user]=lambda:{"actor":"观察测试","role":"viewer"}
        try:
            client=TestClient(app)
            for path,body in [("/api/v2/collection",{}),("/api/v2/runs",{"task":"研究"}),("/api/v2/feedback",{"creative_id":"c","decision":"accepted","reason":"原因"}),("/api/v2/context",{}),("/api/v2/schedule",{})]:
                self.assertEqual(client.post(path,json=body).status_code,403,path)
        finally:
            app.dependency_overrides=prior


if __name__=="__main__":unittest.main()
