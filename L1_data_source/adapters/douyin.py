#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音适配器：视频 + 评论。

已知数据缺陷（如实记录，不粉饰）：抖音首批样本是 degraded_sample ——
点赞/评论数几乎全空、评论表作者哈希大量缺失。归一化后这些字段是 None（不是 0），
下游排序时必须先过滤 None，否则会退化成"按空排序"。
"""

from __future__ import annotations

from schema.content_event import ContentEvent, parse_int, to_iso, hash_author
from .base import BaseAdapter, Dataset, pick, first_line


def _video(row, raw_ref, ctx) -> ContentEvent:
    return ContentEvent(
        platform="douyin",
        source_type="video",
        title=pick(row, "title"),
        content=pick(row, "title"),
        author=hash_author(pick(row, "author_uid_hash") or pick(row, "author_name")),
        published_at=to_iso(pick(row, "publish_time_cn") or pick(row, "crawled_at")),
        views=None,                          # 抖音搜索接口不返回播放量
        likes=parse_int(row.get("digg_count")),
        comments=parse_int(row.get("comment_count")),
        shares=None,
        native_id=pick(row, "aweme_id"),
        url=pick(row, "url"),
        game=pick(row, "keyword"),
        raw_ref=raw_ref,
        extra={"source": pick(row, "source")},
    )


def _comment(row, raw_ref, ctx) -> ContentEvent:
    text = pick(row, "text")
    return ContentEvent(
        platform="douyin",
        source_type="comment",
        title=first_line(text, 40),
        content=text,
        author=hash_author(pick(row, "user_uid_hash")),
        published_at=to_iso(pick(row, "publish_time_cn") or pick(row, "publish_time")),
        likes=parse_int(row.get("like_count")),
        native_id=pick(row, "cid") or pick(row, "comment_id"),
        parent_id=pick(row, "aweme_id"),
        url=pick(row, "video_url"),
        game=pick(row, "keyword"),
        raw_ref=raw_ref,
        extra={"replies": parse_int(row.get("reply_count"))},
    )


class DouyinAdapter(BaseAdapter):
    platform = "douyin"
    datasets = [
        Dataset("douyin_videos",   "videos_sample.csv",   "video",   _video),
        Dataset("douyin_comments", "comments_sample.csv", "comment", _comment),
    ]
