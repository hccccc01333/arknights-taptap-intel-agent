#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1 质量闸门测试 —— 钉死"拦什么、不拦什么"。

★ 最重要的一条：**38 条「好玩」是 38 个不同用户的真实评论**（external_id 各不相同）。
  所以闸门按**信息量**判，不按唯一性 —— 去重会误杀真实情报。
"""

from __future__ import annotations

import os
import sys
import unittest

_L1 = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "L1_data_source")
if _L1 not in sys.path:
    sys.path.insert(0, _L1)

from quality_gate import (  # noqa: E402
    LOW_INFO_WORDS, gate, looks_like_keyword_only, title_is_valid,
)


class TestTitleValid(unittest.TestCase):
    def test_real_titles_pass(self):
        for t in ("三角洲行动二周年", "原神 3.0 版本更新", "明日方舟终末地公测",
                  "black myth wukong 联动", "【明日方舟】更新后问题汇总：闪退/登录"):
            self.assertTrue(title_is_valid(t), t)

    def test_low_info_rejected(self):
        for t in ("好玩", "厉害", "不", "？？", "[表情_狗头]", "  ", "！！！",
                  "好玩爱玩", "牛逼"):
            self.assertFalse(title_is_valid(t), t)

    def test_boundary(self):
        self.assertTrue(title_is_valid("明日方舟"))          # 刚好 4 字
        self.assertFalse(title_is_valid("明日方"))           # 3 字太短


class TestKeywordOnly(unittest.TestCase):
    """搜索词回填：真实存在但无区分度的标题。"""

    def test_detects_keyword_plus_number(self):
        self.assertTrue(looks_like_keyword_only("明日方舟1", "video"))
        self.assertTrue(looks_like_keyword_only("原神 3", "video"))

    def test_not_for_posts(self):
        self.assertFalse(looks_like_keyword_only("明日方舟1", "comment"))
        self.assertFalse(looks_like_keyword_only("明日方舟1", "post"))

    def test_real_video_title_not_flagged(self):
        self.assertFalse(looks_like_keyword_only(
            "【明日方舟】谬因实战测评：她应该是断层第一", "video"))


class TestGate(unittest.TestCase):
    def test_rejects_low_info(self):
        r = gate(title="好玩", content="好玩")
        self.assertTrue(r["reject"])
        self.assertEqual(r["code"], "low_info_title")

    def test_rejects_keyword_only_video(self):
        r = gate(title="明日方舟4", content="", platform="bilibili",
                 source_type="video")
        self.assertTrue(r["reject"])
        self.assertEqual(r["code"], "keyword_only_title")

    def test_rejects_empty_content(self):
        r = gate(title="三角洲行动二周年", content="", source_type="comment")
        self.assertTrue(r["reject"])

    def test_keeps_real_content(self):
        r = gate(title="三角洲行动二周年",
                 content="二周年发布会一次端了个满汉全席! 三角洲官宣联动影之刃零",
                 platform="taptap", source_type="article")
        self.assertFalse(r["reject"])
        self.assertTrue(r["has_entity"])

    def test_video_without_content_is_kept_with_note(self):
        """B 站视频没有正文是正常的，不能因此拦掉。"""
        r = gate(title="明日方舟终末地［盈天台据点］三级预警终端挂机攻略",
                 content="", platform="bilibili", source_type="video")
        self.assertFalse(r["reject"])
        self.assertIsNotNone(r["note"])

    def test_reject_reason_is_actionable(self):
        """拦截必须能说清为什么（否则运营看不懂为什么内容没进来）。"""
        r = gate(title="好玩", content="好玩")
        self.assertIn("好玩", r["reason"])


class TestNoOverBlocking(unittest.TestCase):
    """反向纪律：不能把有情报价值的内容误杀。"""

    def test_real_comments_pass(self):
        real = [
            "这章剧情刀子太狠了，我哭得像个傻子",
            "更新后一直闪退进不去，有一样的吗",
            "这个干员强度是不是有点超标了",
            "0+1=1，明摆着骗抽",
        ]
        for c in real:
            r = gate(title=c[:20], content=c, source_type="comment")
            self.assertFalse(r["reject"], c)

    def test_low_info_word_inside_long_text_is_kept(self):
        """「好玩」作为长句中的一个词，不该拦。"""
        c = "这游戏好玩是真好玩，但抽卡就是纯纯的赌博，我上头了"
        r = gate(title=c[:16], content=c, source_type="comment")
        self.assertFalse(r["reject"])


if __name__ == "__main__":
    unittest.main()