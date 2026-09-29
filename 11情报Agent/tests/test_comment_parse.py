#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/tests/test_comment_parse.py — 评论正文解析兼容性单测。

TapTap 评论正文有两种形态（2026-09-29 实测）：
  a) {"contents": {"text": "..."}}          评分区 / 社区帖评论
  b) {"contents": {"json": [Slate 段落]}}   发现流 / 话题帖评论（含表情节点）
作者名层级也不同：author.user.name vs author.name。
本测试锁住「两种形态都要能解析出正文」这件事，防止改解析器时静默回归成空串。

注：CI 无第三方依赖，故对 requests 做轻量打桩（本用例只覆盖纯函数）。
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "01爬虫"))

if importlib.util.find_spec("requests") is None:  # pragma: no cover - CI 环境无第三方包
    _stub = types.ModuleType("requests")
    _stub.Session = object
    _stub.RequestException = Exception
    sys.modules["requests"] = _stub

from crawl_taptap_community import flatten_contents, parse_comment  # noqa: E402


class CommentParseTest(unittest.TestCase):
    def test_slate_rich_text(self):
        item = {
            "id_str": "1",
            "ups": 3,
            "created_time": 1790599115,
            "author": {"name": "迷迭香的战术装备"},
            "contents": {"json": [{"type": "paragraph", "children": [
                {"text": "这么强力的干员居然望记了吗"},
                {"text": "", "type": "tap_emoji", "children": [{"text": "[表情_斜眼笑]"}]},
            ]}]},
        }
        r = parse_comment(item, "m1", "t")
        self.assertEqual(r["content"], "这么强力的干员居然望记了吗[表情_斜眼笑]")
        self.assertEqual(r["author_name"], "迷迭香的战术装备")
        self.assertEqual(r["supports"], 3)

    def test_legacy_shape_still_works(self):
        item = {
            "comment": {"id_str": "2", "publish_time": 1700000000,
                        "stat": {"supports": 5}, "contents": {"text": "老结构评论"}},
            "author": {"user": {"name": "老用户"}},
        }
        r = parse_comment(item, "m1", "t")
        self.assertEqual(r["content"], "老结构评论")
        self.assertEqual(r["author_name"], "老用户")
        self.assertEqual(r["supports"], 5)

    def test_plain_string_contents(self):
        r = parse_comment({"id_str": "3", "contents": "纯文本评论"}, "m1", "t")
        self.assertEqual(r["content"], "纯文本评论")

    def test_empty_contents_returns_empty_not_none(self):
        r = parse_comment({"id_str": "4"}, "m1", "t")
        self.assertEqual(r["content"], "")

    def test_flatten_contents_skips_blank_nodes(self):
        self.assertEqual(flatten_contents({"json": [{"children": [{"text": "甲"}, {"text": ""}, {"text": "乙"}]}]}), "甲乙")
        self.assertEqual(flatten_contents(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
