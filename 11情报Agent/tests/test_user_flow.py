#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/tests/test_user_flow.py — 用户流动分析单测（纯标准库）。

重点锁三件事：
  1. 流向必须**按时间序**区分进出（把「来之前玩过别的」算成流出是口径事故）
  2. 只出聚合，绝不出现单个用户标识（合规守卫）
  3. 样本不足时显式标注，不硬写结论
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))

import user_flow as uf  # noqa: E402

DAY = 86400
T0 = 1767225600  # 2026-01-01


def post(uid_hash: str, t: int, game: str) -> dict:
    return {"user_id_hash": uid_hash, "moment_id": f"m{t}{game}", "app_title": game,
            "publish_time": str(t), "publish_time_cn": ""}


# 甲：原神(1月) → 鸣潮(2月) → 绝区零(3月)   = 流入后流出
A = [post("u_high_1", T0, "原神"), post("u_high_1", T0 + 30 * DAY, "鸣潮"),
     post("u_high_1", T0 + 60 * DAY, "绝区零")]
# 乙：只在鸣潮            = 停留
B = [post("u_high_2", T0 + 10 * DAY, "鸣潮"), post("u_high_2", T0 + 20 * DAY, "鸣潮")]
# 丙：鸣潮(1月) → 崩铁(2月) → 鸣潮(3月)  = 流出后回流
C = [post("u_low_1", T0, "鸣潮"), post("u_low_1", T0 + 30 * DAY, "崩坏：星穹铁道"),
     post("u_low_1", T0 + 60 * DAY, "鸣潮")]
# 噪声：下架游戏不进主矩阵
N = [post("u_high_1", T0 + 5 * DAY, "该游戏已下架")]

POSTS = A + B + C + N
REVIEWS = [{"user_id": "418132051", "played_hours": "300"},
           {"user_id": "749655850", "played_hours": "20"}]


def make_payload(posts=None, reviews=None):
    """posts/reviews 传 None 才用默认；传 [] 就是真空（区分「未传」与「空」）。"""
    return uf.analyze(POSTS if posts is None else posts,
                      REVIEWS if reviews is None else reviews,
                      focus_game="鸣潮", high_hours=100.0, game_key="wuthering-waves")


def hash_of(plain_id: str) -> str:
    """用真实哈希构造足迹——C 组靠哈希关联评分区时长，测试里不能拿假哈希糊弄。"""
    return uf.hash_ids([plain_id])[plain_id]


class SequenceTest(unittest.TestCase):
    def test_collapse_merges_consecutive_same_game(self):
        seq = [(1, "鸣潮"), (2, "鸣潮"), (3, "原神"), (4, "原神"), (5, "鸣潮")]
        self.assertEqual(uf.collapse(seq), ["鸣潮", "原神", "鸣潮"])

    def test_build_sequences_sorted_by_time(self):
        s = uf.build_sequences([post("u1", 300, "B"), post("u1", 100, "A")])
        self.assertEqual([g for _, g in s["u1"]], ["A", "B"])


class FlowDirectionTest(unittest.TestCase):
    def test_flow_pairs_follow_time_order(self):
        p = make_payload()
        pairs = {(f["from"], f["to"]): f["jumps"] for f in p["A_flow"]["top_flows"]}
        self.assertIn(("鸣潮", "绝区零"), pairs)      # 流出
        self.assertIn(("原神", "鸣潮"), pairs)        # 流入
        self.assertNotIn(("绝区零", "鸣潮"), pairs)   # 反方向不存在

    def test_into_and_out_of_focus_are_separate(self):
        p = make_payload()
        into = {x["from"] for x in p["A_flow"]["into_focus"]}
        out = {x["to"] for x in p["A_flow"]["out_of_focus"]}
        self.assertIn("原神", into)
        self.assertIn("绝区零", out)
        # 崩铁既在流入（回流）也在流出 —— 双向流动是正常的，不能假设两榜互斥
        self.assertIn("崩坏：星穹铁道", into & out)

    def test_noise_games_excluded_from_matrix(self):
        p = make_payload()
        for f in p["A_flow"]["top_flows"]:
            self.assertNotIn("该游戏已下架", (f["from"], f["to"]))
        self.assertEqual(p["sample"]["posts_excluded_as_noise"], 1)

    def test_return_flow_counts(self):
        # 丙：鸣潮→崩铁→鸣潮，应记 1 次流出（鸣潮→崩铁）+ 1 次回流（崩铁→鸣潮）
        p = make_payload([post("x", 1, "鸣潮"), post("x", 2, "崩坏：星穹铁道"), post("x", 3, "鸣潮")], [])
        pairs = {(f["from"], f["to"]) for f in p["A_flow"]["top_flows"]}
        self.assertIn(("鸣潮", "崩坏：星穹铁道"), pairs)
        self.assertIn(("崩坏：星穹铁道", "鸣潮"), pairs)


