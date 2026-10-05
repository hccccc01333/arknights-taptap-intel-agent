#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""百度热搜条目 → ContentEvent。

热榜条目是**聚合对象**（词条而非长文），标成 rank 类型单独处理，
不与帖子/评论混在一起排序 —— 它们是"信号"，不是"内容"。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from schema.content_event import ContentEvent, hash_author, to_iso  # noqa: F401


def _num(v: Any) -> Optional[int]:
    try:
        return int(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _hot(row, raw_ref, ctx) -> ContentEvent:
    word = (row.get("word") or "").strip()
    desc = (row.get("desc") or "").strip()
    score = _num(row.get("hot_score")) or 0
    return ContentEvent(
        platform="baidu_index",   # schema 已预登记的平台（百度指数系）
        source_type="rank",                # 榜单词条，与内容区分（SOURCE_TYPES 已含 rank）
        signal_type="search",               # schema 已定义：用户开始主动寻找
        title=word,
        content=desc or None,
        author_id="unknown",
        published_at=to_iso(row.get("observed_at")),
        # 热度值映射到 views，趋势率映射到 comments（Signal 维度，S3 会用）
        views=score,
        comments=_num(row.get("hot_score_delta")) or None,
        rank=_num(row.get("rank")),
        external_id=f"{row.get('tab','realtime')}:{word}",
        url=row.get("url") or None,
        raw_ref=raw_ref or "",
        metadata={
            "tab": row.get("tab"),
            "hot_change": row.get("hot_change"),      # 百度方向（↑升/↓降/→平）
            "hot_tag": row.get("hot_tag"),            # 新/热/沸/爆
            "trend_rate": row.get("trend_rate"),      # ★ 跨轮热度变化率(%)
            "prev_hot_score": row.get("prev_hot_score"),
            "game_related": bool(row.get("desc") and
                                any(t in (word + " " + row.get("desc", "")).lower()
                                    for t in _GAME_TERMS)),
        },
    )


_GAME_TERMS: list = []   # 采集侧已粗筛并入 metadata，这里不再重复维护词表
