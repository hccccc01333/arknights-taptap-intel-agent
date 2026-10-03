#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第六层核心测试：RBAC（§43）/ 审计（§44）/ 状态机（§12）/ TTA（§13）/ 回写（§38）。

钉死的纪律：
  ① agent 服务账号永远拿不到 approve / publish / kill-switch（§43 硬规则）
  ② 非法状态转移被拒绝 —— 宁可拒绝，不留糊涂账（§12）
  ③ 所有决策动作落 workflow_event + audit_log（§12/§44 可回放）
  ④ 决策自动回写 L5 Decision Memory + L4 human_feedback（§38 监督信号不能只存在 UI 里）
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_L6 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L6)
for _p in (_ROOT, _L6, os.path.join(_L6, "execution"),
           os.path.join(_ROOT, "L5_memory"), os.path.join(_ROOT, "L4_intelligence")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from execution.db import ExecutionDB  # noqa: E402
from execution.audit import AuditLog, PermissionDenied, role_can  # noqa: E402
from execution.workflow import Workflow  # noqa: E402
from execution import ops_context  # noqa: E402


class L6TestBase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(self.path)
        self.db = ExecutionDB(self.path)
        self.audit = AuditLog(self.db)

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.path):
            os.remove(self.path)

    def mk_workflow(self, l5=None, l4=None):
        return Workflow(self.db, self.audit, l5_store=l5, l4_store=l4)


class TestRBAC(L6TestBase):
    def test_agent_never_decides_or_publishes(self):
        """§43 硬规则：agent 拿不到审批/发布/急停。"""
        self.assertFalse(role_can("agent", "decide"))
        self.assertFalse(role_can("agent", "publish"))
        self.assertFalse(role_can("agent", "launch"))
        self.assertFalse(role_can("agent", "kill_switch"))
        self.assertFalse(role_can("agent", "acknowledge_alert"))
        self.assertTrue(role_can("agent", "create_creative"))   # 出草稿是它的本职

    def test_publisher_separate_from_operator(self):
        self.assertFalse(role_can("operator", "launch"))
        self.assertTrue(role_can("publisher", "launch"))
        self.assertTrue(role_can("reviewer", "decide"))

    def test_unregistered_role_denied_everything(self):
        """未登记角色一律拒绝（§43 默认拒绝）。"""
        self.assertFalse(role_can("intern", "decide"))


    def test_audit_records_with_versions(self):
        self.audit.record(action="workflow_approve", actor="A", role="reviewer",
                          object_type="creative", object_id="i1",
                          detail={"from": "REVIEWING"}, prompt_version="p7",
                          analysis_version="v2")
        rows = self.audit.for_object("creative", "i1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt_version"], "p7")     # §44 版本随审计落库


