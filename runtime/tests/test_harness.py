#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""harness / task_contracts 的单元测试：契约纪律、参数校验、幂等、失败分派、预算、锁、轨迹。

重点锁死七件事（都是我在设计里声称的纪律，不测等于没做）：
  1. **契约必须过校验** —— success 可判定 / on_fail 按类型分派 / pending 与内容一致
  2. **参数校验不合法 → 拒绝且不调用工具**（"不进图"）
  3. **结果判定不只看 returncode** —— 返回 0 但写了空文件 = 不达标
  4. **幂等** —— 同任务同工具同参数同窗口 → 第二次命中缓存，不重复执行
  5. **失败按类型分派** —— network 重试到上限后降级；WAF **不重试**直接中止
  6. **预算** —— 超出 max_net_calls / max_tool_calls → 中止（记 budget 类错误）
  7. **空白任务拒绝执行** —— 契约已立但实现空白，harness 不假装能跑
       + 单例锁：第二次 acquire 失败，release 后可再取
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(LAB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    # ⚠️ 必须先注册进 sys.modules：@dataclass 内部会查 sys.modules[cls.__module__]，
    #    不注册会报 `'NoneType' object has no attribute '__dict__'`。
    #    （项目其他测试的模块没用 dataclass，所以此前没踩到。）
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


hr = _load("harness")
tc = _load("task_contracts")
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 1, 14, 0, tzinfo=TZ)


def fake_contract(**over):
    """一个最小合法契约（用于预算/失败分派的确定性测试）。"""
    c = {
        "task_id": "fake_task", "name": "假任务", "goal": "测试用",
        "inputs": [], "tools": ["t_local"], "outputs": [],
        "artifact_contract": {"audience": "测试", "format": "json"},
        "text_access": {"sources": ["title"], "granularity": "short",
                        "via_llm": False, "returns": ["state", "count"]},
        "success": {"min_series_rows": 1},
        "degrade": {}, "schedule": {"mode": "interval", "minutes": 15},
        "budget": {"max_tool_calls": 8, "max_net_calls": 2,
                   "timeout": 30, "max_retries": 2, "token": 0},
        "idempotency_key": "k", "on_fail": {"network": "retry", "empty": "degrade",
                                            "schema": "reject", "waf": "abort"},
        "human": False, "depends_on": [], "status": "built", "pending": [],
    }
    c.update(over)
    return c


class _PatchPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = {k: getattr(hr, k) for k in
                     ("TRACE_JSONL", "HARNESS_DB", "LOCK_FILE", "TOOLS", "tc")}
        hr.TRACE_JSONL = self.tmp / "trace.jsonl"
        hr.HARNESS_DB = self.tmp / "harness.sqlite3"
        hr.LOCK_FILE = self.tmp / "harness.lock"
        self._orig_tools = hr.TOOLS
        # 免去退避等待（重试测试否则要睡 6s）
        self._orig_sleep = hr.time.sleep
        hr.time.sleep = lambda s: None
        self.n_calls = 0
        self.hook_result: dict | None = None

    def tearDown(self):
        hr.time.sleep = self._orig_sleep
        for k, v in self._old.items():
            setattr(hr, k, v)

    # ---- 造数 ------------------------------------------------------

    def install_tools(self, **specs):
        """把工具注册表换成测试桩。specs: name=ToolSpec"""
        hr.TOOLS = lambda: dict(specs)

    def local_tool(self, name="t_local", **over):
        d = {"name": name, "script": Path("."), "net": False}
        d.update(over)
        return hr.ToolSpec(**d)

    def hook(self, result: dict | None = None):
        """call_hook：计数 + 返回指定结果。"""
        def _h(tool_name, params):
            self.n_calls += 1
            return result if result is not None else {"ok": True, "elapsed": 0.01,
                                                      "stdout": "", "stderr": ""}
        return _h

    def fail_hook(self, text: str):
        return self.hook({"ok": False, "elapsed": 0.01, "stdout": "", "stderr": text,
                          "exit_code": 1})


# ============================================================ 契约纪律

