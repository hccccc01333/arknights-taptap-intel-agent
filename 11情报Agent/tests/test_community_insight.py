#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""community_insight 的单元测试：互动统计、spam 启发式、日报节奏。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


ci = _load("community_insight_under_test", ROOT / "11情报Agent" / "community_insight.py")


def post(mid, comments=0, supports=0, ups=0, pv=0, summary="", pt=""):
    return {"moment_id": mid, "comments": comments, "supports": supports, "ups": ups,
            "pv_total": pv, "summary": summary, "title": "", "publish_time": pt}


class TestAnalyze(unittest.TestCase):
    def test_totals_and_hot_ranking(self):
        posts = [
            post("a", comments=2, supports=0, pv=39, summary="谁有金色福袋", pt="1790594510"),
            post("b", comments=0, supports=0, pv=5, summary="水", pt="1790594600"),
            post("c", comments=10, supports=50, ups=1, pv=5000, summary="版本长评", pt="1790594700"),
        ]
        p = ci.analyze(posts, [], "测试游戏")
        self.assertEqual(p["totals"]["posts"], 3)
        self.assertEqual(p["totals"]["comments_declared"], 12)
        self.assertEqual(p["totals"]["zero_comment_posts"], 1)
        self.assertEqual(p["hot_posts"][0]["moment_id"], "c")  # 互动分最高
        self.assertLessEqual(len(p["hot_posts"]), 10)

    def test_spam_heuristic_flags_and_boundaries(self):
        posts = [
            post("a", summary="正常讨论帖"),
            post("b", summary="开户送福利 代练私聊"),
            post("c", summary="活动兑换码兑换成功"),  # 含「兑换码」——已知可能误中活动
        ]
        p = ci.analyze(posts, [], "测试游戏")
        self.assertEqual(p["spam_heuristic"]["suspected_posts"], 2)
        self.assertIn("人工抽检", p["spam_heuristic"]["note"])

    def test_empty_posts_zero_division_safe(self):
        p = ci.analyze([], [], "测试游戏")
        self.assertEqual(p["totals"]["posts"], 0)
        self.assertIsNone(p["spam_heuristic"]["suspected_rate_pp"])

    def test_daily_buckets(self):
        import time as _t
        now = int(_t.time())
        posts = [
            post("a", pt=str(now)),                      # 今天
            post("b", pt=str(now - 86400)),              # 昨天
            post("c", pt=str(now - 86400 * 20)),         # 20 天前（窗口外）
        ]
        p = ci.analyze(posts, [], "测试游戏")
        days = {d["date"]: d["posts"] for d in p["daily_posts_14d"]}
        self.assertEqual(sum(days.values()), 2)  # 窗口外不计入


if __name__ == "__main__":
    unittest.main()
