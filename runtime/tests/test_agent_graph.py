#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""agent_graph（LangGraph 巡检图）测试：图结构、State 守卫、有界重试环、interrupt 跨进程恢复。

重点锁死五件事（都是"图"相比"手写流水线"多出来的能力，不测等于没有）：
  1. **图结构真的存在** —— 节点齐全 + 条件边（含 qc→crawl 重试环），不是一句声明
  2. **State 守卫** —— 原始文本字段名 / 超长字符串 进 State 直接抛异常（锁数纪律在框架层落地）
  3. **有界重试环** —— 质检不过回到 crawl，到上限转 mark_degraded（**不会无限环**）
  4. **interrupt + checkpointer** —— 停在人工确认点，**恢复时不重跑已完成的节点**
  5. **空白任务拒绝建图** —— 与 harness 同一条纪律

降级纪律：langgraph 未安装时**整组跳过**（主套件保持纯标准库可跑）。
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(LAB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # @dataclass / 循环导入都需要（见 test_harness 注释）
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


hr = _load("harness")
ag = _load("agent_graph")
tc = _load("task_contracts")
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 1, 15, 0, tzinfo=TZ)


class _patch_contracts:
    """临时替换 agent_graph 读到的契约（用于验证 blank / 自定义契约路径）。"""

    def __init__(self, contract: dict):
        self.contract = contract
        self._orig = None

    def __enter__(self):
        self._orig = ag.tc
        self._orig_validate = ag.tc.validate

        class _TC:
            def __init__(self, outer):
                self._o = outer

            def get(self, tid):  # noqa: ARG002
                return self._o.contract

            def validate(self, c):  # noqa: ARG002
                return []

        ag.tc = _TC(self)
        return self

    def __exit__(self, *exc):
        ag.tc = self._orig
        return False


# ============================================================ State 守卫（不需要 langgraph）

class TestStateGuard(unittest.TestCase):
    def test_rejects_raw_text_field_names(self):
        for bad in ("raw_text", "comment_content", "review_body", "original_text"):
            with self.assertRaises(ValueError):
                ag.assert_state_clean({"steps": [{bad: "随便什么"}]})

    def test_rejects_long_string(self):
        with self.assertRaises(ValueError):
            ag.assert_state_clean({"note": "长" * (ag.MAX_STATE_STR + 1)})

    def test_allows_facts_and_nested(self):
        ag.assert_state_clean({"run_id": "r", "series_delta": 23,
                               "steps": [{"tool": "crawl", "outcome": "ok",
                                          "artifacts": ["hot_hashtags.csv(14 行)"]}],
                               "log": ["[qc] 14 行"]})

    def test_rejects_banned_inside_list(self):
        with self.assertRaises(ValueError):
            ag.assert_state_clean({"steps": [{"raw_text": "x"}]})

    def test_boundary_guard_is_size_not_semantics(self):
        """★ 显式记录边界：守卫是**体积闸门**，不是语义闸门。

        实测（素材层设计 §3.5.7）：帖 summary 平均 90.4 字 —— **它能过守卫**。
        真正把文本挤出 State 的是**契约**（每个任务的 outputs 必须落指定文件），
        不是这条守卫。所以本用例**故意断言"能过"**，免得以后有人误以为守卫能挡语义。
        """
        post_summary = "这是一段帖子摘要，用来演示体积闸门的边界：" + "内容" * 40
        self.assertLess(len(post_summary), ag.MAX_STATE_STR)
        ag.assert_state_clean({"summary": post_summary})          # ← 不抛：能过闸
        with self.assertRaises(ValueError):
            ag.assert_state_clean({"summary": "长" * (ag.MAX_STATE_STR + 1)})  # 超上限才抛


# ============================================================ 图相关（需要 langgraph）