class TestContracts(unittest.TestCase):
    def test_all_registered_contracts_valid(self):
        problems = tc.validate_all()
        self.assertEqual(problems, {}, f"注册表里有不合法契约：{problems}")

    def test_required_fields_present(self):
        for tid, c in tc.all_tasks().items():
            for f in tc.REQUIRED_FIELDS:
                self.assertIn(f, c, f"{tid} 缺 {f}")

    def test_empty_success_rejected(self):
        c = fake_contract()
        c["success"] = {}
        self.assertTrue(any("success 为空" in p for p in tc.validate(c)))

    def test_single_action_on_fail_rejected(self):
        """纪律 2：on_fail 只有一种动作 → 非法。"""
        c = fake_contract()
        c["on_fail"] = {"network": "abort", "schema": "abort"}
        probs = tc.validate(c)
        self.assertTrue(any("只有一种动作" in p for p in probs), probs)

    def test_unknown_error_class_rejected(self):
        c = fake_contract()
        c["on_fail"] = {"网络": "retry", "empty": "degrade"}
        probs = tc.validate(c)
        self.assertTrue(any("未知错误类型" in p for p in probs), probs)

    def test_budget_incomplete_rejected(self):
        c = fake_contract()
        c["budget"] = {"timeout": 30}
        probs = tc.validate(c)
        self.assertTrue(any("budget 缺" in p for p in probs), probs)

    def test_pending_must_match_content(self):
        """纪律 4：pending 登记了但内容已填 → 非法（反之亦然）。"""
        c = fake_contract()
        c["pending"] = ["human"]
        self.assertTrue(any("human" in p for p in tc.validate(c)))
        c2 = fake_contract()
        c2["human"] = tc.PENDING          # 标了待拍板却没登记
        self.assertTrue(any("未登记进 pending" in p for p in tc.validate(c2)))

    def test_depends_on_unknown_rejected(self):
        c = fake_contract()
        c["depends_on"] = ["不存在的任务"]
        self.assertTrue(any("未注册任务" in p for p in tc.validate(c)))

    def test_trend_intelligence_empty_is_success(self):
        """★「本轮没有新事件」也算成功——这条写进契约也必须有测试看着。"""
        c = tc.get("trend_intelligence")
        self.assertTrue(c["success"]["empty_is_success"])

    def test_chain_dependencies_in_order(self):
        """★ 注册表即六层主链：依赖关系必须构成 L3→L4→L5→L6 的有序链。"""
        self.assertEqual(tc.get("trend_intelligence")["depends_on"], [])
        self.assertEqual(tc.get("intelligence_run")["depends_on"], ["trend_intelligence"])
        self.assertEqual(tc.get("memory_ingest")["depends_on"], ["intelligence_run"])
        self.assertEqual(tc.get("ops_alerts")["depends_on"], ["memory_ingest"])


class TestTextAccess(unittest.TestCase):
    """★ 文本读取声明：不是"不读文本"，而是"读法必须写清楚"。

    动机（2026-10-01 用户追问「这四个还是要原文的，不然就是盲目的」）：
    披露"原文不进 State"极易被误读成"系统不读文本"→ 那会做出**盲目**的系统。
    所以契约强制声明：读哪些源 / 什么粒度 / 是否送 LLM / **读完只回传什么**。
    """

    def test_text_access_required(self):
        c = fake_contract()
        del c["text_access"]
        probs = tc.validate(c)
        # 缺必填字段时 validate 会提前返回（后面的检查没意义）→ 命中必填字段报错
        self.assertTrue(any("text_access" in p for p in probs), probs)

    def test_llm_tool_must_declare_text_read(self):
        """★★ 最要紧的一条：声称做语义判断，就必须声明读文本 —— 否则是盲目的系统。"""
        c = fake_contract(tools=["llm_classify"])
        c["text_access"] = {"sources": [], "granularity": "none",
                            "via_llm": False, "returns": ["count"]}
        probs = tc.validate(c)
        self.assertTrue(any("盲目的系统" in p for p in probs), probs)

    def test_returns_must_not_be_raw_text(self):
        c = fake_contract()
        c["text_access"]["returns"] = ["material_id", "raw_text"]
        self.assertTrue(any("含原文类字段" in p for p in tc.validate(c)))

    def test_granularity_requires_sources(self):
        c = fake_contract()
        c["text_access"] = {"sources": [], "granularity": "full",
                            "via_llm": True, "returns": ["x"]}
        self.assertTrue(any("就是盲目" in p for p in tc.validate(c)))

    def test_via_llm_requires_returns(self):
        c = fake_contract()
        c["text_access"] = {"sources": ["summary"], "granularity": "full",
                            "via_llm": True, "returns": []}
        self.assertTrue(any("不许把原文回传" in p for p in tc.validate(c)))

    def test_real_contracts_declare_text_access(self):
        for tid in ("trend_intelligence", "intelligence_run"):
            ta = tc.get(tid)["text_access"]
            self.assertIn(ta["granularity"], tc.TEXT_GRANULARITIES)
            self.assertTrue(ta["sources"], f"{tid} 必须声明读哪些文本源")
            self.assertTrue(ta["returns"], f"{tid} 必须声明回传什么")

    def test_trend_intelligence_discloses_semantic_handoff(self):
        """诚实记录职责交接点：L3 只回答「什么在发生」，语义判断在第四层 Relevance。"""
        ta = tc.get("trend_intelligence")["text_access"]
        self.assertIn("第四层", ta.get("note", ""))
        self.assertIn("Relevance", ta.get("note", ""))