class InvestmentGroupTest(unittest.TestCase):
    def test_moved_out_is_strictly_after_focus(self):
        # 甲在鸣潮之前玩原神（=流入），之后去绝区零（=流出）；只有后者算 moved_out
        h = hash_of("418132051")  # 该明文在 REVIEWS 里是 300h（高投入）
        p = make_payload([post(h, 1, "原神"), post(h, 2, "鸣潮"), post(h, 3, "绝区零")])
        self.assertEqual(p["C_investment"]["high"]["moved_out_users"], 1)
        self.assertEqual(p["C_investment"]["high"]["came_from_other_users"], 1)

    def test_no_move_out_when_only_before(self):
        # 只在鸣潮之前玩过别的 → moved_out 必须是 0（这是曾经踩过的口径坑）
        h = hash_of("418132051")
        p = make_payload([post(h, 1, "原神"), post(h, 2, "鸣潮")])
        self.assertEqual(p["C_investment"]["high"]["moved_out_users"], 0)
        self.assertEqual(p["C_investment"]["high"]["came_from_other_users"], 1)

    def test_high_low_split_by_threshold(self):
        hi, lo = hash_of("418132051"), hash_of("749655850")  # 300h / 20h
        posts = [post(hi, 1, "鸣潮"), post(lo, 2, "鸣潮")]
        p = make_payload(posts)
        self.assertEqual(p["C_investment"]["high"]["users"], 1)
        self.assertEqual(p["C_investment"]["low"]["users"], 1)

    def test_matched_users_only_counts_users_with_footprint(self):
        # 只有能同时关联上评分区时长 + 有社区足迹的人才进 C 组
        h = hash_of("418132051")
        p = make_payload([post(h, 1, "鸣潮"), post("no_such_hash", 2, "鸣潮")])
        self.assertEqual(p["C_investment"]["matched_users"], 1)

    def test_overall_rate_is_pool_weighted_not_sample_weighted(self):
        """分层取样把两组拉成 1:1，直接算样本会高估——必须按池比例还原。

        构造：高投入池 1 人（全流出 100%），低投入池 3 人（全不流出 0%）
              加权 = (100*1 + 0*3)/4 = 25.0
              不加权（样本里 1:1）= (100*1 + 0*1)/2 = 50.0
        """
        # 注意：足迹的哈希必须来自 reviews 里真实存在的明文，否则进不了 C 组
        hi = hash_of("418132051")   # 300h
        lo = hash_of("low0")        # 10h（低投入组只让它一个人有足迹）
        posts = [post(hi, 1, "鸣潮"), post(hi, 2, "绝区零"),  # 高投入：流出
                 post(lo, 1, "鸣潮")]                          # 低投入：不走
        reviews = [{"user_id": "418132051", "played_hours": "300"}] + [
            {"user_id": f"low{i}", "played_hours": "10"} for i in range(3)
        ]
        p = uf.analyze(posts, reviews, focus_game="鸣潮", high_hours=100.0, game_key="t")
        ow = p["C_investment"]["overall_weighted"]
        self.assertEqual(ow["pool_high"], 1)
        self.assertEqual(ow["pool_low"], 3)
        self.assertEqual(ow["moved_out_rate_pp"], 25.0)
        self.assertEqual(ow["naive_unweighted_pp"], 50.0)
        self.assertNotEqual(ow["moved_out_rate_pp"], ow["naive_unweighted_pp"])

    def test_empty_groups_do_not_crash(self):
        p = make_payload([], [])
        self.assertEqual(p["C_investment"]["low"]["moved_out_rate_pp"], None)
        self.assertEqual(p["sample"]["users"], 0)


class ComplianceTest(unittest.TestCase):
    def test_report_has_no_user_identifier(self):
        p = make_payload()
        md = uf.render_report(p)
        self.assertIsNone(uf.HASH_RE.search(md))

    def test_guard_rejects_hash_in_output(self):
        with self.assertRaises(ValueError):
            uf.assert_aggregate_only("用户 3a9b17c3913f72ba 去了绝区零")

    def test_guard_passes_clean_text(self):
        uf.assert_aggregate_only("| 鸣潮 | 绝区零 | 5 | 5 |")

    def test_privacy_block_present(self):
        p = make_payload()
        self.assertTrue(p["privacy"]["no_individual_records"])
        self.assertIn("aggregate_only", p["privacy"]["mode"])


class SampleDisciplineTest(unittest.TestCase):
    def test_small_sample_flagged_unreliable(self):
        p = make_payload()  # 3 个用户 < 30 门槛
        self.assertFalse(p["sample"]["reliable"])
        self.assertIn("样本不足", uf.render_report(p))

    def test_large_sample_reliable(self):
        posts = [post(f"u{i}", T0 + i, "鸣潮") for i in range(40)]
        p = make_payload(posts, [])
        self.assertTrue(p["sample"]["reliable"])
        self.assertNotIn("样本不足", uf.render_report(p))


if __name__ == "__main__":
    unittest.main(verbosity=2)
