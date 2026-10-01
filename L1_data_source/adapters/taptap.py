#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TapTap 适配器：动态 / 评论 / 话题 / 游戏评论（评分）。

覆盖用户列出的 TapTap 五类中的四类：评论(review)、动态(moment)、评分(rating，随评论带出)、
话题(hashtag)。论坛(forum) 与榜单(rank) 目前没有落盘数据 → required=False 显式登记为"未接"。

字段坑（实测，写在这里避免后人重踩）：
- 原帖点赞在 `ups`，评论的点赞才叫 `supports`；`posts.supports` 实测恒为 0，**不可当传播度**。
- `pv_total` 是曝光，很多行为 0（采集侧没拿到），所以 views 常为 None/0，不能拿来排序。
"""

from __future__ import annotations

from schema.content_event import ContentEvent, parse_int, to_iso, hash_author
from .base import BaseAdapter, Dataset, pick, first_line

# 采集侧 source_type 取值 → 统一 source_type
_SOURCE_MAP = {
    "discover": "moment",
    "hashtag": "moment",
    "moment": "moment",
    "forum": "forum",
    "review": "review",
    "comment": "comment",
}


def _moment(row, raw_ref, ctx) -> ContentEvent:
    summary = pick(row, "summary")
    title = pick(row, "title") or first_line(summary, 40)
    return ContentEvent(
        platform="taptap",
        source_type=_SOURCE_MAP.get(pick(row, "source_type").lower(), "moment"),
        title=title,
        content=summary,
        author_id=hash_author(pick(row, "author_id_hash") or pick(row, "author_name")),
        published_at=to_iso(pick(row, "publish_time")),
        views=parse_int(row.get("pv_total")),
        likes=parse_int(row.get("ups")),            # ★ 原帖看 ups，不看 supports
        comments=parse_int(row.get("comments")),
        shares=None,                                # TapTap 无转发数字，不填 0
        external_id=pick(row, "moment_id"),
        game=pick(row, "app_title"),
        url=f"https://www.taptap.cn/moment/{pick(row, 'moment_id')}" if pick(row, "moment_id") else None,
        raw_ref=raw_ref,
        metadata={
            "group_id": pick(row, "group_id"),
            "hashtag_id": pick(row, "hashtag_id"),
            "hashtag_title": pick(row, "hashtag_title"),
        },
    )


def _comment(row, raw_ref, ctx) -> ContentEvent:
    content = pick(row, "content")
    return ContentEvent(
        platform="taptap",
        source_type="comment",
        title=first_line(content, 40),
        content=content,
        author_id=hash_author(pick(row, "author_name")),
        published_at=to_iso(pick(row, "publish_time")),
        likes=parse_int(row.get("supports")),       # ★ 评论看 supports
        external_id=pick(row, "comment_id"),
        parent_id=pick(row, "moment_id"),
        raw_ref=raw_ref,
    )


def _hashtag(row, raw_ref, ctx) -> ContentEvent:
    """话题是聚合对象，不是单条内容——单独标成 hashtag，避免和帖混在一起排序。"""
    return ContentEvent(
        platform="taptap",
        source_type="hashtag",
        title=pick(row, "title"),
        content=pick(row, "description"),
        author_id="unknown",
        published_at=to_iso(pick(row, "crawled_at")),
        views=parse_int(row.get("page_view")),      # 话题页曝光 ≠ 社区在讨论
        comments=parse_int(row.get("comment_count")),
        external_id=pick(row, "hashtag_id"),
        raw_ref=raw_ref,
        metadata={"hot_id": pick(row, "hot_id"), "sort": parse_int(row.get("sort")),
               "is_new": pick(row, "is_new")},
    )


def _review(row, raw_ref, ctx) -> ContentEvent:
    """游戏评论（带评分与游戏时长）——这是"高投入负向"分析的原料。"""
    text = pick(row, "text") or pick(row, "raw_text")
    return ContentEvent(
        platform="taptap",
        source_type="review",
        title=first_line(text, 40),
        content=text,
        author_id=hash_author(pick(row, "user_id_hash") or pick(row, "user_id")),
        published_at=to_iso(pick(row, "publish_time")),
        likes=parse_int(row.get("support_count")),
        external_id=pick(row, "review_id"),
        parent_id=pick(row, "moment_id"),
        url=pick(row, "source_url"),
        raw_ref=raw_ref,
        metadata={
            "score_raw": parse_int(row.get("score_raw")),
            "score_norm": row.get("score_norm"),
            "played_hours": row.get("played_hours"),
            "is_recommend": pick(row, "is_recommend"),
            "stage_label": pick(row, "stage_label"),
        },
    )


class TapTapAdapter(BaseAdapter):
    platform = "taptap"
    datasets = [
        Dataset("taptap_moments",  "discovery_posts.csv",    "moment",  _moment),
        Dataset("taptap_comments", "discovery_comments.csv", "comment", _comment),
        Dataset("taptap_hashtags", "hot_hashtags.csv",       "hashtag", _hashtag),
        Dataset("taptap_reviews",  "reviews.csv",            "review",  _review),
    ]
