#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""freshness 的单元测试：时效计算、分组、诊断、降级。

重点锁死三件事：
  1. **滞后换算成「采样间隔的倍数」** —— 这是诊断的依据（1× = 采样卡住；>>1× = 判定卡住）
  2. **首日采样（未迁移）不算样本** —— 否则滞后恒为 0，会得出「时效很好」的假结论
  3. **样本不足时不给结论**
"""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(LAB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


fr = _load("freshness")

BASE = datetime(2026, 9, 30, 10, 0, 0, tzinfo=fr.TZ)


def make_db(tmp: Path, rows: list[dict]) -> Path:
    """建一个最小 topic_state 库。"""
    db = tmp / "topic.sqlite3"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE topic_state (
            topic_key TEXT PRIMARY KEY, title TEXT, hashtag_id TEXT,
            metric_kind TEXT, first_seen_at TEXT, last_seen_at TEXT,
            state TEXT, last_metric INTEGER, peak_metric INTEGER,
            sample_count INTEGER, prev_state TEXT, state_changed_at TEXT
        )
    """)
    for r in rows:
        con.execute(
            "INSERT INTO topic_state (topic_key,title,hashtag_id,metric_kind,"
            " first_seen_at,last_seen_at,state,last_metric,peak_metric,"
            " sample_count,prev_state,state_changed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["topic_key"], r.get("title", "T"), "", r.get("metric_kind", "page_view"),
             r["first_seen_at"], r.get("last_seen_at", r["first_seen_at"]),
             r.get("state", "冒头"), 100, 100, r.get("sample_count", 2),
             r.get("prev_state"), r["state_changed_at"]),
        )
    con.commit()
    con.close()
    return db


def ts(hours: float) -> str:
    return (BASE + timedelta(hours=hours)).isoformat(timespec="seconds")


def moved(key: str, lag_hours: float, state: str = "升温", **kw) -> dict:
    """构造一条「发生过迁移」的记录。"""
    return {"topic_key": key, "title": kw.pop("title", key),
            "first_seen_at": ts(0), "state_changed_at": ts(lag_hours),
            "state": state, "sample_count": kw.pop("samples", 3), **kw}


def unmoved(key: str, **kw) -> dict:
    """构造一条「还没迁移」的记录（首日采样常这样）。"""
    return {"topic_key": key, "title": kw.pop("title", key),
            "first_seen_at": ts(0), "state_changed_at": ts(0),
            "state": "冒头", **kw}


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestLoadTransitions(TmpCase):
    def test_missing_db_degrades(self):
        r = fr.load_transitions(self.tmp / "none.sqlite3")
        self.assertFalse(r["available"])
        self.assertIn("不存在", r["reason"])

    def test_broken_db_degrades(self):
        bad = self.tmp / "bad.sqlite3"
        bad.write_text("not a db", encoding="utf-8")
        r = fr.load_transitions(bad)
        self.assertFalse(r["available"])
        self.assertIn("读取失败", r["reason"])

    def test_separates_moved_and_unmoved(self):
        """★ 首日采样 state_changed_at == first_seen_at，不算迁移样本。"""
        db = make_db(self.tmp, [moved("k1", 3), unmoved("k2"), unmoved("k3")])
        r = fr.load_transitions(db)
        self.assertEqual(r["n_total"], 3)
        self.assertEqual(len(r["moved"]), 1)
        self.assertEqual(len(r["unmoved"]), 2)
        self.assertEqual(r["moved"][0]["lag_hours"], 3.0)

    def test_lag_is_state_changed_minus_first_seen(self):
        db = make_db(self.tmp, [moved("k1", 2.5)])
        r = fr.load_transitions(db)
        self.assertAlmostEqual(r["moved"][0]["lag_hours"], 2.5, places=2)


