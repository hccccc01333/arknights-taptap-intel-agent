#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""announcements 的单元测试：宽容提取、schema 校验、重试降级、不丢原文。

重点锁死三件事：
  1. **JSON 坏了也不能抛异常** —— 只通过 parse_status 表达结果
  2. **解析失败不丢原文** —— raw_text 永远保留，可事后补解析
  3. **不许编造时间** —— scheduled_at 可空，但给了就必须是合法 ISO
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(LAB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


an = _load("announcements")

GOOD_EVENT = {"event_type": "version_update", "title": "3.2 版本更新",
              "scheduled_at": "2026-10-15T10:00:00+08:00", "confidence": 0.9,
              "evidence": "10月15日 10:00 版本更新"}
GOOD_JSON = json.dumps({"events": [GOOD_EVENT]}, ensure_ascii=False)


class TestExtractJson(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(an.extract_json(GOOD_JSON)["events"][0]["title"], "3.2 版本更新")

    def test_fenced_code_block(self):
        """★ 最常见：模型把 JSON 裹在 ```json 里。"""
        self.assertIsNotNone(an.extract_json(f"```json\n{GOOD_JSON}\n```"))

    def test_fenced_without_language(self):
        self.assertIsNotNone(an.extract_json(f"```\n{GOOD_JSON}\n```"))

    def test_with_leading_chatter(self):
        """★ 模型爱加前言：「好的，以下是解析结果：{...}」"""
        self.assertIsNotNone(an.extract_json(f"好的，以下是解析结果：\n{GOOD_JSON}"))

    def test_with_trailing_chatter(self):
        self.assertIsNotNone(an.extract_json(f"{GOOD_JSON}\n\n希望有帮助！"))

    def test_truncated_returns_none(self):
        self.assertIsNone(an.extract_json('{"events": [{"title": "未闭'))

    def test_pure_text_returns_none(self):
        self.assertIsNone(an.extract_json("抱歉，我无法解析这段文本。"))

    def test_empty_and_none(self):
        self.assertIsNone(an.extract_json(""))
        self.assertIsNone(an.extract_json(None))

    def test_array_wrapper_extracts_inner_object(self):
        """模型偶尔把对象裹在数组里 —— 宽容抠出内层对象，交给 schema 校验判定。

        （不在这里判生死：抠出来若不合 schema，validate_parsed 会拒。）
        """
        got = an.extract_json('[{"events": []}]')
        self.assertIsInstance(got, dict)

    def test_never_returns_non_dict(self):
        """★ 不变量：绝不返回非 dict（下游只处理对象）。"""
        for bad in ['["a","b"]', '"just a string"', '123', 'null', 'true']:
            with self.subTest(case=bad):
                self.assertIsNone(an.extract_json(bad))


class TestValidate(unittest.TestCase):
    def test_good_passes(self):
        self.assertEqual(an.validate_parsed({"events": [GOOD_EVENT]}), [])

    def test_empty_events_ok(self):
        """没有排期事件是合法结果（原文确实没写时间）。"""
        self.assertEqual(an.validate_parsed({"events": []}), [])

    def test_missing_events_field(self):
        errs = an.validate_parsed({"foo": 1})
        self.assertTrue(any("events" in e for e in errs))

    def test_events_not_list(self):
        self.assertTrue(an.validate_parsed({"events": "x"}))

    def test_missing_required_event_field(self):
        errs = an.validate_parsed({"events": [{"event_type": "event"}]})
        self.assertTrue(any("title" in e for e in errs))

    def test_bad_enum_rejected(self):
        ev = dict(GOOD_EVENT, event_type="随便编的")
        errs = an.validate_parsed({"events": [ev]})
        self.assertTrue(any("不在允许值" in e for e in errs))

    def test_bad_datetime_rejected(self):
        ev = dict(GOOD_EVENT, scheduled_at="下个月中旬")
        errs = an.validate_parsed({"events": [ev]})
        self.assertTrue(any("ISO" in e for e in errs))

    def test_null_datetime_allowed(self):
        """★ 原文没写时间就填 null —— 这是合法的，不许逼模型编一个。"""
        ev = dict(GOOD_EVENT, scheduled_at=None)
        self.assertEqual(an.validate_parsed({"events": [ev]}), [])

    def test_confidence_out_of_range(self):
        for bad in (-0.1, 1.5):
            ev = dict(GOOD_EVENT, confidence=bad)
            self.assertTrue(an.validate_parsed({"events": [ev]}),
                            f"confidence={bad} 应被拒")

    def test_confidence_int_allowed(self):
        ev = dict(GOOD_EVENT, confidence=1)
        self.assertEqual(an.validate_parsed({"events": [ev]}), [])

    def test_errors_are_specific(self):
        """★ 错误信息必须具体 —— 否则重试时没法让模型改对。"""
        errs = an.validate_parsed({"events": [{"event_type": "bogus"}]})
        self.assertTrue(any("events[0]" in e for e in errs), "应指出是第几条")


class TestParseAnnouncement(unittest.TestCase):
    """⚠️ 全部测试都用注入的 caller，**绝不发真实网络请求**。

    实测踩过：本机环境里 DEEPSEEK_API_KEY 是设着的，所以「不传 key」的用例
    会真的去打 API —— 测试必须显式切断环境变量，否则既慢又不可复现。
    """

    def test_no_key_skips_but_keeps_raw(self):
        """★ 无 LLM 时不假装解析成功，但原文必须留下。"""
        from unittest import mock
        # 注意：不能 mock.patch.dict(os.environ) —— 本机 ACC_PRODUCT_CONFIG_V3 有 51 万字符，
        # Windows 环境块上限 32767，恢复时会报「环境变量太长」。所以 patch 取值函数。
        with mock.patch.object(an, "_get_api_key", return_value=""):
            r = an.parse_announcement("10月15日版本更新")
        self.assertEqual(r["parse_status"], "skipped")
        self.assertEqual(r["raw_text"], "10月15日版本更新")
        self.assertIn("原文已保留", r["degraded_reason"])

    def test_whitespace_key_treated_as_missing(self):
        """★ 空白 key 应视为没有 key —— 否则会拿着空格去调 API。"""
        r = an.parse_announcement("x", api_key="   ")
        self.assertEqual(r["parse_status"], "skipped")
        self.assertEqual(r["raw_text"], "x")

    def test_get_api_key_strips(self):
        """★ `_get_api_key` 会 strip：空白视为无 key。

        语义要点（测试写错过一次，记下来）：
          · 传入**空白**（`"   "`）→ strip 后为空 → 视为「显式说了没有」→ 返回 ""
          · 传入**空串**（`""`）  → falsy → **回落到环境变量**（空串 = 没传）
        """
        self.assertEqual(an._get_api_key("  abc  "), "abc")
        self.assertEqual(an._get_api_key("   "), "", "空白应视为无 key")
        self.assertEqual(an._get_api_key("\t\n "), "")

    def test_good_response_ok(self):
        r = an.parse_announcement("x", caller=lambda p: GOOD_JSON)
        self.assertEqual(r["parse_status"], "ok")
        self.assertEqual(len(r["events"]), 1)
        self.assertEqual(r["attempts"], 1)

    def test_bad_json_retries_then_fails_but_keeps_raw(self):
        """★ 重试后仍坏 → failed，但原文保留。"""
        calls = []

        def bad(_p):
            calls.append(1)
            return "我解析不了这段文本"

        r = an.parse_announcement("原文内容", caller=bad, max_retries=1)
        self.assertEqual(r["parse_status"], "failed")
        self.assertEqual(r["raw_text"], "原文内容")
        self.assertEqual(len(calls), 2, "应重试一次")
        self.assertIn("原文已保留", r["degraded_reason"])

    def test_retry_can_succeed(self):
        """★ 关键路径：首次坏、重试好 —— 这正是把错误喂回去的价值。"""
        seq = ["not json", GOOD_JSON]

        def flaky(_p):
            return seq.pop(0)

        r = an.parse_announcement("x", caller=flaky, max_retries=1)
        self.assertEqual(r["parse_status"], "ok")
        self.assertEqual(r["attempts"], 2)

    def test_retry_prompt_carries_errors(self):
        """重试时要把具体错误写进提示词，否则模型不知改哪。"""
        prompts = []

        def spy(p):
            prompts.append(p)
            return "still bad"

        an.parse_announcement("x", caller=spy, max_retries=1)
        self.assertEqual(len(prompts), 2)
        self.assertIn("上次输出有这些问题", prompts[1])

    def test_llm_exception_degrades_not_raises(self):
        """★ 网络/接口异常不能让整批崩。"""
        def boom(_p):
            raise OSError("connection reset")

        r = an.parse_announcement("原文", caller=boom)
        self.assertEqual(r["parse_status"], "failed")
        self.assertIn("LLM 调用失败", r["degraded_reason"])
        self.assertEqual(r["raw_text"], "原文")

    def test_schema_error_after_retries(self):
        """JSON 合法但字段不合规 —— 也要走到 failed。"""
        r = an.parse_announcement("x", caller=lambda p: '{"events":[{"title":"缺类型"}]}',
                                  max_retries=1)
        self.assertEqual(r["parse_status"], "failed")
        self.assertTrue(r["errors"])

    def test_prompt_lists_allowed_event_types(self):
        p = an.build_prompt("x")
        self.assertIn("version_update", p)
        self.assertIn("不要编造时间", p)

    def test_prompt_truncates_huge_input(self):
        p = an.build_prompt("汉字" * 10000)
        self.assertLess(len(p), 20000)


class TestMakeRecord(unittest.TestCase):
    def test_image_only_marks_needs_ocr(self):
        """★ 图片公告不假装解析成功。"""
        rec = an.make_record(game="明日方舟", source="wechat",
                             image_url="https://example.com/a.jpg")
        self.assertEqual(rec["parse_status"], "needs_ocr")
        self.assertIn("未做 OCR", rec["degraded_reason"])
        self.assertEqual(rec["events"], [])

    def test_pending_without_parse(self):
        rec = an.make_record(game="明日方舟", source="manual", raw_text="正文")
        self.assertEqual(rec["parse_status"], "pending")

    def test_carries_parse_result(self):
        parsed = an.parse_announcement("x", caller=lambda p: GOOD_JSON)
        rec = an.make_record(game="明日方舟", source="manual", raw_text="x", parsed=parsed)
        self.assertEqual(rec["parse_status"], "ok")
        self.assertEqual(len(rec["events"]), 1)

    def test_failed_record_keeps_raw_text(self):
        parsed = an.parse_announcement("x", caller=lambda p: "bad")
        rec = an.make_record(game="g", source="s", raw_text="原始公告正文", parsed=parsed)
        self.assertEqual(rec["parse_status"], "failed")
        self.assertEqual(rec["raw_text"], "原始公告正文")

    def test_id_is_stable_for_same_url(self):
        a = an.make_record(game="g", source="s", source_url="https://x/1")
        b = an.make_record(game="g", source="s", source_url="https://x/1")
        # 时间戳会变，但 hash 后缀应一致（同一来源）
        self.assertEqual(a["announcement_id"].split("_")[-1],
                         b["announcement_id"].split("_")[-1])


class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name) / "ann.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_and_load(self):
        recs = [an.make_record(game="g", source="s", raw_text="a")]
        r = an.append_records(recs, self.store)
        self.assertEqual(r["added"], 1)
        self.assertEqual(len(an.load_records(self.store)), 1)

    def test_idempotent_on_same_id(self):
        """★ 幂等：同 id 重复写入会被跳过（可重跑）。"""
        rec = an.make_record(game="g", source="s", raw_text="a")
        rec["announcement_id"] = "fixed_id"
        an.append_records([rec], self.store)
        r2 = an.append_records([rec], self.store)
        self.assertEqual(r2["added"], 0)
        self.assertEqual(r2["skipped_duplicate"], 1)

    def test_load_missing_returns_empty(self):
        self.assertEqual(an.load_records(Path(self._tmp.name) / "no.jsonl"), [])

    def test_tolerates_broken_lines(self):
        self.store.write_text('{"announcement_id":"a"}\n不是json\n', encoding="utf-8")
        self.assertEqual(len(an.load_records(self.store)), 1)

    def test_manual_loader_skips_comments_and_blank(self):
        p = Path(self._tmp.name) / "manual.jsonl"
        p.write_text('# 注释\n\n{"game":"g","raw_text":"x"}\n', encoding="utf-8")
        self.assertEqual(len(an.load_manual(p)), 1)

    def test_store_default_is_in_raw_domain(self):
        """公告库含原文，应落在原始域目录（该目录已 gitignore）。"""
        s = str(an.STORE).replace("\\", "/")     # Windows 路径分隔符归一，避免断言被 \ 卡住
        self.assertIn("data/raw/taptap", s)
        self.assertIn("announcements", s)


class TestUpcoming(unittest.TestCase):
    def _recs(self, offsets_days: list[float]):
        evs = []
        now = an.datetime.now(an.TZ)
        for i, d in enumerate(offsets_days):
            evs.append({"event_type": "event", "title": f"E{i}",
                        "scheduled_at": (now + timedelta(days=d)).isoformat(),
                        "confidence": 0.9, "evidence": "原句"})
        return [{"game": "g", "source_url": "u", "events": evs}]

    def test_filters_past_and_far_future(self):
        recs = self._recs([-10, 1, 30, 400])
        up = an.upcoming(recs, horizon_days=60)
        self.assertEqual(up["n"], 2, "过去与 60 天外的应被滤掉")

    def test_sorted_by_time(self):
        recs = self._recs([30, 1, 10])
        up = an.upcoming(recs, horizon_days=60)
        self.assertEqual([i["title"] for i in up["items"]], ["E1", "E2", "E0"])

    def test_null_scheduled_at_skipped(self):
        recs = [{"game": "g", "events": [{"event_type": "event", "title": "无时间",
                                          "scheduled_at": None}]}]
        self.assertEqual(an.upcoming(recs)["n"], 0)

    def test_days_until_computed(self):
        up = an.upcoming(self._recs([2]), horizon_days=60)
        self.assertAlmostEqual(up["items"][0]["days_until"], 2.0, delta=0.1)

    def test_evidence_passed_through(self):
        up = an.upcoming(self._recs([1]), horizon_days=60)
        self.assertEqual(up["items"][0]["evidence"], "原句")


class TestStatusRender(unittest.TestCase):
    def test_empty_store_is_honest(self):
        rep = an.render_status([])
        self.assertIn("库还是空的", rep)

    def test_shows_parse_status_breakdown(self):
        recs = [{"parse_status": "ok", "events": [GOOD_EVENT]},
                {"parse_status": "failed", "events": []},
                {"parse_status": "needs_ocr", "events": []}]
        rep = an.render_status(recs)
        self.assertIn("解析成功", rep)
        self.assertIn("可补解析", rep)
        self.assertIn("未做 OCR", rep)

    def test_marks_no_fabrication_rule(self):
        rep = an.render_status([{"parse_status": "ok", "events": []}])
        self.assertIn("不许编造", rep)

    def test_marks_raw_text_retention(self):
        rep = an.render_status([{"parse_status": "failed", "events": []}])
        self.assertIn("不丢原文", rep)


class TestOfficialSource(unittest.TestCase):
    """TapTap 官方账号源（by-user）。

    背景：2026-09-30 实测发现官方账号带 `is_official`，公告正文在
    `moment.topic.summary` 且**是纯文字** —— 比公众号（图片）好得多。
    ⚠️ 但也实测到：连续无间隔请求会触发 Aliyun WAF，所以必须 polite sleep。
    """

    OFFICIAL = {"is_top": True, "moment": {
        "id_str": "850436700025915489", "is_official": True, "publish_time": 1789820100,
        "author": {"user": {"id": 429475503, "name": "鸣潮"}},
        "topic": {"title": "《鸣潮》3.7版本PV", "summary": "3.7版本将于9月30日开启！"}}}
    PLAYER = {"moment": {"id_str": "x", "is_official": False,
                         "topic": {"title": "玩家帖", "summary": "不算公告"}}}
    NO_CONTENT = {"moment": {"id_str": "y", "is_official": True, "topic": {}}}

    def test_filters_non_official(self):
        """★ 只取官方帖 —— 玩家帖不能进排期库。"""
        got = an.extract_moments([self.OFFICIAL, self.PLAYER])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["account_name"], "鸣潮")

    def test_skips_official_without_content(self):
        got = an.extract_moments([self.OFFICIAL, self.NO_CONTENT])
        self.assertEqual(len(got), 1, "官方但没正文的（纯图/空贴）应跳过")

    def test_extracts_topic_summary_as_raw_text(self):
        got = an.extract_moments([self.OFFICIAL])[0]
        self.assertEqual(got["raw_text"], "3.7版本将于9月30日开启！")
        self.assertEqual(got["title"], "《鸣潮》3.7版本PV")

    def test_builds_moment_url(self):
        got = an.extract_moments([self.OFFICIAL])[0]
        self.assertIn("/moment/850436700025915489", got["url"])

    def test_publish_time_converted_to_iso(self):
        got = an.extract_moments([self.OFFICIAL])[0]
        self.assertTrue(got["published_at"].startswith("2026-"))

    def test_missing_publish_time_is_none_not_crash(self):
        item = {"moment": dict(self.OFFICIAL["moment"], publish_time=None)}
        got = an.extract_moments([item])[0]
        self.assertIsNone(got["published_at"])

    def test_moments_to_records_keeps_raw_text(self):
        mo = an.extract_moments([self.OFFICIAL])[0]
        recs = an.moments_to_records([mo], game="鸣潮", parsed=False)
        self.assertEqual(recs[0]["raw_text"], "3.7版本将于9月30日开启！")
        self.assertEqual(recs[0]["source"], an.SOURCE_OFFICIAL)
        self.assertEqual(recs[0]["account_name"], "鸣潮")

    def test_source_constant_present(self):
        self.assertEqual(an.SOURCE_OFFICIAL, "taptap_official")


