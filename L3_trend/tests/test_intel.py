#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""intel_stats 的单元测试（纯标准库 unittest）。

intel_stats 是 L3 的统计底座（两比例 z 检验 / Wilson CI / 样本量警告），
也被 L6 实验引擎复用（harness 之外的唯一跨层统计依赖）。
被测模块通过 importlib 从文件路径加载（中文目录名不做包导入）。
运行：python -m unittest discover -s L3_trend/tests -v
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, layer: str = "L3_trend"):
    """跨层加载：分层后模块不在同一层，必须显式指定层。"""
    spec = importlib.util.spec_from_file_location(name, str(ROOT / layer / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


stats = _load("intel_stats")                  # 情报统计属趋势层

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


if __name__ == "__main__":
    unittest.main()