# ============================================================ 参数校验

class TestParamValidation(_PatchPaths):
    def test_defaults_applied(self):
        spec = self.local_tool(params={"limit": {"type": "int", "default": 10,
                                                 "min": 1, "max": 30}})
        clean, problems = hr.validate_params(spec, {})
        self.assertEqual(problems, [])
        self.assertEqual(clean["limit"], 10)

    def test_out_of_range_rejected(self):
        spec = self.local_tool(params={"limit": {"type": "int", "default": 10,
                                                 "min": 1, "max": 30}})
        _, problems = hr.validate_params(spec, {"limit": 999})
        self.assertTrue(any("上限" in p for p in problems))

    def test_wrong_type_rejected(self):
        spec = self.local_tool(params={"limit": {"type": "int", "default": 1}})
        _, problems = hr.validate_params(spec, {"limit": "abc"})
        self.assertTrue(problems)

    def test_unknown_param_rejected(self):
        spec = self.local_tool(params={})
        _, problems = hr.validate_params(spec, {"恶意参数": 1})
        self.assertTrue(any("未知参数" in p for p in problems))


# ============================================================ 失败分类

class TestClassify(_PatchPaths):
    def test_classes(self):
        cases = {
            "requests.exceptions.URLError: Tunnel connection failed: 502": "network",
            "被 WAF 拦下了：挑战页": "waf",
            "Error code: 402 - 您的使用量已超出频率限制": "llm",
            "usage: crawl.py [-h]": "schema",
            "完全没见过的错误 xyz": "schema",     # 未识别 → 打回，不盲目重试
        }
        for text, want in cases.items():
            self.assertEqual(hr.classify_failure(text), want, text)


# ============================================================ 结果判定

class TestJudgeResult(_PatchPaths):
    def test_empty_file_not_success(self):
        """★ 返回 0 却写下空文件 → 不达标（旧逻辑会算成功）。"""
        p = self.tmp / "out.csv"
        p.write_text("a,b\n", encoding="utf-8")          # 只有表头 = 0 行
        spec = self.local_tool(result={"artifact": p, "min_rows": 1})
        ok, why, _ = hr.judge_result(spec)
        self.assertFalse(ok)
        self.assertIn("0 行", why)

    def test_min_rows_pass(self):
        p = self.tmp / "out.csv"
        p.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
        spec = self.local_tool(result={"artifact": p, "min_rows": 2})
        ok, _, arts = hr.judge_result(spec)
        self.assertTrue(ok)
        self.assertTrue(any("2 行" in a for a in arts))

    def test_missing_artifact(self):
        spec = self.local_tool(result={"must_exist": self.tmp / "nope.json"})
        ok, why, _ = hr.judge_result(spec)
        self.assertFalse(ok)
        self.assertIn("不存在", why)

    def test_db_delta(self):
        import sqlite3
        db = self.tmp / "d.sqlite3"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE t (x)")
        con.commit()
        con.close()
        spec = self.local_tool(result={"db_delta": {"path": db, "table": "t", "min_rows": 1}})
        ok, why, _ = hr.judge_result(spec, before={f"{db}::t": 0})
        self.assertFalse(ok)                              # 0 增量 → 不达标
        self.assertIn("新增 0 行", why)


# ============================================================ 幂等

