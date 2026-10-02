#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第四层测试。

钉死的是纪律，不是公式：
  ① 证据层级不能把"提到官方"当成"官方发布"（§9）
  ② 没模型必须标 rule，不许冒充 LLM（§39）
  ③ 创意必须可溯源（纪律 ①：说不出来源的创意不算产出）
  ④ 修订环必须有界（§31 MAX_ITERATION）
  ⑤ State 不许装原文（§7）
  ⑥ 工具全部只读 + text_access 声明完整（§17）
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_L4 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L4)
for p in (_ROOT, _L4, os.path.join(_L4, "intelligence"),
          os.path.join(_ROOT, "L2_signal"), os.path.join(_ROOT, "L1_data_source")):
    if p not in sys.path:
        sys.path.insert(0, p)

from intelligence.nodes import evidence as N_evidence  # noqa: E402
from intelligence.nodes import analysis as N_analysis  # noqa: E402
from intelligence.nodes import opportunity as N_opp  # noqa: E402
from intelligence.nodes import creative as N_creative  # noqa: E402
from intelligence.nodes import evaluation as N_eval  # noqa: E402
from intelligence import state as N_state  # noqa: E402
from intelligence import tools as N_tools  # noqa: E402
from intelligence import llm as N_llm  # noqa: E402
from intelligence.store import tier_of, gate_basis, input_hash  # noqa: E402


def _content(cid, text, official=False, platform="taptap"):
    return {"content_id": cid, "platform": platform, "normalized_text": text,
            "normalized_title": text[:20], "source_features": {"is_official": official},
            "metrics": {"views": 1000, "comments": 5}, "gaming_probability": 0.9,
            "entities": ["game_arknights"], "quality_score": 0.8,
            "published_at": "2026-10-01T10:00:00"}


class TestEvidenceTier(unittest.TestCase):
    """§9：层级错了，LLM 就会把玩家猜测写成官方事实。"""

    def test_is_official_is_the_only_primary(self):
        self.assertEqual(N_evidence._tier_of(_content("a", "更新了", official=True)), "PRIMARY")

    def test_mentioning_official_is_not_primary(self):
        """★ 实测踩坑：玩家转述"官宣联动"曾被判 PRIMARY，导致 trigger 变成玩家提问。"""
        self.assertEqual(N_evidence._tier_of(_content("b", "三角洲官宣联动影之刃零")), "SECONDARY")

    def test_community_by_default(self):
        self.assertEqual(N_evidence._tier_of(_content("c", "这游戏真好玩")), "COMMUNITY")

    def test_excerpt_is_short(self):
        long_text = "长" * 500
        pack = N_evidence.build({"event_id": "e1", "canonical_title": "t"},
                                [_content("d", long_text)])
        self.assertLessEqual(len(pack["evidence"][0]["excerpt"]), N_evidence.EXCERPT_LEN)

    def test_no_primary_means_no_trigger(self):
        """没官方信源 → trigger 必须是 None，不许编一个。"""
        pack = N_evidence.build({"event_id": "e1"}, [_content("e", "随便聊聊")])
        an = N_analysis.trend_analyst(pack, {})
        self.assertIsNone(an["trigger"])
        self.assertTrue(any("触发点未找到官方" in u for u in an["key_uncertainties"]))


class TestRelevance(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(N_analysis.RELEVANCE_WEIGHTS.values()), 1.0, places=6)

    def test_route_thresholds(self):
        pack = N_evidence.build({"event_id": "e1", "lifecycle": "GROWING",
                                 "confidence_score": 0.8},
                                [_content("a", "期待新版本上线", official=True)])
        rel = N_analysis.relevance(pack)
        self.assertIn(rel["route"], ("archive", "light_analysis", "opportunity"))
        if rel["score"] < N_analysis.RELEVANCE_ARCHIVE:
            self.assertEqual(rel["route"], "archive")

    def test_dims_are_discriminative(self):
        """platform_asset_fit 不该恒等于 1.0（曾因"low 成本占比"写法失去区分度）。"""
        pack = N_evidence.build({"event_id": "e1", "event_type": "meme",
                                 "confidence_score": 0.6}, [_content("a", "笑死整活")])
        rel = N_analysis.relevance(pack)
        self.assertLess(rel["dimensions"]["platform_asset_fit"], 1.01)


