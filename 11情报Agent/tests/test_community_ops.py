#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""community_ops 的单元测试：门控、降级、去重、PII、动作闭环。

重点锁死三件事：
  1. **门控**：只有「升温/爆发」才算可行动机会（冒头是噪声，不能混进行动项）
  2. **降级**：任一数据源缺失都要写明原因，绝不静默变成「无内容」
  3. **PII**：产出（报告/JSON）不得出现作者名等标识
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LAB = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(LAB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


co = _load("community_ops")


def write_json(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


def topic(key, title, state, metric, hid, kind="page_view", samples=1):
    return {"topic_key": key, "title": title, "hashtag_id": hid, "metric_kind": kind,
            "state": state, "last_metric": metric, "peak_metric": metric,
            "sample_count": samples, "first_seen_at": "x", "last_seen_at": "y",
            "prev_state": None, "state_changed_at": "z"}


def state_file(tmp: Path, by_state: dict) -> Path:
    return write_json(tmp / "topic_state.json", {
        "schema_version": "1.0", "generated_at": "2026-09-30T10:00:00+08:00",
        "n_topics": sum(len(v) for v in by_state.values()),
        "n_samples": sum(len(v) for v in by_state.values()),
        "by_state": by_state,
    })


def ferment_file(tmp: Path, topics: list) -> Path:
    return write_json(tmp / "ferment_judge.json", {
        "schema_version": "1.0", "mode": "rule", "degraded": True, "topics": topics})


def risk_file(tmp: Path, table: list, facts: dict | None = None) -> Path:
    return write_json(tmp / "risk_insight.json", {
        "schema_version": "1.0", "available": True,
        "facts": facts or {"n_total": 3000, "n_neg": 1108, "neg_rate_pp": 36.93,
                           "n_high_investment_negative": 375,
                           "high_investment_share_of_neg_pp": 33.84},
        "topic_risk_table": table})


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def patch(self, **kw):
        """把模块里的路径常量指向临时目录。"""
        return mock.patch.multiple(co, **kw)


# --------------------------------------------------------------------------- ① 话题机会

class TestTopicOpportunities(TmpCase):
    def test_missing_file_degrades(self):
        with self.patch(TOPIC_STATE=self.tmp / "none.json"):
            r = co.topic_opportunities()
        self.assertFalse(r["available"])
        self.assertIn("未生成", r["reason"])

    def test_broken_file_degrades(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{not-json", encoding="utf-8")
        with self.patch(TOPIC_STATE=bad):
            r = co.topic_opportunities()
        self.assertFalse(r["available"])
        self.assertIn("解析失败", r["reason"])

    def test_bursting_ranks_before_rising(self):
        """★ 排序：爆发比升温紧急，可发酵度缺失时也必须稳住这个次序。"""
        sf = state_file(self.tmp, {
            "冒头": [topic("k1", "甲", "冒头", 100, "1")],
            "升温": [topic("k2", "乙", "升温", 300, "2")],
            "爆发": [topic("k3", "丙", "爆发", 9000, "3")],
            "退潮": [topic("k4", "丁", "退潮", 50, "4")],
        })
        with self.patch(TOPIC_STATE=sf, FERMENT_JSON=self.tmp / "none.json"):
            r = co.topic_opportunities()
        self.assertEqual([t["title"] for t in r["actionable"]], ["丙", "乙"])
        self.assertNotIn("甲", [t["title"] for t in r["actionable"]])

    def test_watch_only_keeps_act_and_watch_verdicts(self):
        sf = state_file(self.tmp, {"冒头": [
            topic("k1", "值得看", "冒头", 100, "1"),
            topic("k2", "不值得", "冒头", 100, "2"),
        ]})
        ff = ferment_file(self.tmp, [
            {"hashtag_id": "1", "ferment_score": 60, "verdict": "act",
             "suggested_action": "建话题"},
            {"hashtag_id": "2", "ferment_score": 10, "verdict": "skip"},
        ])
        with self.patch(TOPIC_STATE=sf, FERMENT_JSON=ff):
            r = co.topic_opportunities()
        self.assertEqual([t["title"] for t in r["watch"]], ["值得看"])
        self.assertEqual(r["watch"][0]["suggested_action"], "建话题")

    def test_dedup_same_topic_across_metrics(self):
        """★ 同一话题的浏览量/互动量是两条序列，运营视角只该看到一条。"""
        sf = state_file(self.tmp, {"升温": [
            topic("hid:9|interaction", "三角洲", "升温", 38, "9", "interaction"),
            topic("hid:9|page_view", "三角洲", "升温", 6583, "9", "page_view"),
        ]})
        with self.patch(TOPIC_STATE=sf, FERMENT_JSON=self.tmp / "none.json"):
            r = co.topic_opportunities()
        self.assertEqual(len(r["actionable"]), 1, "同一话题应合并为一条")
        self.assertEqual(r["actionable"][0]["latest"], 6583, "应保留浏览量序列")
        self.assertEqual(r["actionable"][0]["_metric_kind"], "page_view")

    def test_ferment_missing_sets_note(self):
        sf = state_file(self.tmp, {"升温": [topic("k", "甲", "升温", 1, "1")]})
        with self.patch(TOPIC_STATE=sf, FERMENT_JSON=self.tmp / "none.json"):
            r = co.topic_opportunities()
        self.assertFalse(r["ferment_available"])
        self.assertIn("无法关联", r["ferment_note"])

    def test_limit_applied(self):
        sf = state_file(self.tmp, {"爆发": [topic(f"k{i}", f"T{i}", "爆发", i, str(i))
                                           for i in range(10)]})
        with self.patch(TOPIC_STATE=sf, FERMENT_JSON=self.tmp / "none.json"):
            r = co.topic_opportunities(limit=3)
        self.assertEqual(len(r["actionable"]), 3)


# --------------------------------------------------------------------------- ② 风险预警

class TestRiskAlerts(TmpCase):
    def test_missing_degrades(self):
        with self.patch(RISK_JSON=self.tmp / "none.json"):
            r = co.risk_alerts()
        self.assertFalse(r["available"])
        self.assertIn("未生成", r["reason"])

    def test_upstream_unavailable_propagates(self):
        f = write_json(self.tmp / "r.json", {"available": False, "reason": "上游标注不可用"})
        with self.patch(RISK_JSON=f):
            r = co.risk_alerts()
        self.assertFalse(r["available"])
        self.assertIn("上游", r["reason"])

    def test_neg_rate_gates_and_high_hours_marks(self):
        """★ 负向率是闸门，高投入是优先级标记（不是闸门）。

        理由：「高负向但高投入少」仍可能是真问题，筛掉会漏；
        而「高投入玩家也在骂」是最该先处理的信号 —— 所以标出来，不筛掉。
        """
        f = risk_file(self.tmp, [
            {"topic_cn": "高负向高投入", "neg_rate_pp": 66.2, "neg_high_hours_share_pp": 86.4,
             "n_neg": 49, "actionable_share_pp": 100.0, "intervention": "数值说明"},
            {"topic_cn": "高负向低投入", "neg_rate_pp": 60.0, "neg_high_hours_share_pp": 30.0,
             "n_neg": 20, "intervention": "常规监测"},
            {"topic_cn": "低负向", "neg_rate_pp": 20.0, "neg_high_hours_share_pp": 90.0,
             "n_neg": 5, "intervention": "—"},
        ])
        with self.patch(RISK_JSON=f):
            r = co.risk_alerts()
        names = [a["topic"] for a in r["alerts"]]
        self.assertEqual(names, ["高负向高投入", "高负向低投入"], "低负向应被闸门滤掉")
        by = {a["topic"]: a for a in r["alerts"]}
        self.assertTrue(by["高负向高投入"]["high_stakes"])
        self.assertFalse(by["高负向低投入"]["high_stakes"], "高投入占比低的应标为非高优先")
        self.assertEqual(by["高负向高投入"]["intervention"], "数值说明")

    def test_threshold_exposed_for_audit(self):
        f = risk_file(self.tmp, [])
        with self.patch(RISK_JSON=f):
            r = co.risk_alerts()
        self.assertIn("neg_rate_min_pp", r["threshold"])
        self.assertIn("high_hours_min_pp", r["threshold"])


# --------------------------------------------------------------------------- ③ 内容候选

class TestContentPicks(TmpCase):
    def _csvs(self, posts, comments):
        pd = self.tmp / "posts.csv"
        cd = self.tmp / "comments.csv"
        pd.write_text(
            "moment_id,title,summary,ups,comments,pv_total,app_title,author_name\n"
            + "\n".join(posts), encoding="utf-8")
        cd.write_text(
            "moment_id,comment_id,content,supports,author_name\n"
            + "\n".join(comments), encoding="utf-8")
        return pd, cd

    def test_missing_data_degrades(self):
        with self.patch(POSTS_CSV=self.tmp / "n1.csv", COMMENTS_CSV=self.tmp / "n2.csv"):
            r = co.content_picks()
        self.assertFalse(r["available"])
        self.assertIn("发现流数据", r["reason"])

    def test_sorted_by_likes_and_zero_filtered(self):
        pd, cd = self._csvs(
            ["1,低赞帖,x,1,0,10,方舟,某人",
             "2,高赞帖,y,311,5,999,方舟,某人",
             "3,无赞帖,z,0,0,5,方舟,某人"],
            ["1,101,普通评论,0,某人",
             "1,102,好评论,10,某人"],
        )
        with self.patch(POSTS_CSV=pd, COMMENTS_CSV=cd):
            r = co.content_picks(limit=5)
        self.assertEqual([p["text"] for p in r["posts"]], ["高赞帖", "低赞帖"])
        self.assertEqual([c["likes"] for c in r["comments"]], [10])

    def test_post_likes_read_from_ups_not_supports(self):
        """★ 踩过的坑：帖子点赞在 ups，评论在 supports，命名不一致。"""
        pd, cd = self._csvs(["1,标题,x,250,0,10,方舟,某人"], [])
        with self.patch(POSTS_CSV=pd, COMMENTS_CSV=cd):
            r = co.content_picks()
        self.assertEqual(r["posts"][0]["likes"], 250)

    def test_url_built_from_moment_id(self):
        pd, cd = self._csvs(["853956790541355163,标题,x,5,0,10,方舟,某人"], [])
        with self.patch(POSTS_CSV=pd, COMMENTS_CSV=cd):
            r = co.content_picks()
        self.assertEqual(r["posts"][0]["url"],
                         "https://www.taptap.cn/moment/853956790541355163")

    def test_comment_links_back_to_host_post(self):
        pd, cd = self._csvs(["777,主帖标题,x,1,0,10,方舟,某人"],
                            ["777,9,评论内容,3,某人"])
        with self.patch(POSTS_CSV=pd, COMMENTS_CSV=cd):
            r = co.content_picks()
        self.assertEqual(r["comments"][0]["host_title"], "主帖标题")
        self.assertIn("/moment/777", r["comments"][0]["url"])

    def test_candidates_not_claimed_as_classified(self):
        """未做 LLM 仲裁，就不能声称已分好类（金句/段子）。"""
        pd, cd = self._csvs(["1,标题,x,5,0,10,方舟,某人"], [])
        with self.patch(POSTS_CSV=pd, COMMENTS_CSV=cd):
            r = co.content_picks()
        self.assertIn("非已分类素材", r["note"])


# --------------------------------------------------------------------------- ④ 动作闭环

class TestActionLoop(TmpCase):
    def test_record_and_list(self):
        db = self.tmp / "ops.sqlite3"
        co.record_action("hid:1|page_view", "follow", title="甲", state="爆发", db_path=db)
        rows = co.list_actions(db_path=db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "follow")
        self.assertEqual(rows[0]["state"], "爆发")

    def test_invalid_action_rejected(self):
        with self.assertRaises(ValueError):
            co.record_action("k", "chaos", db_path=self.tmp / "o.sqlite3")

    def test_same_state_not_pushed_twice(self):
        """★ 去重：同话题同状态只推一次，否则第一周就会失去所有使用者。"""
        db = self.tmp / "ops.sqlite3"
        self.assertTrue(co.should_push("k1", "爆发", db_path=db)["should"])
        self.assertTrue(co.mark_pushed("k1", "爆发", db_path=db))
        self.assertFalse(co.mark_pushed("k1", "爆发", db_path=db), "第二次应为已推过")
        self.assertFalse(co.should_push("k1", "爆发", db_path=db)["should"])

    def test_state_upgrade_can_push_again(self):
        db = self.tmp / "ops.sqlite3"
        co.mark_pushed("k1", "升温", db_path=db)
        self.assertTrue(co.should_push("k1", "爆发", db_path=db)["should"],
                        "状态升级应允许再推")

    def test_separate_topics_independent(self):
        db = self.tmp / "ops.sqlite3"
        co.mark_pushed("k1", "爆发", db_path=db)
        self.assertTrue(co.should_push("k2", "爆发", db_path=db)["should"])

    def test_record_then_should_push_unaffected(self):
        db = self.tmp / "ops.sqlite3"
        co.record_action("k1", "ignore", db_path=db)
        self.assertTrue(co.should_push("k1", "爆发", db_path=db)["should"],
                        "动作记录与推送去重是两件事")


# --------------------------------------------------------------------------- 渲染与 PII

class TestRenderAndPrivacy(TmpCase):
    def _reports(self, author="某真实作者名"):
        sf = state_file(self.tmp, {"爆发": [topic("hid:1|page_view", "话题甲", "爆发", 100, "1")]})
        rf = risk_file(self.tmp, [{"topic_cn": "数值", "neg_rate_pp": 66.2,
                                   "neg_high_hours_share_pp": 86.4, "n_neg": 49,
                                   "intervention": "说明"}])
        pd = self.tmp / "p.csv"
        cd = self.tmp / "c.csv"
        pd.write_text("moment_id,title,summary,ups,comments,pv_total,app_title,author_name\n"
                      f"1,标题,摘要,9,0,10,方舟,{author}\n", encoding="utf-8")
        cd.write_text("moment_id,comment_id,content,supports,author_name\n"
                      f"1,9,评论内容,3,{author}\n", encoding="utf-8")
        with self.patch(TOPIC_STATE=sf, FERMENT_JSON=self.tmp / "none.json",
                        RISK_JSON=rf, POSTS_CSV=pd, COMMENTS_CSV=cd):
            return co.topic_opportunities(), co.risk_alerts(), co.content_picks()

    def test_report_never_leaks_author_name(self):
        """★ PII：产出不得出现作者名（溯源靠链接，不靠名字）。"""
        opp, risk, content = self._reports(author="某个不该出现的昵称")
        report = co.render_report(opp, risk, content, [])
        self.assertNotIn("某个不该出现的昵称", report)
        self.assertNotIn("author_name", report)

    def test_json_payload_never_leaks_author_name(self):
        opp, risk, content = self._reports(author="另一个不该出现的昵称")
        blob = json.dumps({"o": opp, "r": risk, "c": content}, ensure_ascii=False)
        self.assertNotIn("另一个不该出现的昵称", blob)

    def test_report_states_thresholds_uncalibrated(self):
        opp, risk, content = self._reports()
        report = co.render_report(opp, risk, content, [])
        self.assertIn("未经真实运营反馈校准", report)

    def test_report_marks_candidates_as_unclassified(self):
        opp, risk, content = self._reports()
        report = co.render_report(opp, risk, content, [])
        self.assertIn("未做「金句/段子」分类", report)

    def test_report_explains_first_day_empty(self):
        sf = state_file(self.tmp, {"冒头": [topic("k", "甲", "冒头", 1, "1")]})
        with self.patch(TOPIC_STATE=sf, FERMENT_JSON=self.tmp / "none.json"):
            opp = co.topic_opportunities()
        report = co.render_report(opp, {"available": False, "reason": "x"},
                                  {"available": False, "reason": "y"}, [])
        self.assertIn("首日全为「冒头」属正常", report)

    def test_unavailable_sections_say_so(self):
        report = co.render_report({"available": False, "reason": "话题库缺失"},
                                  {"available": False, "reason": "风险库缺失"},
                                  {"available": False, "reason": "内容库缺失"}, [])
        for s in ("话题库缺失", "风险库缺失", "内容库缺失"):
            self.assertIn(s, report)
        self.assertIn("显式降级", report)

    def test_actions_rendered_when_present(self):
        report = co.render_report({"available": False, "reason": "x"},
                                  {"available": False, "reason": "x"},
                                  {"available": False, "reason": "x"},
                                  [{"acted_at": "2026-09-30T15:00:00+08:00",
                                    "topic_key": "k", "title": "甲", "action": "follow",
                                    "actor_role": "运营", "note": "已建话题"}])
        self.assertIn("已建话题", report)
        self.assertIn("校准阈值", report)


if __name__ == "__main__":
    unittest.main()