class TestIdempotency(_PatchPaths):
    def test_bucket_and_key(self):
        c = fake_contract(schedule={"mode": "interval", "minutes": 15})
        b1 = hr.idem_bucket(c, NOW)
        b2 = hr.idem_bucket(c, NOW + timedelta(minutes=3))
        b3 = hr.idem_bucket(c, NOW + timedelta(minutes=20))
        self.assertEqual(b1, b2, "同一 15min 窗口应同桶")
        self.assertNotEqual(b1, b3, "跨窗口应换桶")
        self.assertEqual(hr.idem_key("t", "tool", {"limit": 10}, b1),
                         hr.idem_key("t", "tool", {"limit": 10}, b1))
        self.assertNotEqual(hr.idem_key("t", "tool", {"limit": 10}, b1),
                            hr.idem_key("t", "tool", {"limit": 20}, b1))

    def test_second_call_hits_cache(self):
        self.install_tools(t_local=self.local_tool())
        c = fake_contract()
        con = hr.connect()
        s1 = hr.run_step("fake_task", "t_local", c, con=con, now=NOW,
                         call_hook=self.hook())
        s2 = hr.run_step("fake_task", "t_local", c, con=con, now=NOW,
                         call_hook=self.hook())
        self.assertEqual(s1["outcome"], "ok")
        self.assertFalse(s1.get("cached"))
        self.assertTrue(s2.get("cached"), "同窗口第二次应命中幂等缓存")
        self.assertEqual(self.n_calls, 1, "幂等命中时不应重复调用工具")
        con.close()

    def test_force_bypasses_cache(self):
        self.install_tools(t_local=self.local_tool())
        c = fake_contract()
        con = hr.connect()
        hr.run_step("fake_task", "t_local", c, con=con, now=NOW, call_hook=self.hook())
        hr.run_step("fake_task", "t_local", c, con=con, now=NOW, force=True,
                    call_hook=self.hook())
        self.assertEqual(self.n_calls, 2)
        con.close()


# ============================================================ 失败分派

class TestFailDispatch(_PatchPaths):
    def _run(self, contract, text):
        self.install_tools(t_local=self.local_tool())
        return hr.run_step("fake_task", "t_local", contract, now=NOW,
                           call_hook=self.fail_hook(text))

    def test_network_retries_then_degrades(self):
        c = fake_contract()                        # network → retry, max_retries=2
        step = self._run(c, "requests.exceptions.URLError: Tunnel connection failed: 502")
        self.assertEqual(step["attempt"], 3, "应重试到上限（1+2 次）")
        self.assertEqual(step["outcome"], "degraded", "重试耗尽后降级，不是失败")
        self.assertEqual(step["error_class"], "network")

    def test_waf_aborts_without_retry(self):
        """★ 关键：反爬拦截**不重试**——越重试越糟。"""
        c = fake_contract()
        step = self._run(c, "被 WAF 拦：检测到挑战页")
        self.assertEqual(step["attempt"], 1, "WAF 不该重试")
        self.assertEqual(step["outcome"], "aborted")
        self.assertEqual(step["error_class"], "waf")
        self.assertEqual(self.n_calls, 1)

    def test_schema_rejects_immediately(self):
        c = fake_contract()
        step = self._run(c, "usage: crawl.py [-h] --source")
        self.assertEqual(step["attempt"], 1)
        self.assertEqual(step["outcome"], "rejected", "schema 类 → 打回，不重试")

    def test_bad_params_never_call_tool(self):
        """参数不合法 → 拒绝，且**工具一次都不许被调用**。"""
        self.install_tools(t_local=self.local_tool(
            params={"limit": {"type": "int", "default": 10, "min": 1, "max": 30}}))
        c = fake_contract()
        step = hr.run_step("fake_task", "t_local", c, params={"limit": 999},
                           now=NOW, call_hook=self.hook())
        self.assertEqual(step["outcome"], "rejected")
        self.assertEqual(self.n_calls, 0, "参数校验不过就不该调工具（不进图）")

    def test_unimplemented_tool_follows_contract_policy(self):
        """★ 未实现的工具**不是一律中止** —— 按 `unavailable_class` 走契约的 on_fail。

        `.schema` 类 → 打回（编程错误，需人看）；
        `.llm` 类 → 降级（如素材的梗/二创角度需 LLM，当前无 key → 出可出的两类）。
        **部分产出 + 显式标注 > 全部失败。**
        """
        # schema 类 → reject
        self.install_tools(t_local=self.local_tool(implemented=False, note="未写"))
        c = fake_contract()
        step = hr.run_step("fake_task", "t_local", c, now=NOW, call_hook=self.hook())
        self.assertEqual(step["outcome"], "rejected")
        self.assertEqual(step["error_class"], "schema")
        self.assertEqual(self.n_calls, 0, "不可用的工具不该被调用")

        # llm 类：**契约声明了** llm→degrade 时才降级
        self.install_tools(t_local=self.local_tool(
            implemented=False, note="需 LLM", unavailable_class="llm"))
        c_llm = fake_contract(on_fail={"network": "retry", "empty": "degrade",
                                       "schema": "reject", "waf": "abort",
                                       "llm": "degrade"})
        step2 = hr.run_step("fake_task", "t_local", c_llm, now=NOW, call_hook=self.hook())
        self.assertEqual(step2["outcome"], "degraded")
        self.assertEqual(step2["error_class"], "llm")
        self.assertIn("degrade", step2["note"])

        # 契约**没声明** llm 策略时 → 兜底 abort（不擅自降级）
        step3 = hr.run_step("fake_task", "t_local", c, now=NOW, call_hook=self.hook())
        self.assertEqual(step3["outcome"], "aborted",
                         "契约未声明该错误类型的策略时应保守中止")