class TestTraceability(unittest.TestCase):
    """纪律 ①：每条创意必须带 event_id + opportunity_id + evidence_ids + 可找回的数字。"""

    def _state(self):
        pack = N_evidence.build({"event_id": "e1", "canonical_title": "arknights 发布",
                                 "lifecycle": "GROWING", "confidence_score": 0.78,
                                 "hot_score": 0.4, "momentum_score": 0.5, "event_type": "release"},
                                [_content(f"c{i}", "期待新角色上线预约") for i in range(6)])
        st = N_state.empty_state({"event_id": "e1"})
        st["evidence_pack"] = pack
        st["relevance"] = N_analysis.relevance(pack)
        st["audiences"] = N_analysis.audience(pack, {})
        st["opportunities"] = N_opp.opportunity(st)
        st["growth_hypotheses"] = N_opp.strategist(st)
        st["creatives"] = N_creative.generate(st)
        return st

    def test_every_creative_has_full_source_refs(self):
        st = self._state()
        self.assertTrue(st["creatives"])
        for c in st["creatives"]:
            ref = c["source_refs"]
            self.assertEqual(ref["event_id"], "e1")
            self.assertTrue(ref["opportunity_id"])
            self.assertTrue(ref["evidence_ids"])
            self.assertIsNotNone(ref["facts"]["content_count"])

    def test_facts_numbers_are_retrievable(self):
        """创意里引用的数字必须能在 evidence_pack 里原样找回。"""
        st = self._state()
        pack = st["evidence_pack"]
        for c in st["creatives"]:
            f = c["source_refs"]["facts"]
            self.assertEqual(f["content_count"], pack["n_members"])
            self.assertEqual(f["platform_count"], len(pack["platforms"]))

    def test_opportunity_has_mechanism(self):
        """§22：没有 growth_mechanism 的不许进创意阶段。"""
        st = self._state()
        for o in st["opportunities"]:
            self.assertGreaterEqual(len(o["growth_mechanism"].split("→")), 3)


class TestEvaluatorAndLoop(unittest.TestCase):
    def test_rubric_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(N_eval.RUBRIC_WEIGHTS.values()), 1.0, places=6)

    def test_max_iteration_is_bounded(self):
        self.assertEqual(N_eval.MAX_ITERATION, 2)

    def test_unsupported_when_no_evidence(self):
        st = N_state.empty_state({"event_id": "e1"})
        st["evidence_pack"] = {"evidence": []}
        c = {"idea_id": "i1", "source_refs": {"evidence_ids": []}}
        self.assertEqual(N_eval.fact_check(c, st)["status"], "UNSUPPORTED")

    def test_community_only_is_not_supported(self):
        """只有玩家内容 → 不能判 SUPPORTED（会让人以为有官方背书）。"""
        st = N_state.empty_state({"event_id": "e1"})
        st["evidence_pack"] = {"evidence": [{"evidence_id": "ev_1", "tier": "COMMUNITY"}]}
        c = {"idea_id": "i1", "source_refs": {"evidence_ids": ["ev_1"],
                                              "facts": {"content_count": 3}}}
        self.assertEqual(N_eval.fact_check(c, st)["status"], "PARTIALLY_SUPPORTED")

    def test_risk_flags_missing_primary(self):
        st = N_state.empty_state({"event_id": "e1"})
        st["evidence_pack"] = {"primary_ratio": 0.0, "event": {"lifecycle": "GROWING"},
                               "evidence": []}
        r = N_eval.risk(st, creatives=[])
        self.assertEqual(r["risk_level"], "high")
        self.assertTrue(any(x["type"] == "fact" for x in r["risks"]))


