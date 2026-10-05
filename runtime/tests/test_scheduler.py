#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scheduler 的单元测试：节流、探测链形状、下游链编排、状态报告。

重点锁死四件事：
  1. **节流**：cron 可以每分钟调，但调度器只在间隔到时才真探测（幂等可重入）
  2. **探测链 = 1 步巡检图**（时钟驱动任务，不是脚本）
  3. **下游链**：L4→L5→L6 每轮跟随执行、单任务失败不杀整轮、可 --no-chain 关闭
  4. **编排者不自己起进程**（子进程出口唯一在 harness）
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

LAB = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(LAB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


sch = _load("scheduler")
TZ = sch.TZ


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
        sch.log_probe(self.con, ok=True, elapsed=1.0)
        r = sch.due_for_probe(self.con, now, 15)
        self.assertFalse(r["due"])
        self.assertIn("<15", r["reason"])

    def test_due_after_interval(self):
        now = datetime.now(TZ)
        sch.log_probe(self.con, ok=True, elapsed=1.0)
        later = now + timedelta(minutes=20)
        r = sch.due_for_probe(self.con, later, 15)
        self.assertTrue(r["due"])

    def test_failed_probe_does_not_block_next(self):
        """探测失败不应把下一轮卡住 —— 否则失败一次要等一整个间隔。"""
        sch.log_probe(self.con, ok=False, elapsed=1.0, error="网络失败")
        r = sch.due_for_probe(self.con, datetime.now(TZ), 15)
        self.assertTrue(r["due"], "上次失败时应允许立即重试")


# --------------------------------------------------------------------------- 探测日志

class TestProbeLog(TmpCase):
    def test_log_and_read_back(self):
        sch.log_probe(self.con, ok=True, task="trend_intelligence", elapsed=2.5)
        row = self.con.execute("SELECT * FROM probe_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["task"], "trend_intelligence")
        self.assertEqual(row["ok"], 1)

    def test_failure_records_error(self):
        sch.log_probe(self.con, ok=False, elapsed=0.3, error="退出码 1")
        row = self.con.execute("SELECT * FROM probe_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["ok"], 0)
        self.assertIn("退出码", row["error"])

    def test_default_db_is_not_in_repo(self):
        """状态库必须落在 state/ 下（该目录已 gitignore）。"""
        self.assertIn("state", str(sch.SCHED_DB))
        self.assertTrue(str(sch.SCHED_DB).endswith("scheduler.sqlite3"))

    def test_sched_db_is_patchable(self):
        """★ 回归锁：路径必须在**调用时**解析，不能在定义时绑定。"""
        other = Path(self._tmp.name) / "other.sqlite3"
        with mock.patch.object(sch, "SCHED_DB", other):
            con = sch.connect()
            con.execute("CREATE TABLE t(x)")
            con.close()
        self.assertTrue(other.exists(), "patch 未生效：默认参数可能被定义时绑定")


# --------------------------------------------------------------------------- 状态报告

class TestStatusReport(TmpCase):
    def test_empty_report_says_no_records(self):
        with mock.patch.object(sch, "SCHED_DB", self.db):
            rep = sch.status_report()
        self.assertIn("尚无探测记录", rep)

    def test_report_lists_probes(self):
        sch.log_probe(self.con, ok=True, task="trend_intelligence", elapsed=2.5)
        sch.log_probe(self.con, ok=False, task="trend_intelligence", elapsed=9.0,
                      error="退出码 1")
        with mock.patch.object(sch, "SCHED_DB", self.db):
            rep = sch.status_report()
        self.assertIn("trend_intelligence", rep)
        self.assertIn("失败", rep)
        self.assertIn("退出码 1", rep)

    def test_report_marks_uncalibrated_values(self):
        sch.log_probe(self.con, ok=True, elapsed=1.0)
        with mock.patch.object(sch, "SCHED_DB", self.db):
            rep = sch.status_report()
        self.assertIn("未校准", rep)
        self.assertIn("已推导", rep)

    def test_report_flags_freq_calibration_as_rebuild_pending(self):
        """★ 诚实边界：旧校准机制（每轮变化量）依赖已移除的话题状态机，
        报告必须如实标注「待重建」，不能用旧数据假装连续。"""
        with mock.patch.object(sch, "SCHED_DB", self.db):
            rep = sch.status_report()
        self.assertIn("待重建", rep)


class TestPreflight(unittest.TestCase):
    """环境预检：子进程用同一个解释器，缺依赖要**快速失败并给可执行建议**。"""

    def test_ok_when_deps_present(self):
        with mock.patch.object(sch, "_has_module", return_value=True):
            self.assertTrue(sch.preflight()["ok"])

    def test_reports_missing_with_actionable_hint(self):
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
        with mock.patch.object(sch, "_has_module", return_value=True):
            self.assertIsNone(sch.preflight()["hint"])


# --------------------------------------------------------------------------- 编排（不真跑外部脚本）

class TestOrchestration(TmpCase):
    def test_dry_run_does_not_call_subprocess(self):
        """干跑：不碰子进程出口；下游任务只做**干跑计划**（run_task 以 dry_run 调用）。"""
        with mock.patch.object(sch, "SCHED_DB", self.db), \
             mock.patch.object(sch.harness, "exec_command") as fake, \
             mock.patch.object(sch.harness, "run_task") as fake_task:
            r = sch.run_once(dry_run=True)
        fake.assert_not_called()
        self.assertEqual(fake_task.call_count, 3, "下游三个任务都应以 dry_run 计划")
        self.assertTrue(all(kw.get("dry_run") for _, kw in fake_task.call_args_list))
        self.assertTrue(r["dry_run"])

    def test_dry_run_does_not_write_log(self):
        with mock.patch.object(sch, "SCHED_DB", self.db):
            sch.run_once(dry_run=True)
        n = self.con.execute("SELECT COUNT(*) c FROM probe_log").fetchone()["c"]
        self.assertEqual(n, 0, "干跑不应写日志（否则会污染校准数据）")

    def test_not_due_skips_probe_but_runs_chain(self):
        sch.log_probe(self.con, ok=True, elapsed=1.0)
        with mock.patch.object(sch, "SCHED_DB", self.db), \
             mock.patch.object(sch.harness, "exec_command"), \
             mock.patch.object(sch.harness, "run_task",
                               return_value={"ok": True, "n_steps": 1}):
            r = sch.run_once(dry_run=True, skip_probe_if_not_due=True)
        self.assertFalse(r["probe_due"]["due"])
        self.assertTrue(r["probe"].get("skipped"))
        self.assertIn("chain", r, "跳过探测时下游链仍应给出结果")

    def test_probe_steps_shape(self):
        """探测链已收成 **1 步**：交给巡检图 —— 「时钟驱动任务」的落点。"""
        steps = sch.probe_steps(dry_run=True)
        self.assertEqual(len(steps), 1, "探测链应为 1 步：跑巡检图")
        cmd = steps[0].get("cmd", "")
        self.assertIn("agent_graph.py", cmd, "唯一一步应是巡检图")
        self.assertIn("--task", cmd, "任务由契约注册表决定（默认 trend_intelligence）")
        self.assertIn("trend_intelligence", cmd)
        self.assertIn("--run", cmd)
        self.assertIn("--thread", cmd, "thread 决定幂等窗口与检查点归属")

    def test_chain_runs_all_downstream_tasks(self):
        """★ 下游链 = L4 → L5 → L6 三个任务，按注册表顺序执行。"""
        ran = []

        def fake_run_task(tid, **kw):
            ran.append(tid)
            return {"ok": True, "n_steps": 1}

        with mock.patch.object(sch.harness, "run_task", side_effect=fake_run_task):
            steps = sch.downstream_steps(dry_run=False)
        self.assertEqual(ran, ["intelligence_run", "memory_ingest", "ops_alerts"])
        self.assertEqual(len(steps), 3)
        self.assertTrue(all(s["ok"] for s in steps))

    def test_chain_failure_does_not_kill_other_tasks(self):
        """★ 降级纪律：一个下游任务失败，其余任务**照常执行**（不杀整轮）。"""
        def fake_run_task(tid, **kw):
            if tid == "memory_ingest":
                return {"ok": False, "n_steps": 1, "reason": "db 缺失"}
            return {"ok": True, "n_steps": 1}

        with mock.patch.object(sch.harness, "run_task", side_effect=fake_run_task):
            steps = sch.downstream_steps(dry_run=False)
        self.assertEqual(len(steps), 3, "失败不中断后续任务")
        by_id = {s["task_id"]: s for s in steps}
        self.assertFalse(by_id["memory_ingest"]["ok"])
        self.assertTrue(by_id["ops_alerts"]["ok"], "失败之后面的任务仍要跑")

    def test_run_once_aggregates_chain_result(self):
        """run_once 的 ok 要反映链上失败 —— 但 refused/skipped 的任务不算失败。"""
        # 预检只验「子进程依赖在不在」（requests），与本用例要验的聚合逻辑无关。
        # CI 裸环境没装 requests → preflight 判失败 → ok 恒 False → 误报。
        pf_ok = {"ok": True, "missing": [], "interpreter": "python", "hint": None}
        with mock.patch.object(sch, "SCHED_DB", self.db), \
             mock.patch.object(sch, "preflight", return_value=pf_ok), \
             mock.patch.object(sch.harness, "exec_command"), \
             mock.patch.object(sch.harness, "run_task",
                               side_effect=[{"ok": True, "n_steps": 1},
                                            {"refused": True, "reason": "契约拒绝"},
                                            {"ok": True, "n_steps": 1}]):
            r = sch.run_once(dry_run=False, skip_probe_if_not_due=True)
        self.assertTrue(r["ok"], "refused 的任务不应拖垮整轮 ok 判定")

    def test_no_chain_flag_skips_downstream(self):
        with mock.patch.object(sch, "SCHED_DB", self.db), \
             mock.patch.object(sch.harness, "run_task") as fake_task:
            r = sch.run_once(dry_run=True, skip_probe_if_not_due=True,
                             run_downstream=False)
        fake_task.assert_not_called()
        self.assertNotIn("chain", r)


if __name__ == "__main__":
    unittest.main()
