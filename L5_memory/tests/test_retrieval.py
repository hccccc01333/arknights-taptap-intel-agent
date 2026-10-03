#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检索引擎测试（§24-§28 / §45-§46 / §52）。

钉死的纪律：
  ① 权限（§54）：未授权的 memory_type 直接 blocked，返回空而不是报错
  ② §46 多样性：同类别不许占满 top_k
  ③ §27 performance filter：relative_lift ">0" 过滤真实生效
  ④ §52 每次检索落观测日志，mark_used 可回填
  ⑤ 空库返回空结果 + backend 如实标 lexical_bigram（不假装有向量）
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
from memory.retrieval import (RetrievalEngine, understand_query,  # noqa: E402
                              diversify, _item_category)


class RetrievalTestBase(unittest.TestCase):
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

    def seed_case(self, case_id: str, title: str, ctype: str,
                  reliability: float = 0.7, verified: int = 0,
                  outcome: dict = None) -> None:
        self.store.db.execute(
            "INSERT INTO growth_case(case_id, title, trend_type, event_id, entity_ids,"
            " audience_segments, user_motivations, opportunity_type, strategy, creative_type,"
            " creative_summary, growth_goal, primary_metric, outcome, lessons, anti_patterns,"
            " applicability_conditions, reliability_score, source_refs, embedding_model,"
            " tier, authority, source, source_type, confidence, version, valid_from, valid_to,"
            " verified_by, human_verified, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (case_id, title, "collab", f"evt_{case_id}", "[]", "[]", "[]", ctype,
             "UGC 排行榜", ctype, title, "ugc", "ugc_count",
             self.store.db.dumps(outcome), "[]", "[]", "[]", reliability, "[]", None,
             "verified" if verified else "candidate", "P2", "test", "test", 0.6, 1,
             None, None, None, verified, None, None))
        self.store.db.commit()


class TestQueryUnderstanding(unittest.TestCase):
    def test_alias_and_goals(self):
        qu = understand_query("明日方舟 UGC 投稿 分享", {"明日方舟": "arknights",
                                                       "方舟": "arknights"})
        self.assertIn("arknights", qu["entities"])
        self.assertIn("ugc", qu["goals"])
        self.assertIn("share", qu["goals"])

    def test_no_entities_is_neutral(self):
        qu = understand_query("随便什么", {})
        self.assertEqual(qu["entities"], [])


class TestHybridScoring(RetrievalTestBase):
    def test_metadata_and_performance_rank_higher(self):
        """同语义下：growth_goal 命中 + 正 lift + 可靠的案例应排前面（§25）。"""
        self.seed_case("case_weak", "角色捏脸活动", "ugc", reliability=0.2,
                       outcome={"ugc": None})
        self.seed_case("case_strong", "角色捏脸 UGC 投稿挑战", "ugc", reliability=0.9,
                       verified=1, outcome={"ugc_count": 0.62})
        res = self.engine.retrieve("角色捏脸 UGC 投稿", memory_type="case",
                                   caller="opportunity_agent", top_k=2, log=False)
        self.assertEqual(res["items"][0]["memory_id"], "case_strong")

    def test_empty_db_honest_empty(self):
        res = self.engine.retrieve("任意", memory_type="case", caller="opportunity_agent")
        self.assertEqual(res["items"], [])
        self.assertEqual(res["backend"], "lexical_bigram")   # §57 不假装有向量
        self.assertEqual(res["blocked"], "")

    def test_blocked_caller_gets_empty_with_reason(self):
        res = self.engine.retrieve("test", memory_type="decision",
                                   caller="opportunity_agent")
        self.assertEqual(res["items"], [])
        self.assertIn("无权", res["blocked"])

    def test_unknown_filter_column_rejected(self):
        with self.assertRaises(ValueError):
            self.store.candidates("case", filters={"nonexistent": 1})