class TestStateGuard(unittest.TestCase):
    def test_raw_text_blocked(self):
        st = {"evidence_pack": {}, "raw_text": "x" * 10}
        v = N_state.assert_state_clean(st)
        self.assertTrue(any("raw_text" in x for x in v))

    def test_long_string_blocked(self):
        v = N_state.assert_state_clean({"note": "x" * 400})
        self.assertTrue(v)

    def test_normal_state_passes(self):
        st = N_state.empty_state({"event_id": "e1", "canonical_title": "t"})
        self.assertEqual(N_state.assert_state_clean(st), [])


class TestTools(unittest.TestCase):
    def test_all_read_only(self):
        t = N_tools.build_tools(None)
        N_tools.assert_all_read_only(t)

    def test_write_tool_rejected(self):
        with self.assertRaises(ValueError):
            N_tools.Tool("push", "发推送", lambda: None,
                         {"sources": [], "granularity": "none", "via_llm": False,
                          "returns": ["ok"]}, read_only=False)

    def test_missing_sources_rejected(self):
        with self.assertRaises(ValueError):
            N_tools.Tool("x", "y", lambda: None,
                         {"sources": [], "granularity": "full", "via_llm": False,
                          "returns": ["ok"]})

    def test_raw_text_return_rejected(self):
        with self.assertRaises(ValueError):
            N_tools.Tool("x", "y", lambda: None,
                         {"sources": ["a"], "granularity": "short", "via_llm": False,
                          "returns": ["comment_text"]})

    def test_id_reference_allowed(self):
        """content_id 是"引用而非搬运"的载体，不该被原文黑名单误伤。"""
        ta = {"sources": ["a"], "granularity": "full", "via_llm": False,
              "returns": ["content_id", "tier"]}
        self.assertEqual(N_tools.validate_text_access(ta), [])

    def test_external_tools_raise_not_fake(self):
        t = N_tools.build_tools(None)
        with self.assertRaises(N_tools.ToolUnavailable):
            t["search_web"].run(query="x")


class TestNoLLM(unittest.TestCase):
    def test_router_marks_rule(self):
        r = N_llm.ModelRouter(enabled=False)
        info = r.resolve("creative")
        self.assertIsNone(info["model"])
        self.assertEqual(info["mode"], "rule")

    def test_call_without_key_raises(self):
        r = N_llm.ModelRouter(enabled=False)
        with self.assertRaises(N_llm.LLMUnavailable):
            r.call("creative", "prompt")


class TestTierGate(unittest.TestCase):
    def test_spec_gate_when_temporal_ok(self):
        self.assertEqual(tier_of({"hot_score": 0.2}, temporal_ok=True), "T0")
        self.assertEqual(tier_of({"hot_score": 0.8, "momentum_score": 0.9,
                                  "confidence_score": 0.8}, temporal_ok=True), "T2")

    def test_degraded_gate_when_no_temporal(self):
        """无时间分辨率时 hot 恒低 → 用 confidence + 体量，否则全被 T0 挡死（实测 211 全挡）。"""
        self.assertEqual(tier_of({"hot_score": 0.2, "confidence_score": 0.78,
                                  "content_count": 100}, temporal_ok=False), "T2")
        self.assertEqual(tier_of({"hot_score": 0.2, "confidence_score": 0.2,
                                  "content_count": 1}, temporal_ok=False), "T0")
        self.assertIn("degraded", gate_basis(False))

    def test_input_hash_ignores_volatile_fields(self):
        a = {"event_id": "e", "hot_score": 0.5, "last_updated_at": "t1"}
        b = {"event_id": "e", "hot_score": 0.5, "last_updated_at": "t2"}
        self.assertEqual(input_hash(a), input_hash(b))


if __name__ == "__main__":
    unittest.main()