@unittest.skipUnless(HAS_LANGGRAPH, "langgraph 未安装")
class _GraphBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = {k: getattr(hr, k) for k in
                     ("TRACE_JSONL", "HARNESS_DB", "EVENTS_DB", "TOOLS")}
        hr.TRACE_JSONL = self.tmp / "trace.jsonl"
        hr.HARNESS_DB = self.tmp / "harness.sqlite3"
        hr.EVENTS_DB = self.tmp / "events.sqlite3"
        self.calls: dict[str, int] = {}
        # 采集产物：可控行数 → 用来驱动质检闸门
        self.artifact = self.tmp / "hot_hashtags.csv"
        self.set_rows(14)
        self.install_tools()
        self.con = hr.connect()          # 复用一条连接，tearDown 关闭（避免 ResourceWarning）

    def tearDown(self):
        self.con.close()
        for k, v in self._old.items():
            setattr(hr, k, v)

    def set_rows(self, n: int):
        self.artifact.write_text("hashtag_id,title\n" +
                                 "".join(f"{i},t{i}\n" for i in range(n)), encoding="utf-8")

    def install_tools(self):
        """桩工具：都不发网络、不真执行；crawl 的 artifact 指向可控 CSV。"""
        def mk(name, **over):
            d = {"name": name, "script": Path("."), "net": False}
            d.update(over)
            return hr.ToolSpec(**d)
        hr.TOOLS = lambda: {
            "crawl_hot_hashtags": mk("crawl_hot_hashtags",
                                     result={"artifact": self.artifact, "min_rows": 1}),
            "platform_facts": mk("platform_facts"),
            "topic_sample": mk("topic_sample"),
            "events_run": mk("events_run"),
            "features_run": mk("features_run"),
        }

    def hook(self):
        def _h(tool, params):
            self.calls[tool] = self.calls.get(tool, 0) + 1
            return {"ok": True, "elapsed": 0.01, "stdout": "", "stderr": ""}
        return _h

    def g(self, **kw):
        """跑图（**不要叫 run** —— 会覆盖 TestCase.run(result) 的签名）。"""
        kw.setdefault("con", self.con)
        kw.setdefault("trace_path", self.tmp / "trace.jsonl")
        kw.setdefault("db_path", self.tmp / "ck.sqlite3")
        kw.setdefault("now", NOW)
        kw.setdefault("call_hook", self.hook())
        return ag.run_graph(**kw)


class TestGraphStructure(_GraphBase):
    def test_nodes_and_conditional_edges(self):
        """★ 「有图」的可核对证据：节点**由契约工具链生成** + 条件边（含重试环）。"""
        m = ag.mermaid(task_id="hotspot_track")
        for node in ("tick", "qc", "gate", "finish", "mark_degraded"):
            self.assertIn(node, m, f"图里缺公共节点 {node}")
        # 工具节点由契约顺序生成
        for tool in ("crawl_hot_hashtags", "platform_facts", "topic_sample",
                     "events_run", "features_run"):
            self.assertIn(tool, m, f"图里缺工具节点 {tool}")
        self.assertIn(" -. ", m, "应有条件边（虚线，带标签）")
        self.assertIn("retry", m, "应有一条「重试」条件边")
        self.assertIn("mark_degraded", m, "超上限要有出口，不能无限环")

    def test_graph_is_contract_driven(self):
        """★★ 框架性证据：**同一张图**跑第二个任务 → 节点完全不同。

        这一条不做，图就只是「一个流程」而不是「一个框架」。
        """
        m1 = ag.mermaid(task_id="hotspot_track")
        m2 = ag.mermaid(task_id="material_extract")
        self.assertIn("crawl_hot_hashtags", m1)
        self.assertIn("material_extract_code", m2, "第二个任务的节点应出现在图里")
        self.assertIn("llm_classify", m2)
        self.assertNotIn("crawl_hot_hashtags", m2, "不同任务不该共用热点专属节点")
        # 骨架相同（tick/qc/gate/finish 都在），节点不同 → 这就是「框架」
        for skeleton in ("tick", "qc", "finish", "mark_degraded"):
            self.assertIn(skeleton, m1)
            self.assertIn(skeleton, m2)

    def test_normal_run_ok(self):
        r = self.g(task_id="hotspot_track")
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["interrupted"])
        self.assertEqual(r["n_steps"], 5, "5 个工具各跑一次")
        self.assertGreaterEqual(r["n_rows"], 0, "产物行数（质检判据）")
        self.assertEqual(r["qc_rounds"], 1)

    def test_qc_retry_loop_is_bounded(self):
        """★ 质检不过 → 重来 → 还不过 → 标降级继续（**有界环**）。"""
        self.set_rows(0)                       # 产物 0 行 → 质检不过
        r = self.g(task_id="hotspot_track")
        self.assertEqual(r["qc_rounds"], ag.MAX_QC_ROUNDS, "应重来到上限")
        self.assertEqual(self.calls.get("crawl_hot_hashtags"), ag.MAX_QC_ROUNDS)
        self.assertTrue(any("降级" in d for d in r["degraded"]), r["degraded"])
        self.assertTrue(r["ok"], "降级不是失败：仍应继续走完")

    def test_qc_pass_no_retry(self):
        r = self.g(task_id="hotspot_track")
        self.assertEqual(self.calls.get("crawl_hot_hashtags"), 1, "质检过了不该重采")

    def test_blank_task_refused(self):
        """契约已立、实现空白 → 图不假装能跑。

        （`material_extract` 2026-10-01 起已翻为 `partial`，所以用桩契约验这条路径。）
        """
        c = dict(tc.get("hotspot_track"))
        c.update({"task_id": "blank_task", "status": "blank"})
        with _patch_contracts(c):
            r = self.g(task_id="blank_task")
        self.assertTrue(r["refused"])
        self.assertIn("实现空白", r["reason"])


