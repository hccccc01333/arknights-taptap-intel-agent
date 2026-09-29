#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/tests/test_platform_insight.py — 平台级发现流洞察单测（纯标准库）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))

import platform_insight as pi  # noqa: E402

TAGS = [
    {"hot_id": "1", "hashtag_id": "100", "title": "三角洲行动二周年", "description": "二周年发布会",
     "sort": "1", "page_view": "6270", "comment_count": "0", "is_new": "false", "crawled_at": "t"},
    {"hot_id": "2", "hashtag_id": "200", "title": "鸣潮3.7版本", "description": "鸣潮新版本上线",
     "sort": "2", "page_view": "300", "comment_count": "0", "is_new": "false", "crawled_at": "t"},
    {"hot_id": "3", "hashtag_id": "t:无id话题", "title": "无id话题", "description": "",
     "sort": "3", "page_view": "10", "comment_count": "0", "is_new": "false", "crawled_at": "t"},
]
POSTS = [
    {"moment_id": "m1", "group_id": "1", "app_id": "1", "app_title": "三角洲行动", "author_name": "a",
     "title": "三角洲好起来", "summary": "", "comments": "12", "supports": "0", "ups": "30",
     "pv_total": "675", "publish_time": "1", "hashtags_json": "[]", "hashtag_id": "100",
     "hashtag_title": "三角洲行动二周年", "source_type": "hashtag", "crawled_at": "t"},
    {"moment_id": "m2", "group_id": "1", "app_id": "1", "app_title": "三角洲行动", "author_name": "b",
     "title": "二周年福利", "summary": "", "comments": "8", "supports": "0", "ups": "12",
     "pv_total": "0", "publish_time": "1", "hashtags_json": "[]", "hashtag_id": "100",
     "hashtag_title": "三角洲行动二周年", "source_type": "hashtag", "crawled_at": "t"},
    {"moment_id": "m3", "group_id": "2", "app_id": "2", "app_title": "该游戏已下架", "author_name": "c",
     "title": "日常", "summary": "", "comments": "3", "supports": "0", "ups": "5",
     "pv_total": "100", "publish_time": "1", "hashtags_json": "[]", "hashtag_id": "",
     "hashtag_title": "", "source_type": "discover", "crawled_at": "t"},
]
COMMENTS = [{"moment_id": "m1", "comment_id": "c1", "author_name": "x", "content": "好评",
             "supports": "1", "publish_time": "1", "source_type": "hashtag", "crawled_at": "t"}]
PROFILES = [{"key": "wuthering-waves", "name": "鸣潮", "aliases": ["鸣潮", "库洛"]}]


class PlatformInsightTest(unittest.TestCase):
    def test_hot_board_order_and_game_match(self):
        p = pi.analyze(TAGS, POSTS, COMMENTS, PROFILES)
        self.assertEqual([h["sort"] for h in p["hot_board"]], [1, 2, 3])
        self.assertEqual(p["hot_board"][0]["title"], "三角洲行动二周年")
        # 别名命中：话题 2 命中已建档游戏「鸣潮」；话题 1/3 不命中
        self.assertEqual(p["hot_board"][1]["matched_games"], ["鸣潮"])
        self.assertEqual(p["hot_board"][0]["matched_games"], [])

    def test_topic_dig_aggregates_hashtag_posts_only(self):
        p = pi.analyze(TAGS, POSTS, COMMENTS, PROFILES)
        self.assertEqual(len(p["topic_dig"]), 1)
        d = p["topic_dig"][0]
        self.assertEqual(d["hashtag_id"], "100")
        self.assertEqual(d["posts"], 2)
        self.assertEqual(d["comments_declared"], 20)
        self.assertEqual(d["ups"], 42)
        # S5 discover 帖不计入话题深挖
        self.assertEqual(p["inputs"]["posts_discover"], 1)
        self.assertEqual(p["inputs"]["posts_hashtag"], 2)

    def test_event_signal_rules(self):
        p = pi.analyze(TAGS, POSTS, COMMENTS, PROFILES)
        kinds = {s["kind"] for s in p["event_signals"]}
        titles = [s["title"] for s in p["event_signals"]]
        # 热榜前 3（规则 hot_board_top_n）无条件进信号；page_view 未达 10000 的也不因 pv 重复进
        self.assertIn("hot_topic", kinds)
        self.assertIn("三角洲行动二周年", titles)
        self.assertIn("鸣潮3.7版本", titles)
        for s in p["event_signals"]:
            self.assertIsInstance(s["score"], int)

    def test_discover_ecosystem_and_delisted_note(self):
        p = pi.analyze(TAGS, POSTS, COMMENTS, PROFILES)
        eco = p["discover_ecosystem"]
        self.assertEqual(eco["posts"], 1)
        self.assertEqual(eco["delisted_game_posts"], 1)
        self.assertIn("下架", eco["delisted_note"])

    def test_empty_inputs_still_valid(self):
        p = pi.analyze([], [], [], [])
        self.assertEqual(p["inputs"]["posts"], 0)
        self.assertEqual(p["event_signals"], [])
        self.assertEqual(p["topic_dig"], [])

    def test_render_report_contains_key_sections(self):
        p = pi.analyze(TAGS, POSTS, COMMENTS, PROFILES)
        md = pi.render_report(p)
        for key in ("事件信号", "话题热榜", "话题深挖", "发现流生态"):
            self.assertIn(key, md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
