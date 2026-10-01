#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L4_decision/tests/test_topic_tracker.py — 话题追踪状态机测试。

覆盖重点：
  · 话题身份归一（该合的合、不该合的不合）
  · 状态迁移判定（看变化率而非绝对值）
  · 跨天记忆（这是「追踪」的定义性能力）
  · 存储层的幂等与事务
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "L4_decision"))

import topic_tracker as tt  # noqa: E402

TZ = timezone(timedelta(hours=8))


def _tmp_db() -> str:
    d = tempfile.mkdtemp()
    return str(Path(d) / "t.sqlite3")


class NormalizeTest(unittest.TestCase):
    """话题身份归一——跨天认出同一话题的基础。"""

    def test_fullwidth_punctuation_folds(self):
        self.assertEqual(tt.normalize_title("测试：话题！"), "测试:话题!")
        self.assertEqual(tt.normalize_title("测试:话题!"), "测试:话题!")

    def test_hashtag_wrapper_stripped(self):
        self.assertEqual(tt.normalize_title("#三角洲行动二周年#"), "三角洲行动二周年")
        self.assertEqual(tt.normalize_title("三角洲行动二周年"), "三角洲行动二周年")

    def test_whitespace_collapsed_and_case_folded(self):
        self.assertEqual(tt.normalize_title("  ArkNights   二周年 "), "arknights 二周年")

    def test_hashtag_id_preferred_over_title(self):
        """有 hashtag_id 时用 id——它跨天稳定，比标题可靠。"""
        k1 = tt.topic_key("三角洲行动二周年", "2537190")
        k2 = tt.topic_key("三角洲行动二周年", "2537190")
        self.assertEqual(k1, k2)
        self.assertTrue(k1.startswith("hid:"))

    def test_same_topic_different_title_wording_folds_by_id(self):
        """标题措辞漂移但 id 相同时仍认作同一话题。"""
        a = tt.topic_key("三角洲行动二周年", "2537190")
        b = tt.topic_key("三角洲行动 二周年", "2537190")
        self.assertEqual(a, b)

    def test_falls_back_to_title_when_no_id(self):
        k = tt.topic_key("卡厄思梦境换帅", "")
        self.assertEqual(k, "tit:卡厄思梦境换帅")

    def test_metric_kind_part_of_identity(self):
        """★ 回归测试：不同指标必须是不同序列。

        实测 bug（2026-09-29）：同一话题在 hot_board（浏览量 6270）与
        topic_dig（互动量 38）各采样一次，若共用 key，序列变成
        [6270, 38, 6583, 39, ...]，状态机把「换了指标」误读成
        「跌到 0.6% → 退潮」，下次又误读成「涨 173× → 爆发」。
        """
        pv = tt.topic_key("三角洲行动二周年", "2537190", "page_view")
        it = tt.topic_key("三角洲行动二周年", "2537190", "interaction")
        self.assertNotEqual(pv, it, "不同指标不能共用 key")

    def test_empty_title_yields_empty_key(self):
        self.assertEqual(tt.normalize_title(""), "")
        self.assertEqual(tt.topic_key("", ""), "tit:")


class ClassifyTest(unittest.TestCase):
    """状态迁移判定——核心是看变化率而不是绝对值。"""

    def test_no_transition_when_prev_metric_zero(self):
        """上次为 0 时不能算比值，否则会除零或产生虚假爆发。"""
        state, ratio, reason = tt.classify_transition("冒头", 0, 9999, 3)
        self.assertEqual(state, "冒头")
        self.assertIsNone(ratio)
        self.assertIn("无法计算", reason)

    def test_insufficient_samples_holds_state(self):
        """只有一次历史时不足以判趋势。"""
        state, ratio, _ = tt.classify_transition("冒头", 100, 200, 1)
        self.assertEqual(state, "冒头")
        self.assertAlmostEqual(ratio, 2.0)

    def test_first_comparison_burst_still_fires(self):
        """样本不足但涨幅极大时，直接记爆发——避免错过快起快爆的话题。"""
        state, ratio, reason = tt.classify_transition("冒头", 100, 500, 1)
        self.assertEqual(state, "爆发")
        self.assertIn("直接记为爆发", reason)

    def test_rising_detected(self):
        state, ratio, _ = tt.classify_transition("冒头", 100, 160, 5)
        self.assertEqual(state, "升温")
        self.assertAlmostEqual(ratio, 1.6)

    def test_burst_detected(self):
        state, _, _ = tt.classify_transition("升温", 100, 400, 5)
        self.assertEqual(state, "爆发")

    def test_fade_detected(self):
        state, _, _ = tt.classify_transition("爆发", 100, 50, 5)
        self.assertEqual(state, "退潮")

    def test_stable_holds_state(self):
        """小幅波动不迁移——避免状态抖动导致推送轰炸。"""
        state, _, reason = tt.classify_transition("升温", 100, 110, 5)
        self.assertEqual(state, "升温")
        self.assertIn("未达迁移阈值", reason)

    def test_high_absolute_but_flat_stays_unchanged(self):
        """绝对值很高但没变化 → 不误判为爆发。这是「看变化率」的关键验证。"""
        state, _, _ = tt.classify_transition("冒头", 100000, 105000, 5)
        self.assertEqual(state, "冒头")


