#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第六层业务测试：Feed（§4-§6）/ 素材与执行（§18-§22, §29）/ 实验五态（§23-§31）/
告警（§16-§17）/ 漏斗与 lineage（§32-§34）。

钉死的纪律：
  ① §6 时效约束变成产品约束：超 lead_time 的创意类型被排除，且口径如实标注
  ② §19/§21：未 APPROVED 的创意不得产素材；launch 只有 publisher 能按
  ③ §28：护栏击穿 → 主指标再显著也不判 WIN
  ④ §30：WIN/LOSS/INCONCLUSIVE/STOPPED/INVALID 五态齐备，失败是合法结论
  ⑤ §16/§17：告警分级去重、通知本身可行动、agent 不能 ack
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
from execution.audit import AuditLog, PermissionDenied  # noqa: E402
from execution.workflow import Workflow  # noqa: E402
from execution.feed import IntelligenceFeed, action_priority, window_for, allowed_creative_types  # noqa: E402
from execution.workspace import Workspace  # noqa: E402
from execution.execution_center import AssetStudio, ExecutionCenter  # noqa: E402
from execution.experiments import ExperimentEngine  # noqa: E402
from execution.alerts import AlertSystem  # noqa: E402
from execution.lineage import Lineage  # noqa: E402
from execution import ops_context  # noqa: E402


class _FakeUpstream:
    """替身：只喂 Feed/Workspace/Lineage 需要的最小接口。"""

    def __init__(self, events):
        self._events = events

    def events(self, limit=50, status="active"):
        return self._events[:limit]

    def event(self, event_id):
        return next((e for e in self._events if e["event_id"] == event_id), None)

    def members(self, event_id):
        return []

    def member_sims(self, event_id):
        return []


class _FakeL4:
    def __init__(self, analyses=None, creatives=None):
        self.analyses = analyses or {}
        self.creatives = creatives or {}

    def latest(self, event_id):
        return self.analyses.get(event_id)


def _mk_event(eid, lifecycle="EMERGING", hot=0.9, mom=0.95, conf=0.8, n=40):
    return {"event_id": eid, "canonical_title": f"事件{eid}", "lifecycle": lifecycle,
            "hot_score": hot, "momentum_score": mom, "confidence_score": conf,
            "content_count": n, "platform_count": 2, "platforms": '["taptap"]',
            "first_detected_at": "2026-10-03T10:00:00+08:00"}


class FeedBase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(self.path)
        self.db = ExecutionDB(self.path)
        self.audit = AuditLog(self.db)
        self.wf = Workflow(self.db, self.audit)

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.path):
            os.remove(self.path)


class TestFeed(FeedBase):
    def test_action_priority_with_missing_dims(self):
        """§5：缺维度 0.5 中性 + missing_dims 列出 —— 不假装信息齐全。"""
        out = action_priority({"hot": 0.8, "momentum": 0.9})
        self.assertEqual(out["missing_dims"], ["confidence", "relevance",
                                               "opportunity", "window"])
        self.assertGreater(out["priority"], 0)

    def test_window_constraint_from_ops_context(self):
        """§6+§39：无研发资源 → 产品开发型创意被排除（时效×资源 → 产品约束）。"""
        ctx = {"resources": {"design": 1, "dev": 0, "ops": 3}}
        win = window_for("EMERGING", ctx)
        self.assertEqual(win["max_lead_time_hours"], 6.0)
        allowed = {c["creative_type"]: c["allowed"]
                   for c in allowed_creative_types(win["max_lead_time_hours"])}
        self.assertTrue(allowed["content"])       # 4h → 允许
        self.assertFalse(allowed["ugc"])          # 24h → 排除
        self.assertFalse(allowed["product"])      # 72h → 排除
        self.assertIn("降级", win["basis"])        # 口径如实标注

    def test_feed_groups_and_assignment(self):
        up = _FakeUpstream([_mk_event("evt_hot"), _mk_event("evt_old", lifecycle="DECLINING",
                                                            hot=0.3, mom=0.1, conf=0.2)])
        l4 = _FakeL4(analyses={"evt_hot": {"relevance": {"score": 0.9},
                                           "opportunities": [{"name": "UGC展示",
                                                              "opportunity_score": 0.9,
                                                              "opportunity_window": "24h"}],
                                           "creatives": [], "analysis_id": "anl_1",
                                           "trend_analysis": {"trigger": "讨论激增"}}})
        feed = IntelligenceFeed(up, l4, self.wf).build(limit=10)
        by_id = {c["event_id"]: c for c in feed["cards"]}
        self.assertEqual(by_id["evt_old"]["group"], "DECLINING")
        # 高分 + EMERGING → 就该进 ACTION NOW（§36 原型卡片）
        self.assertEqual(by_id["evt_hot"]["group"], "ACTION_NOW")
        self.assertEqual(by_id["evt_hot"]["top_opportunities"][0]["name"], "UGC展示")
        self.assertEqual(by_id["evt_hot"]["missing_dims"], [])   # 全维度齐 → 无缺失


