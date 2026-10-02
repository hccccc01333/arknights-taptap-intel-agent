#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 集成测试（**不联网**：用假 router 替身）。

钉死的纪律：
  ① 只能用免费模型（`TIER_MODELS` 里不能出现付费模型）
  ② 模型输出必须过结构校验才采纳，不通过就**保留规则结果**并标 mode
  ③ 溯源字段（source_refs）绝不由模型生成 —— 沿用规则版，防模型编 id
  ④ 无 key / 调用失败 → 规则兜底，不返回假输出
"""

from __future__ import annotations

import json
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

from intelligence import llm as L  # noqa: E402
from intelligence import prompts as P  # noqa: E402
from intelligence.nodes import llm_augment as AUG  # noqa: E402
from intelligence.nodes import evidence as N_evidence  # noqa: E402
from intelligence.nodes import analysis as N_analysis  # noqa: E402
from intelligence.nodes import creative as N_creative  # noqa: E402
from intelligence.nodes import evaluation as N_eval  # noqa: E402


class FakeRouter:
    """替身：按 `reply` 返回结构化结果；`fail=True` 时模拟调用失败。"""

    def __init__(self, reply=None, fail=False):
        self.enabled = True
        self.reply = reply if reply is not None else {}
        self.fail = fail
        self.last_errors: list = []
        self.last_raw: dict = {}
        self.calls: int = 0

    def call_json(self, node, prompt, system=None, max_tokens=1500, repair_hint=None):
        self.calls += 1
        if self.fail:
            self.last_errors.append(f"{node}: 模拟失败")
            return None
        if not self.reply:                      # 空回复：模拟「解析成功但内容为空」
            self.last_errors.append(f"{node}: 输出不是 JSON 对象")
            return None
        out = dict(self.reply)
        out["_llm"] = {"model": "fake/model:free", "seconds": 0.1, "usage": {}}
        return out


def _pack():
    return N_evidence.build(
        {"event_id": "e1", "canonical_title": "某游戏捏脸爆火", "lifecycle": "GROWING",
         "confidence_score": 0.8, "hot_score": 0.5, "momentum_score": 0.6,
         "event_type": "meme"},
        [{"content_id": f"c{i}", "platform": "taptap", "normalized_text": "捏脸太好玩了笑死",
          "normalized_title": "捏脸太好玩了笑死", "source_features": {"is_official": False},
          "metrics": {"views": 1000, "comments": 8}, "gaming_probability": 0.95,
          "entities": ["game_x"], "quality_score": 0.8,
          "published_at": "2026-10-01T10:00:00"} for i in range(6)])


class TestFreeModelsOnly(unittest.TestCase):
    def test_no_paid_model_in_routing_table(self):
        """路由表里出现的模型必须都是 :free 后缀（付费模型一律不进这张表）。"""
        for tier, m in L.TIER_MODELS.items():
            if m is None:
                continue
            self.assertTrue(str(m).endswith(":free") or m == "openrouter/free",
                            f"{tier} -> {m} 不是免费模型")

    def test_fallbacks_also_free(self):
        for main, subs in L.FALLBACKS.items():
            self.assertTrue(str(main).endswith(":free") or main == "openrouter/free", main)
            for s in subs:
                self.assertTrue(str(s).endswith(":free") or s == "openrouter/free", s)

    def test_router_reports_rule_when_disabled(self):
        r = L.ModelRouter(enabled=False)
        self.assertEqual(r.resolve("creative")["mode"], "rule")

    def test_resolve_gives_model_when_enabled(self):
        r = L.ModelRouter(enabled=True) if L.has_llm() else None
        if r is None:
            self.skipTest("本机无 FREE_API_KEY（跳过真实路由断言）")
        info = r.resolve("creative")
        self.assertEqual(info["mode"], "llm")
        self.assertTrue(info["model"].endswith(":free"))

    def test_extract_json_handles_fence(self):
        self.assertEqual(L._extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(L._extract_json('前缀 {"a": 2} 后缀'), {"a": 2})
        self.assertIsNone(L._extract_json("没有JSON"))


class TestAugmentFallback(unittest.TestCase):
    def test_failed_call_keeps_rule_output(self):
        pack = _pack()
        rule = N_analysis.trend_analyst(pack, {})
        out = AUG.augment_trend_analyst(rule, pack, FakeRouter(fail=True))
        self.assertEqual(out["mode"], "rule_fallback_after_llm_error")
        self.assertEqual(out["what_happened"], rule["what_happened"])

    def test_bad_structure_keeps_rule_output(self):
        pack = _pack()
        rule = N_analysis.relevance(pack)
        out = AUG.augment_relevance(rule, pack, FakeRouter(reply={"dimensions": "不是字典"}))
        self.assertEqual(out["dimensions"], rule["dimensions"])

    def test_missing_one_dimension_rejected(self):
        """少一维就整体不采纳 —— 半拉子模型输出比纯规则更危险。"""
        pack = _pack()
        rule = N_analysis.relevance(pack)
        out = AUG.augment_relevance(rule, pack, FakeRouter(reply={
            "dimensions": {"user_overlap": 0.9, "community_fit": 0.8}}))
        self.assertEqual(out["mode"], "rule_fallback_after_llm_error")
        self.assertEqual(out["score"], rule["score"])

    def test_valid_dims_recompute_score(self):
        pack = _pack()
        rule = N_analysis.relevance(pack)
        dims = {"user_overlap": 0.9, "community_fit": 0.9, "platform_asset_fit": 0.9,
                "growth_potential": 0.9, "timing_fit": 0.9}
        out = AUG.augment_relevance(rule, pack, FakeRouter(reply={"dimensions": dims}))
        self.assertEqual(out["mode"], "llm")
        self.assertAlmostEqual(out["score"], 0.9, places=2)
        self.assertEqual(out["dimensions_source"], "llm")

    def test_dims_out_of_range_rejected(self):
        pack = _pack()
        rule = N_analysis.relevance(pack)
        out = AUG.augment_relevance(rule, pack, FakeRouter(reply={
            "dimensions": {"user_overlap": 5, "community_fit": 0.8, "platform_asset_fit": 0.8,
                           "growth_potential": 0.8, "timing_fit": 0.8}}))
        self.assertEqual(out["mode"], "rule_fallback_after_llm_error")


class TestControlledVocabulary(unittest.TestCase):
    """模型不许自创增长目标 / 创意类型 / 动机 / 风险类型。"""

    def _state(self, pack):
        st = {"event_id": "e1", "evidence_pack": pack,
              "relevance": N_analysis.relevance(pack),
              "audiences": N_analysis.audience(pack, {}),
              "opportunities": [], "growth_hypotheses": [], "creatives": []}
        st["opportunities"] = N_analysis and __import__(
            "intelligence.nodes.opportunity", fromlist=["opportunity"]).opportunity(st)
        st["growth_hypotheses"] = __import__(
            "intelligence.nodes.opportunity", fromlist=["strategist"]).strategist(st)
        st["creatives"] = N_creative.generate(st)
        return st

    def test_unknown_growth_goal_dropped(self):
        pack = _pack()
        st = self._state(pack)
        out = AUG.augment_opportunity(st["opportunities"], st,
                                      FakeRouter(reply={"opportunities": [
                                          {"name": "x", "growth_goal": "自创的奇怪KPI",
                                           "growth_mechanism": "a→b→c"}]}))
        self.assertEqual(out, st["opportunities"])   # 全被拒 → 回退规则

    def test_unknown_creative_type_dropped(self):
        pack = _pack()
        st = self._state(pack)
        out = AUG.augment_creative(st["creatives"], st, FakeRouter(reply={
            "creatives": [{"creative_type": "自创类型", "idea_name": "x"}]}))
        self.assertEqual(out, st["creatives"])

    def test_unknown_motivation_dropped(self):
        pack = _pack()
        base = N_analysis.audience(pack, {})
        out = AUG.augment_audience(base, pack, FakeRouter(reply={
            "audiences": [{"segment": "x", "motivation_id": "不存在的动机"}]}))
        self.assertEqual(out, base)

    def test_unknown_risk_type_dropped(self):
        st = {"evidence_pack": _pack(), "creatives": []}
        rule = N_eval.risk(st)
        out = AUG.augment_risk(rule, st, FakeRouter(reply={
            "risks": [{"type": "外星风险", "level": "high", "description": "x"}]}))
        self.assertEqual([r["type"] for r in out["risks"]],
                         [r["type"] for r in rule["risks"]])


class TestTraceabilityPreserved(unittest.TestCase):
    """★★ 模型改写了创意内容，但溯源字段必须由规则版提供（防模型编造 id）。"""

    def test_source_refs_come_from_rule(self):
        pack = _pack()
        st = {"event_id": "e1", "evidence_pack": pack,
              "relevance": N_analysis.relevance(pack),
              "audiences": N_analysis.audience(pack, {}),
              "opportunities": [], "growth_hypotheses": [], "creatives": []}
        opp_mod = __import__("intelligence.nodes.opportunity", fromlist=["opportunity"])
        st["opportunities"] = opp_mod.opportunity(st)
        st["growth_hypotheses"] = opp_mod.strategist(st)
        rule_creatives = N_creative.generate(st)
        st["creatives"] = rule_creatives

        reply = {"creatives": [{"creative_type": "ugc", "idea_name": "模型想的捏脸大赛",
                                "concept": "让玩家上传捏脸作品并投票",
                                "primary_metric": "ugc_count",
                                "source_refs": {"event_id": "伪造的事件",
                                                "evidence_ids": ["假证据"],
                                                "facts": {"content_count": 99999}}}]}
        out = AUG.augment_creative(rule_creatives, st, FakeRouter(reply=reply))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["idea_name"], "模型想的捏脸大赛")     # 内容采纳模型
        self.assertEqual(out[0]["source_refs"]["event_id"], "e1")      # 溯源仍是规则版
        self.assertNotEqual(out[0]["source_refs"]["facts"]["content_count"], 99999)
        self.assertEqual(out[0]["source_refs"]["facts"]["content_count"], pack["n_members"])


class TestSilentEmptyIsVisible(unittest.TestCase):
    """★ 实测踩坑钉死：JSON 解析成功但关键列表为空时，曾**静默回退规则且不留痕**——
    产物看起来正常、`llm_errors` 却是空的，完全查不出「模型其实没贡献」。"""

    def _state(self):
        pack = _pack()
        st = {"event_id": "e1", "evidence_pack": pack,
              "relevance": N_analysis.relevance(pack),
              "audiences": N_analysis.audience(pack, {}),
              "opportunities": [], "growth_hypotheses": [], "creatives": []}
        st["opportunities"] = __import__(
            "intelligence.nodes.opportunity", fromlist=["opportunity"]).opportunity(st)
        return st

    def test_empty_list_recorded_as_soft_empty(self):
        st = self._state()
        rule_opps = st["opportunities"]
        self.assertTrue(rule_opps, "前置：规则版必须有产出，否则本测试无意义")
        r = FakeRouter(reply={"opportunities": []})       # 解析成功，但列表为空
        out = AUG.augment_opportunity(rule_opps, st, r)
        self.assertEqual(out, rule_opps)                   # 回退规则
        self.assertTrue(any("soft_empty" in e for e in r.last_errors),
                        f"必须留痕，实际={r.last_errors}")

    def test_missing_key_recorded_as_soft_empty(self):
        st = self._state()
        r = FakeRouter(reply={"some_other_key": [{"name": "x"}]})
        out = AUG.augment_opportunity(st["opportunities"], st, r)
        self.assertEqual(out, st["opportunities"])
        self.assertTrue(any("soft_empty" in e for e in r.last_errors))

    def test_non_empty_list_not_flagged(self):
        st = self._state()
        r = FakeRouter(reply={"opportunities": [
            {"name": "机会A", "audience": "深度玩家", "user_motivation": "m",
             "motivation_id": "curiosity", "growth_goal": "促活",
             "growth_mechanism": "热点→话题讨论→社区沉淀→长期活跃",
             "observed_signal": "s", "platform_advantage": "p",
             "expected_metrics": ["互动率"], "motivation_strength": 0.7}]})
        AUG.augment_opportunity(st["opportunities"], st, r)
        self.assertFalse(any("soft_empty" in e for e in r.last_errors),
                         f"有内容不该报错，实际={r.last_errors}")


class TestQuotaCircuitBreaker(unittest.TestCase):
    """免费额度是**每日硬上限**（实测 X-RateLimit-Limit: 50）。不熔断 →
    一次跑批里每个节点各撞一次 429、各等一轮退避，还把失败噪声灌满 llm_errors。"""

    def test_quota_error_detected(self):
        msg = ("HTTP 429: {\"error\":{\"message\":\"Rate limit exceeded: "
               "free-models-per-day. Add 10 credits to unlock 1000 free model requests per day\"}}")
        self.assertTrue(L._is_quota_error(msg))
        self.assertFalse(L._is_quota_error("HTTP 500: 内部错误"))

    def test_breaker_stops_further_calls(self):
        r = L.ModelRouter(enabled=False)
        r.enabled = True
        r.overrides["trend_analyst"] = "m/free"
        r.quota_exhausted = True
        r.quota_reason = "free-models-per-day"
        with self.assertRaises(L.LLMUnavailable):
            r.call("trend_analyst", "p")
        self.assertIsNone(r.call_json("trend_analyst", "p"))   # 不再重试、不再记噪声

    def test_errors_not_polluted_after_breaker(self):
        r = L.ModelRouter(enabled=False)
        r.quota_exhausted = True
        before = len(r.last_errors)
        self.assertIsNone(r.call_json("opportunity", "p"))
        self.assertEqual(len(r.last_errors), before)


class TestNullObjectDoesNotCrash(unittest.TestCase):
    """★ 实测踩坑钉死：`_items()` 恒返回 list（obj=None → []），所以
    `if not isinstance(items, list)` 是**死守卫**，压根拦不住解析失败；
    代码继续走到 `obj.get("_llm")` → AttributeError，整个事件崩掉、产出全丢。
    判空必须看 `obj` 本身。"""

    def _state(self):
        pack = _pack()
        st = {"event_id": "e1", "evidence_pack": pack,
              "relevance": N_analysis.relevance(pack),
              "audiences": N_analysis.audience(pack, {}),
              "opportunities": [], "growth_hypotheses": [], "creatives": []}
        st["opportunities"] = __import__(
            "intelligence.nodes.opportunity", fromlist=["opportunity"]).opportunity(st)
        st["creatives"] = N_creative.generate(st)
        return st

    def test_risk_survives_null_object(self):
        st = self._state()
        rule_risk = N_eval.risk(st)
        out = AUG.augment_risk(rule_risk, st, FakeRouter(fail=True))   # call_json → None
        self.assertIsNotNone(out)
        self.assertEqual(out["risks"], rule_risk["risks"])             # 回退规则版

    def test_evaluator_survives_null_object(self):
        st = self._state()
        creatives = st["creatives"]
        self.assertTrue(creatives, "前置：规则版要有创意")
        rule_evals = [N_eval.evaluate(c, st, peers=creatives) for c in creatives]
        out = AUG.augment_evaluation(rule_evals, creatives, st, FakeRouter(fail=True))
        self.assertEqual(len(out), len(creatives))                     # 不丢条目
        self.assertTrue(all(e.get("mode") == "rule_fallback_after_llm_error" for e in out))


class TestSystemPromptHasNoContradiction(unittest.TestCase):
    """★ 实测踩坑钉死（2026-10-02）：曾对**所有**节点都追加「输出必须分为
    facts/inferences/unknowns 三段」的纪律，而列表型节点同时又要求输出
    `{"opportunities": [...]}` —— 模型原话指出这两条**互相矛盾**，然后拒绝产出 JSON，
    节点静默回退规则。纪律只能加在它适用的节点上。"""

    LIST_NODES = ("opportunity", "creative", "evaluator", "risk")
    NARRATIVE_NODES = ("trend_analyst", "relevance", "audience")

    def test_list_nodes_have_no_three_part_contract(self):
        marker = P.FACT_INFERENCE_CONTRACT.splitlines()[0]
        for n in self.LIST_NODES:
            self.assertNotIn(marker, P.system_for(n), f"{n} 不该要求三分输出")

    def test_narrative_nodes_keep_three_part_contract(self):
        marker = P.FACT_INFERENCE_CONTRACT.splitlines()[0]
        for n in self.NARRATIVE_NODES:
            self.assertIn(marker, P.system_for(n), f"{n} 必须有三分纪律")

    def test_all_nodes_forbid_thinking_out_loud(self):
        """推理模型把思考写进 content 会烧光 max_tokens → 没空间输出 JSON。"""
        for n in self.LIST_NODES + self.NARRATIVE_NODES:
            self.assertIn("不要输出任何思考过程", P.system_for(n), n)

    def test_all_nodes_carry_json_spec(self):
        for n in self.LIST_NODES + self.NARRATIVE_NODES:
            self.assertIn("输出结构", P.system_for(n), n)
            self.assertIn(P.JSON_ONLY, P.system_for(n), n)


class TestUsageAccounting(unittest.TestCase):
    def test_usage_records_tokens(self):
        u = L.Usage()
        u.add("m/free", {"prompt_tokens": 100, "completion_tokens": 50})
        d = u.as_dict()
        self.assertEqual(d["prompt_tokens"], 100)
        self.assertEqual(d["cost_usd"], 0.0)
        self.assertEqual(d["by_model"]["m/free"], 1)


if __name__ == "__main__":
    unittest.main()