class TestCompute(TmpCase):
    def test_missing_db_propagates(self):
        r = fr.compute(self.tmp / "none.sqlite3", interval_min=15)
        self.assertFalse(r["available"])

    def test_no_transitions_is_not_enough_samples(self):
        """★ 0 条迁移不能说「时效很好」——那只是还没数据。"""
        db = make_db(self.tmp, [unmoved(f"k{i}") for i in range(10)])
        r = fr.compute(db, interval_min=15)
        self.assertTrue(r["available"])
        self.assertEqual(r["n_moved"], 0)
        self.assertFalse(r["enough_samples"])

    def test_enough_samples_after_five(self):
        db = make_db(self.tmp, [moved(f"k{i}", 1 + i * 0.5) for i in range(5)])
        r = fr.compute(db, interval_min=15)
        self.assertTrue(r["enough_samples"])

    def test_interval_multiple_conversion(self):
        """★ 核心换算：滞后(小时) → 采样间隔的倍数。15min 间隔下 0.25h = 1×。"""
        db = make_db(self.tmp, [moved(f"k{i}", 0.25) for i in range(5)])
        r = fr.compute(db, interval_min=15)
        self.assertAlmostEqual(r["interval_multiple_stats"]["median"], 1.0, places=1)

    def test_interval_multiple_scales_with_interval(self):
        """同样 1 小时的滞后，间隔越小「倍数」越大（说明采样不是瓶颈）。"""
        db = make_db(self.tmp, [moved(f"k{i}", 1.0) for i in range(5)])
        r15 = fr.compute(db, interval_min=15)
        r60 = fr.compute(db, interval_min=60)
        self.assertGreater(r15["interval_multiple_stats"]["median"],
                           r60["interval_multiple_stats"]["median"])

    def test_grouped_by_target_state(self):
        db = make_db(self.tmp, [
            moved("k1", 1, "升温"), moved("k2", 1, "升温"),
            moved("k3", 8, "爆发"), moved("k4", 8, "爆发"),
        ])
        r = fr.compute(db, interval_min=15)
        self.assertEqual(r["by_target_state"]["升温"]["n"], 2)
        self.assertEqual(r["by_target_state"]["爆发"]["median"], 8.0)

    def test_slowest_sorted_desc(self):
        db = make_db(self.tmp, [moved("k1", 1), moved("k2", 20), moved("k3", 10)])
        r = fr.compute(db, interval_min=15)
        self.assertEqual([m["topic_key"] for m in r["slowest"]][:2], ["k2", "k3"])

    def test_interval_source_recorded(self):
        db = make_db(self.tmp, [moved("k1", 1)])
        r = fr.compute(db, interval_min=20)
        self.assertEqual(r["interval_min"], 20)
        self.assertEqual(r["interval_source"], "显式传入")


class TestDiagnosis(TmpCase):
    """诊断逻辑：从「滞后是几个采样间隔」推断瓶颈在哪。"""

    def _render(self, lag_hours: float, interval: int = 15, n: int = 5):
        db = make_db(self.tmp, [moved(f"k{i}", lag_hours) for i in range(n)])
        r = fr.compute(db, interval_min=interval)
        return fr.render(r)

    def test_near_one_interval_blames_sampling(self):
        """滞后≈1×间隔 → 采样卡住（话题一出现就被采到，判定也及时）。"""
        rep = self._render(0.25, interval=15)     # 15min = 1×
        self.assertIn("瓶颈在采样频率", rep)

    def test_far_above_interval_blames_judgement(self):
        """滞后远大于 1×间隔 → 判定卡住（采到了但没迁移）。"""
        rep = self._render(6.0, interval=15)      # 24×
        self.assertIn("瓶颈可能在判定", rep)

    def test_reports_interval_baseline(self):
        rep = self._render(1.0)
        self.assertIn("采样间隔基准", rep)
        self.assertIn("15 分钟", rep)

    def test_not_enough_samples_says_so_and_no_conclusion(self):
        db = make_db(self.tmp, [moved("k1", 1)])
        rep = fr.render(fr.compute(db, interval_min=15))
        self.assertIn("样本不足", rep)
        self.assertNotIn("瓶颈在采样频率", rep)
        self.assertNotIn("瓶颈可能在判定", rep)

    def test_marks_theoretical_floor(self):
        """★ 报告必须说明「理论下限 = 采样间隔」，否则会误以为滞后高就是系统差。"""
        rep = self._render(1.0)
        self.assertIn("理论下限", rep)

    def test_notes_delivery_not_included(self):
        """只测到「判出迁移」，不含「员工看到」—— 边界要说清。"""
        rep = self._render(1.0)
        self.assertIn("不含", rep)

    def test_unavailable_renders_reason(self):
        rep = fr.render({"available": False, "reason": "库不存在"})
        self.assertIn("不可用", rep)
        self.assertIn("库不存在", rep)


class TestIntervalProbe(unittest.TestCase):
    def test_reads_from_scheduler(self):
        iv, src = fr._probe_interval_min()
        self.assertGreater(iv, 0)
        self.assertIn("scheduler", src, "应优先从 scheduler 读，保持单一真相源")

    def test_fallback_used_when_unavailable(self):
        from unittest import mock
        with mock.patch.object(fr, "LAB", Path("/nonexistent")):
            iv, src = fr._probe_interval_min()
        self.assertEqual(iv, fr.FALLBACK_INTERVAL_MIN)
        self.assertIn("兜底", src)


if __name__ == "__main__":
    unittest.main()
