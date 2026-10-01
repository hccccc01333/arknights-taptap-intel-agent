#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scheduler 的单元测试：节流、自适应决策、去重、冷却、校准数据。

重点锁死三件事：
  1. **节流**：cron 可以每分钟调，但调度器只在间隔到时才真探测（幂等可重入）
  2. **自适应用可发酵度而非当前热度** —— 这是避开「漏掉冷启动」悖论的关键
  3. **去重**：同一话题的浏览量/互动量是两条序列，但深采按 hashtag_id 发请求，不能重复采
"""

from __future__ import annotations

import importlib.util
import json
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


sch = _load("scheduler")
TZ = sch.TZ


def topic(key, title, state, metric, hid, kind="page_view"):
    return {"topic_key": key, "title": title, "hashtag_id": hid, "metric_kind": kind,
            "state": state, "last_metric": metric, "peak_metric": metric, "sample_count": 1}


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "sched.sqlite3"
        self.con = sch.connect(self.db)

    def tearDown(self):
        self.con.close()
        self._tmp.cleanup()


# --------------------------------------------------------------------------- 节流

class TestThrottle(TmpCase):
    def test_first_time_is_due(self):
        now = datetime.now(TZ)
        r = sch.due_for_probe(self.con, now, 15)
        self.assertTrue(r["due"])
        self.assertIn("首次", r["reason"])

    def test_not_due_within_interval(self):
        """★ cron 每分钟调也不会重复探测。"""
        now = datetime.now(TZ)
        sch.log_probe(self.con, ok=True, n_topics=10, n_changed=2, max_delta=50,
                      elapsed=1.0)
        r = sch.due_for_probe(self.con, now, 15)
        self.assertFalse(r["due"])
        self.assertIn("<15", r["reason"])

    def test_due_after_interval(self):
        now = datetime.now(TZ)
        sch.log_probe(self.con, ok=True, n_topics=10, n_changed=2, max_delta=50,
                      elapsed=1.0)
        later = now + timedelta(minutes=20)
        r = sch.due_for_probe(self.con, later, 15)
        self.assertTrue(r["due"])

    def test_failed_probe_does_not_block_next(self):
        """探测失败不应把下一轮卡住 —— 否则失败一次要等一整个间隔。"""
        sch.log_probe(self.con, ok=False, n_topics=None, n_changed=None,
                      max_delta=None, elapsed=1.0, error="网络失败")
        r = sch.due_for_probe(self.con, datetime.now(TZ), 15)
        self.assertTrue(r["due"], "上次失败时应允许立即重试")


# --------------------------------------------------------------------------- 自适应决策

class TestDecideDrill(TmpCase):
    def _state(self, **kw):
        return {"by_state": kw}

    def test_channel_a_rising_and_bursting_first(self):
        ts = self._state(冒头=[topic("k9", "冒头话题", "冒头", 100, "9")],
                         升温=[topic("k2", "升温话题", "升温", 300, "2")],
                         爆发=[topic("k3", "爆发话题", "爆发", 9000, "3")])
        plan = sch.decide_drill(self.con, ts, {}, datetime.now(TZ))
        titles = [p["title"] for p in plan["picked"]]
        self.assertEqual(titles[:2], ["爆发话题", "升温话题"], "爆发应排在升温前")
        self.assertEqual(plan["picked"][0]["channel"], "A")

    def test_channel_b_cold_but_fermentable(self):
        """★ 核心：冷话题只要可发酵度高就该深采 —— 这是避开观察者悖论的地方。

        如果按「当前热度」筛，这个话题（热度 100）会被跳过，
        结果就是永远看不见它升温。
        """
        ts = self._state(冒头=[topic("k1", "冷但可发酵", "冒头", 100, "1"),
                               topic("k2", "冷且不可发酵", "冒头", 90, "2")])
        ferment = {"topics": [
            {"hashtag_id": "1", "ferment_score": 60, "verdict": "act"},
            {"hashtag_id": "2", "ferment_score": 10, "verdict": "skip"},
        ]}
        plan = sch.decide_drill(self.con, ts, ferment, datetime.now(TZ))
        by = {p["title"]: p for p in plan["picked"]}
        self.assertIn("冷但可发酵", by)
        self.assertEqual(by["冷但可发酵"]["channel"], "B")
        self.assertNotEqual(by.get("冷且不可发酵", {}).get("channel"), "B",
                            "verdict=skip 的不该走 B 通道")

    def test_dedup_across_metric_kinds(self):
        """★ 同一话题的浏览量/互动量是两条序列，但深采按 hashtag_id 发请求，不能重复采。"""
        ts = self._state(升温=[topic("hid:9|page_view", "三角洲", "升温", 6583, "9", "page_view"),
                               topic("hid:9|interaction", "三角洲", "升温", 38, "9", "interaction")])
        plan = sch.decide_drill(self.con, ts, {}, datetime.now(TZ))
        self.assertEqual(len(plan["picked"]), 1, "同一 hashtag 只该采一次")
        self.assertEqual(plan["n_unique_topics"], 1)

    def test_cooldown_skips_recently_drilled(self):
        ts = self._state(爆发=[topic("k1", "刚采过", "爆发", 100, "1")])
        now = datetime.now(TZ)
        sch.log_drill(self.con, "k1", "刚采过", "状态=爆发")
        plan = sch.decide_drill(self.con, ts, {}, now, cooldown_min=60)
        self.assertEqual(plan["n_picked"], 0)
        self.assertGreaterEqual(plan["n_skipped_cooldown"], 1)

    def test_cooldown_expires(self):
        ts = self._state(爆发=[topic("k1", "采过很久", "爆发", 100, "1")])
        sch.log_drill(self.con, "k1", "采过很久", "状态=爆发")
        future = datetime.now(TZ) + timedelta(minutes=90)
        plan = sch.decide_drill(self.con, ts, {}, future, cooldown_min=60)
        self.assertEqual(plan["n_picked"], 1, "冷却到期后应可再采")

    def test_max_topics_cap(self):
        ts = self._state(冒头=[topic(f"k{i}", f"T{i}", "冒头", i, str(i)) for i in range(20)])
        plan = sch.decide_drill(self.con, ts, {}, datetime.now(TZ), max_topics=3)
        self.assertEqual(len(plan["picked"]), 3)

    def test_fill_channel_c_used_when_room_left(self):
        ts = self._state(冒头=[topic("k1", "普通冒头", "冒头", 10, "1")])
        plan = sch.decide_drill(self.con, ts, {}, datetime.now(TZ), max_topics=5)
        self.assertEqual(len(plan["picked"]), 1)
        self.assertEqual(plan["picked"][0]["channel"], "C")

    def test_empty_state_yields_empty_plan(self):
        """★ 降级：没有状态数据时不能崩，应返回空名单。"""
        plan = sch.decide_drill(self.con, {}, {}, datetime.now(TZ))
        self.assertEqual(plan["n_picked"], 0)
        self.assertEqual(plan["picked"], [])

    def test_malformed_state_does_not_crash(self):
        plan = sch.decide_drill(self.con, {"by_state": None}, {"topics": None},
                                datetime.now(TZ))
        self.assertEqual(plan["n_picked"], 0)


# --------------------------------------------------------------------------- 校准数据

class TestDiffProbe(TmpCase):
    def test_counts_changes(self):
        prev = {"a": 100, "b": 200, "c": 300}
        curr = {"a": 100, "b": 250, "c": 300}
        d = sch.diff_probe(prev, curr)
        self.assertEqual(d["n_changed"], 1)
        self.assertEqual(d["max_delta"], 50)

    def test_no_change_detected(self):
        """★ 这个数字长期为 0 → 说明采得太密，可以放宽间隔。"""
        prev = {"a": 100, "b": 200}
        d = sch.diff_probe(prev, dict(prev))
        self.assertEqual(d["n_changed"], 0)

    def test_new_topic_not_counted_as_change(self):
        prev = {"a": 100}
        curr = {"a": 100, "b": 500}
        d = sch.diff_probe(prev, curr)
        self.assertEqual(d["n_changed"], 0, "新增话题不是「变化」，是「新出现」")
        self.assertEqual(d["n_common"], 1)

    def test_empty_prev(self):
        d = sch.diff_probe({}, {"a": 1})
        self.assertEqual(d["n_changed"], 0)


class TestProbeLog(TmpCase):
    def test_log_and_read_back(self):
        sch.log_probe(self.con, ok=True, n_topics=14, n_changed=3, max_delta=120,
                      elapsed=2.5)
        row = self.con.execute("SELECT * FROM probe_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["n_topics"], 14)
        self.assertEqual(row["n_changed"], 3)
        self.assertEqual(row["ok"], 1)

    def test_failure_records_error(self):
        sch.log_probe(self.con, ok=False, n_topics=None, n_changed=None,
                      max_delta=None, elapsed=0.3, error="退出码 1")
        row = self.con.execute("SELECT * FROM probe_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["ok"], 0)
        self.assertIn("退出码", row["error"])

    def test_default_db_is_not_in_repo(self):
        """状态库必须落在 state/ 下（该目录已 gitignore）。"""
        self.assertIn("state", str(sch.SCHED_DB))
        self.assertTrue(str(sch.SCHED_DB).endswith("scheduler.sqlite3"))

    def test_sched_db_is_patchable(self):
        """★ 回归锁：路径必须在**调用时**解析，不能在定义时绑定。

        若 `connect(db_path=SCHED_DB)` 直接写默认值，默认参数会在导入时绑定，
        `mock.patch.object` 就失效 —— 测试会写到真实状态库并污染校准数据。
        """
        from unittest import mock
        other = Path(self._tmp.name) / "other.sqlite3"
        with mock.patch.object(sch, "SCHED_DB", other):
            con = sch.connect()
            con.execute("CREATE TABLE t(x)")
            con.close()
        self.assertTrue(other.exists(), "patch 未生效：默认参数可能被定义时绑定")


# --------------------------------------------------------------------------- 状态报告

class TestStatusReport(TmpCase):
    def test_empty_report_says_no_records(self):
        from unittest import mock
        with mock.patch.object(sch, "SCHED_DB", self.db):
            rep = sch.status_report()
        self.assertIn("尚无探测记录", rep)

    def test_report_shows_calibration_guidance(self):
        from unittest import mock
        for _ in range(6):
            sch.log_probe(self.con, ok=True, n_topics=14, n_changed=0,
                          max_delta=0, elapsed=1.0)
        with mock.patch.object(sch, "SCHED_DB", self.db):
            rep = sch.status_report()
        self.assertIn("采得太密", rep, "全 0 变化且样本足够时应提示可以放宽")
        self.assertIn("反爬上界", rep)

    def test_report_refuses_conclusion_with_too_few_samples(self):
        """★ 回归锁：样本不足时不许下结论。

        实测踩过：两次探测间隔 1 分钟 → 0 变化 → 报告建议「放宽到 30min」。
        间隔太短时「0 变化」是必然的，不能据此推断频率过密。
        """
        from unittest import mock
        sch.log_probe(self.con, ok=True, n_topics=14, n_changed=0,
                      max_delta=0, elapsed=1.0)
        with mock.patch.object(sch, "SCHED_DB", self.db):
            rep = sch.status_report()
        self.assertIn("样本不足", rep)
        # 断言「建议本身」缺席（说明文字里会引用「采得太密」这个词，不能拿它当判据）
        self.assertNotIn("可考虑放宽间隔", rep, "1 个样本不能给出放宽间隔的建议")
        self.assertNotIn("当前间隔是必要的", rep, "1 个样本也不能给出维持间隔的建议")

    def test_report_warns_when_changes_are_frequent(self):
        from unittest import mock
        for _ in range(6):
            sch.log_probe(self.con, ok=True, n_topics=14, n_changed=5,
                          max_delta=900, elapsed=1.0)
        with mock.patch.object(sch, "SCHED_DB", self.db):
            rep = sch.status_report()
        self.assertIn("当前间隔是必要的", rep)

    def test_report_marks_uncalibrated_values(self):
        from unittest import mock
        sch.log_probe(self.con, ok=True, n_topics=14, n_changed=1,
                      max_delta=10, elapsed=1.0)
        with mock.patch.object(sch, "SCHED_DB", self.db):
            rep = sch.status_report()
        self.assertIn("未校准", rep)
        self.assertIn("已推导", rep)

    def test_drill_records_shown(self):
        from unittest import mock
        sch.log_drill(self.con, "k1", "某话题", "冒头但可发酵度 act（60）")
        with mock.patch.object(sch, "SCHED_DB", self.db):
            rep = sch.status_report()
        self.assertIn("某话题", rep)
        self.assertIn("可发酵度 act", rep)


class TestPreflight(unittest.TestCase):
    """环境预检：子进程用同一个解释器，缺依赖要**快速失败并给可执行建议**。

    背景：踩过两次坑——
      1. 用只装标准库的解释器跑 → 爬虫 ImportError → 只看到「退出码 1」，很难查
      2. 别的测试往 sys.modules 塞了 `__spec__=None` 的 requests 桩，
         导致 find_spec 抛 ValueError，让预检自己崩了
    """

    def test_ok_when_deps_present(self):
        from unittest import mock
        with mock.patch.object(sch, "_has_module", return_value=True):
            self.assertTrue(sch.preflight()["ok"])

    def test_reports_missing_with_actionable_hint(self):
        from unittest import mock
        with mock.patch.object(sch, "_has_module", return_value=False):
            pf = sch.preflight()
        self.assertFalse(pf["ok"])
        self.assertIn("requests", pf["missing"])
        self.assertIn("python.exe", pf["hint"], "建议里应给出可直接复制的解释器路径")
        self.assertIn("scheduler.py", pf["hint"])

    def test_survives_module_without_spec(self):
        """★ 回归锁：sys.modules 里存在 `__spec__=None` 的模块时不能抛异常。"""
        import sys
        import types
        stub = types.ModuleType("__fake_dep_without_spec__")
        self.assertIsNone(stub.__spec__)
        sys.modules["__fake_dep_without_spec__"] = stub
        try:
            self.assertTrue(sch._has_module("__fake_dep_without_spec__"))
        finally:
            sys.modules.pop("__fake_dep_without_spec__", None)

    def test_returns_false_for_truly_absent_module(self):
        self.assertFalse(sch._has_module("__surely_not_installed_xyz__"))

    def test_hint_none_when_ok(self):
        from unittest import mock
        with mock.patch.object(sch, "_has_module", return_value=True):
            self.assertIsNone(sch.preflight()["hint"])


# --------------------------------------------------------------------------- 编排（不真跑外部脚本）

class TestOrchestration(TmpCase):
    def test_dry_run_does_not_call_subprocess(self):
        from unittest import mock
        with mock.patch.object(sch, "SCHED_DB", self.db), \
             mock.patch.object(sch.subprocess, "run") as fake:
            r = sch.run_once(dry_run=True)
        fake.assert_not_called()
        self.assertTrue(r["dry_run"])

    def test_dry_run_does_not_write_log(self):
        from unittest import mock
        with mock.patch.object(sch, "SCHED_DB", self.db):
            sch.run_once(dry_run=True)
        n = self.con.execute("SELECT COUNT(*) c FROM probe_log").fetchone()["c"]
        self.assertEqual(n, 0, "干跑不应写日志（否则会污染校准数据）")

    def test_not_due_skips_probe_but_still_plans_drill(self):
        from unittest import mock
        sch.log_probe(self.con, ok=True, n_topics=14, n_changed=1,
                      max_delta=5, elapsed=1.0)
        with mock.patch.object(sch, "SCHED_DB", self.db), \
             mock.patch.object(sch.subprocess, "run") as fake:
            r = sch.run_once(dry_run=True, skip_probe_if_not_due=True)
        self.assertFalse(r["probe_due"]["due"])
        self.assertIn("drill_plan", r, "跳过探测时仍应给出深采计划")

    def test_probe_steps_shape(self):
        """探测链已收成 **1 步**：交给巡检图（LangGraph）—— 「时钟驱动任务」的落点。

        2026-10-01 改：此前是 5 步、由调度器自己拼脚本
        （拉热榜 → facts → 采样 → 触发链 → 特征落盘）；
        现在任务内部的流转由 `agent_graph.py` 负责
        （节点/条件边/有界重试环/checkpointer 由 tests/test_agent_graph.py 锁死）。
        """
        steps = sch.probe_steps(dry_run=True)
        self.assertEqual(len(steps), 1, "探测链应为 1 步：跑巡检图")
        cmd = steps[0].get("cmd", "")
        self.assertIn("agent_graph.py", cmd, "唯一一步应是巡检图")
        self.assertIn("--run", cmd)
        self.assertIn("--thread", cmd, "thread 决定幂等窗口与检查点归属")

    def test_drill_steps_empty_ids(self):
        self.assertEqual(sch.drill_steps([], dry_run=True), [])

    def test_drill_steps_uses_hashtag_ids(self):
        steps = sch.drill_steps(["1", "2"], dry_run=True)
        self.assertEqual(len(steps), 2)
        self.assertIn("--hashtag-ids", steps[0]["cmd"])
        self.assertIn("1,2", steps[0]["cmd"])


if __name__ == "__main__":
    unittest.main()