class TestWafDetection(unittest.TestCase):
    """★ 反爬检测：被 WAF 拦时必须**明确抛出**，不能返回空列表假装没数据。

    2026-09-30 实测：连续 ~50 次无间隔请求 → Aliyun WAF 挑战页
    （`aliyun_waf_aa/bb`），150 秒后仍未恢复。
    """

    def _mock_urlopen(self, body: bytes, ctype: str):
        from unittest import mock
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.headers.get.return_value = ctype
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        return mock.patch.object(an.urllib.request, "urlopen", return_value=resp)

    def test_waf_html_page_raises(self):
        html = b'<html><meta name="aliyun_waf_aa" content="x"></html>'
        with self._mock_urlopen(html, "text/html; charset=utf-8"):
            with self.assertRaises(an.WafBlocked):
                an._fetch_json("https://x/y", {})

    def test_plain_html_raises(self):
        with self._mock_urlopen(b"<html>blocked</html>", "text/html"):
            with self.assertRaises(an.WafBlocked):
                an._fetch_json("https://x/y", {})

    def test_non_json_ctype_raises_runtime(self):
        with self._mock_urlopen(b"hello", "text/plain"):
            with self.assertRaises(RuntimeError):
                an._fetch_json("https://x/y", {})

    def test_valid_json_passes(self):
        with self._mock_urlopen(b'{"success":true,"data":{}}', "application/json"):
            self.assertTrue(an._fetch_json("https://x/y", {})["success"])

    def test_fetch_official_propagates_waf(self):
        """★ WAF 时 fetch 不能吞掉异常、假装成功。"""
        html = b'<html><meta name="aliyun_waf_aa"></html>'
        with self._mock_urlopen(html, "text/html"):
            with self.assertRaises(an.WafBlocked):
                an.fetch_official_moments("429475503", pages=1)

    def test_polite_sleep_between_pages(self):
        """★ 翻页之间必须 sleep —— 不给 WAF 机会（实测教训）。"""
        slept = []
        bodies = [b'{"data":{"list":[{"moment":{"is_official":true,"topic":{}}}]}}',
                  b'{"data":{"list":[]}}']
        from unittest import mock
        resp = mock.MagicMock()
        resp.read.side_effect = bodies
        resp.headers.get.return_value = "application/json"
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        with mock.patch.object(an.urllib.request, "urlopen", return_value=resp):
            an.fetch_official_moments("1", pages=2, sleeper=slept.append)
        self.assertEqual(len(slept), 1, "第 2 页前应 sleep 一次")