# ============================================================ 预算

class TestBudget(_PatchPaths):
    def _task(self, contract, tools):
        self.install_tools(**tools)
        hr.tc = _FakeTC(contract)
        return hr.run_task("fake_task", now=NOW, use_lock=False,
                           trace_path=self.tmp / "trace.jsonl",
                           call_hook=self.hook())

    def test_net_budget_aborts(self):
        c = fake_contract(tools=["t_net1", "t_net2"],
                          budget={"max_tool_calls": 8, "max_net_calls": 1,
                                  "timeout": 30, "max_retries": 0, "token": 0})
        tools = {"t_net1": self.local_tool("t_net1", net=True),
                 "t_net2": self.local_tool("t_net2", net=True)}
        r = self._task(c, tools)
        self.assertEqual(r["steps"][1]["error_class"], "budget")
        self.assertEqual(r["steps"][1]["outcome"], "aborted")
        self.assertEqual(r["net_calls"], 1, "只应发出 1 次网络调用")
        self.assertEqual(self.n_calls, 1)

    def test_tool_calls_budget_aborts(self):
        c = fake_contract(tools=["a", "b", "c"],
                          budget={"max_tool_calls": 2, "max_net_calls": 5,
                                  "timeout": 30, "max_retries": 0, "token": 0})
        tools = {n: self.local_tool(n) for n in ("a", "b", "c")}
        r = self._task(c, tools)
        self.assertEqual(len(r["steps"]), 3)
        self.assertEqual(r["steps"][2]["error_class"], "budget")
        self.assertEqual(self.n_calls, 2)


class _FakeTC:
    """把 task_contracts 换成桩，让预算测试可以喂自定义契约。"""
    def __init__(self, contract):
        self._c = contract

    def get(self, tid):
        return self._c

    def validate(self, c):
        return []


# ============================================================ 拒绝 / 锁 / 轨迹

