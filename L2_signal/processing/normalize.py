#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Normalization（§6 / §22）—— 时间、指标、URL、作者、标签标准化。

★ 指标映射原则（规格 §6）：**公共指标归一，平台特有指标原样保留**。
不要强行把 B站投币映射成"点赞"，也不要把微博转发塞进"分享"再丢掉原始字段。
所以产出两份：metrics（公共 5 项） + platform_metrics（其余全留）。

★ 跨平台可比性（§22）：微博 1000 赞 ≠ B站 1000 赞。
绝对数不可横向比，所以额外产出**平台内百分位**（由 features 计算，见 features.py）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from schema.content_event import to_iso, parse_int, hash_author  # noqa: E402 (L1 已注入 sys.path)

# 公共指标：跨平台都有的概念
COMMON_METRICS = ("views", "likes", "comments", "shares", "favorites")

# 平台特有指标 → 不做语义映射，原样进 platform_metrics
PLATFORM_SPECIFIC = {
    "bilibili": ("danmaku", "coin", "favorite", "reply"),
    "weibo": ("repost", "attitudes", "read_count"),
    "douyin": ("digg", "collect", "share_count"),
    "taptap": ("ups", "supports", "page_view", "played_hours", "score_norm"),
    "reddit": ("score", "upvote_ratio", "num_comments"),
}


def normalize_time(value: Any) -> Optional[str]:
    """统一 ISO8601（东八区）。交给 L1 的 to_iso，避免两处各写一份解析逻辑。"""
    return to_iso(value)


def normalize_url(url: Optional[str]) -> Optional[str]:
    """规范化 URL：去 utm/sp 等追踪参数、去末尾斜杠、统一小写 host。"""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    if not parts.scheme:
        return url
    drop = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "spm", "share_token", "from", "ref", "sp"}
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if k.lower() not in drop]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") or "/",
                       urlencode(q), ""))


def normalize_author(raw: Optional[str]) -> str:
    """作者脱敏：已是 hash 就用，否则 hash 掉（PII 不进产物）。"""
    return hash_author(raw)


def normalize_tags(tags: Any) -> list:
    """标签标准化：去 #、去空、去重、保序。"""
    if not tags:
        return []
    if isinstance(tags, str):
        tags = [tags]
    out, seen = [], set()
    for t in tags:
        s = str(t).strip().lstrip("#").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def split_metrics(metrics: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """把一条记录的指标拆成「公共指标」与「平台特有指标」。"""
    common: Dict[str, Any] = {}
    specific: Dict[str, Any] = {}
    for k, v in (metrics or {}).items():
        if v is None:
            continue
        if k in COMMON_METRICS:
            n = parse_int(v)
            if n is not None:
                common[k] = n
        else:
            specific[k] = v
    return common, specific


def is_platform_specific(platform: str, key: str) -> bool:
    return key in PLATFORM_SPECIFIC.get(platform, ())


RE_NUM = re.compile(r"^[\d,\.]+$")


def coerce_number(v: Any) -> Optional[float]:
    """把 '1.2万' '12,000' '10k' 这类平台常见写法转成数字。转不了返回 None（不猜）。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    m = {
        "万": 10000, "w": 10000, "W": 10000, "k": 1000, "K": 1000,
        "亿": 100000000, "m": 1000000, "M": 1000000,
    }
    for suf, mult in m.items():
        if s.endswith(suf):
            try:
                return float(s[: -len(suf)]) * mult
            except ValueError:
                return None
    if RE_NUM.match(s):
        try:
            return float(s)
        except ValueError:
            return None
    return None
