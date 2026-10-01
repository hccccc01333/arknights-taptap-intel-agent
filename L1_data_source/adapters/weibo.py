#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微博适配器：微博 + 评论。

微博是四个平台里唯一有真实转发数的（`reposts_count`）→ shares 只有这里不为空。
"""

from __future__ import annotations

from schema.content_event import ContentEvent, parse_int, to_iso, hash_author
from .base import BaseAdapter, Dataset, pick, first_line


def _post(row, raw_ref, ctx) -> ContentEvent:
    text = pick(row, "text")
    return ContentEvent(
        platform="weibo",
        source_type="post",
        title=first_line(text, 40),
        content=text,
        author_id=hash_author(pick(row, "user_id_hash")),
        published_at=to_iso(pick(row, "publish_time_cn") or pick(row, "publish_time")),
        views=None,
        likes=parse_int(row.get("like_count")),
        comments=parse_int(row.get("comments_count")),
        shares=parse_int(row.get("reposts_count")),   # 只有微博有真实转发数
        external_id=pick(row, "mid") or pick(row, "id"),
        url=pick(row, "url"),
        game=pick(row, "keyword"),
        raw_ref=raw_ref,
        metadata={"source": pick(row, "source")},
    )


def _comment(row, raw_ref, ctx) -> ContentEvent:
    text = pick(row, "text")
    return ContentEvent(
        platform="weibo",
        source_type="comment",
        title=first_line(text, 40),
        content=text,
        author_id=hash_author(pick(row, "user_id_hash")),
        published_at=to_iso(pick(row, "publish_time_cn") or pick(row, "publish_time")),
        likes=parse_int(row.get("like_count")),
        external_id=pick(row, "cid") or pick(row, "comment_id"),
        parent_id=pick(row, "post_mid") or pick(row, "mid"),
        url=pick(row, "url"),
        game=pick(row, "keyword"),
        raw_ref=raw_ref,
        metadata={"replies": parse_int(row.get("reply_count")), "item_type": pick(row, "item_type")},
    )


class WeiboAdapter(BaseAdapter):
    platform = "weibo"
    datasets = [
        Dataset("weibo_posts",    "posts_sample.csv",    "post",    _post),
        Dataset("weibo_comments", "comments_sample.csv", "comment", _comment),
    ]
