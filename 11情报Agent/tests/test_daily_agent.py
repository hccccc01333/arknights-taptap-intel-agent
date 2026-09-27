#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""daily_agent 的单元测试：异常分类、决策路由、降级路径、简报渲染。"""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock

LAB = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(LAB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


da = _load("daily_agent")


def make_facts(significant: bool = False, delta_pp: float = 0.0, available: bool = True):
    return {
        "generated_at": "2026-09-27T23:00:00+08:00",
        "risk": {
            "headline": "测试 headline",
            "n_total": 3000,
            "n_neg": 1108,
            "neg_rate_pp": 36.93,
            "n_high_investment_negative": 375,
            "high_investment_share_of_neg_pp": 33.84,
            "n_rhetoric_disguised": 154,
            "n_actionable_negative": 981,
            "top_topics": [
                {"topic": "数值/平衡/养成", "neg_rate_pp": 66.22,
                 "high_hours_share_pp": 86.36, "actionable_share_pp": 100.0}
            ],
            "support_dimension": "不可用（本切片 support_count 全 0）",
        },
        "anomaly": {
            "available": available,
            "reason": None if available else "分析依赖未安装，已自动降级为常规监测模式",
            "headline": "测试异动",
            "delta_pp": delta_pp,
            "p_value": 0.0097 if significant else 0.5,
            "significant": significant,
            "verdict": "负向率相对对照显著上升（α=0.05）" if significant else "变化未达统计显著",
            "top_contributors": [
                {"topic": "关卡/玩法", "total_pp": 17.95, "neg_delta": 4,
                 "kind": "topic_delta"}
            ] if significant else [],
        },
    }


class TestFriendlyReason(unittest.TestCase):
    def test_module_not_found(self):
        self.assertIn("依赖未安装", da.friendly_reason(ModuleNotFoundError("No module named 'pandas'")))

    def test_runtime_error_wrapping_import(self):
        e = RuntimeError("anomaly_diagnosis.py 运行失败:\nModuleNotFoundError: No module named 'pandas'")
        self.assertIn("依赖未安装", da.friendly_reason(e))

    def test_timeout(self):
        e = subprocess.TimeoutExpired(cmd="x", timeout=300)
        self.assertIn("超时", da.friendly_reason(e))

    def test_file_not_found(self):
        self.assertIn("脚本缺失", da.friendly_reason(FileNotFoundError("tool 不存在")))

    def test_generic(self):
        self.assertIn("运行异常", da.friendly_reason(ValueError("boom")))

    def test_no_traceback_leak(self):
        e = RuntimeError("失败:\nTraceback (most recent call last):\n  File ...")
        reason = da.friendly_reason(e)
        self.assertNotIn("Traceback", reason)
        self.assertNotIn("File", reason)


class TestRuleDecision(unittest.TestCase):
    def test_significant_increase_triggers_deep_dive(self):
        d = da.rule_decision(make_facts(significant=True, delta_pp=19.22))
        self.assertEqual(d["mode"], "deep_dive")
        self.assertEqual(d["decider"], "rule")
        self.assertIn("显著上升", d["rationale"])

    def test_not_significant_routine(self):
        d = da.rule_decision(make_facts(significant=False, delta_pp=2.0))
        self.assertEqual(d["mode"], "routine")

    def test_significant_decrease_routine(self):
        # 显著下降不是风险信号，维持常规
        d = da.rule_decision(make_facts(significant=True, delta_pp=-8.0))
        self.assertEqual(d["mode"], "routine")

    def test_anomaly_unavailable_routine(self):
        d = da.rule_decision(make_facts(available=False))
        self.assertEqual(d["mode"], "routine")
        self.assertIn("降级", d["rationale"])


class TestLlmDecisionFallback(unittest.TestCase):
    def test_network_error_returns_none(self):
        with mock.patch.object(da.urllib.request, "urlopen", side_effect=OSError("net down")):
            self.assertIsNone(da.llm_decision(make_facts(), "sk-test"))

    def test_bad_json_returns_none(self):
        resp = mock.MagicMock()
        resp.read.return_value = b'{"choices":[{"message":{"content":"not-json"}}]}'
        resp.__enter__ = mock.MagicMock(return_value=resp)
        resp.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch.object(da.urllib.request, "urlopen", return_value=resp):
            self.assertIsNone(da.llm_decision(make_facts(), "sk-test"))

    def test_invalid_mode_returns_none(self):
        resp = mock.MagicMock()
        resp.read.return_value = b'{"choices":[{"message":{"content":"{\\"mode\\": \\"chaos\\"}"}}]}'
        resp.__enter__ = mock.MagicMock(return_value=resp)
        resp.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch.object(da.urllib.request, "urlopen", return_value=resp):
            self.assertIsNone(da.llm_decision(make_facts(), "sk-test"))


class TestBriefRendering(unittest.TestCase):
    def test_brief_has_locked_facts_and_no_traceback(self):
        facts = make_facts()
        decision = da.rule_decision(facts)
        action = da.act(facts, decision)
        brief = da.render_brief(action, "定性文本")
        self.assertIn("锁数事实", brief)
        self.assertNotIn("Traceback", brief)
        self.assertNotIn("ModuleNotFoundError", brief)
        self.assertIn("边界", brief)

    def test_deep_dive_brief_has_sections(self):
        facts = make_facts(significant=True, delta_pp=19.22)
        decision = da.rule_decision(facts)
        action = da.act(facts, decision)
        brief = da.render_brief(action, "定性文本")
        self.assertIn("深挖", brief)
        self.assertIn("关卡/玩法", brief)
        self.assertIn("高投入负向代表样本", brief)

    def test_qualitative_template_fills_facts_numbers(self):
        facts = make_facts()
        decision = da.rule_decision(facts)
        text = da.qualitative_text(facts, decision, api_key=None)
        self.assertIn("36.93", text)
        self.assertIn("375", text)

    def test_qualitative_ignores_llm_when_no_key(self):
        # 无 key 时即使 decider=llm 也走模板
        facts = make_facts()
        decision = da.rule_decision(facts)
        decision["decider"] = "llm"
        text = da.qualitative_text(facts, decision, api_key=None)
        self.assertIn("36.93", text)


if __name__ == "__main__":
    unittest.main()