class TestPerformanceFilter(RetrievalTestBase):
    def test_positive_lift_only(self):
        """§27：{"relative_lift": ">0"} —— 负效果 / 无效果的实验不进候选。"""
        self.store.save_experiment(
            {"experiment_name": "好实验", "creative_id": "c1", "event_id": "e1",
             "treatment": "A", "control_definition": "B", "status": "completed",
             "context": {}},
            metrics=[{"metric_name": "m", "relative_lift": 0.4, "p_value": 0.01,
                      "sample_size": 10000}])
        self.store.save_experiment(
            {"experiment_name": "差实验", "creative_id": "c2", "event_id": "e2",
             "treatment": "C", "control_definition": "D", "status": "completed",
             "context": {}},
            metrics=[{"metric_name": "m", "relative_lift": -0.1, "p_value": 0.4,
                      "sample_size": 10000}])
        res = self.engine.retrieve("实验", memory_type="experiment",
                                   caller="opportunity_agent", top_k=5,
                                   filters={"performance_filter": {"relative_lift": ">0"}},
                                   log=False)
        ids = [i["experiment_id"] for i in res["items"]]
        self.assertEqual(len(ids), 1)


class TestDiversity(RetrievalTestBase):
    def test_same_category_capped(self):
        """§46：5 条同款"捏脸挑战"+ 1 条别的 → 第一轮每类最多 2 条。"""
        scored = [(0.9 - i * 0.01, ({"creative_type": "ugc", "title": f"捏脸{i}"},
                                    {})) for i in range(5)]
        scored.append((0.5, ({"creative_type": "content", "title": "专题"}, {})))
        picked = diversify(scored, lambda p: _item_category(p[0]), top_k=4, per_category=2)
        cats = [_item_category(p[0]) for _, p in picked]
        self.assertEqual(cats.count("ugc"), 3)   # 第一轮 2 条 + 补齐轮 1 条
        self.assertIn("content", cats)           # 别的类别必须进得来

    def test_diversify_on_real_retrieval(self):
        for i in range(5):
            self.seed_case(f"c{i}", f"捏脸挑战{i}", "ugc")
        self.seed_case("cx", "联动专题", "content")
        res = self.engine.retrieve("捏脸", memory_type="case",
                                   caller="opportunity_agent", top_k=3, log=False)
        types = [i.get("creative_type") for i in res["items"]]
        self.assertIn("content", types)


class TestTrendShortTermMerge(RetrievalTestBase):
    def test_short_term_trends_retrievable(self):
        """§18：进行中热点存短期记忆，trend 检索必须能看到（horizon=short_term）。"""
        self.store.put_short_term("active_trend", {
            "event_id": "evt_live", "title": "联动活动讨论爆了", "event_type": "collab",
            "entities": ["arknights"]}, memory_id="stm_evt_live")
        res = self.engine.retrieve("联动 活动", memory_type="trend",
                                   caller="relevance_agent", top_k=3, log=False)
        self.assertTrue(any(i["memory_id"] == "stm:evt_live" and
                            i["horizon"] == "short_term" for i in res["items"]))


class TestObservability(RetrievalTestBase):
    def test_log_written_and_mark_used(self):
        """§52：日志带 query/candidates/returned；mark_used 回填利用率可测。"""
        self.seed_case("case_1", "角色 UGC 挑战", "ugc")
        res = self.engine.retrieve("角色 UGC", memory_type="case",
                                   caller="opportunity_agent", top_k=2)
        self.assertTrue(res.get("log_id"))
        row = self.store.db.query_one("SELECT * FROM retrieval_log WHERE log_id=?",
                                      (res["log_id"],))
        self.assertEqual(row["caller"], "opportunity_agent")
        self.assertGreater(row["candidate_count"], 0)
        RetrievalEngine.mark_used(self.store, res["log_id"], ["case_1"], "adopted")
        row = self.store.db.query_one("SELECT * FROM retrieval_log WHERE log_id=?",
                                      (res["log_id"],))
        self.assertEqual(self.store.db.loads(row["used_ids"]), ["case_1"])

    def test_case_package_shape(self):
        """§28：Case Package 有 context/strategy/result/lessons，不是裸 chunk。"""
        self.seed_case("case_pkg", "角色捏脸", "ugc", outcome={"ugc_count": 0.5})
        res = self.engine.retrieve("捏脸", memory_type="case",
                                   caller="opportunity_agent", top_k=1, log=False)
        pkg = res["items"][0]
        for key in ("case_id", "context", "strategy", "result", "lessons", "score"):
            self.assertIn(key, pkg)


if __name__ == "__main__":
    unittest.main()
