#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""治理层测试（§17-§20 / §22-§23 / §37-§39 / §53-§55）。

钉死的纪律：
  ① LLM 推断绝不自动进长期记忆（§19/§53 知识污染防护）
  ② PII 绝不入记忆（§55）—— 记忆会被检索进 Agent Context
  ③ 未登记的 caller 默认拒绝（§54）
  ④ 时效失效的知识不进候选（§22），冲突时听最新 + 权威 + 仍有效的（§38）
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_L5 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L5)
for _p in (_ROOT, _L5):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from memory import governance as G  # noqa: E402


class TestWritePolicy(unittest.TestCase):
    def test_llm_inference_never_long_term(self):
        route = G.write_route("llm_inference")
        self.assertEqual(route["horizon"], "short_term")
        self.assertEqual(route["tier"], "agent_generated")

    def test_experiment_and_human_approval_are_long_term_verified(self):
        self.assertEqual(G.write_route("experiment_result")["horizon"], "long_term")
        self.assertEqual(G.write_route("experiment_result")["tier"], "verified")
        self.assertEqual(G.write_route("human_approved_creative")["tier"], "verified")
        self.assertEqual(G.write_route("human_decision")["tier"], "verified")

    def test_event_lifecycle_ended_is_candidate_not_verified(self):
        # §19：生命周期结束 → 长期，但没有人工确认 → candidate，不是 verified
        route = G.write_route("event_lifecycle_ended")
        self.assertEqual((route["horizon"], route["tier"]), ("long_term", "candidate"))

    def test_unknown_kind_is_most_conservative(self):
        route = G.write_route("something_new")
        self.assertEqual(route["horizon"], "short_term")
        self.assertEqual(route["tier"], "agent_generated")
        self.assertIn("保守", route["reason"])


class TestPII(unittest.TestCase):
    def test_raw_user_id_blocked(self):
        bad = G.scan_pii({"experiment_name": "x", "user_id": "12345678"})
        self.assertEqual(len(bad), 1)
        self.assertIn("user_id", bad[0])

    def test_hashed_user_id_allowed(self):
        self.assertEqual(G.scan_pii({"user_id": "a" * 32}), [])

    def test_nested_and_list_detection(self):
        bad = G.scan_pii({"ctx": {"audience": [{"nickname": "张三"}]}})
        self.assertTrue(any("nickname" in b for b in bad))

    def test_aggregate_fields_allowed(self):
        self.assertEqual(G.scan_pii({"sample_size": 5000, "baseline_value": 0.1}), [])


class TestTemporalAndConflict(unittest.TestCase):
    def test_valid_window(self):
        row = {"valid_from": "2026-01-01T00:00:00+08:00",
               "valid_to": "2026-12-31T00:00:00+08:00"}
        self.assertTrue(G.is_currently_valid(row, at="2026-06-01T00:00:00+08:00"))
        self.assertFalse(G.is_currently_valid(row, at="2027-06-01T00:00:00+08:00"))

    def test_open_ended_valid(self):
        self.assertTrue(G.is_currently_valid({"valid_from": None, "valid_to": None},
                                             at="2026-06-01T00:00:00+08:00"))

    def test_conflict_resolution_prefers_current_then_authority(self):
        rows = [
            {"authority": "P1", "valid_to": "2020-01-01", "updated_at": "2026-01-01"},  # 已失效
            {"authority": "P4", "valid_to": None, "updated_at": "2026-06-01"},          # 新但弱
            {"authority": "P1", "valid_to": None, "updated_at": "2025-01-01"},          # 旧但权威
        ]
        out = G.resolve_conflicts(rows, at="2026-06-01T00:00:00+08:00")
        # 仍有效者排前；同为有效 → 权威高者胜（P1 旧版胜过 P4 新版，§38/§39）
        self.assertEqual(out[0]["authority"], "P1")
        self.assertEqual(out[0]["updated_at"], "2025-01-01")
        self.assertEqual(out[-1]["valid_to"], "2020-01-01")   # 已失效的排最后

    def test_version_increments(self):
        self.assertEqual(G.next_version(None), 1)
        self.assertEqual(G.next_version({"version": 3}), 4)


class TestReasonTaxonomy(unittest.TestCase):
    def test_mapping(self):
        cases = {
            "上线太晚了，窗口过了": "TOO_LATE",
            "预算不够，太贵": "TOO_EXPENSIVE",
            "和上月撞车了": "DUPLICATE_IDEA",
            "品牌上有风险": "BRAND_RISK",
        }
        for text, code in cases.items():
            self.assertEqual(G.normalize_reason_code(text), code)

    def test_unmatched_is_other(self):
        self.assertEqual(G.normalize_reason_code("就是不喜欢"), "OTHER")


class TestTrustDecayAccess(unittest.TestCase):
    def test_trust_ordering(self):
        verified = G.trust_score({"authority": "P0", "tier": "verified",
                                  "human_verified": 1})
        agent = G.trust_score({"authority": "P4", "tier": "agent_generated",
                               "human_verified": 0})
        self.assertGreater(verified, agent)

    def test_decay_by_type(self):
        import datetime
        now = datetime.datetime.now().astimezone()
        old = (now - datetime.timedelta(days=180)).isoformat()
        fresh = now.isoformat()
        # 热点规律衰减快（§37）；实体/业务知识不衰减
        self.assertGreater(G.recency_weight(fresh, "trend", now),
                           G.recency_weight(old, "trend", now))
        self.assertEqual(G.recency_weight(old, "entity", now), 1.0)

    def test_access_default_deny(self):
        self.assertFalse(G.access_allowed("unknown_agent", "case"))
        self.assertTrue(G.access_allowed("creative_agent", "case"))
        self.assertFalse(G.access_allowed("creative_agent", "decision"))
        self.assertTrue(G.access_allowed("human", "decision"))


if __name__ == "__main__":
    unittest.main()