class MemoryTest(unittest.TestCase):
    """跨天记忆——「追踪」的定义性能力。"""

    def setUp(self):
        self.db = _tmp_db()
        self.con = tt.connect(self.db)

    def tearDown(self):
        self.con.close()

    def test_first_sample_records_冒头(self):
        t0 = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)
        samples = [{"title": "话题A", "hashtag_id": "1", "metric": 100,
                    "metric_kind": "page_view", "source": "S4"}]
        tr = tt.record_samples(self.con, samples, now=t0)
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]["to"], "冒头")
        self.assertIsNone(tr[0]["from"])

    def test_second_day_recognizes_same_topic(self):
        """关键：第二天同一话题不会被当成新话题，且能算出变化。"""
        t0 = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)
        t1 = datetime(2026, 9, 30, 8, 0, tzinfo=TZ)
        s = [{"title": "话题A", "hashtag_id": "1", "metric": 100,
              "metric_kind": "page_view", "source": "S4"}]
        tt.record_samples(self.con, s, now=t0)

        s2 = [dict(s[0], metric=400)]
        tr = tt.record_samples(self.con, s2, now=t1)

        self.assertEqual(len(tr), 1, "同一话题应被认出，只产生一条迁移记录")
        self.assertEqual(tr[0]["to"], "爆发")

        row = self.con.execute(
            "SELECT * FROM topic_state WHERE topic_key = ?",
            ("hid:1|page_view",)
        ).fetchone()
        self.assertEqual(row["sample_count"], 2)
        self.assertEqual(row["first_seen_at"][:10], "2026-09-29")
        self.assertEqual(row["last_seen_at"][:10], "2026-09-30")
        self.assertEqual(row["peak_metric"], 400)

    def test_series_accumulates_across_days(self):
        """时间序列要跨天累积——这是算斜率的前提。"""
        t0 = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)
        t1 = datetime(2026, 9, 30, 8, 0, tzinfo=TZ)
        s = [{"title": "话题A", "hashtag_id": "1", "metric": 100,
              "metric_kind": "page_view", "source": "S4"}]
        tt.record_samples(self.con, s, now=t0)
        tt.record_samples(self.con, [dict(s[0], metric=400)], now=t1)

        series = tt.get_series(self.con, "hid:1|page_view")
        self.assertEqual(len(series), 2)
        self.assertEqual({r["metric"] for r in series}, {100, 400})

    def test_no_transition_when_state_unchanged(self):
        """状态没变时不产生迁移事件——避免重复打扰。"""
        t0 = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)
        t1 = datetime(2026, 9, 30, 8, 0, tzinfo=TZ)
        s = [{"title": "话题A", "hashtag_id": "1", "metric": 100,
              "metric_kind": "page_view", "source": "S4"}]
        tt.record_samples(self.con, s, now=t0)
        tr = tt.record_samples(self.con, [dict(s[0], metric=105)], now=t1)
        self.assertEqual(tr, [])

    def test_two_metrics_tracked_separately(self):
        """浏览量曲线与互动量曲线不混算——同一话题两条独立序列。"""
        t0 = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)
        tt.record_samples(self.con, [
            {"title": "话题A", "hashtag_id": "1", "metric": 100,
             "metric_kind": "page_view", "source": "S4"},
            {"title": "话题A", "hashtag_id": "1", "metric": 7,
             "metric_kind": "interaction", "source": "S6"},
        ], now=t0)
        series = tt.get_series(self.con, "hid:1|page_view")
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["metric"], 100)
        series_it = tt.get_series(self.con, "hid:1|interaction")
        self.assertEqual(len(series_it), 1)
        self.assertEqual(series_it[0]["metric"], 7)

    def test_metric_switch_does_not_fake_a_crash(self):
        """★ 回归测试：换指标不能产生虚假的「退潮/爆发」。

        这是实测 bug 的行为级验证——同一天里浏览量很高、互动量很低，
        若共用一个 key 就会被判成「暴跌」，实际只是量纲不同。
        """
        t0 = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)
        tr = tt.record_samples(self.con, [
            {"title": "话题A", "hashtag_id": "3", "metric": 6270,
             "metric_kind": "page_view", "source": "S4"},
            {"title": "话题A", "hashtag_id": "3", "metric": 38,
             "metric_kind": "interaction", "source": "S6"},
        ], now=t0)
        # 两条都应是「首次观测 → 冒头」，不能出现退潮/爆发
        self.assertEqual(len(tr), 2)
        self.assertEqual({t["to"] for t in tr}, {"冒头"})

    def test_reopen_db_persists_state(self):
        """持久化验证：关掉重开后状态还在（这是「全天跨进程」的基础）。"""
        t0 = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)
        tt.record_samples(self.con, [
            {"title": "话题A", "hashtag_id": "1", "metric": 100,
             "metric_kind": "page_view", "source": "S4"}], now=t0)
        self.con.close()

        con2 = tt.connect(self.db)
        row = con2.execute(
            "SELECT * FROM topic_state WHERE topic_key = ?",
            ("hid:1|page_view",)
        ).fetchone()
        self.assertIsNotNone(row, "重开数据库后状态应仍在")
        self.assertEqual(row["state"], "冒头")
        con2.close()


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.db = _tmp_db()
        self.con = tt.connect(self.db)

    def tearDown(self):
        self.con.close()

    def test_stale_topic_archived(self):
        old = datetime(2026, 9, 20, 8, 0, tzinfo=TZ)
        tt.record_samples(self.con, [
            {"title": "老话题", "hashtag_id": "9", "metric": 100,
             "metric_kind": "page_view", "source": "S4"}], now=old)
        tr = tt.archive_stale(self.con, now=old + timedelta(hours=100))
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]["to"], "沉寂")

    def test_fresh_topic_not_archived(self):
        now = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)
        tt.record_samples(self.con, [
            {"title": "新话题", "hashtag_id": "8", "metric": 100,
             "metric_kind": "page_view", "source": "S4"}], now=now)
        tr = tt.archive_stale(self.con, now=now + timedelta(hours=1))
        self.assertEqual(tr, [])