class TestWorkflow(L6TestBase):
    def _mk_item(self, wf, object_id="idea_1"):
        return wf.ensure_item("creative", object_id, title="T", event_id="evt_1",
                              detected_at="2026-10-03T10:00:00+08:00")

    def test_full_happy_path_states(self):
        wf = self.mk_workflow()
        self._mk_item(wf)
        self.assertEqual(wf.apply("creative", "idea_1", "evaluate_ready",
                                  "agent", "agent")["to"], "AI_READY")
        self.assertEqual(wf.apply("creative", "idea_1", "submit",
                                  "op", "operator")["to"], "REVIEWING")
        self.assertEqual(wf.apply("creative", "idea_1", "approve",
                                  "rev", "reviewer")["to"], "APPROVED")

    def test_illegal_transition_rejected(self):
        wf = self.mk_workflow()
        self._mk_item(wf)
        with self.assertRaises(ValueError):
            wf.apply("creative", "idea_1", "launch", "pub", "publisher")  # DRAFT 不能 launch

    def test_agent_cannot_approve_even_in_right_state(self):
        wf = self.mk_workflow()
        self._mk_item(wf)
        wf.apply("creative", "idea_1", "evaluate_ready", "agent", "agent")
        wf.apply("creative", "idea_1", "submit", "op", "operator")
        with self.assertRaises(PermissionDenied):             # 权限先于转移
            wf.apply("creative", "idea_1", "approve", "bot", "agent")

    def test_reject_family_records_reason(self):
        wf = self.mk_workflow()
        self._mk_item(wf)
        wf.apply("creative", "idea_1", "evaluate_ready", "agent", "agent")
        wf.apply("creative", "idea_1", "submit", "op", "operator")
        out = wf.apply("creative", "idea_1", "too_late", "rev", "reviewer",
                       note="窗口已经过了")
        self.assertEqual(out["to"], "REJECTED")
        hist = wf.history("creative", "idea_1")
        self.assertEqual([e["action"] for e in hist][-1], "too_late")

    def test_assign_ownership(self):
        wf = self.mk_workflow()
        self._mk_item(wf)
        wf.assign("creative", "idea_1", owner="社区运营", reviewer="增长负责人",
                  deadline="2026-10-04T18:00", actor="负责人", role="reviewer")
        item = wf.items_by_state()[0]
        self.assertEqual((item["owner"], item["reviewer"]), ("社区运营", "增长负责人"))
        with self.assertRaises(PermissionDenied):
            wf.assign("creative", "idea_1", owner="x", actor="op", role="operator")

    def test_time_to_action_medians(self):
        wf = self.mk_workflow()
        self._mk_item(wf)   # detected 10:00
        wf.apply("creative", "idea_1", "evaluate_ready", "agent", "agent")
        wf.apply("creative", "idea_1", "submit", "op", "operator")
        wf.apply("creative", "idea_1", "approve", "rev", "reviewer")
        wf.apply("creative", "idea_1", "start_execution", "op", "operator")
        wf.apply("creative", "idea_1", "launch", "pub", "publisher")
        tta = wf.time_to_action()
        self.assertIsNotNone(tta["time_to_insight_h"])
        self.assertIsNotNone(tta["time_to_decision_h"])
        self.assertIsNotNone(tta["time_to_action_h"])
        self.assertGreaterEqual(tta["time_to_action_h"], tta["time_to_decision_h"])

    def test_tta_honest_when_no_launch(self):
        wf = self.mk_workflow()
        self._mk_item(wf)
        tta = wf.time_to_action()
        self.assertIsNone(tta["time_to_action_h"])   # 没上线就没有 TTA，不冒充
        self.assertEqual(tta["n_action"], 0)

    def test_decision_writeback_to_l5_and_l4(self):
        """§38：决策回写 L5 Decision Memory + L4 feedback（可用的 store 才写）。"""
        fd5, p5 = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd5)
        os.remove(p5)
        fd4, p4 = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd4)
        os.remove(p4)
        try:
            from memory.store import MemoryStore
            from intelligence.store import IntelligenceStore
            l5 = MemoryStore(p5)
            l4 = IntelligenceStore(p4)
            l4.save_analysis({"event_id": "evt_1"}, {"status": "x", "creatives": []})
            wf = self.mk_workflow(l5=l5, l4=l4)
            self._mk_item(wf, "cre_x")
            wf.apply("creative", "cre_x", "evaluate_ready", "agent", "agent")
            wf.apply("creative", "cre_x", "submit", "op", "operator")
            wf.apply("creative", "cre_x", "approve", "rev", "reviewer", note="可落地")
            decisions = l5.candidates("decision")
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["decision"], "approve")
            self.assertEqual(l4.feedback_stats().get("adopt"), 1)
            l5.close()
            l4.close()
        finally:
            os.remove(p5)
            os.remove(p4)


class TestOpsContext(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        self._old = os.environ.get("L6_OPS_CONTEXT")
        os.environ["L6_OPS_CONTEXT"] = self.path

    def tearDown(self):
        if self._old is not None:
            os.environ["L6_OPS_CONTEXT"] = self._old
        else:
            os.environ.pop("L6_OPS_CONTEXT", None)
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_defaults_when_missing(self):
        ctx = ops_context.load()
        self.assertIn("degraded", ctx)          # 未配置如实标注

    def test_save_load_and_resource_cap(self):
        ops_context.save(resources={"design": 1, "dev": 0, "ops": 3},
                         slots_24h={"push": 2})
        ctx = ops_context.load()
        self.assertEqual(ctx["resources"]["dev"], 0)
        self.assertNotIn("degraded", ctx)
        self.assertEqual(ops_context.max_lead_time_hours(ctx), 6.0)   # 无研发 → ≤6h
        ops_context.save(resources={"design": 0, "dev": 2, "ops": 3}, slots_24h={})
        self.assertEqual(ops_context.max_lead_time_hours(), 24.0)

    def test_expired_config_degrades(self):
        """§39：过期约束回退 defaults —— 不拿上周资源位配置误导本周决策。"""
        ops_context.save(resources={"design": 9, "dev": 9, "ops": 9}, slots_24h={},
                         ttl_hours=-1.0)
        ctx = ops_context.load()
        self.assertIn("过期", ctx.get("degraded", ""))
        self.assertEqual(ctx["resources"]["dev"], 0)


if __name__ == "__main__":
    unittest.main()
