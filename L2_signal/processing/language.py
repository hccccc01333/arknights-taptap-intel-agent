#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Language Detection（§10）—— 规则版，确定性、可复现、零依赖。

为什么要它（游戏场景尤其重要）：
    Reddit 先爆 → X → YouTube → 中文互联网
如果只有中文信号，会晚好几个小时。所以第二层必须标出语言，
让第三层能"按语种分队列"处理（国际信号提前预警）。

★ 不翻译（§11）：翻译成本高且没必要。
保存原文 language；跨语言检索靠 multilingual embedding（本机未接模型，接口已留）。
只对"高潜内容/重要实体/趋势候选"才考虑翻译。
"""

from __future__ import annotations

import re
from typing import Dict, Tuple

# Unicode 区间（够用且确定，不需要模型）
RANGES = {
    "zh": [(0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF)],   # 中日韩统一表意（含汉字）
    "ja": [(0x3040, 0x309F), (0x30A0, 0x30FF)],                      # 平假名 / 片假名（日语特有）
    "ko": [(0xAC00, 0xD7AF), (0x1100, 0x11FF)],                      # 韩文音节 / 字母
    "ru": [(0x0400, 0x04FF)],
    "ar": [(0x0600, 0x06FF)],
    "th": [(0x0E00, 0x0E7F)],
}
RE_LATIN = re.compile(r"[A-Za-z]")
RE_DIGIT = re.compile(r"\d")

# 中文简体/繁体的粗略特征字（不做完整简繁转换，只标注）
SIMPLIFIED_HINT = set("们这来时说国学习电脑网络")
TRADITIONAL_HINT = set("們這來時說國學習電腦網絡")


def detect(text: str) -> Dict[str, Any]:
    """返回 {language, script, confidence, has_latin, is_cjk}。"""
    t = text or ""
    counts = {k: 0 for k in RANGES}
    total = 0
    for ch in t:
        if ch.isspace():
            continue
        total += 1
        cp = ord(ch)
        for lang, rs in RANGES.items():
            if any(lo <= cp <= hi for lo, hi in rs):
                counts[lang] += 1
                break
    if total == 0:
        return {"language": "unknown", "script": "unknown", "confidence": 0.0,
                "has_latin": False, "is_cjk": False}

    # 日语判定优先：有假名就是日语（汉字是中日共有，不能单独判定）
    if counts["ja"] > 0:
        return {"language": "ja", "script": "japanese", "confidence": min(0.95, 0.6 + counts["ja"] / total),
                "has_latin": bool(RE_LATIN.search(t)), "is_cjk": True}
    if counts["ko"] > 0:
        return {"language": "ko", "script": "hangul", "confidence": min(0.95, 0.6 + counts["ko"] / total),
                "has_latin": bool(RE_LATIN.search(t)), "is_cjk": True}
    if counts["zh"] > 0:
        ratio = counts["zh"] / total
        # 简繁粗判（规格 §10 提到的"简繁转换"需求，这里只标注不转换）
        variant = "Hans"
        if sum(1 for c in t if c in TRADITIONAL_HINT) > sum(1 for c in t if c in SIMPLIFIED_HINT):
            variant = "Hant"
        lang = f"zh-CN" if variant == "Hans" else "zh-TW"
        return {"language": lang, "script": "han", "confidence": min(0.95, 0.7 + ratio * 0.3),
                "has_latin": bool(RE_LATIN.search(t)), "is_cjk": True}
    if counts["ru"] > 0:
        return {"language": "ru", "script": "cyrillic", "confidence": 0.8,
                "has_latin": bool(RE_LATIN.search(t)), "is_cjk": False}
    if RE_LATIN.search(t):
        # 纯拉丁：默认英语（不做语种细分，避免瞎猜；需要细分时交给模型）
        return {"language": "en", "script": "latin", "confidence": 0.6,
                "has_latin": True, "is_cjk": False}
    return {"language": "unknown", "script": "unknown", "confidence": 0.0,
            "has_latin": False, "is_cjk": False}