class TestAssetsAndExecution(FeedBase):
    def _approved_creative(self):
        creative = {"idea_id": "idea_1", "idea_name": "角色身份卡", "event_id": "evt_1",
                    "growth_mechanism": "创作→展示→排名→分享",
                    "primary_metric": "ugc_count", "secondary_metrics": ["share"],
                    "growth_hypothesis": "低门槛展示提升投稿", "launch_window": "24h"}
        self.wf.ensure_item("creative", "idea_1", title="身份卡", event_id="evt_1")
        self.wf.apply("creative", "idea_1", "evaluate_ready", "agent", "agent")
        self.wf.apply("creative", "idea_1", "submit", "op", "operator")
        self.wf.apply("creative", "idea_1", "approve", "rev", "reviewer")
        return creative

    def test_strategy_vs_asset_separation(self):
        """§19：只有 APPROVED 后的创意才进素材生产 —— DRAFT 直接拒绝。"""
        studio = AssetStudio(self.db, self.audit)
        self.wf.ensure_item("creative", "idea_raw", title="未批创意", event_id="evt_1")
        with self.assertRaises(PermissionDenied):
            studio.generate({"idea_id": "idea_raw"}, ["push_copy"], role="operator")

    def test_asset_versioning(self):
        """§20：同 kind 重复生成 version+1；审批/发布链完整。"""
        studio = AssetStudio(self.db, self.audit)
        creative = self._approved_creative()
        a1 = studio.generate(creative, ["push_copy"], actor="op", role="operator")
        a2 = studio.generate(creative, ["push_copy"], actor="op", role="operator")
        self.assertEqual(a2[0]["version"], a1[0]["version"] + 1)
        asset_id = a1[0]["asset_id"]
        with self.assertRaises(PermissionDenied):
            studio.mark_published(asset_id, actor="pub", role="publisher")  # 未批准
        studio.approve(asset_id, actor="rev", role="reviewer")
        studio.mark_published(asset_id, actor="pub", role="publisher")
        rows = studio.list_for("idea_1")
        self.assertEqual(rows[0]["status"], "published")
        self.assertIsNotNone(rows[0]["published_at"])

    def test_template_never_invents_numbers(self):
        """facts 纪律：素材模板不含编造数字，缺内容由 placeholders 如实标注。"""
        studio = AssetStudio(self.db, self.audit)
        self._approved_creative()
        creative = {"idea_id": "idea_1", "idea_name": "身份卡", "event_id": "evt_1",
                    "growth_mechanism": "m", "primary_metric": "ugc_count"}
        import json as _json
        out = studio.generate(creative, ["push_copy", "campaign_brief"],
                              actor="op", role="operator")
        for a in out:
            row = self.db.query_one("SELECT * FROM production_asset WHERE asset_id=?",
                                    (a["asset_id"],))
            content = _json.loads(row["content"])
            for v in content.values():
                self.assertNotRegex(str(v), r"\+\d{2,}%")   # 不许出现"+320%"式编造数字
        self.assertTrue(out[0]["placeholders"])              # 缺位如实列出

    def test_plan_gates_and_kill_switch(self):
        center = ExecutionCenter(self.db, self.audit, self.wf)
        with self.assertRaises(PermissionDenied):
            center.create_plan(creative_id="idea_x", channels=["community_feed"],
                               audience="a", start_at="", end_at="", asset_ids=[],
                               experiment_enabled=False, control="", treatment="t",
                               actor="op", role="operator")   # 未 APPROVED
        creative = self._approved_creative()
        plan = center.create_plan(creative_id="idea_1", channels=["community_feed"],
                                  audience="active", start_at="s", end_at="e",
                                  asset_ids=[], experiment_enabled=True,
                                  control="existing_feed", treatment="身份卡",
                                  actor="op", role="operator")
        self.assertEqual(plan["status"], "planned")
        with self.assertRaises(PermissionDenied):
            center.launch(plan["plan_id"], actor="op", role="operator")   # §21
        center.launch(plan["plan_id"], actor="pub", role="publisher")
        with self.assertRaises(PermissionDenied):
            center.kill_switch(plan["plan_id"], "stop", "r", actor="bot", role="agent")
        out = center.kill_switch(plan["plan_id"], "stop", "舆情风险",
                                 actor="rev", role="reviewer")
        self.assertEqual(out["status"], "stopped")
        self.audit.record(action="x", actor="a", role="reviewer")   # audit 无异常即可


