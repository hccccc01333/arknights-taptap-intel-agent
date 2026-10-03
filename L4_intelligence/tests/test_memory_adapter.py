#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L4 → L5 记忆适配测试（L4 README「待建」项 §37/§38 的回归）。

钉死的纪律：
  ① L5 记忆库不存在 → available()=False、检索返回空（= 没历史，不是没检索到）
  ② 有库时 search_experiments / search_similar_cases / get_game_profile 走真记忆
  ③ Research 节点的检索计划里，记忆工具先于外部检索（§43）
  ④ 工具契约：全部只读 + text_access 合法（build_tools 自带校验，注册即检）
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_L4 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L4)
for _p in (_ROOT, _L4, os.path.join(_L4, "intelligence"), os.path.join(_ROOT, "L5_memory")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from intelligence import retrieval as MEM            # noqa: E402
from intelligence import tools as T                  # noqa: E402
from intelligence.nodes import research as R         # noqa: E402


class _DummyUpstream:
    def events(self, limit=0, status="active"):
        return []

    def members(self, event_id, **kw):
        return []


class AdapterBase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(self.path)
        self._old_env = os.environ.get("L5_MEMORY_DB")
        os.environ["L5_MEMORY_DB"] = self.path
        self.store = None

    def _seed(self):
        from memory.store import MemoryStore
        self.store = MemoryStore(self.path)
        return self.store

    def tearDown(self):
        if self.store is not None:
            self.store.close()
        if self._old_env is not None:
            os.environ["L5_MEMORY_DB"] = self._old_env
        else:
            os.environ.pop("L5_MEMORY_DB", None)
        if os.path.exists(self.path):
            os.remove(self.path)


class TestUnavailable(AdapterBase):
    def test_missing_db_is_honest_empty(self):
        """库不存在：available=False，检索返回 [] —— 绝不编造历史效果。"""
        self.assertFalse(MEM.available())
        self.assertEqual(MEM.similar_experiments("排行"), [])
        self.assertEqual(MEM.similar_cases("捏脸"), [])
        self.assertIsNone(MEM.entity_profile("arknights"))
        self.assertIsNone(MEM.context_for("opportunity_agent", "x"))

    def test_tools_return_empty_without_memory(self):
        tools = T.build_tools(_DummyUpstream())
        self.assertEqual(tools["search_experiments"].run(growth_goal="ugc"), [])
        prof = tools["get_game_profile"].run(entity="arknights")
        self.assertNotIn("l5.memory", prof.get("source", ""))   # 走了降级分支


class TestWithMemory(AdapterBase):
    def setUp(self):
        super().setUp()
        store = self._seed()
        store.save_experiment(
            {"experiment_name": "角色捏脸 UGC 排行榜", "creative_id": "c1",
             "event_id": "evt_1", "treatment": "排行榜+分享卡",
             "control_definition": "普通话题页", "status": "completed",
             "primary_metric": "ugc_count", "context": {"game": "arknights"}},
            metrics=[{"metric_name": "ugc_count", "relative_lift": 0.62,
                      "p_value": 0.003, "sample_size": 50000}])
        store.upsert_knowledge(
            "entity", "arknights", "明日方舟",
            {"key": "arknights", "name": "明日方舟", "aliases": ["明日方舟", "方舟"]},
            tier="verified", human_verified=True, verified_by="games.registry")
        store.close()

    def test_available_and_search(self):
        self.assertTrue(MEM.available())
        exps = MEM.similar_experiments("角色捏脸 UGC")
        self.assertEqual(len(exps), 1)
        self.assertEqual(exps[0]["result"]["best_relative_lift"], 0.62)
        self.assertGreater(exps[0]["reliability"], 0.5)

    def test_entity_profile_hit(self):
        prof = MEM.entity_profile("arknights")
        self.assertEqual(prof["subject"], "arknights")
        self.assertIn("方舟", prof["payload"]["aliases"])

    def test_tools_hit_memory(self):
        tools = T.build_tools(_DummyUpstream())
        T.assert_all_read_only(tools)                       # §17 全只读不回退
        exps = tools["search_experiments"].run(growth_goal="ugc")
        self.assertEqual(len(exps), 1)
        self.assertEqual(exps[0]["lift"], 0.62)
        self.assertIn("case_id", exps[0])
        prof = tools["get_game_profile"].run(entity="arknights")
        self.assertTrue(prof.get("source", "").startswith("l5.memory:"))
        self.assertEqual(prof["aliases"], ["明日方舟", "方舟"])
        cases = tools["search_similar_cases"].run(query="捏脸")
        self.assertEqual(cases, [])                        # Case 还没蒸馏 → 空但工具通

    def test_research_plan_prefers_memory_over_web(self):
        """§43：记忆工具进 Research 计划，且排在被 block 的外部检索之前。"""
        store = self._seed()
        state = {"event_id": "evt_x", "event_title": "角色捏脸",
                 "evidence_pack": {"n_evidence": 1, "event": {"confidence": 0.3},
                                   "primary_ratio": 0, "unknowns": [], "entities": []}}
        res = R.research(state, _DummyUpstream(), T.build_tools(_DummyUpstream()))
        names = [s["tool"] for s in res["steps"]]
        self.assertIn("search_similar_cases", names)
        self.assertIn("search_web", [b["tool"] for b in res["blocked_tools"]])
        store.close()


if __name__ == "__main__":
    unittest.main()