class CollectTest(unittest.TestCase):
    def test_collects_both_metric_kinds_from_facts(self):
        pj = {
            "hot_board": [{"title": "话题A", "hashtag_id": "1", "page_view": 500}],
            "topic_dig": [{"title": "话题B", "hashtag_id": "2", "interaction_total": 30}],
        }
        s = tt.collect_samples(pj)
        kinds = {x["metric_kind"] for x in s}
        self.assertEqual(kinds, {"page_view", "interaction"})
        self.assertEqual(len(s), 2)

    def test_skips_empty_titles(self):
        pj = {"hot_board": [{"title": "", "hashtag_id": "1", "page_view": 5}],
              "topic_dig": []}
        self.assertEqual(tt.collect_samples(pj), [])

    def test_missing_facts_returns_empty(self):
        self.assertEqual(tt.collect_samples({}), [])


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.db = _tmp_db()
        self.con = tt.connect(self.db)

    def tearDown(self):
        self.con.close()

    def test_snapshot_groups_by_state(self):
        t0 = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)
        tt.record_samples(self.con, [
            {"title": "A", "hashtag_id": "1", "metric": 100,
             "metric_kind": "page_view", "source": "S4"},
            {"title": "B", "hashtag_id": "2", "metric": 50,
             "metric_kind": "page_view", "source": "S4"},
        ], now=t0)
        snap = tt.snapshot(self.con)
        self.assertEqual(snap["n_topics"], 2)
        self.assertEqual(snap["counts"]["冒头"], 2)

    def test_report_states_thresholds_are_uncalibrated(self):
        """报告必须显式声明阈值未经校准——与 ferment_judge 同样的诚实纪律。"""
        t0 = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)
        tt.record_samples(self.con, [
            {"title": "A", "hashtag_id": "1", "metric": 100,
             "metric_kind": "page_view", "source": "S4"}], now=t0)
        md = tt.render_snapshot(tt.snapshot(self.con))
        self.assertIn("未经真实运营反馈校准", md)
        self.assertIn("变化率", md)


if __name__ == "__main__":
    unittest.main()
