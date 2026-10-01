#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ferment_judge 单测：判据改写 + 降级纪律 + 已知盲区。"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

LAB = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ferment_judge", LAB / "ferment_judge.py")
fj = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(fj)


def make_platform() -> dict:
    return {
        "hot_board": [
            {"sort": 1, "title": "三角洲行动二周年", "hashtag_id": "1",
             "page_view": 6270, "comment_count": 0, "matched_games": []},
            {"sort": 2, "title": "米哈游反舞弊通报", "hashtag_id": "2",
             "page_view": 2524, "comment_count": 0, "matched_games": []},
        ],
        "topic_dig": [
            {"hashtag_id": "1", "title": "三角洲行动二周年",
             "pv_total": 675, "interaction_total": 38, "matched_games": []},
        ],
    }


class TestCollectTopics(unittest.TestCase):
    def test_dedup_across_board_and_dig(self):
        topics = fj.collect_topics(make_platform())
        # 三角洲同时出现在热榜与下钻里，应只留一条
        self.assertEqual(len(topics), 2)
        titles = [t["title"] for t in topics]
        self.assertEqual(len(titles), len(set(titles)))

    def test_keeps_matched_games_for_contrast(self):
        # 旧判据保留作对照，但不参与打分
        topics = fj.collect_topics(make_platform())
        self.assertTrue(all("matched_games" in t for t in topics))


class TestRuleFallback(unittest.TestCase):
    def test_known_blindspot_controversial_topic_scores_low(self):
        """已知盲区（记录用）：争议类话题热度不高 → 代理分低。

        「米哈游反舞弊通报」从社区视角很可发酵（玩家会吵），但 pv=2524、
        互动 0、标题不含关键词表 → 规则给 0 分。
        这条测试锁死的是「规则兜底不可当结论」这个事实，
        也正是必须上 LLM 的理由。
        """
        t = {"title": "米哈游反舞弊通报", "page_view": 2524, "interaction": 0}
        score, _ = fj.rule_score(t)
        self.assertLess(score, 35, "争议类话题本应可发酵，规则兜底却给低分——这是已知盲区")
        self.assertEqual(fj.verdict_of(score), "skip")

    def test_hot_topic_with_kind_hint_scores_higher(self):
        hot = {"title": "白银之城技术测试招募开启", "page_view": 16370, "interaction": 83}
        cold = {"title": "源初之结官方十大问答", "page_view": 1691, "interaction": 0}
        self.assertGreater(fj.rule_score(hot)[0], fj.rule_score(cold)[0])


class TestVerdict(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(fj.verdict_of(60), "act")
        self.assertEqual(fj.verdict_of(35), "watch")
        self.assertEqual(fj.verdict_of(34), "skip")


class TestJudgeDegradation(unittest.TestCase):
    def test_no_key_marks_degraded(self):
        res = fj.judge(fj.collect_topics(make_platform()), api_key=None)
        self.assertTrue(res["degraded"])
        self.assertEqual(res["mode"], "rule_fallback")
        self.assertIsNotNone(res["degrade_reason"])
        self.assertIn("不等于可发酵度", res["degrade_reason"])

    def test_llm_failure_falls_back(self):
        with mock.patch.object(fj.urllib.request, "urlopen", side_effect=TimeoutError):
            res = fj.judge(fj.collect_topics(make_platform()), api_key="sk-x")
        self.assertTrue(res["degraded"])
        self.assertEqual(res["mode"], "rule_fallback")

    def test_llm_success_uses_llm_scores(self):
        payload = {"results": [
            {"idx": 0, "score": 80, "verdict": "act", "reason": "有讨论动机",
             "suggested_action": "建话题：二周年回忆征集"},
            {"idx": 1, "score": 70, "verdict": "act", "reason": "争议性强",
             "suggested_action": "建话题：反舞弊怎么看"},
        ]}

        class _Resp:
            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(fj.urllib.request, "urlopen", return_value=_Resp()):
            res = fj.judge(fj.collect_topics(make_platform()), api_key="sk-x")
        self.assertFalse(res["degraded"])
        self.assertEqual(res["mode"], "llm")
        topics = {t["title"]: t for t in res["topics"]}
        # 争议话题被 LLM 判为 act —— 与规则兜底（skip）形成对照
        self.assertEqual(topics["米哈游反舞弊通报"]["verdict"], "act")
        self.assertEqual(res["summary"]["act"], 2)

    def test_sorted_by_score_desc(self):
        res = fj.judge(fj.collect_topics(make_platform()), api_key=None)
        scores = [t["ferment_score"] for t in res["topics"]]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestReportSafety(unittest.TestCase):
    def test_report_marks_degradation(self):
        res = fj.judge(fj.collect_topics(make_platform()), api_key=None)
        text = fj.render_report(res)
        self.assertIn("降级模式", text)
        self.assertIn("未命中建档游戏", text)


if __name__ == "__main__":
    unittest.main()