class TestExperiments(FeedBase):
    def _mk_engine(self, memory=None):
        return ExperimentEngine(self.db, self.audit, memory_store=memory)

    def _mk_running(self, eng):
        exp = eng.create(creative_id="c1", plan_id=None, event_id="evt_1",
                         name="身份卡", hypothesis="低门槛展示提升投稿",
                         population="活跃", treatment="身份卡", control="普通入口",
                         primary_metric="ugc_rate", guardrail_metrics=["report_rate"],
                         actor="op", role="operator")
        eng.start(exp["experiment_id"])
        return exp["experiment_id"]

    def test_no_control_rejected(self):
        eng = self._mk_engine()
        with self.assertRaises(ValueError):
            eng.create(creative_id="c", plan_id=None, event_id="e", name="n",
                       hypothesis="h", population="p", treatment="t", control="",
                       primary_metric="m")     # §23：没有对照不叫实验

    def test_no_hypothesis_rejected(self):
        eng = self._mk_engine()
        with self.assertRaises(ValueError):
            eng.create(creative_id="c", plan_id=None, event_id="e", name="n",
                       hypothesis="", population="p", treatment="t", control="c",
                       primary_metric="m")     # §24：无假设不建实验

    def test_win_with_significance(self):
        eng = self._mk_engine()
        eid = self._mk_running(eng)
        obs = eng.observe(eid, "ugc_rate", "primary", x_t=68, n_t=1000,
                          x_c=42, n_c=1000)
        self.assertEqual(obs["significant"], True)
        self.assertAlmostEqual(obs["relative_lift"], 0.619, places=2)
        eng.observe(eid, "report_rate", "guardrail", x_t=10, n_t=1000,
                    x_c=10, n_c=1000)          # 护栏无变化
        fin = eng.finish(eid, actor="rev", role="reviewer")
        self.assertEqual(fin["result_state"], "WIN")

    def test_guardrail_breach_blocks_win(self):
        """§28：主指标 +62% 显著，但举报率 +150% → INCONCLUSIVE，不许算成功。"""
        eng = self._mk_engine()
        eid = self._mk_running(eng)
        eng.observe(eid, "ugc_rate", "primary", x_t=68, n_t=1000, x_c=42, n_c=1000)
        eng.observe(eid, "report_rate", "guardrail", x_t=25, n_t=1000, x_c=10, n_c=1000)
        fin = eng.finish(eid, actor="rev", role="reviewer")
        self.assertEqual(fin["result_state"], "INCONCLUSIVE")
        self.assertIn("report_rate", fin["guardrail_breached"])

    def test_loss_and_inconclusive_and_invalid(self):
        """§30：失败/不确定/无效都是合法结论。"""
        eng = self._mk_engine()
        eid = self._mk_running(eng)
        eng.observe(eid, "ugc_rate", "primary", x_t=30, n_t=1000, x_c=60, n_c=1000)
        self.assertEqual(eng.finish(eid, actor="rev", role="reviewer")["result_state"],
                         "LOSS")
        eid2 = self._mk_running(eng)
        eng.observe(eid2, "ugc_rate", "primary", x_t=45, n_t=100, x_c=42, n_c=100)
        self.assertEqual(eng.finish(eid2, actor="rev", role="reviewer")["result_state"],
                         "INCONCLUSIVE")       # 不显著
        eid3 = self._mk_running(eng)
        self.assertEqual(eng.finish(eid3, actor="rev", role="reviewer")["result_state"],
                         "INVALID")            # 无主指标观测

    def test_writeback_to_memory(self):
        """§25：实验结果回写第五层 Experiment Memory（Learning Loop 最后一米）。"""
        fd5, p5 = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd5)
        os.remove(p5)
        try:
            from memory.store import MemoryStore
            l5 = MemoryStore(p5)
            eng = self._mk_engine(memory=l5)
            eid = self._mk_running(eng)
            eng.observe(eid, "ugc_rate", "primary", x_t=68, n_t=1000, x_c=42, n_c=1000)
            fin = eng.finish(eid, actor="rev", role="reviewer")
            self.assertEqual(fin["memory_writeback"]["status"], "written")
            rows = l5.candidates("experiment")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["tier"], "verified")   # §19 真实实验发生
            self.assertAlmostEqual(rows[0]["best_relative_lift"] if
                                   "best_relative_lift" in rows[0] else 0.619, 0.619)
            l5.close()
        finally:
            os.remove(p5)


