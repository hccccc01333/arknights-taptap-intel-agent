#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""搜索 agent 的搜索结果 → RawContentEvent。

agent 一次话题搜索会从贴吧/B站/微博拿回三类结果，统一落在
data/raw/agent/search_results.csv。这里把它们归一成「搜索信号」事件：

  · platform = agent（真实平台放 metadata.real_platform，总线层不冒充原平台）
  · signal_type = search —— agent 数据的本体价值就是"话题被主动搜索到了"，
    与 organic 内容（social/content/community）分开，第三层才能算 Search ↑
  · external_id = query:item_id —— 同一视频被不同话题搜到算不同事件
    （话题扩散证据），同话题同视频跨轮重复则由 L2 去重兜住
"""

from __future__ import annotations

import re
from typing import Any, Optional

from schema.content_event import RawContentEvent, hash_author, to_iso


def _n(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _search_result(row, raw_ref, ctx) -> RawContentEvent:
    title = re.sub(r"</?em[^>]*>", "", row.get("title") or "").strip()
    if not title:
        return None                       # 空标题行主动丢弃
    item_id = (row.get("item_id") or "").strip()
    query = (row.get("query") or "").strip()
    ext = f"{query}:{item_id}" if item_id else f"q:{hash_author(title)}"[:64]

    # metric 三种形态：B站 play（纯数字）、贴吧 回帖数（纯数字）、微博 "转/评/赞"
    metric_raw = (row.get("metric") or "").strip()
    views = likes = comments = shares = None
    if "/" in metric_raw:
        parts = [_n(p) for p in metric_raw.split("/")]
        while len(parts) < 3:
            parts.append(None)
        shares, comments, likes = parts[0], parts[1], parts[2]
    else:
        views = _n(metric_raw)

    try:
        published_at = to_iso(row.get("create_time"))
    except Exception:
        published_at = None

    return RawContentEvent(
        platform="agent",
        source_type="post",
        signal_type="search",
        title=title[:200],
        content=None,
        author_id=hash_author(row.get("where") or "unknown"),
        published_at=published_at,
        views=views, likes=likes, comments=comments, shares=shares,
        external_id=ext,
        url=row.get("url") or None,
        metadata={
            "real_platform": row.get("platform"),   # 真实平台（tieba/bili/weibo）
            "where": row.get("where"),              # 吧名 / UP主 / 微博昵称
            "query": query,
            "trigger_word": row.get("trigger_word"),
            "trigger_type": row.get("trigger_type"),
            "metric_raw": metric_raw,
        },
    )