class TestGuardAndTrace(_PatchPaths):
    def test_blank_task_refused(self):
        """★ 契约已立、实现空白 → 明确拒绝（harness 不假装能跑）。

        （用桩契约验拒绝路径。）
        """
        self.install_tools(a=self.local_tool("a"))
        hr.tc = _FakeTC(fake_contract(task_id="blank_task", tools=["a"], status="blank"))
        r = hr.run_task("blank_task", now=NOW, use_lock=False,
                        trace_path=self.tmp / "trace.jsonl", call_hook=self.hook())
        self.assertTrue(r["refused"])
        self.assertIn("实现空白", r["reason"])
        self.assertEqual(self.n_calls, 0, "拒绝时不该跑任何工具")

    def test_lock_exclusive_and_reusable(self):
        a = hr.TaskLock()
        self.assertTrue(a.acquire(), "首次抢锁应成功")
        b = hr.TaskLock()
        self.assertFalse(b.acquire(), "持锁期间第二个不该抢到")
        a.release()
        c = hr.TaskLock()
        self.assertTrue(c.acquire(), "释放后应可再抢")
        c.release()

    def test_lock_blocks_run_task(self):
        holder = hr.TaskLock()
        self.assertTrue(holder.acquire())
        try:
            r = hr.run_task("trend_intelligence", now=NOW, trace_path=self.tmp / "trace.jsonl")
            self.assertTrue(r.get("skipped"), "有锁在持 → 本轮跳过（防 tick 重叠）")
        finally:
            holder.release()

    def test_trace_written_per_step_and_summary(self):
        self.install_tools(a=self.local_tool("a"), b=self.local_tool("b"))
        hr.tc = _FakeTC(fake_contract(tools=["a", "b"]))
        tp = self.tmp / "trace.jsonl"
        hr.run_task("fake_task", now=NOW, use_lock=False, trace_path=tp,
                    call_hook=self.hook())
        rows = [json.loads(l) for l in tp.read_text(encoding="utf-8").splitlines() if l.strip()]
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds.count("step"), 2, "每步一条轨迹")
        self.assertEqual(kinds.count("run_summary"), 1, "每次运行一条汇总")
        for r in rows:
            self.assertIn("ts", r)
        step = [r for r in rows if r["kind"] == "step"][0]
        for k in ("tool", "outcome", "attempt", "elapsed"):
            self.assertIn(k, step, "轨迹必须含 attempt/耗时/成败（可回放的前提）")

    def test_empty_run_still_success_for_trend(self):
        """empty_is_success 生效：无产出增量也判成功，但要有降级说明。"""
        self.install_tools(l1_collect=self.local_tool("l1_collect"),
                           l2_process=self.local_tool("l2_process"))
        c = dict(tc.get("trend_intelligence"))
        c["tools"] = ["l1_collect", "l2_process"]   # 只装这两个（stub 全通过、无产物）
        hr.tc = _FakeTC(c)
        r = hr.run_task("trend_intelligence", now=NOW, use_lock=False,
                        trace_path=self.tmp / "trace.jsonl", call_hook=self.hook())
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("empty_is_success" in d for d in r["degraded"]), r["degraded"])


# ============================================================ 执行层（子进程出口）

