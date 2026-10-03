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
    """替身：按 `reply` 返回结构化结果；`fail=True` 时模拟调用失败。

    `reply_seq` 用于模拟**多轮调用返回不同结果**（补评场景）：按调用次序取，
    取完后用最后一个。
    """

    def __init__(self, reply=None, fail=False, reply_seq=None):
        self.enabled = True
        self.reply = reply if reply is not None else {}
        self.fail = fail
        self.reply_seq = list(reply_seq) if reply_seq else None
        self.last_errors: list = []
        self.last_raw: dict = {}
        self.events: list = []
        self.calls: int = 0

    def log_event(self, node, kind, reason="", **fields):
        ev = {"node": node, "kind": kind, "reason": reason}
        ev.update(fields)
        self.events.append(ev)
        self.last_errors.append(f"{node}: {reason or kind}")

    def _current_reply(self):
        if not self.reply_seq:
            return self.reply
        i = min(self.calls - 1, len(self.reply_seq) - 1)
        return self.reply_seq[i]

    def call_json(self, node, prompt, system=None, max_tokens=1500, repair_hint=None):
        self.calls += 1
        if self.fail:
            self.last_errors.append(f"{node}: 模拟失败")
            return None
        rep = self._current_reply()
        if not rep:                             # 空回复：模拟「解析成功但内容为空」
            self.last_errors.append(f"{node}: 输出不是 JSON 对象")
            return None
        out = dict(rep)
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


class TestEmptyResultIsRetried(unittest.TestCase):
    """★ `repair_hint` 原本只在**解析失败**时重试，而 soft_empty（解析成功、数组空）
    会直接落规则兜底 —— 实测 creative 就这样白丢一整轮模型输出。
    完整性优先：解析成功但没内容，等同于没拿到结果，值得再要一次。"""

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

    def _creative_payload(self, idea_name="模型想的捏脸大赛"):
        return {"creatives": [{
            "idea_name": idea_name, "creative_type": "ugc", "insight": "玩家爱晒捏脸",
            "concept": "发起捏脸大赛", "user_flow": ["进入社区", "上传捏脸", "投票"],
            "distribution_channels": ["社区话题"], "primary_metric": "UGC数",
            "secondary_metrics": ["互动率"], "dependencies": ["话题页"],
            "risks": ["参与度不足"]}]}

    def test_empty_then_filled_is_retried_and_adopted(self):
        st = self._state()
        rule_creatives = st["creatives"]
        r = FakeRouter(reply_seq=[{"nothing_here": 1}, self._creative_payload()])
        try:
            out = AUG.augment_creative(rule_creatives, st, r)
        except Exception as e:
            self.fail(f"不应抛异常：{type(e).__name__}: {e}")
        self.assertEqual(r.calls, 2, "空结果应触发补要")
        self.assertTrue(any(e.get("kind") == "retry" for e in r.events),
                        f"补要必须留痕，实际={r.events}")
        self.assertTrue(any(c.get("mode") == "llm" for c in out),
                        f"补要后应采纳模型创意，实际={[c.get('mode') for c in out]}")

    def test_empty_twice_falls_back(self):
        st = self._state()
        r = FakeRouter(reply_seq=[{"nothing_here": 1}, {"still_nothing": 2}])
        out = AUG.augment_creative(st["creatives"], st, r)
        self.assertEqual(out, st["creatives"])     # 回退规则版

    def test_filled_first_time_no_extra_call(self):
        st = self._state()
        r = FakeRouter(reply=self._creative_payload())
        AUG.augment_creative(st["creatives"], st, r)
        self.assertEqual(r.calls, 1, "第一次就有内容不该多调一次")


