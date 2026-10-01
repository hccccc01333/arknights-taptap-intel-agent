#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Text Cleaning（§7）—— 拆成独立 processor，各自可单独换版。

处理：HTML 标签 / 平台表情占位符（[doge]）/ Emoji / Hashtag / Mention / URL /
Unicode 全角 / 空白 / 平台尾巴（"展开全文"）。

★ 铁律：**不删除原始文本**。永远同时保留 `raw_text` 与 `normalized_text` ——
清洗只产出新字段。本项目在素材层吃过"原文丢失导致无法溯源"的亏。
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any, Dict, List, Tuple

# —— 各种模式 ——
RE_HTML = re.compile(r"<[^>]{1,200}>")
RE_URL = re.compile(r"https?://[\w\-./?%&=#:+~@\[\]]+", re.I)
RE_MENTION = re.compile(r"@([^\s@#：:，,。]{1,30})")
RE_HASHTAG_PAIR = re.compile(r"#([^#\n]{1,50})#")          # 微博：#话题#
RE_HASHTAG_SINGLE = re.compile(r"#([^\s#]{1,50})")          # 推特式：#话题
RE_PLATFORM_TAG = re.compile(r"\[[^\]\n]{1,20}\]")           # [doge] [表情_嫌弃]
RE_TAIL = re.compile(r"(展开全文|全文\s*$|查看图片|网页链接|O网页链接|—\s*查看动图)$")
RE_SPACE = re.compile(r"\s+")
RE_REPEAT = re.compile(r"(.)\1{3,}")                          # !!!! / 啊啊啊啊

# Emoji / 符号区间（简化版：常见 emoji 与杂项符号）
EMOJI_RANGES = [
    (0x1F300, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
    (0xFE00, 0xFE0F), (0x1F000, 0x1F2FF), (0x2190, 0x21FF),
]


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in EMOJI_RANGES)


class HTMLCleaner:
    @staticmethod
    def clean(text: str) -> str:
        t = RE_HTML.sub(" ", text or "")
        return html.unescape(t)


class URLExtractor:
    @staticmethod
    def extract(text: str) -> Tuple[str, List[str]]:
        urls = RE_URL.findall(text or "")
        return RE_URL.sub(" ", text or ""), urls


class MentionExtractor:
    @staticmethod
    def extract(text: str) -> Tuple[str, List[str]]:
        ms = RE_MENTION.findall(text or "")
        return RE_MENTION.sub(" ", text or ""), ms


class HashtagExtractor:
    @staticmethod
    def extract(text: str) -> Tuple[str, List[str]]:
        tags = RE_HASHTAG_PAIR.findall(text or "")
        t = RE_HASHTAG_PAIR.sub(" ", text or "")
        tags += RE_HASHTAG_SINGLE.findall(t)
        t = RE_HASHTAG_SINGLE.sub(" ", t)
        return t, [x.strip() for x in tags if x.strip()]


class EmojiNormalizer:
    """移除 emoji 与平台表情占位符，但**统计下来**（数量本身是信号）。"""

    @staticmethod
    def normalize(text: str) -> Tuple[str, int]:
        t = RE_PLATFORM_TAG.sub(" ", text or "")
        kept = []
        n = 0
        for ch in t:
            if _is_emoji(ch):
                n += 1
                continue
            kept.append(ch)
        return "".join(kept), n


class UnicodeNormalizer:
    @staticmethod
    def normalize(text: str) -> str:
        # NFKC：全角→半角、兼容字符归一（ＦＵＬＬ→FULL）
        return unicodedata.normalize("NFKC", text or "")


class WhitespaceNormalizer:
    @staticmethod
    def normalize(text: str) -> str:
        t = RE_TAIL.sub("", text or "")
        t = RE_REPEAT.sub(r"\1\1", t)          # !!!! → !!
        return RE_SPACE.sub(" ", t).strip()


def clean_text(raw: str) -> Dict[str, Any]:
    """跑完整清洗链，返回 {normalized_text, hashtags, mentions, urls, emoji_count}。"""
    t = HTMLCleaner.clean(raw or "")
    t, urls = URLExtractor.extract(t)
    t, mentions = MentionExtractor.extract(t)
    t, hashtags = HashtagExtractor.extract(t)
    t, emoji_count = EmojiNormalizer.normalize(t)
    t = UnicodeNormalizer.normalize(t)
    t = WhitespaceNormalizer.normalize(t)
    return {
        "normalized_text": t,
        "hashtags": hashtags,
        "mentions": mentions,
        "urls": urls,
        "emoji_count": emoji_count,
    }
