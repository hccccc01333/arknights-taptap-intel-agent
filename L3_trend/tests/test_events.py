#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""events 的单元测试：幂等、失败隔离、分发门槛、迁移识别。

重点锁死三件事：
  1. **幂等** —— 调度器每 5 分钟跑一次，同一次迁移只能触发一次事件
  2. **失败隔离** —— 一个订阅者挂了不能拖垮其他，也不能让事件卡住
  3. **首日采样不算迁移** —— prev_state 为空或 state_changed_at == first_seen_at 的要滤掉
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]          # 本层目录（L3_trend）
ROOT = Path(__file__).resolve().parents[2]         # 项目根
RUNTIME = ROOT / "runtime"                          # 控制面：scheduler / harness / task_contracts / agent_graph

# 分层后归属 runtime 的模块（其余仍在 L3_trend）
_RUNTIME_MODULES = {"scheduler", "harness", "task_contracts", "agent_graph"}


def _load(name: str):
    base = RUNTIME if name in _RUNTIME_MODULES else LAB
    spec = importlib.util.spec_from_file_location(name, str(base / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


ev = _load("events")


def topic(key="hid:1|page_view", title="某话题", state="升温",
          prev="冒头", changed="2026-09-30T12:00:00+08:00",
          first="2026-09-30T10:00:00+08:00", hid="1", metric=1000, samples=3):
    return {"topic_key": key, "title": title, "hashtag_id": hid, "metric_kind": "page_view",
            "state": state, "prev_state": prev, "state_changed_at": changed,
            "first_seen_at": first, "last_metric": metric, "peak_metric": metric,
            "sample_count": samples}


def state_of(items, state_name="升温"):
    return {"by_state": {state_name: items}}


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "ev.sqlite3"
        self.con = ev.connect(self.db)

    def tearDown(self):
        self.con.close()
        self._tmp.cleanup()


# --------------------------------------------------------------------------- 幂等

class TestIdempotency(TmpCase):
    def test_event_id_is_deterministic(self):
        a = ev.make_event_id("k", "升温", "2026-09-30T12:00:00+08:00")
        b = ev.make_event_id("k", "升温", "2026-09-30T12:00:00+08:00")
        self.assertEqual(a, b)

    def test_event_id_differs_by_state(self):
        a = ev.make_event_id("k", "升温", "t")
        b = ev.make_event_id("k", "爆发", "t")
        self.assertNotEqual(a, b)

    def test_event_id_differs_by_time(self):
        """★ 同一话题两次「升温」（先升后退再升）应是两个事件。"""
        a = ev.make_event_id("k", "升温", "2026-09-30T12:00:00+08:00")
        b = ev.make_event_id("k", "升温", "2026-10-05T12:00:00+08:00")
        self.assertNotEqual(a, b)

    def test_emit_twice_creates_one_event(self):
        """★★ 核心：调度器重跑不能重复触发。"""
        ts = state_of([topic()])
        r1 = ev.emit_transitions(self.con, ts)
        r2 = ev.emit_transitions(self.con, ts)
        self.assertEqual(r1["n_new"], 1)
        self.assertEqual(r2["n_new"], 0, "第二次不该产生新事件")
        n = self.con.execute("SELECT COUNT(*) c FROM topic_event").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_emit_idempotent_across_new_connection(self):
        """换连接重跑也要幂等（调度器每轮都是新进程）。"""
        ts = state_of([topic()])
        ev.emit_transitions(self.con, ts)
        self.con.close()
        con2 = ev.connect(self.db)
        try:
            r = ev.emit_transitions(con2, ts)
            self.assertEqual(r["n_new"], 0)
        finally:
            con2.close()
        self.con = ev.connect(self.db)     # 供 tearDown 关闭


# --------------------------------------------------------------------------- 迁移识别

class TestCollectTransitions(TmpCase):
    def test_filters_first_sight(self):
        """★ 首日采样 prev_state=null → 不算迁移。"""
        ts = state_of([topic(prev=None, changed="2026-09-30T10:00:00+08:00")])
        self.assertEqual(ev.collect_transitions(ts), [])

    def test_filters_when_changed_equals_first_seen(self):
        ts = state_of([topic(changed="2026-09-30T10:00:00+08:00",
                             first="2026-09-30T10:00:00+08:00")])
        self.assertEqual(ev.collect_transitions(ts), [], "同刻不算迁移")

    def test_accepts_real_transition(self):
        got = ev.collect_transitions(state_of([topic()]))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["from_state"], "冒头")
        self.assertEqual(got[0]["to_state"], "升温")
        self.assertTrue(got[0]["dispatchable"])

    def test_marks_non_dispatchable_states(self):
        for st in ("冒头", "退潮", "沉寂"):
            got = ev.collect_transitions(state_of([topic(state=st)], st))
            self.assertEqual(len(got), 1)
            self.assertFalse(got[0]["dispatchable"], f"{st} 不该主动分发")

    def test_severity_order(self):
        """爆发 > 升温 > 退潮。"""
        self.assertGreater(ev._SEVERITY["爆发"], ev._SEVERITY["升温"])
        self.assertGreater(ev._SEVERITY["升温"], ev._SEVERITY["退潮"])

    def test_attaches_ferment_info(self):
        ferment = {"topics": [{"hashtag_id": "1", "ferment_score": 60,
                               "suggested_action": "建话题"}]}
        got = ev.collect_transitions(state_of([topic()]), ferment)[0]
        self.assertEqual(got["ferment_score"], 60)
        self.assertEqual(got["suggested_action"], "建话题")

    def test_missing_ferment_does_not_crash(self):
        got = ev.collect_transitions(state_of([topic()]), None)[0]
        self.assertIsNone(got["ferment_score"])

    def test_empty_state(self):
        self.assertEqual(ev.collect_transitions({}), [])
        self.assertEqual(ev.collect_transitions({"by_state": None}), [])


# --------------------------------------------------------------------------- 失败隔离

class TestDispatchIsolation(TmpCase):
    def _ev(self):
        return {"event_id": "e1", "topic_key": "k", "title": "T",
                "from_state": "冒头", "to_state": "升温",
                "occurred_at": "2026-09-30T12:00:00+08:00", "severity": 2,
                "payload": {"metrics": {}, "ferment_score": 60}}

    def test_one_subscriber_failure_does_not_block_others(self):
        """★★ 核心：一个订阅者挂了，其他必须照常收到。"""
        called = []
        def good(e):
            called.append("good")
            return {"ok": True, "detail": "ok"}
        def bad(e):
            called.append("bad")
            raise RuntimeError("订阅者炸了")
        res = ev.dispatch_event(self._ev(), [("good", good), ("bad", bad)])
        self.assertIn("good", called)
        self.assertIn("bad", called)
        self.assertTrue(res["good"]["ok"])
        self.assertFalse(res["bad"]["ok"])
        self.assertIn("订阅者炸了", res["bad"]["error"])

    def test_all_subscribers_called_even_if_first_fails(self):
        order = []
        def bad(e):
            order.append("bad"); raise ValueError("x")
        def good(e):
            order.append("good"); return {"ok": True}
        ev.dispatch_event(self._ev(), [("bad", bad), ("good", good)])
        self.assertEqual(order, ["bad", "good"], "第一个失败后仍要调第二个")

    def test_empty_subscribers_ok(self):
        self.assertEqual(ev.dispatch_event(self._ev(), []), {})


class TestDispatchPending(TmpCase):
    def _seed(self, n=2, state="升温", prev="冒头"):
        items = [topic(key=f"k{i}", title=f"T{i}", state=state, prev=prev,
                       changed=f"2026-09-30T12:0{i}:00+08:00", hid=str(i))
                 for i in range(n)]
        ev.emit_transitions(self.con, state_of(items, state))
        return ev.pending_events(self.con)

    def test_dispatches_and_marks(self):
        self._seed(2)
        r = ev.dispatch_pending(self.con, [("good", lambda e: {"ok": True})])
        self.assertEqual(r["n_sent"], 2)
        self.assertEqual(r["n_ok"], 2)
        self.assertEqual(len(ev.pending_events(self.con)), 0)

    def test_marks_even_on_failure(self):
        """★ 失败的也要标记 —— 否则会永远卡在队列里被反复重发。"""
        self._seed(1)
        def bad(e):
            raise RuntimeError("x")
        r = ev.dispatch_pending(self.con, [("bad", bad)])
        self.assertEqual(r["n_failed"], 1)
        self.assertEqual(len(ev.pending_events(self.con)), 0, "失败后也不该留在待发队列")

    def test_non_dispatchable_not_sent(self):
        items = [topic(key="k1", state="退潮", prev="爆发",
                       changed="2026-09-30T12:00:00+08:00")]
        ev.emit_transitions(self.con, state_of(items, "退潮"))
        r = ev.dispatch_pending(self.con, [("good", lambda e: {"ok": True})])
        self.assertEqual(r["n_sent"], 0, "退潮只记录不分发")
        n = self.con.execute("SELECT COUNT(*) c FROM topic_event").fetchone()["c"]
        self.assertEqual(n, 1, "但要记进事件流")

    def test_no_pending_is_ok(self):
        r = ev.dispatch_pending(self.con)
        self.assertTrue(r["ok"])
        self.assertEqual(r["n_pending"], 0)

    def test_dispatch_result_recorded(self):
        self._seed(1)
        ev.dispatch_pending(self.con, [("s1", lambda e: {"ok": True, "detail": "d1"})])
        row = self.con.execute("SELECT dispatch_result FROM topic_event").fetchone()
        self.assertIn("s1", row["dispatch_result"])


# --------------------------------------------------------------------------- 报告

class TestStatus(TmpCase):
    def test_empty_is_honest(self):
        rep = ev.render_status(self.con)
        self.assertIn("还没有事件", rep)
        self.assertIn("首日采样", rep)

    def test_counts_by_to_state(self):
        ev.emit_transitions(self.con, state_of([topic(key="k1"), topic(key="k2")]))
        st = ev.status(self.con)
        self.assertEqual(st["n_total"], 2)
        self.assertEqual(st["by_to_state"].get("升温"), 2)

    def test_pending_count(self):
        ev.emit_transitions(self.con, state_of([topic()]))
        self.assertEqual(ev.status(self.con)["n_pending"], 1)

    def test_render_lists_events(self):
        ev.emit_transitions(self.con, state_of([topic(title="某热点")]))
        rep = ev.render_status(self.con)
        self.assertIn("某热点", rep)
        self.assertIn("冒头→升温", rep)

    def test_render_marks_push_not_wired(self):
        """推送没接入这件事必须写在报告里，别让人以为已经在推了。"""
        rep = ev.render_status(self.con)
        self.assertIn("推送订阅者尚未接入", rep)

    def test_render_states_disciplines(self):
        rep = ev.render_status(self.con)
        self.assertIn("幂等", rep)
        self.assertIn("失败隔离", rep)


# --------------------------------------------------------------------------- 集成

class TestRunIntegration(TmpCase):
    def test_run_end_to_end(self):
        ts_path = Path(self._tmp.name) / "ts.json"
        fj_path = Path(self._tmp.name) / "fj.json"
        ts_path.write_text(json.dumps(state_of([topic()]), ensure_ascii=False),
                           encoding="utf-8")
        fj_path.write_text(json.dumps({"topics": []}), encoding="utf-8")
        sent = []
        r = ev.run(db_path=self.db, topic_state_path=ts_path, ferment_path=fj_path,
                   subscribers=[("spy", lambda e: (sent.append(e), {"ok": True})[1])])
        self.assertEqual(r["emit"]["n_new"], 1)
        self.assertEqual(r["dispatch"]["n_sent"], 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["title"], "某话题")
        self.assertEqual(sent[0]["to_state"], "升温")

    def test_run_twice_is_idempotent(self):
        ts_path = Path(self._tmp.name) / "ts.json"
        ts_path.write_text(json.dumps(state_of([topic()]), ensure_ascii=False),
                           encoding="utf-8")
        sent = []
        kw = dict(db_path=self.db, topic_state_path=ts_path,
                  ferment_path=Path(self._tmp.name) / "none.json",
                  subscribers=[("spy", lambda e: (sent.append(e), {"ok": True})[1])])
        ev.run(**kw)
        ev.run(**kw)
        self.assertEqual(len(sent), 1, "第二次不该重复分发")

    def test_run_with_missing_files_does_not_crash(self):
        r = ev.run(db_path=self.db,
                   topic_state_path=Path(self._tmp.name) / "none.json",
                   ferment_path=Path(self._tmp.name) / "none.json")
        self.assertTrue(r["ok"])
        self.assertEqual(r["emit"]["n_new"], 0)


class TestSchedulerWiring(unittest.TestCase):
    """锁死「触发链仍然会被跑到」—— 否则状态机判出的迁移没人知道。

    2026-10-01 改：探测链收成 1 步（跑巡检图），触发链移进图的 `act` 节点内部。
    所以本组测试从"断言调度器步骤里有 events.py"改为"断言图里确实调了 events/features"。
    """

    def test_probe_chain_delegates_to_graph(self):
        sch = _load("scheduler")
        steps = sch.probe_steps(dry_run=True)
        self.assertEqual(len(steps), 1, "探测链应只剩跑图一步")
        self.assertIn("agent_graph.py", steps[0]["cmd"])

    def test_graph_runs_contract_tool_chain(self):
        """结构证据：图**由契约的工具链驱动**（不再硬编码脚本顺序）。

        2026-10-01 改：图已契约驱动（节点由 `contract["tools"]` 生成，同一张图跑任何任务），
        所以「触发链 / 特征落盘会不会被跑到」这个问题，答案在**契约**里 —— 不能靠 grep 图源码。
        同时顺序要求也移到契约上：采样必须在触发链之前。
        """
        src = (RUNTIME / "agent_graph.py").read_text(encoding="utf-8")
        self.assertIn('contract.get("tools")', src, "图应从契约取工具链（契约驱动）")
        tools = _load("task_contracts").get("hotspot_track")["tools"]
        self.assertLess(tools.index("topic_sample"), tools.index("events_run"),
                        "采样须在触发链之前 —— 否则扫不到刚产生的迁移")
        self.assertIn("features_run", tools, "特征落盘应在契约的工具链里")

    def test_trigger_chain_tool_exists(self):
        """触发链是个真 tool（不是只写在契约里的名字）。"""
        hr = _load("harness")
        self.assertIn("events_run", hr.TOOLS())
        self.assertTrue(hr.TOOLS()["events_run"].implemented)


if __name__ == "__main__":
    unittest.main()