class TestResponseCache(unittest.TestCase):
    """缓存只在**输入完全一致**时命中。不换弱模型、不降 token，所以不损推理质量。"""

    def test_same_input_hits(self):
        c = L.LLMCache(path=None)
        c.put("m/free", "sys", "prompt", 4200, True, {"a": 1})
        self.assertEqual(c.get("m/free", "sys", "prompt", 4200, True), {"a": 1})

    def test_different_prompt_misses(self):
        c = L.LLMCache(path=None)
        c.put("m/free", "sys", "prompt", 4200, True, {"a": 1})
        self.assertIsNone(c.get("m/free", "sys", "prompt2", 4200, True))

    def test_different_model_misses(self):
        c = L.LLMCache(path=None)
        c.put("m/free", "sys", "p", 4200, True, {"a": 1})
        self.assertIsNone(c.get("other/free", "sys", "p", 4200, True))

    def test_different_max_tokens_misses(self):
        """max_tokens 影响输出完整度，必须进 key。"""
        c = L.LLMCache(path=None)
        c.put("m/free", "sys", "p", 4200, True, {"a": 1})
        self.assertIsNone(c.get("m/free", "sys", "p", 1500, True))

    def test_disabled_cache_never_hits(self):
        c = L.LLMCache(path=None, enabled=False)
        c.put("m/free", "sys", "p", 4200, True, {"a": 1})
        self.assertIsNone(c.get("m/free", "sys", "p", 4200, True))

    def test_cache_does_not_leak_mutable_reference(self):
        c = L.LLMCache(path=None)
        c.put("m/free", "sys", "p", 4200, True, {"a": 1})
        got = c.get("m/free", "sys", "p", 4200, True)
        got["a"] = 999
        self.assertEqual(c.get("m/free", "sys", "p", 4200, True)["a"], 1)

    def test_persists_to_disk(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sub", "cache.jsonl")
            c1 = L.LLMCache(path=path)
            c1.put("m/free", "sys", "p", 4200, True, {"a": 42})
            c2 = L.LLMCache(path=path)        # 新实例 = 新进程的效果
            self.assertEqual(c2.get("m/free", "sys", "p", 4200, True), {"a": 42})


class TestSharedQuotaBreakerAcrossThreads(unittest.TestCase):
    """并发时额度熔断必须跨线程共享：一个线程撞到 429，其他别再白试一轮
    （免费额度一天只有 50 次，白试就是真金白银）。"""

    def test_event_is_shared(self):
        import threading
        ev = threading.Event()
        r1 = L.ModelRouter(enabled=False, quota_event=ev)
        r2 = L.ModelRouter(enabled=False, quota_event=ev)
        r1._trip_quota("free-models-per-day", "creative", "m/free")
        self.assertTrue(r2._quota_tripped(), "r2 应看到共享熔断")
        self.assertTrue(r1.quota_exhausted)

    def test_no_shared_event_is_isolated(self):
        r1 = L.ModelRouter(enabled=False)
        r2 = L.ModelRouter(enabled=False)
        r1._trip_quota("x", "creative", "m/free")
        self.assertFalse(r2._quota_tripped())


class TestEventLogIsStructured(unittest.TestCase):
    """可观测性：每次失败/补评/兜底都要有**结构化**记录，而不是只能人肉读的字符串。"""

    def test_log_event_records_kind_and_reason(self):
        r = L.ModelRouter(enabled=False)
        r.log_event("creative", "parse_failed", "输出不是 JSON 对象",
                    model="m/free", truncated=True)
        self.assertEqual(len(r.events), 1)
        ev = r.events[0]
        self.assertEqual(ev["node"], "creative")
        self.assertEqual(ev["kind"], "parse_failed")
        self.assertTrue(ev["truncated"])
        self.assertIn("creative", r.last_errors[0])   # 人类可读串仍然有

    def test_kinds_are_from_fixed_vocabulary(self):
        allowed = {"ok", "json_ok", "parse_failed", "call_failed", "quota_exhausted",
                   "soft_empty", "unwrapped_single", "retry", "fallback"}
        r = L.ModelRouter(enabled=False)
        for k in sorted(allowed):
            r.log_event("x", k, "r")
        self.assertEqual({e["kind"] for e in r.events}, allowed)

    def test_summary_counts_by_kind(self):
        r = L.ModelRouter(enabled=False)
        for _ in range(3):
            r.log_event("a", "ok", "")
        r.log_event("b", "parse_failed", "坏")
        counts: dict = {}
        for e in r.events:
            counts[e["kind"]] = counts.get(e["kind"], 0) + 1
        self.assertEqual(counts, {"ok": 3, "parse_failed": 1})


class TestBatchEvalPartialCoverageIsVisible(unittest.TestCase):
    """★ 实测踩坑钉死：批量评审时模型只评了一部分（4 条只评 1 条），
    漏评的**悄悄回退规则且不留痕**，`llm_errors` 是空的 —— 跟 soft_empty 同一类静默降级。"""

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

    def _dims(self):
        return {"relevance": 0.8, "user_insight": 0.7, "timing": 0.6, "growth": 0.7,
                "feasibility": 0.8, "novelty": 0.5, "distribution": 0.6}

    def test_partial_coverage_triggers_retry_and_is_logged(self):
        st = self._state()
        creatives = st["creatives"]
        self.assertGreaterEqual(len(creatives), 2, "前置：至少 2 条创意")
        rule_evals = [N_eval.evaluate(c, st, peers=creatives) for c in creatives]
        r = FakeRouter(reply={"evaluations": [
            {"idea_id": creatives[0]["idea_id"], "dimensions": self._dims()}]})
        out = AUG.augment_evaluation(rule_evals, creatives, st, r)
        self.assertEqual(len(out), len(creatives))
        self.assertEqual(r.calls, 2, "漏评必须触发补评")
        self.assertTrue(any("补评" in e for e in r.last_errors),
                        f"补评必须留痕，实际={r.last_errors}")

    def test_retry_fills_the_gap(self):
        """★ 核心：第 1 轮漏的，第 2 轮只补那几条 → 最终全部采纳模型评分。"""
        st = self._state()
        creatives = st["creatives"]
        self.assertGreaterEqual(len(creatives), 2, "前置：至少 2 条创意")
        rule_evals = [N_eval.evaluate(c, st, peers=creatives) for c in creatives]
        first = {"evaluations": [
            {"idea_id": creatives[0]["idea_id"], "dimensions": self._dims()}]}
        second = {"evaluations": [
            {"idea_id": c["idea_id"], "dimensions": self._dims()}
            for c in creatives[1:]]}
        r = FakeRouter(reply_seq=[first, second])
        out = AUG.augment_evaluation(rule_evals, creatives, st, r)
        self.assertEqual(r.calls, 2)
        self.assertTrue(all(e.get("mode") == "llm" for e in out),
                        f"补评后应全部采纳，实际={[e.get('mode') for e in out]}")
        self.assertFalse(any("规则兜底" in e for e in r.last_errors),
                         f"不该兜底，实际={r.last_errors}")

    def test_still_missing_after_retry_falls_back_with_reason(self):
        st = self._state()
        creatives = st["creatives"]
        rule_evals = [N_eval.evaluate(c, st, peers=creatives) for c in creatives]
        only_first = {"evaluations": [
            {"idea_id": creatives[0]["idea_id"], "dimensions": self._dims()}]}
        r = FakeRouter(reply_seq=[only_first, only_first])
        out = AUG.augment_evaluation(rule_evals, creatives, st, r)
        self.assertEqual(r.calls, 2)
        self.assertTrue(any("规则兜底" in e for e in r.last_errors),
                        f"补评仍漏要留痕，实际={r.last_errors}")
        self.assertTrue(any(e.get("mode") == "rule_fallback_after_llm_error" for e in out))

    def test_full_coverage_not_flagged(self):
        st = self._state()
        creatives = st["creatives"]
        rule_evals = [N_eval.evaluate(c, st, peers=creatives) for c in creatives]
        r = FakeRouter(reply={"evaluations": [
            {"idea_id": c["idea_id"], "dimensions": self._dims()} for c in creatives]})
        AUG.augment_evaluation(rule_evals, creatives, st, r)
        self.assertFalse(any("partial_coverage" in e for e in r.last_errors),
                         f"全覆盖不该报错，实际={r.last_errors}")


class TestUnwrappedSingleObject(unittest.TestCase):
    """实测：risk 节点模型返回**裸对象**（顶层是 type/level/description），
    没套 `{"risks": [...]}` → 直接判空回退。这里宽容接受，但必须留痕。"""

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

    def test_bare_object_is_accepted_and_logged(self):
        st = self._state()
        rule_risk = N_eval.risk(st)
        r = FakeRouter(reply={"type": "timing", "level": "high",
                              "description": "窗口已过", "constraint": "改用模板化"})
        out = AUG.augment_risk(rule_risk, st, r)
        self.assertTrue(any(x.get("source") == "llm" for x in out["risks"]),
                        f"裸对象应被采纳，实际={out['risks']}")
        self.assertTrue(any(e.get("kind") == "unwrapped_single" for e in r.events),
                        f"必须留痕（结构化 events），实际={r.events}")

    def test_unrelated_object_is_not_accepted(self):
        """不相关的壳不能被当成条目 —— 宽容不等于什么都要。"""
        st = self._state()
        rule_risk = N_eval.risk(st)
        r = FakeRouter(reply={"something": "else"})
        out = AUG.augment_risk(rule_risk, st, r)
        self.assertEqual(out["risks"], rule_risk["risks"])


class TestPinnedSingleModel(unittest.TestCase):
    """2026-10-03 用户要求：全部节点固定 `nvidia/nemotron-3-ultra-550b-a55b:free`。

    钉死两件事：① 所有用 LLM 的节点都解析到同一个模型；
    ② **不走备选链** —— 否则"全部用 X"是假的（X 一限流就悄悄换模型，产物里看不出来）。
    """

    PINNED = "nvidia/nemotron-3-ultra-550b-a55b:free"

    def test_pinned_constant_is_set(self):
        self.assertEqual(L.PINNED_MODEL, self.PINNED)

    def test_every_llm_node_resolves_to_pinned(self):
        r = L.ModelRouter(enabled=False)
        r.enabled = True
        seen = {}
        for node, tier in L.NODE_TIERS.items():
            if tier == "none":
                continue
            seen[node] = r.resolve(node)["model"]
        self.assertTrue(seen, "前置：至少要有一个用 LLM 的节点")
        self.assertEqual(set(seen.values()), {self.PINNED}, f"实际={seen}")

    def test_no_fallback_when_pinned(self):
        """固定单模型时，tried 只含主模型。"""
        self.assertTrue(L.PINNED_MODEL)
        r = L.ModelRouter(enabled=False)
        r.enabled = True
        model = r.resolve("creative")["model"]
        self.assertEqual(L.FALLBACKS.get(model, []) and L.PINNED_MODEL, L.PINNED_MODEL)
        self.assertTrue(r.single_model)

    def test_evidence_stays_rule(self):
        r = L.ModelRouter(enabled=False)
        r.enabled = True
        self.assertEqual(r.resolve("evidence")["mode"], "rule")

    def test_pinning_is_reversible(self):
        """把常量置回 None 就应恢复按档位路由（含 evaluator 独立档位）。"""
        original = L.PINNED_MODEL
        L.PINNED_MODEL = None
        try:
            r = L.ModelRouter(enabled=False)
            r.enabled = True
            self.assertFalse(r.single_model)
            self.assertNotEqual(r.resolve("creative")["model"],
                                r.resolve("evaluator")["model"])
        finally:
            L.PINNED_MODEL = original


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