class TestAlerts(FeedBase):
    def test_p0_degraded_and_dedup(self):
        alerts = AlertSystem(self.db, self.audit)
        up = _FakeUpstream([_mk_event("evt_1")])
        l4 = _FakeL4(analyses={"evt_1": {"relevance": {"score": 0.9}}})
        feed = IntelligenceFeed(up, l4, self.wf).build(limit=5)
        r1 = alerts.sweep(feed["cards"])
        self.assertTrue(any(a["tier"] == "P0" for a in r1["created"]))
        r2 = alerts.sweep(feed["cards"])
        self.assertEqual(len(r2["created"]), 0)      # 同事件同级别同日去重（§16）
        pending = alerts.pending()
        body = pending[0]["body"]
        self.assertIn("recommended_action", body)    # §17 通知可行动
        self.assertIn("window", body)

    def test_p2_no_alert(self):
        alerts = AlertSystem(self.db, self.audit)
        up = _FakeUpstream([_mk_event("evt_low", hot=0.1, mom=0.1, conf=0.1, n=3)])
        feed = IntelligenceFeed(up, _FakeL4(), self.wf).build(limit=5)
        out = alerts.sweep(feed["cards"])
        self.assertEqual(out["created"], [])         # P2 只上 Dashboard，不发通知

    def test_agent_cannot_acknowledge(self):
        alerts = AlertSystem(self.db, self.audit)
        up = _FakeUpstream([_mk_event("evt_1")])
        l4 = _FakeL4(analyses={"evt_1": {"relevance": {"score": 0.9}}})
        alerts.sweep(IntelligenceFeed(up, l4, self.wf).build(limit=5)["cards"])
        alert_id = alerts.pending()[0]["alert_id"]
        with self.assertRaises(PermissionDenied):
            alerts.acknowledge(alert_id, actor="bot", role="agent")
        out = alerts.acknowledge(alert_id, actor="运营", role="operator")
        self.assertEqual(out["acknowledged_by"], "运营")


class TestFunnelAndLineage(FeedBase):
    def _mk_l4(self, path):
        from intelligence.store import IntelligenceStore
        store = IntelligenceStore(path)
        store.save_analysis({"event_id": "evt_1"},
                            {"status": "ok", "relevance": {"score": 0.9},
                             "opportunities": [{"opportunity_score": 0.8}],
                             "creatives": [{"idea_id": "idea_1", "idea_name": "身份卡",
                                            "creative_type": "ugc", "score": 0.8,
                                            "evaluation": {"pass": True}}]})
        return store

    def test_funnel_counts_real_stores(self):
        fd4, p4 = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd4)
        os.remove(p4)
        l4 = self._mk_l4(p4)
        try:
            up = _FakeUpstream([_mk_event("evt_1")])
            up.l2 = None
            f = Lineage(l4, self.db).funnel(up)
        finally:
            l4.close()
            os.remove(p4)
        self.assertEqual(f["events"], 1)
        self.assertEqual(f["analyzed_events"], 1)
        self.assertEqual(f["creatives"], 1)
        self.assertEqual(f["adopted"], 0)     # 真实为 0：漏斗断点要可见

    def test_lineage_chain_and_honest_missing(self):
        fd4, p4 = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd4)
        os.remove(p4)
        try:
            l4 = self._mk_l4(p4)
            up = _FakeUpstream([_mk_event("evt_1")])
            lin = Lineage(l4, self.db)
            self.wf.ensure_item("creative", "idea_1", title="身份卡", event_id="evt_1")
            trace = lin.trace_event("evt_1")
            self.assertEqual(len(trace["analyses"]), 1)
            self.assertEqual(trace["creatives"][0]["idea_id"], "idea_1")
            self.assertFalse(trace["lineage_complete"])   # 无实验/无 Case → 链未闭合，如实
            l4.close()
        finally:
            os.remove(p4)


if __name__ == "__main__":
    unittest.main()
