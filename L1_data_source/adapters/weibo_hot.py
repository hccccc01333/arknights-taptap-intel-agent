#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微博热搜条目 → ContentEvent。

热榜词条是**信号**（谁在什么话题上聚集），不是内容，所以标成 rank 类型，
和帖子/评论分开，不参与内容排序。热度值映射到 views，趋势率映射到 comments。
"""

from __future__ import annotations

from typing import Any, Optional

from schema.content_event import ContentEvent


def _n(v: Any) -> Optional[int]:
    try:
        return int(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _hot(row, raw_ref, ctx) -> ContentEvent:
    word = (row.get("word") or "").strip()
    score = _n(row.get("hot_score")) or 0
    return ContentEvent(
        platform="weibo",
        source_type="rank",          # 榜单词条（SOURCE_TYPES 已含 rank）
        signal_type="social",        # schema 已定义：用户开始讨论
        title=word,
        content=None,
        author_id="unknown",
        published_at=None,           # 热搜没有发布时间，只有"当前在榜"
        views=score,                 # num = 热度值
        comments=_n(row.get("hot_score_delta")),   # 跨轮热度增量 = 趋势信号
        rank=_n(row.get("rank")),
        external_id=word,
        url=row.get("url") or None,
        raw_ref=raw_ref or "",
        metadata={
            "trend_rate": row.get("trend_rate"),      # ★ 热度变化率(%)
            "is_new": row.get("is_new") == "true",   # 本轮新进榜
            "label": row.get("label"),               # 新/热/沸/爆（微博官方分级）
            "category": row.get("category"),
            "raw_hot": row.get("raw_hot"),
        },
    )
