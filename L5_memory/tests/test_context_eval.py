#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Context Builder（§43/§44）与 Memory Evaluation（§50-§52）测试。"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_L5 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L5)
for _p in (_ROOT, _L5):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from memory.store import MemoryStore  # noqa: E402
from memory.retrieval import RetrievalEngine  # noqa: E402
from memory.context import ContextBuilder  # noqa: E402
from memory import evaluation  # noqa: E402


class ContextEvalBase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(self.path)
        self.store = MemoryStore(self.path)
        self.engine = RetrievalEngine(self.store)
        self.builder = ContextBuilder(self.store, self.engine)

    def tearDown(self):
        self.store.close()
        if os.path.exists(self.path):
            os.remove(self.path)


class TestContextBuilder(ContextEvalBase):
    def test_every_registered_agent_gets_its_recipe(self):
        """§44：不同 Agent 不同 Memory —— 各自的 sections 至少能空跑出结构。"""
        for agent in ("relevance_agent", "opportunity_agent", "creative_agent",
                      "evaluator", "research_agent"):
            ctx = self.builder.build(agent, query="测试")
            self.assertEqual(ctx["agent_type"], agent)
            self.assertIn("sections", ctx)
            self.assertIn("log_ids", ctx)

    def test_unknown_agent_rejected(self):
        with self.assertRaises(ValueError):
            self.builder.build("super_rag_agent")     # 超级 RAG 不存在（§44）

    def test_sections_differ_by_agent(self):
        """同一记忆，Relevance 拿 business，Opportunity 拿 experiment —— 互不串门。"""
        self.store.upsert_knowledge("business", "asset:forum", "论坛", {"c": 1})
        self.store.save_experiment({"experiment_name": "排行实验", "creative_id": "c",
                                    "event_id": "e", "status": "completed",
                                    "treatment": "T", "control_definition": "C",
                                    "context": {}})
        rel = self.builder.build("relevance_agent", query="论坛 产品能力")
        opp = self.builder.build("opportunity_agent", query="排行实验")
        self.assertIn("business", rel["sections"])
        self.assertNotIn("business", opp["sections"])
        self.assertIn("experiment", opp["sections"])
        self.assertNotIn("experiment", rel["sections"])

    def test_provenance_carries_tier(self):
        """provenance 带记忆的信任级别 —— Agent 引用时可知这条记忆有多硬。"""
        self.store.upsert_knowledge("business", "asset:push", "Push", {})
        ctx = self.builder.build("relevance_agent", query="Push 产品能力")
        prov = ctx["provenance"]
        self.assertTrue(all({"memory_type", "memory_id", "tier"} <= set(p) for p in prov))


class TestEvaluation(ContextEvalBase):
    def test_utilization_none_before_logs(self):
        """§50 诚实纪律：没有日志 → None/insufficient，不返回 0 冒充。"""
        self.assertIsNone(evaluation.utilization(self.store))
        summary = evaluation.summary(self.store)
        self.assertEqual(summary["utilization"]["status"], "insufficient_data")
        self.assertEqual(summary["improvement_lift_ab"]["status"], "insufficient_data")

    def test_utilization_counts_unmarked(self):
        self.engine.retrieve("q1", memory_type="case", caller="opportunity_agent", top_k=3)
        util = evaluation.utilization(self.store)
        self.assertEqual(util["n_unmarked_logs"], util["n_returned"])
        self.assertIsNone(util["utilization"])

    def test_utilization_after_mark_used(self):
        self.store.upsert_knowledge("business", "asset:push", "Push", {})
        res = self.engine.retrieve("Push", memory_type="business",
                                   caller="relevance_agent", top_k=2)
        ids = [i["memory_id"] for i in res["items"]]
        RetrievalEngine.mark_used(self.store, res["log_id"], ids[:1])
        util = evaluation.utilization(self.store)
        self.assertGreater(util["n_used"], 0)
        self.assertEqual(util["utilization"], round(util["n_used"] / util["n_returned"], 4))

    def test_case_reuse_rate(self):
        self.store.upsert_knowledge("business", "asset:x", "X", {})
        self.assertIsNone(evaluation.case_reuse_rate(self.store))   # 无 case 无日志

    def test_grounding_rate_against_fake_l4(self):
        """Grounding Rate：L4 创意 source_refs 引用了 L5 记忆 id 的占比。"""
        fd, l4path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(l4path)
        conn = sqlite3.connect(l4path)
        conn.execute("CREATE TABLE intelligence_creative (idea_id TEXT, source_refs TEXT)")
        conn.execute("INSERT INTO intelligence_creative VALUES('i1', '{\"memory\": \"case_abc\"}')")
        conn.execute("INSERT INTO intelligence_creative VALUES('i2', '{\"evidence\": \"evt_1\"}')")
        conn.commit()
        conn.close()
        out = evaluation.grounding_rate(self.store, l4_db=l4path)
        self.assertEqual(out["n_creatives"], 2)
        self.assertEqual(out["grounded"], 1)
        self.assertEqual(out["grounding_rate"], 0.5)
        os.remove(l4path)

    def test_grounding_rate_none_without_l4(self):
        self.assertIsNone(evaluation.grounding_rate(self.store, l4_db="Z:/nope.sqlite3"))

    def test_freshness_counts_expired(self):
        self.store.upsert_knowledge("business", "a", "A", {},
                                    valid_to="2000-01-01T00:00:00+08:00")
        self.store.upsert_knowledge("business", "b", "B", {})
        fresh = evaluation.freshness(self.store)["knowledge_item"]
        self.assertEqual((fresh["total"], fresh["expired"]), (2, 1))
        self.assertEqual(fresh["fresh_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()