class TestExecCommand(unittest.TestCase):
    """`exec_command` 是执行层的**唯一**子进程出口 —— 编码/超时/不弹窗/失败分类都在这里统一。

    2026-10-01：此前 scheduler 与 harness **各写了一份** subprocess 调用（编码注入、超时、降级），
    用户指出「这种应该放进 agent harness 里面」→ 收敛到这一个出口，并加了不变量测试防止再分裂。
    """

    def test_success(self):
        r = hr.exec_command([sys.executable, "-c", "print('hi')"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["exit_code"], 0)
        self.assertIn("hi", r["stdout"])
        self.assertIsNone(r["error"])
        self.assertIsNone(r["error_class"])
        self.assertGreaterEqual(r["elapsed"], 0)

    def test_nonzero_exit_is_classified(self):
        r = hr.exec_command([sys.executable, "-c",
                             "import sys; sys.stderr.write('usage: x [-h]'); sys.exit(1)"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["exit_code"], 1)
        self.assertIn("退出码 1", r["error"])
        self.assertEqual(r["error_class"], "schema", "失败类型应被归一")

    def test_network_like_error_classified(self):
        r = hr.exec_command([sys.executable, "-c",
                             "import sys; sys.stderr.write('URLError: 502'); sys.exit(1)"])
        self.assertEqual(r["error_class"], "network")

    def test_timeout_returns_structured_failure(self):
        r = hr.exec_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
        self.assertFalse(r["ok"])
        self.assertIn("超时", r["error"])
        self.assertEqual(r["error_class"], "network", "超时按可重试类（network）处理")

    def test_missing_script_classified_not_raised(self):
        """脚本不存在时：解释器**起得来**、自己报错退出 2 → 按退出码分支归一为 schema。"""
        r = hr.exec_command([sys.executable, "绝对不存在的脚本.py"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_class"], "schema", "'No such file' 属打回类")
        self.assertIn("No such file", r["stderr"])

    def test_bad_executable_returns_structured_failure(self):
        """解释器本身起不来（OSError）→ 也必须是结构化失败，不抛异常。"""
        r = hr.exec_command(["绝对不存在的解释器.exe", "-c", "pass"])
        self.assertFalse(r["ok"])
        self.assertIn("无法启动进程", r["error"])
        self.assertEqual(r["error_class"], "schema")

    def test_non_utf8_output_does_not_crash(self):
        """★ Windows 编码坑：子进程吐 GBK 字节也不能让整轮崩（errors='replace'）。"""
        code = "import sys; sys.stdout.buffer.write(b'\\xd6\\xd0\\xce\\xc4'); sys.stdout.flush()"
        r = hr.exec_command([sys.executable, "-c", code])
        self.assertTrue(r["ok"])
        self.assertIsInstance(r["stdout"], str)

    def test_dry_run_does_not_execute(self):
        from pathlib import Path
        import tempfile
        marker = Path(tempfile.mkdtemp()) / "should_not_exist.txt"
        r = hr.exec_command([sys.executable, "-c",
                             f"open(r'{marker}', 'w').write('x')"], dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertFalse(marker.exists(), "dry-run 不许真执行")
        self.assertEqual(r["elapsed"], 0.0)

    def test_no_window_flags_value(self):
        kw = hr.no_window_kwargs()
        if hr.IS_WINDOWS:
            self.assertEqual(kw, {"creationflags": 0x08000000}, "应传 CREATE_NO_WINDOW")
        else:
            self.assertEqual(kw, {}, "非 Windows 不传 creationflags")


class TestStdStreamsFallback(unittest.TestCase):
    """pythonw 无控制台时 sys.stdout 为 None → 必须兜底，否则 print() 抛异常、调度器静默崩。"""

    def _tmp_log(self):
        import tempfile
        return Path(tempfile.mkdtemp()) / "console.log"

    def test_noop_when_streams_valid(self):
        before = sys.stdout
        self.assertIsNone(hr.ensure_std_streams(self._tmp_log()), "有控制台时不许动标准流")
        self.assertIs(sys.stdout, before)

    def test_redirects_when_streams_missing(self):
        log = self._tmp_log()
        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            sys.stdout = None
            sys.stderr = None
            ret = hr.ensure_std_streams(log)
            self.assertEqual(ret, log)
            print("这行必须落到日志里")          # 旧代码在这里抛 AttributeError
            sys.stderr.write("stderr 也要能写\n")
        finally:
            fh = sys.stdout
            if fh is not None and hasattr(fh, "close"):
                fh.close()
            sys.stdout, sys.stderr = saved_out, saved_err
        text = log.read_text(encoding="utf-8")
        self.assertIn("无控制台启动，输出转存至此", text)
        self.assertIn("这行必须落到日志里", text)

    def test_rotates_when_too_large(self):
        log = self._tmp_log()
        log.write_text("x" * 50, encoding="utf-8")
        old = hr.MAX_CONSOLE_LOG_BYTES
        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            hr.MAX_CONSOLE_LOG_BYTES = 10
            sys.stdout = None
            sys.stderr = None
            hr.ensure_std_streams(log)
        finally:
            fh = sys.stdout
            if fh is not None and hasattr(fh, "close"):
                fh.close()
            sys.stdout, sys.stderr = saved_out, saved_err
            hr.MAX_CONSOLE_LOG_BYTES = old
        self.assertTrue(log.with_suffix(".log.1").exists(), "超上限应轮转")


class TestSuppressConsole(unittest.TestCase):
    """计划任务用 python.exe 启动会弹控制台 —— 若该控制台**属于本进程**就自己隐藏。

    ★ 安全前提：从终端/IDE 启动时控制台是**继承**来的，隐藏它会把**用户的终端窗口**也隐掉。
      所以必须先判「控制台是否为本进程所有」（`GetConsoleProcessList` 只有自己）。
    """

    def _log(self):
        import tempfile
        return Path(tempfile.mkdtemp()) / "console.log"

    def test_does_not_hide_when_console_shared_or_absent(self):
        """实测（本环境派生进程无控制台）：返回 False —— **绝不动**可能属于用户的控制台。"""
        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            self.assertFalse(hr.suppress_console(self._log()),
                             "没有自有控制台时不许隐藏任何东西")
            self.assertIs(sys.stdout, saved_out, "也不该改标准流")
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err

    def test_false_when_no_console_handle(self):
        import ctypes
        calls = []

        class _K32:
            @staticmethod
            def GetConsoleWindow():
                return 0

        class _U32:
            @staticmethod
            def ShowWindow(hwnd, cmd):
                calls.append((hwnd, cmd))

        from unittest import mock
        with mock.patch.object(ctypes, "windll",
                               type("W", (), {"kernel32": _K32, "user32": _U32}),
                               create=True):
            self.assertFalse(hr.suppress_console(self._log()))
        self.assertEqual(calls, [], "没有窗口句柄就不该调 ShowWindow")

    @unittest.skipUnless(sys.platform == "win32",
                         "控制台隐藏/重定向是 Windows 特有行为（ctypes.windll），非 Windows 平台不适用")
    def test_hides_and_redirects_when_own_console(self):
        """正向路径（本环境无法端到端复现，用 mock 验控制流）：隐藏 + 输出转日志。"""
        import ctypes
        from unittest import mock
        log = self._log()
        calls = []

        class _K32:
            @staticmethod
            def GetConsoleWindow():
                return 0x1234

        class _U32:
            @staticmethod
            def ShowWindow(hwnd, cmd):
                calls.append((hwnd, cmd))
                return 1

        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            with mock.patch.object(ctypes, "windll",
                                   type("W", (), {"kernel32": _K32, "user32": _U32}),
                                   create=True):
                with mock.patch.object(hr, "_owns_own_console", return_value=True):
                    ok = hr.suppress_console(log)
            self.assertTrue(ok)
            self.assertEqual(calls, [(0x1234, 0)], "应调 ShowWindow(hwnd, SW_HIDE=0)")
            self.assertIn("无可见控制台", log.read_text(encoding="utf-8"),
                          "窗口藏了 → 输出必须转日志")
        finally:
            fh = sys.stdout
            if fh is not None and hasattr(fh, "close") and fh not in (saved_out, saved_err):
                fh.close()
            sys.stdout, sys.stderr = saved_out, saved_err

    def test_entrypoints_suppress_console(self):
        """结构证据：三个入口都调了（否则改动会被无意删掉）。"""
        for name, needle in (("harness.py", "suppress_console()"),
                             ("scheduler.py", "harness.suppress_console()"),
                             ("agent_graph.py", "harness.suppress_console()")):
            s = (LAB / name).read_text(encoding="utf-8")
            self.assertIn(needle, s, f"{name} 入口应隐藏自有控制台")


class TestExecLayerInvariants(unittest.TestCase):
    """★ 架构不变量：**执行层只有 harness 一个子进程出口**（防止重复实现再长回来）。"""

    def src(self, name: str) -> str:
        return (LAB / name).read_text(encoding="utf-8")

    def test_harness_owns_the_subprocess_entry(self):
        s = self.src("harness.py")
        self.assertIn("def exec_command(", s, "harness 应提供唯一子进程出口")
        self.assertIn("PYTHONIOENCODING", s, "编码注入应只在 harness 里做一次")
        self.assertIn("**no_window_kwargs()", s, "子进程须带不弹窗标志")

    def test_scheduler_does_not_spawn_processes_itself(self):
        """编排者不该自己起进程 —— 否则编码/超时/平台细节又会分裂成两份。"""
        s = self.src("scheduler.py")
        self.assertIn("harness.exec_command(", s, "调度器应走 harness 的出口")
        self.assertNotIn("subprocess.run(", s, "调度器不该自己 subprocess.run")
        self.assertNotIn("PYTHONIOENCODING", s, "编码注入不该在调度器里重复")

    def test_platform_module_was_merged_into_harness(self):
        self.assertFalse((LAB / "win_env.py").exists(),
                         "win_env 应已并入 harness（用户：这种应该放进 agent harness 里面）")
        self.assertIn("IS_WINDOWS", self.src("harness.py"))

    def test_entrypoints_have_console_fallback(self):
        for name, needle in (("harness.py", "ensure_std_streams()"),
                             ("scheduler.py", "harness.ensure_std_streams()"),
                             ("agent_graph.py", "harness.ensure_std_streams()")):
            self.assertIn(needle, self.src(name), f"{name} 入口须补标准流兜底")

    def test_register_script_prefers_pythonw(self):
        s = (LAB.parent / "scripts" / "register_scheduler_task.ps1").read_text(
            encoding="utf-8-sig")
        self.assertIn("pythonw.exe", s)
        self.assertIn("$Runner", s, "动作应使用 pythonw 优先的 Runner")


if __name__ == "__main__":
    unittest.main()
