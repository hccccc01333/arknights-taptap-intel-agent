#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B站适配器：视频 + 评论。

字段坑：视频表用 `play`(播放) 与 `review`(评论数)，没有点赞/转发；
评论表有 `like_count` 与 `reply_count`（回复数，不是转发）→ 回复数放 extra，不冒充 shares。
"""

from __future__ import annotations

from schema.content_event import ContentEvent, parse_int, to_iso, hash_author
from .base import BaseAdapter, Dataset, pick, first_line


def _video(row, raw_ref, ctx) -> ContentEvent:
    return ContentEvent(
        platform="bilibili",
        source_type="video",
        title=pick(row, "title"),
        content=pick(row, "title"),          # 视频表没有正文，标题即内容
        author=hash_author(pick(row, "up_mid") or pick(row, "up_name")),
        published_at=to_iso(pick(row, "pubdate_cn")),
        views=parse_int(row.get("play")),
        likes=None,                          # 搜索接口不返回点赞，不填 0
        comments=parse_int(row.get("review")),
        shares=None,
        native_id=pick(row, "aid"),
        url=pick(row, "url"),
        game=pick(row, "keyword"),
        raw_ref=raw_ref,
        extra={"bvid": pick(row, "bvid"), "up_name_hashed": True, "source": pick(row, "source")},
    )


def _comment(row, raw_ref, ctx) -> ContentEvent:
    text = pick(row, "text")
    return ContentEvent(
        platform="bilibili",
        source_type="comment",
        title=first_line(text, 40),
        content=text,
        author=hash_author(pick(row, "user_mid_hash")),
        published_at=to_iso(pick(row, "publish_time_cn") or pick(row, "publish_time")),
        likes=parse_int(row.get("like_count")),
        native_id=pick(row, "rpid") or pick(row, "comment_id"),
        parent_id=pick(row, "aid"),
        url=pick(row, "video_url"),
        game=pick(row, "keyword"),
        raw_ref=raw_ref,
        extra={"replies": parse_int(row.get("reply_count")), "bvid": pick(row, "bvid")},
    )


class BilibiliAdapter(BaseAdapter):
    platform = "bilibili"
    datasets = [
        Dataset("bili_videos",   "videos_sample.csv",   "video",   _video),
        Dataset("bili_comments", "comments_sample.csv", "comment", _comment),
    ]
