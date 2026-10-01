#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""intel_stats / risk_insight / anomaly_lite 的单元测试（纯标准库 unittest）。

被测模块通过 importlib 从文件路径加载（中文目录名不做包导入）。
运行：python -m unittest discover -s L3_trend/tests -v
"""

from __future__ import annotations

import importlib.util
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = Path(__file__).resolve().parents[1]     # 本层目录（L3_trend）


def _load(name: str, layer: str = "L3_trend"):
    """跨层加载：分层后这三个模块已不在同一层，必须显式指定层。"""
    spec = importlib.util.spec_from_file_location(name, str(ROOT / layer / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


ri = _load("risk_insight", "L5_generation")   # 风险分层属生成层
stats = _load("intel_stats")                  # 情报统计属决策层
al = _load("anomaly_lite")                    # 异动感知属决策层


# ---------------------------------------------------------------- intel_stats

class TestTwoPropZtest(unittest.TestCase):
    def test_equal_props_zero_z(self):
        z, p, sig = stats.two_prop_ztest(10, 100, 10, 100)
        self.assertAlmostEqual(z, 0.0, places=8)
        self.assertAlmostEqual(p, 1.0, places=8)
        self.assertFalse(sig)

    def test_higher_prop_positive_z(self):
        z, p, sig = stats.two_prop_ztest(50, 100, 30, 100)
        self.assertGreater(z, 0)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertLess(p, 0.05)
        self.assertTrue(sig)

    def test_invalid_n_returns_none(self):
        for args in [(5, 0, 5, 10), (5, 10, 5, 0), (5, -1, 5, 10)]:
            z, p, sig = stats.two_prop_ztest(*args)
            self.assertIsNone(z)
            self.assertIsNone(p)
            self.assertIsNone(sig)

    def test_negative_or_out_of_range_counts_rejected(self):
        # 负计数或 x>n（数据完整性守卫）
        for args in [(-1, 10, 5, 10), (5, 10, -1, 10), (11, 10, 5, 10)]:
            z, p, sig = stats.two_prop_ztest(*args)
            self.assertIsNone(z)

    def test_degenerate_all_vs_none(self):
        # 全 1 vs 全 0：pooled=0.5 非退化，正常检验 → 极显著
        z, p, sig = stats.two_prop_ztest(10, 10, 0, 10)
        self.assertTrue(sig)
        self.assertLess(p, 0.001)

    def test_degenerate_pooled_zero(self):
        # 两侧全 0：pooled 方差为 0 → 退化处理，p=1
        z, p, sig = stats.two_prop_ztest(0, 10, 0, 10)
        self.assertEqual(p, 1.0)
        self.assertFalse(sig)

    def test_degenerate_same_extreme(self):
        z, p, sig = stats.two_prop_ztest(10, 10, 10, 10)
        self.assertEqual(p, 1.0)
        self.assertFalse(sig)

    def test_matches_known_value(self):
        # 27/68 vs 17/83（2026-07 实测窗，与探索脚本交叉核对）
        z, p, sig = stats.two_prop_ztest(27, 68, 17, 83)
        self.assertAlmostEqual(z, 2.5865, places=3)
        self.assertAlmostEqual(p, 0.0097, places=3)
        self.assertTrue(sig)


class TestWilsonCI(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(stats.wilson_ci(5, 0), (None, None))

    def test_all_fail_lower_bound_zero(self):
        lo, hi = stats.wilson_ci(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0.0)
        self.assertLessEqual(hi, 1.0)

    def test_all_success_upper_bound_one(self):
        lo, hi = stats.wilson_ci(10, 10)
        self.assertEqual(hi, 1.0)
        self.assertGreaterEqual(lo, 0.0)

    def test_contains_point_estimate(self):
        lo, hi = stats.wilson_ci(27, 68)
        point = 27 / 68
        self.assertLessEqual(lo, point)
        self.assertGreaterEqual(hi, point)


class TestWarnings(unittest.TestCase):
    def test_small_current_warns(self):
        warns = stats.sample_warnings(10, 100)
        self.assertTrue(any("本期" in w for w in warns))

    def test_thin_prior_warns(self):
        warns = stats.sample_warnings(100, 20)
        self.assertTrue(any("对照" in w for w in warns))

    def test_healthy_no_warns(self):
        self.assertEqual(stats.sample_warnings(100, 100), [])

    def test_allow_delta(self):
        self.assertTrue(stats.allow_delta_narrative(100, 60))
        self.assertFalse(stats.allow_delta_narrative(29, 60))
        self.assertFalse(stats.allow_delta_narrative(100, 20))


# ---------------------------------------------------------------- risk_insight

def make_row(**kw):
    base = {
        "review_id": "r1",
        "topic": "gacha",
        "sentiment": "负",
        "rhetoric": "none",
        "actionable": "是",
        "played_hours": 200.0,
        "is_recommend": False,
        "support_count": 0,
        "reply_count": 0,
        "text": "差评",
        "score_norm": 0.4,
        "publish_time_cn": "2026-07-15T10:00:00+08:00",
    }
    base.update(kw)
    return base


class TestParsers(unittest.TestCase):
    def test_parse_hours(self):
        self.assertEqual(ri.parse_hours("522.0"), 522.0)
        self.assertIsNone(ri.parse_hours(""))
        self.assertIsNone(ri.parse_hours("abc"))
        self.assertIsNone(ri.parse_hours(None))

    def test_parse_recommend(self):
        self.assertIs(ri.parse_recommend("True"), True)
        self.assertIs(ri.parse_recommend("False"), False)
        self.assertIs(ri.parse_recommend("true"), True)
        self.assertIsNone(ri.parse_recommend(""))
        self.assertIsNone(ri.parse_recommend(None))


class TestPayload(unittest.TestCase):
    """risk_insight.build_payload：分层计数、headline 三分支、主题表排序。"""

    @staticmethod
    def make_rows(n_neg_high=1, n_neg_low=34, n_disguised=0, n_pos=10):
        """合成行：负向 n_neg_high+n_neg_low 条，保证 n_neg>=30 走正常 headline。"""
        rows = []
        rid = 0
        for _ in range(n_neg_high):  # 高投入负向
            rows.append(make_row(review_id=f"h{rid}", played_hours=6000.0, sentiment="负"))
            rid += 1
        for _ in range(n_neg_low):  # 低投入负向
            rows.append(make_row(review_id=f"l{rid}", played_hours=20.0, sentiment="负"))
            rid += 1
        for _ in range(n_disguised):  # 伪装负向（正情绪 + 修辞）——不计入负向
            rows.append(make_row(review_id=f"d{rid}", played_hours=500.0, sentiment="正",
                                 rhetoric="sarcasm", actionable="否"))
            rid += 1
        for _ in range(n_pos):
            rows.append(make_row(review_id=f"p{rid}", played_hours=30.0, sentiment="正"))
            rid += 1
        return rows

    AVAIL = {"played_hours_available": True, "recommend_available": True,
             "support_available": False}

    def test_facts_layering(self):
        payload = ri.build_payload(self.make_rows(n_neg_high=5), self.AVAIL, "x.csv")
        f = payload["facts"]
        self.assertEqual(f["n_total"], 49)  # 5 高投入负向 + 34 低投入负向 + 10 正向
        self.assertEqual(f["n_neg"], 39)
        self.assertEqual(f["n_high_investment_negative"], 5)
        self.assertEqual(f["n_high_investment_not_recommend"], 5)  # 合成行默认不推荐
        self.assertEqual(f["n_actionable_negative"], 39)  # 合成行默认可行动

    def test_headline_normal(self):
        payload = ri.build_payload(self.make_rows(), self.AVAIL, "x.csv")
        self.assertIn("高投入", payload["headline"])
        self.assertIn("非用户流失预测", payload["caliber"])

    def test_headline_missing_hours(self):
        avail = {"played_hours_available": False, "recommend_available": True,
                 "support_available": False}
        payload = ri.build_payload(self.make_rows(), avail, "x.csv")
        self.assertIn("不可用", payload["headline"])

    def test_headline_tiny_sample(self):
        rows = [make_row(review_id="a", sentiment="负")]
        payload = ri.build_payload(rows, self.AVAIL, "x.csv")
        self.assertIn("n=1", payload["headline"])
        self.assertIn("仅供观察", payload["headline"])

    def test_topic_table_sorted_desc(self):
        payload = ri.build_payload(self.make_rows(), self.AVAIL, "x.csv")
        rates = [t["neg_rate"] or 0 for t in payload["topic_risk_table"]]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_boundaries_declared(self):
        payload = ri.build_payload(self.make_rows(), self.AVAIL, "x.csv")
        joined = " ".join(payload["boundaries"])
        self.assertIn("非用户流失预测", joined)
        self.assertIn("不可用", joined)


# ---------------------------------------------------------------- anomaly_lite

class TestDailyCounts(unittest.TestCase):
    def test_aggregate_and_skip_invalid(self):
        rows = [
            make_row(review_id="a", publish_time_cn="2026-07-15T10:00:00+08:00", sentiment="负"),
            make_row(review_id="b", publish_time_cn="2026-07-15T11:00:00+08:00", sentiment="正"),
            make_row(review_id="c", publish_time_cn="bad-date", sentiment="负"),
            make_row(review_id="d", publish_time_cn="", sentiment="负"),
        ]
        counts = al.daily_counts(rows)
        self.assertEqual(counts, {date(2026, 7, 15): [2, 1]})

    def test_agg_window(self):
        # 返回 (x, n)：先负向计数后总样本
        counts = {
            date(2026, 7, 1): [10, 5],
            date(2026, 7, 15): [20, 10],
        }
        self.assertEqual(al.agg_window(counts, date(2026, 7, 13), date(2026, 7, 19)), (10, 20))
        self.assertEqual(al.agg_window(counts, date(2026, 7, 1), date(2026, 7, 7)), (5, 10))


class TestCompareWeek(unittest.TestCase):
    def test_small_n_blocks_delta_narrative(self):
        # 本窗/对照各 20 样本（<30）：即使比例差大，也不允许「显著异动」叙事
        counts = {
            date(2026, 7, 14): [20, 10],
            date(2026, 7, 7): [20, 4],
        }
        c = al.compare_week(counts, date(2026, 7, 20))
        self.assertFalse(c["significant"])
        self.assertIn("样本不足", c["verdict"])

    def test_significant_with_enough_n(self):
        counts = {
            date(2026, 7, 14): [40, 20],
            date(2026, 7, 7): [40, 8],
        }
        c = al.compare_week(counts, date(2026, 7, 20))
        self.assertTrue(c["significant"])
        self.assertGreater(c["delta_pp"], 0)
        self.assertIn("显著上升", c["verdict"])
        self.assertEqual(c["id"], "week_vs_prev")
        self.assertEqual(c["period"]["n"], 40)

    def test_empty_windows_returns_none(self):
        self.assertIsNone(al.compare_week({}, date(2026, 7, 20)))


class TestTopicContributions(unittest.TestCase):
    def test_sorted_by_neg_delta(self):
        rows = [
            make_row(review_id="a", topic="gacha", publish_time_cn="2026-07-15T00:00:00+08:00", sentiment="负"),
            make_row(review_id="b", topic="gacha", publish_time_cn="2026-07-16T00:00:00+08:00", sentiment="负"),
            make_row(review_id="c", topic="client", publish_time_cn="2026-07-16T00:00:00+08:00", sentiment="负"),
        ]
        out = al.topic_contributions(
            rows, date(2026, 7, 14), date(2026, 7, 20), date(2026, 7, 7), date(2026, 7, 13)
        )
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(out[0]["topic"], "gacha")
        self.assertEqual(out[0]["neg_delta"], 2)

    def test_prior_window_counted(self):
        rows = [
            make_row(review_id="old", topic="gacha", publish_time_cn="2026-07-08T00:00:00+08:00", sentiment="负"),
        ]
        out = al.topic_contributions(
            rows, date(2026, 7, 14), date(2026, 7, 20), date(2026, 7, 7), date(2026, 7, 13)
        )
        gacha = [r for r in out if r["topic"] == "gacha"][0]
        self.assertEqual(gacha["neg0"], 1)
        self.assertEqual(gacha["neg1"], 0)
        self.assertEqual(gacha["neg_delta"], -1)


if __name__ == "__main__":
    unittest.main()
