#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""学习引擎测试（§29-§31 / §40 / §42 / §48）。

钉死的纪律：
  ① §30：Event 未闭合 / 实验未结束 → deferred，不拿半截信息冒充经验
  ② §31：lesson 每条必须带 evidence_ids；LLM 挂了回退规则版
  ③ §42：Playbook 只能以 candidate 产生，approved 之前检索层不可见
  ④ 无实验的 Case reliability 压低 —— 不许冒充强证据
"""

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

from memory.store import MemoryStore  # noqa: E402
from memory import cases  # noqa: E402
from memory.retrieval import RetrievalEngine  # noqa: E402


class CasesTestBase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(self.path)
        self.store = MemoryStore(self.path)
        self.engine = RetrievalEngine(self.store)

    def tearDown(self):
        self.store.close()
        if os.path.exists(self.path):
            os.remove(self.path)

    def seed_event(self, event_id="evt_1", creative_type="product",
                   lifecycle_hours=None, ended=False):
        trend = {"event_id": event_id, "title": f"{event_id} 热点", "event_type": "collab",
                 "entities": ["arknights"], "started_at": "2026-09-01T00:00:00+08:00",
                 "ended_at": "2026-09-03T00:00:00+08:00" if ended else None,
                 "lifecycle_duration_hours": lifecycle_hours,
                 "content_count": 30, "platform_count": 2}
        if ended:
            self.store.save_trend(trend)
        else:
            self.store.put_short_term("active_trend", trend,
                                      memory_id=f"stm_{event_id}")
        self.store.save_creative({"analysis_id": f"anl_{event_id}",
                                  "idea_id": f"idea_{event_id}", "event_id": event_id,
                                  "creative_type": creative_type, "title": "挑战",
                                  "growth_mechanism": "排行刺激投稿", "passed": 1,
                                  "primary_metric": "ugc_count"})


class TestDistillation(CasesTestBase):
    def test_deferred_without_creatives(self):
        out = cases.distill_event(self.store, "evt_none")
        self.assertEqual(out["status"], "deferred")
        self.assertIn("Creative Memory", out["reason"])

    def test_deferred_when_event_not_closed_and_not_forced(self):
        self.seed_event("evt_1", ended=False)     # 只在短期记忆（生命周期未闭合）
        out = cases.distill_event(self.store, "evt_1")
        self.assertEqual(out["status"], "deferred")
        self.assertIn("生命周期未闭合", out["reason"])

    def test_force_creates_case_marked_forced(self):
        self.seed_event("evt_1", ended=False)
        out = cases.distill_event(self.store, "evt_1", force=True)
        self.assertEqual(out["status"], "created")
        row = self.store.db.query_one("SELECT * FROM growth_case WHERE case_id=?",
                                      (out["case_id"],))
        self.assertIn("forced", row["applicability_conditions"])   # 越界必须留痕
        self.assertEqual(row["tier"], "candidate")

    def test_no_experiment_lesson_honest(self):
        """Creative ≠ Experiment（§12）：已采纳但没实验 → 效果未知，不编。"""
        self.seed_event("evt_1", ended=True)
        cre = self.store.candidates("creative", filters={"event_id": "evt_1"})[0]
        self.store.record_decision("creative", cre["creative_id"], "approve",
                                   reason_text="可落地", reviewer_role="运营A")
        out = cases.distill_event(self.store, "evt_1")
        self.assertIsNone(out["outcome"])
        self.assertLess(out["reliability"], 0.4)
        row = self.store.db.query_one("SELECT lessons FROM growth_case WHERE case_id=?",
                                      (out["case_id"],))
        lessons = self.store.db.loads(row["lessons"])
        self.assertTrue(all(l.get("evidence_ids") for l in lessons))  # §31 证据必带
        self.assertIn("未实验", lessons[0]["lesson"])

    def test_no_human_decision_lesson_also_honest(self):
        """创意停留在 Agent 产出、没人看过 → 如实说无结论可沉淀。"""
        self.seed_event("evt_1", ended=True)
        out = cases.distill_event(self.store, "evt_1")
        row = self.store.db.query_one("SELECT lessons FROM growth_case WHERE case_id=?",
                                      (out["case_id"],))
        self.assertIn("未进入人工决策", self.store.db.loads(row["lessons"])[0]["lesson"])

    def test_with_experiment_lesson_has_lift(self):
        self.seed_event("evt_1", ended=True)
        self.store.save_experiment(
            {"experiment_name": "排行榜", "creative_id": "cre_x", "event_id": "evt_1",
             "treatment": "T", "control_definition": "C", "status": "completed",
             "primary_metric": "ugc_count", "context": {}},
            metrics=[{"metric_name": "ugc_count", "relative_lift": 0.4,
                      "p_value": 0.01, "sample_size": 10000}])
        out = cases.distill_event(self.store, "evt_1")
        self.assertEqual(out["outcome"]["ugc_count"], 0.4)
        self.assertGreater(out["reliability"], 0.5)

    def test_deferred_when_experiment_unfinished(self):
        self.seed_event("evt_1", ended=True)
        self.store.save_experiment({"experiment_name": "x", "creative_id": "c",
                                    "event_id": "evt_1", "status": "running",
                                    "context": {}})
        out = cases.distill_event(self.store, "evt_1")
        self.assertEqual(out["status"], "deferred")
        self.assertIn("未结束", out["reason"])

    def test_anti_pattern_short_window_high_cost(self):
        """§40：热点窗口 < 48h + 产品开发型创意 → anti-pattern 记忆。"""
        self.seed_event("evt_1", creative_type="product", lifecycle_hours=24, ended=True)
        out = cases.distill_event(self.store, "evt_1")
        self.assertEqual(len(out["anti_patterns"]), 1)
        ap = self.store.candidates("anti_pattern")[0]
        self.assertEqual(ap["pattern"], "short_window + high_engineering_cost")
        # 同一 pattern 再犯 → 证据数增加、confidence 上升
        self.store.save_creative({"analysis_id": "a2", "idea_id": "i2",
                                  "event_id": "evt_1", "creative_type": "product",
                                  "title": "挑战2", "passed": 1})
        out2 = cases.distill_event(self.store, "evt_1", force=True)
        self.assertEqual(out2["status"], "updated")
        ap = self.store.candidates("anti_pattern")[0]
        self.assertEqual(len(ap["evidence_cases"]), 1)   # 同一事件同一 case_id，不重复计

    def test_llm_lesson_hook_with_fallback(self):
        self.seed_event("evt_1", ended=True)
        good = lambda d: [{"lesson": "LLM 总结", "evidence_ids": d["evidence_ids"][:1]}]
        out = cases.distill_event(self.store, "evt_1", lesson_llm=good)
        self.assertEqual(out["lessons"][0]["lesson"], "LLM 总结")
        self.assertEqual(out["lessons"][0]["source"], "llm")
        bad = lambda d: (_ for _ in ()).throw(RuntimeError("llm down"))
        out2 = cases.distill_event(self.store, "evt_1", lesson_llm=bad)
        self.assertNotIn("source", out2["lessons"][0])   # 回退规则版


class TestPlaybook(CasesTestBase):
    def _seed_strong_cases(self, trend_type="collab", n=3):
        for i in range(n):
            self.store.db.execute(
                "INSERT INTO growth_case(case_id, title, trend_type, event_id, entity_ids,"
                " audience_segments, user_motivations, opportunity_type, strategy,"
                " creative_type, creative_summary, growth_goal, primary_metric, outcome,"
                " lessons, anti_patterns, applicability_conditions, reliability_score,"
                " source_refs, embedding_model, tier, authority, source, source_type,"
                " confidence, version, valid_from, valid_to, verified_by, human_verified,"
                " created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"case_{trend_type}_{i}", f"t{i}", trend_type, f"evt_{i}", "[]", "[]",
                 "[]", "ugc", "排行榜", "ugc", "s", "ugc", "m", "[]", "[]", "[]", "[]",
                 0.7, "[]", None, "candidate", "P2", "test", "test", 0.6, 1,
                 None, None, None, 0, None, None))
        self.store.db.commit()

    def test_propose_deferred_with_too_few_cases(self):
        out = cases.propose_playbook(self.store, "collab")
        self.assertEqual(out["status"], "deferred")

    def test_propose_then_approve_then_retrievable(self):
        """§42 全链：candidate → 人工 approve → 检索可见；未 approve 不可见。"""
        self._seed_strong_cases()
        out = cases.propose_playbook(self.store, "collab")
        self.assertEqual(out["status"], "proposed")
        pb_id = out["playbook_id"]
        # candidate 状态检索不到
        res = self.engine.retrieve("collab 怎么打", memory_type="playbook",
                                   caller="creative_agent", log=False)
        self.assertEqual(res["items"], [])
        cases.approve_playbook(self.store, pb_id, approved_by="运营A")
        res2 = self.engine.retrieve("collab 怎么打", memory_type="playbook",
                                    caller="creative_agent", log=False)
        self.assertEqual(res2["items"][0]["playbook_id"], pb_id)

    def test_supersede_chain(self):
        self._seed_strong_cases()
        out = cases.propose_playbook(self.store, "collab")
        cases.approve_playbook(self.store, out["playbook_id"], approved_by="运营A")
        sup = cases.supersede_playbook(self.store, out["playbook_id"],
                                       ["新打法"], approved_by="运营B")
        self.assertEqual(sup["status"], "superseded")
        old = self.store.db.query_one("SELECT * FROM playbook WHERE playbook_id=?",
                                      (out["playbook_id"],))
        self.assertEqual(old["status"], "superseded")     # 留档不删除（§48）
        new = self.store.db.query_one("SELECT * FROM playbook WHERE playbook_id=?",
                                      (sup["new"],))
        self.assertEqual((new["status"], new["supersedes"]), ("approved", out["playbook_id"]))


if __name__ == "__main__":
    unittest.main()
