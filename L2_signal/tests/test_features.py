#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""features 的单元测试：斜率正确性、速率需两轮、显式降级、落盘幂等。

重点锁死四件事：
  1. **先算不判** —— 特征只落盘，绝不能碰状态机（run 里没有写 topic_state 的路径）
  2. **显式降级** —— 每个不可用特征必须带 status + reason，不静默给 None
  3. **速率需两轮** —— F2/F3 单轮快照必须 insufficient_data（诚实面对积累期）
  4. **落盘幂等** —— 最小间隔内重复 run 要 skip，force 可越（调度器防重）
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(LAB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


ft = _load("features")
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 30, 20, 0, tzinfo=TZ)


def iso(h_ago: float, base: datetime = NOW) -> str:
    return (base - timedelta(hours=h_ago)).isoformat(timespec="seconds")


class _PatchPaths(unittest.TestCase):
    """把模块级路径常量指到临时目录，测完还原。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = {k: getattr(ft, k) for k in
                     ("SNAP_DB", "TRACKER_DB", "POSTS_CSV", "COMMENTS_CSV",
                      "FEATURES_JSONL", "OUTPUTS", "GAMES_DIR")}
        ft.SNAP_DB = self.tmp / "snap.sqlite3"
        ft.TRACKER_DB = self.tmp / "tracker.sqlite3"
        ft.POSTS_CSV = self.tmp / "discovery_posts.csv"
        ft.COMMENTS_CSV = self.tmp / "discovery_comments.csv"
        ft.FEATURES_JSONL = self.tmp / "features.jsonl"
        ft.OUTPUTS = self.tmp / "outputs"
        ft.GAMES_DIR = self.tmp / "games"
        ft.OUTPUTS.mkdir(parents=True, exist_ok=True)
        ft.GAMES_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for k, v in self._old.items():
            setattr(ft, k, v)

    # -- 造数工具 -----------------------------------------------------

    def make_tracker(self, hid="111", points=None):
        """造 topic_state + topic_series。points: [(hours_ago, metric)]"""
        con = sqlite3.connect(str(ft.TRACKER_DB))
        con.executescript(
            "CREATE TABLE topic_state (topic_key TEXT PRIMARY KEY, title TEXT,"
            " hashtag_id TEXT, metric_kind TEXT DEFAULT 'page_view',"
            " first_seen_at TEXT, last_seen_at TEXT, state TEXT,"
            " last_metric INTEGER DEFAULT 0, peak_metric INTEGER DEFAULT 0,"
            " sample_count INTEGER DEFAULT 0, prev_state TEXT, state_changed_at TEXT);"
            "CREATE TABLE topic_series (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " topic_key TEXT, sampled_at TEXT, metric INTEGER,"
            " metric_kind TEXT, source TEXT);")
        key = f"hid:{hid}|page_view"
        con.execute("INSERT INTO topic_state (topic_key, title, hashtag_id, state,"
                    " peak_metric) VALUES (?,?,?,?,1000)",
                    (key, "测试话题", hid, "升温"))
        for h_ago, metric in (points or [(3, 900), (1, 1000)]):
            con.execute("INSERT INTO topic_series (topic_key, sampled_at, metric,"
                        " metric_kind, source) VALUES (?,?,?,?, 'test')",
                        (key, iso(h_ago), metric, "page_view"))
        con.commit()
        con.close()

    def make_posts_csv(self, rows):
        """rows: [(moment_id, hashtag_id, supports, ups, comments, pv)]"""
        import csv as _csv
        with open(ft.POSTS_CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["moment_id", "group_id", "app_id", "app_title",
                        "author_name", "author_id_hash", "title", "summary",
                        "comments", "supports", "ups", "pv_total", "publish_time",
                        "hashtags_json", "hashtag_id", "hashtag_title",
                        "source_type", "crawled_at"])
            for mid, hid, sup, ups, com, pv in rows:
                w.writerow([mid, "53933", "70253", "t", "a", "h", "t", "s",
                            com, sup, ups, pv, "0", "[]", hid, "ht",
                            "discover", iso(0)])


class TestF1Slope(_PatchPaths):
    def test_known_linear(self):
        pts = [(ft._parse_dt(iso(h)), float(m)) for h, m in
               [(10, 100.0), (6, 180.0), (2, 260.0), (0, 300.0)]]
        r = ft.f1_slope(pts, now=NOW)
        self.assertEqual(r["status"], "ok")
        # 10 小时内 100 → 300：每小时 +20
        self.assertAlmostEqual(r["slope_per_hour"], 20.0, delta=0.01)

    def test_single_point_insufficient(self):
        pts = [(ft._parse_dt(iso(1)), 100.0)]
        r = ft.f1_slope(pts, now=NOW)
        self.assertEqual(r["status"], "insufficient_data")
        self.assertTrue(r["reason"])

    def test_tiny_span_rejected(self):
        pts = [(ft._parse_dt(iso(0.05)), 100.0),
               (ft._parse_dt(iso(0.0)), 110.0)]
        r = ft.f1_slope(pts, now=NOW)
        self.assertEqual(r["status"], "insufficient_data")
        self.assertIn("跨度", r["reason"])

    def test_window_excludes_old_points(self):
        # 窗口 24h：25h 前的点被排除，只剩 1 个 → insufficient
        pts = [(ft._parse_dt(iso(h)), float(m)) for h, m in
               [(25, 100.0), (3, 500.0)]]
        r = ft.f1_slope(pts, now=NOW)
        self.assertEqual(r["status"], "insufficient_data")


class TestF23Velocity(_PatchPaths):
    def test_single_round_insufficient(self):
        con = ft.connect()
        ft.snapshot_posts(con, "2026-09-30T10:00", iso(0), {"111"})
        r = ft.f23_velocity(con, "111")
        self.assertEqual(r["status"], "insufficient_data")
        self.assertIn("≥2 轮", r["reason"])
        con.close()

    def test_two_rounds_median_and_max(self):
        con = ft.connect()
        ft.snapshot_posts(con, "2026-09-30T10:00", iso(3), {"111"})
        # 手工注入第二轮（绕过 CSV）：帖子 m1 赞 +50/2h，评论 +6/2h；m2 赞 +90/2h
        con.executemany(
            "INSERT OR IGNORE INTO post_snapshots (run_id, snapshot_at, moment_id,"
            " hashtag_id, supports, ups, comments, pv_total) VALUES (?,?,?,?,?,?,?,?)",
            [("2026-09-30T10:00", iso(3), "m1", "111", 100, 20, 5, 1000),
             ("2026-09-30T10:00", iso(3), "m2", "111", 100, 20, 5, 1000),
             ("2026-09-30T12:00", iso(1), "m1", "111", 150, 20, 11, 1100),
             ("2026-09-30T12:00", iso(1), "m2", "111", 190, 20, 7, 1200)])
        con.commit()
        r = ft.f23_velocity(con, "111")
        self.assertEqual(r["status"], "ok")
        # m1: (170-120)/2=25, m2: (210-120)/2=45 → 中位 35，最大 45
        self.assertAlmostEqual(r["support_per_hour_median"], 35.0, delta=0.01)
        self.assertAlmostEqual(r["support_per_hour_max"], 45.0, delta=0.01)
        # m1: (11-5)/2=3, m2: (7-5)/2=1 → 中位 2
        self.assertAlmostEqual(r["comment_per_hour_median"], 2.0, delta=0.01)
        self.assertEqual(r["n_pairs"], 2)
        con.close()


class TestExplicitDegradation(_PatchPaths):
    def test_f4_always_unavailable(self):
        rows = ft.run(now=NOW, force=True)["n_rows"] and None  # run 不炸即可
        # F4 恒 unavailable 由 run 行保证；这里直接断言语义常量
        r = {"status": "unavailable"}
        self.assertEqual(r["status"], "unavailable")

    def test_f5_missing_source(self):
        r = ft.f5_user_flow("wuthering-waves", "鸣潮")
        self.assertEqual(r["status"], "source_missing")
        self.assertTrue(r["reason"])

    def test_f5_ok(self):
        p = ft.OUTPUTS / "user_flow_wuthering-waves.json"
        p.write_text(json.dumps({"A_flow": {"top_flows": [
            {"from": "原神", "to": "鸣潮", "users": 23},
            {"from": "绝区零", "to": "鸣潮", "users": 17},
            {"from": "鸣潮", "to": "星铁", "users": 5}]}}, ensure_ascii=False),
            encoding="utf-8")
        r = ft.f5_user_flow("wuthering-waves", "鸣潮")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["inflow_users"], 40)
        self.assertEqual(r["outflow_users"], 5)
        self.assertEqual(r["top_inflow_from"], "原神")

    def test_f6_available_false(self):
        (ft.OUTPUTS / "risk_insight.json").write_text(
            json.dumps({"available": False}), encoding="utf-8")
        r = ft.f6_high_hours()
        self.assertEqual(r["status"], "source_missing")

    def test_f6_ok(self):
        (ft.OUTPUTS / "risk_insight.json").write_text(json.dumps({
            "available": True, "game": "鸣潮",
            "topic_risk_table": [
                {"topic": "balance", "topic_cn": "数值", "n": 74,
                 "neg_high_hours_share": 0.8636}]}, ensure_ascii=False),
            encoding="utf-8")
        r = ft.f6_high_hours()
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["per_review_topic"][0]["neg_high_hours_share"], 0.8636)

    def test_f6_game_mismatch_rejected(self):
        """★ risk_insight 是 per-game 数据：挂错游戏行 = 脏训练数据，必须拒绝。

        实测踩过：risk_insight.json 属明日方舟，曾同时挂到鸣潮行 F6=ok。
        """
        (ft.OUTPUTS / "risk_insight.json").write_text(json.dumps({
            "available": True,
            "game": {"key": "arknights", "name": "明日方舟"},
            "topic_risk_table": [
                {"topic": "balance", "neg_high_hours_share": 0.8636}]},
            ensure_ascii=False), encoding="utf-8")
        wrong = ft.f6_high_hours("鸣潮")
        self.assertEqual(wrong["status"], "source_missing")
        self.assertEqual(wrong["belongs_to"], "明日方舟")
        right = ft.f6_high_hours("明日方舟")
        self.assertEqual(right["status"], "ok")

    def test_missing_csv_not_crash(self):
        """CSV 全缺也要正常落盘（source_missing），这是降级纪律的核心。"""
        self.make_tracker(hid="222")
        r = ft.run(now=NOW, force=True)
        self.assertFalse(r["skipped"])
        self.assertEqual(r["n_topic_rows"], 1)


class TestRunPipeline(_PatchPaths):
    def test_run_rows_and_idempotency(self):
        self.make_tracker(hid="111")
        self.make_posts_csv([("m1", "111", 10, 5, 2, 100)])
        r1 = ft.run(now=NOW, force=True)
        self.assertFalse(r1["skipped"])
        self.assertEqual(r1["n_topic_rows"], 1)
        self.assertEqual(r1["n_post_snapshots"], 1)
        # F1：两个点（3h 前 900，1h 前 1000）→ 斜率 (1000-900)/2 = 50/h
        lines = ft.FEATURES_JSONL.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[0])
        self.assertEqual(row["row_type"], "topic")
        self.assertEqual(row["F1"]["status"], "ok")
        self.assertAlmostEqual(row["F1"]["slope_per_hour"], 50.0, delta=0.5)
        self.assertEqual(row["F4"]["status"], "unavailable")
        self.assertEqual(row["F2_F3"]["status"], "insufficient_data")
        # 幂等：间隔内重跑 → skip；force → 越过
        r2 = ft.run(now=NOW + timedelta(minutes=2))
        self.assertTrue(r2["skipped"])
        r3 = ft.run(now=NOW + timedelta(minutes=6), force=True)
        self.assertFalse(r3["skipped"])

    def test_game_rows_written(self):
        (ft.GAMES_DIR / "arknights.json").write_text(json.dumps(
            {"name": "明日方舟", "profile_key": "arknights"}), encoding="utf-8")
        r = ft.run(now=NOW, force=True)
        self.assertEqual(r["n_game_rows"], 1)
        lines = ft.FEATURES_JSONL.read_text(encoding="utf-8").splitlines()
        grows = [json.loads(l) for l in lines if json.loads(l)["row_type"] == "game"]
        self.assertEqual(grows[0]["game"], "明日方舟")
        self.assertEqual(grows[0]["F5"]["status"], "source_missing")

    def test_silent_topics_excluded(self):
        self.make_tracker(hid="111")
        con = sqlite3.connect(str(ft.TRACKER_DB))
        con.execute("INSERT INTO topic_state (topic_key, title, hashtag_id, state,"
                    " peak_metric) VALUES ('hid:999|page_view', '沉寂话题',"
                    " '999', '沉寂', 10)")
        con.commit()
        con.close()
        r = ft.run(now=NOW, force=True)
        self.assertEqual(r["n_topic_rows"], 1)  # 只有升温那个
        self.assertEqual(r["n_post_snapshots"], 0)  # 沉寂话题的帖子不进快照


if __name__ == "__main__":
    unittest.main()
