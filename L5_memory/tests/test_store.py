#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""存储层测试：写入策略 / 版本化 / PII 拦截 / 可靠性分 / 短期记忆清扫。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_L5 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L5)
for _p in (_ROOT, _L5):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from memory.store import MemoryStore, WriteRejected  # noqa: E402


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(self.path)
        self.store = MemoryStore(self.path)

    def tearDown(self):
        self.store.close()
        if os.path.exists(self.path):
            os.remove(self.path)


class TestKnowledge(StoreTestBase):
    def test_upsert_versioning_supersedes_old(self):
        r1 = self.store.upsert_knowledge("business", "asset:forum", "论坛",
                                         {"capabilities": ["发帖"]})
        r2 = self.store.upsert_knowledge("business", "asset:forum", "论坛",
                                         {"capabilities": ["发帖", "投票"]})
        self.assertEqual(r2["version"], 2)
        self.assertEqual(r2["superseded"], r1["item_id"])
        old = self.store.db.query_one(
            "SELECT * FROM knowledge_item WHERE item_id=?", (r1["item_id"],))
        self.assertIsNotNone(old["valid_to"])          # 旧版收口，不删除（§23 历史可复现）

    def test_promote_requires_verified_by(self):
        r = self.store.upsert_knowledge("business", "s", "t", {})
        with self.assertRaises(WriteRejected):
            self.store.promote_knowledge(r["item_id"], verified_by="")
        out = self.store.promote_knowledge(r["item_id"], verified_by="运营A")
        self.assertEqual(out["tier"], "verified")

    def test_pii_rejected_on_knowledge(self):
        with self.assertRaises(WriteRejected):
            self.store.upsert_knowledge("entity", "g", "g",
                                        {"user_id": "12345678"})

    def test_candidates_business_entity_split(self):
        """回归：business/entity 同表，candidates 必须按 memory_type 分流。"""
        self.store.upsert_knowledge("business", "asset:x", "X", {})
        self.store.upsert_knowledge("entity", "arknights", "明日方舟", {})
        biz = self.store.candidates("business")
        ent = self.store.candidates("entity")
        self.assertEqual([b["subject"] for b in biz], ["asset:x"])
        self.assertEqual([e["subject"] for e in ent], ["arknights"])


class TestTrend(StoreTestBase):
    def test_save_and_query(self):
        out = self.store.save_trend({
            "event_id": "evt_1", "title": "联动爆了", "event_type": "collab",
            "entities": ["arknights"], "started_at": "2026-09-01T00:00:00+08:00",
            "ended_at": "2026-09-03T00:00:00+08:00", "lifecycle_duration_hours": 48,
            "content_count": 30, "platform_count": 2})
        self.assertEqual(out["tier"], "candidate")     # §19：无人工确认不 verified
        rows = self.store.candidates("trend")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entities"], ["arknights"])   # JSON 解码

    def test_missing_event_id_rejected(self):
        with self.assertRaises(WriteRejected):
            self.store.save_trend({"title": "no id"})


class TestCreativeAndDecision(StoreTestBase):
    def test_creative_deterministic_id_and_reject_kept(self):
        """§10：被拒绝的也要存；id 确定性（重跑不重复）。"""
        rec = {"analysis_id": "anl_1", "idea_id": "idea_1", "event_id": "evt_1",
               "creative_type": "ugc", "title": "投稿挑战", "passed": 0}
        r1 = self.store.save_creative(rec)
        r2 = self.store.save_creative(rec)
        self.assertEqual(r1["creative_id"], r2["creative_id"])
        self.assertEqual(r1["tier"], "agent_generated")   # AI 产出默认不信任（§53）
        self.store.record_decision("creative", r1["creative_id"], "reject",
                                   reason_text="开发太贵")
        row = self.store.db.query_one(
            "SELECT * FROM creative_memory WHERE creative_id=?", (r1["creative_id"],))
        self.assertEqual(row["human_decision"], "reject")
        self.assertEqual(row["tier"], "agent_generated")  # 拒绝不升级

    def test_approve_promotes_to_verified(self):
        r = self.store.save_creative({"analysis_id": "a", "idea_id": "i", "event_id": "e"})
        self.store.record_decision("creative", r["creative_id"], "approve",
                                   reason_text="可落地", reviewer_role="运营A")
        row = self.store.db.query_one(
            "SELECT tier, human_verified, verified_by FROM creative_memory"
            " WHERE creative_id=?", (r["creative_id"],))
        self.assertEqual((row["tier"], row["human_verified"], row["verified_by"]),
                         ("verified", 1, "运营A"))

    def test_reject_reason_taxonomy(self):
        r = self.store.save_creative({"analysis_id": "a", "idea_id": "i", "event_id": "e"})
        out = self.store.record_decision("creative", r["creative_id"], "reject",
                                         reason_text="预算不够")
        self.assertEqual(out["reason_code"], "TOO_EXPENSIVE")

    def test_invalid_decision_rejected(self):
        with self.assertRaises(WriteRejected):
            self.store.record_decision("creative", "x", "maybe")


class TestExperiment(StoreTestBase):
    def test_reliability_small_sample_low(self):
        """§34：sample=40 的 +80% 不许和大样本同权重。"""
        small = MemoryStore.reliability_score(sample_size=40, p_value=None,
                                              control_defined=True, context_present=False)
        big = MemoryStore.reliability_score(sample_size=5_000_000, p_value=0.001,
                                            control_defined=True, context_present=True)
        self.assertLess(small, 0.5)
        self.assertGreater(big, 0.8)

    def test_save_experiment_with_metrics(self):
        out = self.store.save_experiment(
            {"experiment_name": "排行榜", "creative_id": "cre_1", "event_id": "evt_1",
             "treatment": "排行榜", "control_definition": "普通话题页", "status": "completed",
             "context": {"placement": "首屏"}},
            metrics=[{"metric_name": "ugc_count", "baseline_value": 100,
                      "experiment_value": 150, "absolute_lift": 50,
                      "relative_lift": 0.5, "p_value": 0.01, "sample_size": 100000}])
        self.assertEqual(out["tier"], "verified")      # §19 真实实验发生
        self.assertGreater(out["reliability_score"], 0.8)
        self.assertEqual(self.store.best_relative_lift(out["experiment_id"]), 0.5)

    def test_pii_blocked_on_experiment(self):
        with self.assertRaises(WriteRejected):
            self.store.save_experiment({"experiment_name": "x", "user_id": "12345678"})


class TestShortTerm(StoreTestBase):
    def test_put_and_sweep(self):
        self.store.put_short_term("active_trend", {"event_id": "evt_1", "title": "t"},
                                  expires_at="2000-01-01T00:00:00+08:00")
        self.store.put_short_term("active_trend", {"event_id": "evt_2", "title": "t2"})
        self.assertEqual(len(self.store.short_term("active_trend")), 1)  # 过期不可见
        removed = self.store.sweep_expired()
        self.assertEqual(removed, 1)

    def test_pii_blocked_on_short_term(self):
        with self.assertRaises(WriteRejected):
            self.store.put_short_term("llm_inference", {"nickname": "张三"})


if __name__ == "__main__":
    unittest.main()