class TestInterrupt(_GraphBase):
    def test_interrupt_then_resume_without_rerun(self):
        """★ 停在人工确认点；**恢复时不重跑已完成的节点**（checkpointer 的实质价值）。"""
        r1 = self.g(task_id="hotspot_track", use_gate=True, thread="g1")
        self.assertTrue(r1["interrupted"], r1)
        self.assertEqual(r1["n_steps"], 1, "质检通过后即停在 gate 前（只跑了首工具）")
        self.assertEqual(self.calls.get("crawl_hot_hashtags"), 1)
        self.assertIn("是否批准", r1["interrupt"]["ask"])

        crawl_before = self.calls.get("crawl_hot_hashtags", 0)
        r2 = self.g(task_id="hotspot_track", use_gate=True, thread="g1",
                      resume=True)
        self.assertTrue(r2["ok"], r2)
        self.assertFalse(r2["interrupted"])
        self.assertEqual(r2["n_steps"], 5, "恢复后补齐 act 的两个工具")
        self.assertEqual(self.calls.get("crawl_hot_hashtags"), crawl_before,
                         "★ 恢复时不该重跑 crawl（检查点已存）")

    def test_checkpoint_persisted_to_sqlite(self):
        """中断状态真的落到了 sqlite（跨进程可恢复的前提）。"""
        self.g(task_id="hotspot_track", use_gate=True, thread="g2")
        db = self.tmp / "ck.sqlite3"
        self.assertTrue(db.exists(), "checkpoint 库应存在")
        con = sqlite3.connect(str(db))
        n = con.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='g2'").fetchone()[0]
        con.close()
        self.assertGreater(n, 0, "应有检查点行")

    def test_gate_off_skips_interrupt(self):
        r = self.g(task_id="hotspot_track", use_gate=False)
        self.assertFalse(r["interrupted"], "契约 human=False 时不该停")
        self.assertEqual(r["n_steps"], 5)


class TestTraces(_GraphBase):
    def test_graph_run_trace_written(self):
        self.g(task_id="hotspot_track")
        rows = [json.loads(l) for l in (self.tmp / "trace.jsonl")
                .read_text(encoding="utf-8").splitlines() if l.strip()]
        kinds = [r["kind"] for r in rows]
        self.assertIn("graph_run", kinds, "图运行也应进轨迹（可回放）")
        gr = [r for r in rows if r["kind"] == "graph_run"][0]
        for k in ("thread", "ok", "n_steps", "n_rows", "qc_rounds"):
            self.assertIn(k, gr)


if __name__ == "__main__":
    unittest.main()