class TestReparse(unittest.TestCase):
    """补解析 —— 「解析失败不丢原文」那句承诺的兑现处。

    没有这个能力，原文就只是躺在文件里，补解析是句空话。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name) / "ann.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _seed(self, status="pending", raw="10月15日版本更新"):
        rec = an.make_record(game="鸣潮", source="s", raw_text=raw)
        rec["announcement_id"] = "fixed_1"
        rec["parse_status"] = status
        an.append_records([rec], self.store)
        return rec

    def test_empty_store(self):
        r = an.reparse_pending(self.store)
        self.assertTrue(r["ok"])
        self.assertIn("空", r["note"])

    def test_dry_run_does_not_touch_file(self):
        """★ 干跑必须零副作用。"""
        self._seed()
        before = self.store.read_text(encoding="utf-8")
        r = an.reparse_pending(self.store, dry_run=True)
        self.assertEqual(r["n_would_reparse"], 1)
        self.assertEqual(self.store.read_text(encoding="utf-8"), before, "干跑不应改文件")

    def test_reparse_updates_status(self):
        self._seed(status="pending")
        r = an.reparse_pending(self.store, caller=lambda p: GOOD_JSON)
        self.assertEqual(r["n_reparsed"], 1)
        self.assertEqual(r["n_ok"], 1)
        got = an.load_records(self.store)[0]
        self.assertEqual(got["parse_status"], "ok")
        self.assertEqual(len(got["events"]), 1)
        self.assertIn("reparsed_at", got)

    def test_raw_text_survives_rewrite(self):
        """★ 最关键：重写整个文件后原文不能丢。"""
        self._seed(raw="原始公告正文不能丢")
        an.reparse_pending(self.store, caller=lambda p: "bad json")
        got = an.load_records(self.store)[0]
        self.assertEqual(got["raw_text"], "原始公告正文不能丢")
        self.assertEqual(got["parse_status"], "failed")

    def test_backup_created(self):
        self._seed()
        r = an.reparse_pending(self.store, caller=lambda p: GOOD_JSON)
        self.assertTrue(Path(r["backup"]).exists(), "重写前应留备份")

    def test_skips_already_ok(self):
        self._seed(status="ok")
        r = an.reparse_pending(self.store, caller=lambda p: GOOD_JSON)
        self.assertEqual(r["n_reparsed"], 0, "已成功的不该重复解析")

    def test_skips_records_without_raw_text(self):
        rec = an.make_record(game="g", source="s", image_url="https://x/a.jpg")
        rec["announcement_id"] = "img_1"
        an.append_records([rec], self.store)
        r = an.reparse_pending(self.store, caller=lambda p: GOOD_JSON)
        self.assertEqual(r["n_reparsed"], 0, "图片记录（无正文）不该被补解析")

    def test_failed_llm_keeps_status_failed_not_crash(self):
        """LLM 挂掉时补解析应如实标 failed，且不丢原文。"""
        self._seed()
        def boom(_p):
            raise OSError("no network")
        r = an.reparse_pending(self.store, caller=boom)
        self.assertTrue(r["ok"])
        got = an.load_records(self.store)[0]
        self.assertEqual(got["parse_status"], "failed")
        self.assertTrue(got["raw_text"])

    def test_record_count_preserved(self):
        for i in range(3):
            rec = an.make_record(game="g", source="s", raw_text=f"正文{i}")
            rec["announcement_id"] = f"id_{i}"
            an.append_records([rec], self.store)
        an.reparse_pending(self.store, caller=lambda p: GOOD_JSON)
        self.assertEqual(len(an.load_records(self.store)), 3)


if __name__ == "__main__":
    unittest.main()
