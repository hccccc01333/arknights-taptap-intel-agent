#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""daily_agent 的单元测试：异常分类、决策路由、降级路径、简报渲染。"""

from __future__ import annotations

import importlib.util
import inspect
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


class TestPlatformFacts(unittest.TestCase):
    """平台侧为可选维度：缺失/损坏都要显式降级，且不能污染主链决策。"""

    def test_missing_file_degrades_explicitly(self):
        with mock.patch.object(da, "PLATFORM_JSON", Path("/nonexistent/platform_insight.json")):
            pf = da.load_platform()
        self.assertFalse(pf["available"])
        self.assertIn("未生成", pf["reason"])

    def test_corrupted_json_degrades_explicitly(self, ):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{not-json")
            tmp = Path(f.name)
        try:
            with mock.patch.object(da, "PLATFORM_JSON", tmp):
                pf = da.load_platform()
            self.assertFalse(pf["available"])
            self.assertIn("降级", pf["reason"])
        finally:
            tmp.unlink()

    def test_rule_decision_unaffected_without_platform(self):
        # make_facts 不带 platform（老 facts 形态）：决策逻辑必须照旧
        d = da.rule_decision(make_facts(significant=True, delta_pp=19.22))
        self.assertEqual(d["mode"], "deep_dive")

    def test_brief_renders_platform_section_when_available(self):
        facts = make_facts()
        facts["platform"] = {
            "available": True,
            "generated_at": "2026-09-29T12:39:00+08:00",
            "stale": False,
            "inputs": {"hot_hashtags": 10, "posts": 118, "posts_discover": 84,
                       "posts_hashtag": 34, "loaded_comments": 74},
            "top_signals": [{"kind": "hot_topic", "title": "三角洲行动二周年",
                             "score": 6270, "evidence": "热榜第 1 名 / 浏览 6270",
                             "matched_games": []}],
            "tracked_game_hits": [],
        }
        decision = da.rule_decision(facts)
        brief = da.render_brief(da.act(facts, decision), "定性文本")
        self.assertIn("平台侧发现流", brief)
        self.assertIn("三角洲行动二周年", brief)
        self.assertIn("本次快照无平台事件命中已建档游戏", brief)

    def test_brief_marks_stale_snapshot(self):
        facts = make_facts()
        facts["platform"] = {
            "available": True, "generated_at": "2026-01-01T00:00:00+08:00", "stale": True,
            "inputs": {}, "top_signals": [], "tracked_game_hits": [],
        }
        brief = da.render_brief(da.act(facts, da.rule_decision(facts)), "定性文本")
        self.assertIn("已过期", brief)


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


class TestTopicTrendFacts(unittest.TestCase):
    """话题追踪（跨天）作为 facts 可选维度：缺失显式降级，有则渲染表格。

    这一维度回答「在升温还是退潮」，是「热点需要追踪」的落地点。
    测试要锁死两件事：(1) 状态库缺失绝不静默变成「无趋势」；
    (2) 简报里必须带阈值未校准的警示——阈值是拍的，不能当可信参数。
    """

    def _make_db(self, rows):
        """建一个最小 topic_state 库，返回临时路径。"""
        import sqlite3
        import tempfile

        tmpdir = tempfile.mkdtemp()
        db = Path(tmpdir) / "topic.sqlite3"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE topic_state ("
            " topic_key TEXT PRIMARY KEY, title TEXT, hashtag_id TEXT,"
            " metric_kind TEXT, first_seen_at TEXT, last_seen_at TEXT,"
            " state TEXT, last_metric INTEGER, peak_metric INTEGER,"
            " sample_count INTEGER, prev_state TEXT, state_changed_at TEXT)"
        )
        for r in rows:
            con.execute(
                "INSERT INTO topic_state (topic_key, title, hashtag_id, metric_kind,"
                " first_seen_at, last_seen_at, state, last_metric, peak_metric,"
                " sample_count, prev_state, state_changed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                r,
            )
        con.commit()
        con.close()
        return db

    @staticmethod
    def _row(key, title, kind, state, last, peak, samples=3):
        return (key, title, "", kind, "2026-09-27T10:00:00+08:00",
                "2026-09-29T10:00:00+08:00", state, last, peak, samples, "冒头",
                "2026-09-29T10:00:00+08:00")

    def test_missing_db_degrades_explicitly(self):
        with mock.patch.object(da, "TOPIC_DB", Path("/nonexistent/topic.sqlite3")):
            tt = da.load_topic_state()
        self.assertFalse(tt["available"])
        self.assertIn("尚无话题追踪状态", tt["reason"])

    def test_broken_db_degrades_not_raises(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".sqlite3", delete=False) as f:
            f.write("this is not a sqlite file")
            bad = Path(f.name)
        try:
            with mock.patch.object(da, "TOPIC_DB", bad):
                tt = da.load_topic_state()
            self.assertFalse(tt["available"])
            self.assertIn("状态库读取失败", tt["reason"])
        finally:
            # Windows 上 sqlite 可能仍持有句柄；清理失败不应让测试失败
            try:
                bad.unlink()
            except (PermissionError, OSError):
                pass

    def test_returns_only_rising_and_bursting(self):
        db = self._make_db([
            self._row("tit:甲|page_view", "甲", "page_view", "爆发", 9000, 9000),
            self._row("tit:乙|page_view", "乙", "page_view", "升温", 300, 300),
            self._row("tit:丙|page_view", "丙", "page_view", "冒头", 100, 100),
            self._row("tit:丁|page_view", "丁", "page_view", "退潮", 50, 900),
        ])
        with mock.patch.object(da, "TOPIC_DB", db):
            tt = da.load_topic_state()
        self.assertTrue(tt["available"])
        self.assertEqual(tt["n_tracked"], 4)
        titles = [r["title"] for r in tt["rising"]]
        self.assertEqual(titles, ["甲", "乙"])  # 爆发排前，冒头/退潮不入选

    def test_metric_kind_preserved_in_facts(self):
        db = self._make_db([
            self._row("hid:5|page_view", "三角洲行动二周年", "page_view", "爆发", 6583, 6583),
            self._row("hid:5|interaction", "三角洲行动二周年", "interaction", "爆发", 39, 39),
        ])
        with mock.patch.object(da, "TOPIC_DB", db):
            tt = da.load_topic_state()
        kinds = sorted(r["metric_kind"] for r in tt["rising"])
        self.assertEqual(kinds, ["interaction", "page_view"])

    def test_limit_caps_rows(self):
        db = self._make_db([
            self._row(f"tit:{i}|page_view", f"话题{i}", "page_view", "爆发", 1000 - i, 1000)
            for i in range(8)
        ])
        with mock.patch.object(da, "TOPIC_DB", db):
            tt = da.load_topic_state(limit=3)
        self.assertEqual(len(tt["rising"]), 3)

    def test_brief_renders_trend_section_with_warning(self):
        facts = make_facts()
        facts["topic_trend"] = {
            "available": True,
            "n_tracked": 14,
            "rising": [{
                "title": "米哈游反舞弊通报", "metric_kind": "page_view",
                "state": "爆发", "latest": 38520, "peak": 38520,
                "samples": 2, "first_seen_at": "2026-09-27T10:00:00+08:00",
            }],
        }
        brief = da.render_brief(da.act(facts, da.rule_decision(facts)), "定性文本")
        self.assertIn("话题趋势（跨天追踪）", brief)
        self.assertIn("米哈游反舞弊通报", brief)
        self.assertIn("浏览量", brief)          # metric_kind 已中文化
        self.assertIn("变化率", brief)          # 说明判据是变化率
        self.assertIn("未经真实运营反馈校准", brief)  # 阈值警示必须在

    def test_brief_explains_first_day_when_no_rising(self):
        facts = make_facts()
        facts["topic_trend"] = {"available": True, "n_tracked": 10, "rising": []}
        brief = da.render_brief(da.act(facts, da.rule_decision(facts)), "定性文本")
        self.assertIn("话题趋势（跨天追踪）", brief)
        self.assertIn("首日全部为「冒头」属正常", brief)

    def test_brief_marks_unavailable_not_silent(self):
        facts = make_facts()
        facts["topic_trend"] = {"available": False, "reason": "尚无话题追踪状态"}
        brief = da.render_brief(da.act(facts, da.rule_decision(facts)), "定性文本")
        self.assertIn("话题趋势（跨天追踪）", brief)
        self.assertIn("不可用", brief)
        self.assertIn("尚无话题追踪状态", brief)

    def test_missing_key_treated_as_unavailable(self):
        # 老 facts（无 topic_trend 键）不能让渲染崩掉
        facts = make_facts()
        self.assertNotIn("topic_trend", facts)
        brief = da.render_brief(da.act(facts, da.rule_decision(facts)), "定性文本")
        self.assertIn("话题趋势（跨天追踪）", brief)
        self.assertIn("不可用", brief)

    def test_topic_trend_does_not_change_decision(self):
        # 追踪维度是「信息」不是「决策项」：不应干扰规则决策
        facts = make_facts(significant=True, delta_pp=19.22)
        facts["topic_trend"] = {"available": True, "n_tracked": 3, "rising": []}
        self.assertEqual(da.rule_decision(facts)["mode"], "deep_dive")


class TestModelRouting(unittest.TestCase):
    """DeepSeek 模型分层路由 + 退役模型名回归锁。

    背景：deepseek-chat / deepseek-reasoner 已于 2026-07-24 退役且无静默回退，
    旧代码里写的正是 deepseek-chat，调用必失败。这里锁死不让它回来。
    """

    def setUp(self):
        self.src = (Path(__file__).resolve().parents[1] / "daily_agent.py").read_text(
            encoding="utf-8"
        )

    def test_no_retired_model_names_in_code(self):
        # 去掉注释行后再断言，避免注释里的警示文本误伤
        code_lines = [
            ln for ln in self.src.splitlines() if not ln.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        for retired in ("deepseek-chat", "deepseek-reasoner"):
            self.assertNotIn(retired, code, f"退役模型名再次出现: {retired}")

    def test_flash_and_pro_constants_exist(self):
        self.assertTrue(da.MODEL_FLASH)
        self.assertTrue(da.MODEL_PRO)
        self.assertNotEqual(da.MODEL_FLASH, da.MODEL_PRO)

    def test_discriminative_tasks_use_flash(self):
        # 决策（判断）与简报执笔（复述 facts）走 Flash，不是 Pro
        llm_src = inspect.getsource(da.llm_decision)
        self.assertIn("MODEL_FLASH", llm_src)
        self.assertNotIn("MODEL_PRO", llm_src)
        qual_src = inspect.getsource(da.qualitative_text)
        self.assertIn("MODEL_FLASH", qual_src)


if __name__ == "__main__":
    unittest.main()
