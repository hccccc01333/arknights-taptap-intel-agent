#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""webapp 集成测试：登录/角色矩阵/读写端点（全临时库，不碰真实数据）。

钉死的纪律：
  ① 未登录 401；伪造/过期令牌 401
  ② 权限矩阵在**服务端**强制：viewer 按不了审批、operator 发不了 launch ——
     前端藏按钮只是体验，403 才是边界（§43）
  ③ 写路径全部走 L6 真模块（Workflow/Center/Experiments）——
     Web 层没有第二条业务实现，CLI 与 UI 永远同一套规则
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))     # webapp/tests → webapp → 仓库根
for _p in (_ROOT, _HERE, os.path.join(_ROOT, "L6_execution"),
           os.path.join(_ROOT, "L5_memory"), os.path.join(_ROOT, "L4_intelligence")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class WebAppFixture(unittest.TestCase):
    """临时库 + 种子数据 + TestClient。env 在 **创建 state 之前** 设置。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._old_env = {k: os.environ.get(k) for k in
                         ("L6_EXECUTION_DB", "L5_MEMORY_DB")}
        os.environ["L6_EXECUTION_DB"] = str(tmp / "l6.sqlite3")
        os.environ["L5_MEMORY_DB"] = str(tmp / "l5.sqlite3")
        self._patch_l4_l3(tmp)
        # App 只在 L5 库文件已存在时才挂 MemoryStore —— 预建空库
        from memory.store import MemoryStore
        _l5 = MemoryStore(os.environ["L5_MEMORY_DB"])
        _l5.close()
        self._seed(tmp)
        import webapp.services as S
        import webapp.auth as A
        self.S = S
        S.reset_state()
        self.S.reset_state()
        from fastapi.testclient import TestClient
        from webapp.main import app
        self.client = TestClient(app)
        self.tokens = {}
        for actor, role in (("运营A", "operator"), ("评审B", "reviewer"),
                            ("管理D", "admin"), ("观察E", "viewer")):
            r = self.client.post("/api/login", json={"actor": actor, "password": "demo"})
            self.assertEqual(r.status_code, 200, r.text)
            self.tokens[role] = r.json()["token"]
        self.headers = lambda role: {"Authorization": f"Bearer {self.tokens[role]}"}

    def tearDown(self):
        self.S.reset_state()
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    # ---- 隔离：L4/L3 指到临时库（它们的默认路径是模块常量，调用时读取 → 可 patch）----
    def _patch_l4_l3(self, tmp: Path):
        import sqlite3
        import intelligence.store as istore
        import intelligence.upstream as iup
        l4_path = str(tmp / "l4.sqlite3")
        self._old_l4_default = istore._DB_DEFAULT
        self._old_l3_db = iup.L3_DB
        istore._DB_DEFAULT = l4_path
        iup.L3_DB = str(tmp / "l3.sqlite3")
        # 最小 L3 库：两个 active 事件（Feed/Workspace 的数据源）
        con = sqlite3.connect(iup.L3_DB)
        con.execute("""CREATE TABLE trend_event(
            event_id TEXT PRIMARY KEY, canonical_title TEXT, title_source TEXT,
            event_type TEXT, primary_topic TEXT, status TEXT, started_at TEXT,
            first_detected_at TEXT, last_updated_at TEXT, peak_at TEXT, ended_at TEXT,
            lifecycle TEXT, hot_score REAL, momentum_score REAL, confidence_score REAL,
            opportunity_score REAL, novelty_score REAL, velocity_score REAL,
            acceleration_score REAL, burst_score REAL, diffusion_score REAL,
            engagement_score REAL, source_diversity_score REAL, credibility_score REAL,
            content_count INTEGER, platform_count INTEGER, primary_platform TEXT,
            entity_ids TEXT, platforms TEXT, representative_content_id TEXT,
            split_flag INTEGER, parent_event_id TEXT, metadata TEXT, engine_version TEXT)""")
        base = dict(title_source="rule", primary_topic="t",
                    started_at="2026-10-03T08:00:00+08:00",
                    first_detected_at="2026-10-03T08:00:00+08:00",
                    last_updated_at="2026-10-03T08:00:00+08:00", peak_at=None,
                    ended_at=None, lifecycle="EMERGING", hot_score=0.31,
                    momentum_score=0.2, confidence_score=0.78, opportunity_score=0.4,
                    novelty_score=0.5, velocity_score=0.1, acceleration_score=0.0,
                    burst_score=0.0, diffusion_score=0.2, engagement_score=0.3,
                    source_diversity_score=0.5, credibility_score=0.5,
                    content_count=42, platform_count=3, primary_platform="taptap",
                    entity_ids='["arknights"]', platforms='["taptap","bilibili"]',
                    representative_content_id="x", split_flag=0, parent_event_id=None,
                    metadata="{}", engine_version="t")
        cols = list(base)
        for eid, title in (("evt_test1", "测试联动事件"), ("evt_test2", "第二个事件")):
            row = dict(base, event_id=eid, canonical_title=title,
                       event_type="collab", status="active")
            cols = list(row)          # ★ 含 event_id——之前漏了它，SQLite TEXT PK 竟允许 NULL
            con.execute(
                f"INSERT INTO trend_event({','.join(cols)}) "
                f"VALUES({','.join('?' * len(cols))})",
                tuple(row[c] for c in cols))
        con.commit()
        con.close()
        # 最小 L4 库：一条通过评审的创意（决策/素材/计划链的靶子）
        l4 = istore.IntelligenceStore(l4_path)
        l4.save_analysis(
            {"event_id": "evt_test1"},
            {"status": "ok", "analysis_version": "t",
             "relevance": {"score": 0.9},
             "opportunities": [{"opportunity_id": "opp_1", "name": "UGC 展示",
                                "opportunity_score": 0.9,
                                "growth_mechanism": "创作→展示→排名→分享",
                                "growth_goal": "ugc"}],
             "creatives": [{"idea_id": "idea_test", "idea_name": "角色身份卡",
                            "creative_type": "ugc", "score": 0.88,
                            "growth_hypothesis": "低门槛展示提升 UGC 投稿",
                            "growth_mechanism": "创作→展示→排名→分享",
                            "primary_metric": "ugc_count",
                            "secondary_metrics": ["share"],
                            "lead_time_hours": 4,
                            "opportunity_id": "opp_1",
                            "evaluation": {"pass": True, "score": 0.88},
                            "event_id": "evt_test1",
                            "analysis_id": "anl_test"}]})
        l4.close()

    def _seed(self, tmp: Path):
        pass                                    # 业务种子在 _patch_l4_l3 内完成


class TestAuth(WebAppFixture):
    def test_login_bad_credentials_401(self):
        r = self.client.post("/api/login", json={"actor": "运营A", "password": "错"})
        self.assertEqual(r.status_code, 401)

    def test_me_with_token(self):
        r = self.client.get("/api/me", headers=self.headers("operator"))
        self.assertEqual(r.json()["role"], "operator")

    def test_tampered_token_401(self):
        r = self.client.get("/api/me", headers={"Authorization": "Bearer x|y|1|z"})
        self.assertEqual(r.status_code, 401)

    def test_reads_require_login(self):
        self.assertEqual(self.client.get("/api/feed").status_code, 401)


class TestReads(WebAppFixture):
    def test_feed_shape(self):
        r = self.client.get("/api/feed", headers=self.headers("viewer"))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("cards", body)
        self.assertEqual(body["cards"][0]["event_id"], "evt_test1")

    def test_funnel_and_value(self):
        h = self.headers("viewer")
        self.assertIn("creatives", self.client.get("/api/funnel", headers=h).json())
        self.assertIn("creative_adoption", self.client.get("/api/value", headers=h).json())

    def test_workspace_not_found_404(self):
        r = self.client.get("/api/events/nope/workspace", headers=self.headers("viewer"))
        self.assertEqual(r.status_code, 404)


class TestWritesAndPermissions(WebAppFixture):
    def test_follow_event_as_operator(self):
        r = self.client.post("/api/feed/evt_test1/follow",
                             json={"action": "follow"}, headers=self.headers("operator"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["to"], "REVIEWING")

    def test_assign_requires_reviewer(self):
        self.client.post("/api/feed/evt_test1/follow",
                         json={"action": "follow"}, headers=self.headers("operator"))
        r = self.client.post("/api/workflow/event/evt_test1/assign",
                             json={"owner": "某人"}, headers=self.headers("operator"))
        self.assertEqual(r.status_code, 403, "operator 无权 assign（§43）")
        r2 = self.client.post("/api/workflow/event/evt_test1/assign",
                              json={"owner": "某人"}, headers=self.headers("reviewer"))
        self.assertEqual(r2.status_code, 200)

    def test_viewer_cannot_decide(self):
        self.client.post("/api/feed/evt_test1/follow",
                         json={"action": "follow"}, headers=self.headers("operator"))
        r = self.client.post("/api/workflow/event/evt_test1/decide",
                             json={"action": "approve"}, headers=self.headers("viewer"))
        self.assertEqual(r.status_code, 403)

    def test_creative_decision_updates_l5_memory(self):
        """★ 全链证据：UI 上点「通过」→ L6 状态机 → L5 Decision Memory（闭环①）。"""
        # 先人工 approve（L6 审批通过后素材生产才解锁）
        from execution.pipeline import _ensure_creative_item
        st = self.S.get_state()

        def seed_item():
            creative = self.S._creative(app=st.app, idea_id="idea_test")
            return _ensure_creative_item(st.app, creative)

        st.run(seed_item)
        r = self.client.post("/api/workflow/creative/idea_test/decide",
                             json={"action": "submit"}, headers=self.headers("operator"))
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/workflow/creative/idea_test/decide",
                             json={"action": "approve", "note": "机制清晰"},
                             headers=self.headers("reviewer"))
        self.assertEqual(r.status_code, 200)
        decisions = st.run(lambda: st.app.memory.candidates("decision"))
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["decision"], "approve")

    def test_plan_requires_approved_creative(self):
        r = self.client.post("/api/creatives/idea_test/plan",
                             json={"channels": ["community_feed"]},
                             headers=self.headers("operator"))
        self.assertEqual(r.status_code, 403, "未 APPROVED 不得建计划（§21）")

    def test_full_plan_launch_flow(self):
        """运营建计划 → operator 不能上线 → publisher 上线（Level 2 闸门）。"""
        st = self.S.get_state()

        def approve_first():
            from execution.pipeline import _ensure_creative_item
            creative = self.S._creative(app=st.app, idea_id="idea_test")
            item = _ensure_creative_item(st.app, creative)
            st.app.wf.apply("creative", item["object_id"], "submit", "op", "operator")
            return st.app.wf.apply("creative", item["object_id"], "approve",
                                   "rev", "reviewer")

        st.run(approve_first)
        r = self.client.post("/api/creatives/idea_test/plan",
                             json={"channels": ["community_feed"], "experiment": True},
                             headers=self.headers("operator"))
        self.assertEqual(r.status_code, 200, r.text)
        plan_id = r.json()["plan_id"]
        self.assertIn("experiment", r.json())
        r_no = self.client.post(f"/api/plans/{plan_id}/transition",
                                json={"action": "launch"},
                                headers=self.headers("operator"))
        self.assertEqual(r_no.status_code, 403, "operator 不能 launch（§21/§43）")
        r_rev = self.client.post(f"/api/plans/{plan_id}/transition",
                                 json={"action": "launch"},
                                 headers=self.headers("reviewer"))
        self.assertEqual(r_rev.status_code, 403, "reviewer 也不能 launch（§43）")
        r_ok = self.client.post(f"/api/plans/{plan_id}/transition",
                                json={"action": "launch"},
                                headers=self.headers("admin"))
        self.assertEqual(r_ok.status_code, 200)

    def test_experiment_finish_guardrail_blocks_win(self):
        """§28 走 HTTP 依旧成立：护栏击穿 → INCONCLUSIVE。"""
        st = self.S.get_state()

        def seed_exp():
            exp = st.app.experiments.create(
                creative_id="c", plan_id=None, event_id="evt_test1", name="n",
                hypothesis="h", population="p", treatment="t", control="c",
                primary_metric="ugc_rate", guardrail_metrics=["report_rate"],
                actor="op", role="operator")
            st.app.experiments.start(exp["experiment_id"])
            st.app.experiments.observe(exp["experiment_id"], "ugc_rate", "primary",
                                       x_t=68, n_t=1000, x_c=42, n_c=1000)
            st.app.experiments.observe(exp["experiment_id"], "report_rate", "guardrail",
                                       x_t=25, n_t=1000, x_c=10, n_c=1000)
            return exp["experiment_id"]

        exp_id = st.run(seed_exp)
        r = self.client.post(f"/api/experiments/{exp_id}/finish",
                             headers=self.headers("reviewer"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["result_state"], "INCONCLUSIVE")

    def test_invalid_metric_class_400(self):
        st = self.S.get_state()

        def seed_exp():
            exp = st.app.experiments.create(
                creative_id="c", plan_id=None, event_id="e", name="n", hypothesis="h",
                population="p", treatment="t", control="c", primary_metric="m",
                actor="op", role="operator")
            st.app.experiments.start(exp["experiment_id"])
            return exp["experiment_id"]

        exp_id = st.run(seed_exp)
        r = self.client.post(f"/api/experiments/{exp_id}/observe",
                             json={"metric_name": "m", "metric_class": "wrong"},
                             headers=self.headers("operator"))
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
