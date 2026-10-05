#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Source Registry —— 整个第一层的核心。

★ 铁律：**Platform ≠ Source**。
「B站」不是一个 Source，它被拆成「B站游戏热门 / 搜索结果 / 视频详情 / UP主动态 / 关键词监控」。
只有拆到这个粒度，才能精确控制成本与更新频率（热搜 2 分钟、长尾 30 分钟）。

★ 铁律二：**不要把 `crawl_bilibili_every_5_min()` 硬编码进代码**。
所有 Source 必须注册，采集系统按注册表动态运行。

每个 Source 的关键字段（对齐用户给的 DDL）：
    priority / base|min|max_interval_seconds / timeout / max_concurrency
    rate_limit_per_minute / parser_version / config / compliance_config
    + signal_type（信号价值分类，第三层靠它识别传播路径）
    + freshness_slo_seconds（新鲜度 SLO，热点系统的核心 SLO）
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

_L1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _L1 not in sys.path:
    sys.path.insert(0, _L1)

from storage.metadata_db import MetadataStore  # noqa: E402

DEFAULT_DB_PATH = os.path.join("data", "state", "l1_source_registry.sqlite3")

# 合规默认：允许抓、保留 180 天、**不含 PII**、原始内容存 Raw Lake
DEFAULT_COMPLIANCE = {
    "allowed": True,
    "retention_days": 180,
    "contains_pii": False,
    "raw_content_storage": True,
}

# ★ 种子：Platform 下挂多个 Source。acquisition_mode=internal 表示读本地已落盘数据
# （本机没有 API 凭据与外网采集能力，这是诚实的实现边界，不是设计妥协）。
# ★ TapTap 源按 S1-S6+U1 通道设计命名（docs/Agent-v2-架构设计.md §1，2026-09-28 抓包定案）：
#   S1 group/v1/recommend            全平台社区地图（仅作 group_id 映射，不单独落盘）
#   S2 feed/v7/by-group              单游戏社区帖子流（community/posts.csv，含内嵌热评）
#   S3 moment-comment/v1/by-moment   帖子评论流（community/comments.csv，moment_id 关联 S2）
#   S4 hashtag/v2/hot-hashtags       全站话题热榜（hot_hashtags.csv）
#   S5 discover-categories/v2/feed-list + S6 feed/v7/by-hashtag
#                                    发现页/话题帖（discovery_posts.csv，source_type 区分）
#   S6 帖子评论（discovery_comments.csv）
#   U1 feed/v7/by-user               用户社区足迹（user_flow/user_posts.csv）
DEFAULT_SOURCES: List[Dict[str, Any]] = [
    # ---------- 外部：全网热点的搜索意图信号 ----------
    dict(source_id="baidu_hot_search", platform="baidu_index", source_name="百度热搜榜（搜索意图）",
         source_type="rank", signal_type="search", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.95,
         base_interval_seconds=600, min_interval_seconds=300, max_interval_seconds=3600,
         timeout_seconds=20, max_concurrency=1, rate_limit_per_minute=20,
         freshness_slo_seconds=1800, parser_version="baidu-1.0",
         config={"dataset": "hot_search", "file": "hot_search.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    # ---------- TapTap：S1-S6+U1 通道（主战场）----------
    dict(source_id="tap_group_map", platform="taptap", source_name="S1 全平台社区地图",
         source_type="group", signal_type="community", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.9,
         base_interval_seconds=21600, min_interval_seconds=3600, max_interval_seconds=86400,
         timeout_seconds=60, max_concurrency=1, rate_limit_per_minute=10,
         freshness_slo_seconds=86400, parser_version="taptap-1.0",
         config={"dataset": "group_map", "file": "community/community_map.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="tap_group_categories", platform="taptap", source_name="S1 板块分类清单",
         source_type="group", signal_type="community", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.85,
         base_interval_seconds=21600, min_interval_seconds=3600, max_interval_seconds=86400,
         timeout_seconds=60, max_concurrency=1, rate_limit_per_minute=10,
         freshness_slo_seconds=86400, parser_version="taptap-1.0",
         config={"dataset": "group_map", "file": "community/community_categories.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="tap_hot_hashtags", platform="taptap", source_name="S4 全站话题热榜（辅助展示）",
         source_type="hashtag", signal_type="community", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.95,
         base_interval_seconds=300, min_interval_seconds=60, max_interval_seconds=900,
         timeout_seconds=20, max_concurrency=1, rate_limit_per_minute=30,
         freshness_slo_seconds=600, parser_version="taptap-1.0",
         config={"dataset": "hashtags", "file": "hot_hashtags.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="tap_topic_feed", platform="taptap", source_name="S5+S6 发现流与话题帖",
         source_type="moment", signal_type="community", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.9,
         base_interval_seconds=300, min_interval_seconds=60, max_interval_seconds=1200,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=30,
         freshness_slo_seconds=900, parser_version="taptap-1.0",
         config={"dataset": "moments", "file": "discovery_posts.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="tap_moment_comments", platform="taptap", source_name="S5/S6 帖子评论（全量+增量）",
         source_type="comment", signal_type="community", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.8,
         base_interval_seconds=300, min_interval_seconds=120, max_interval_seconds=1800,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=30,
         freshness_slo_seconds=900, parser_version="taptap-1.0",
         config={"dataset": "comments", "file": "discovery_comments.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="tap_community_posts", platform="taptap",
         source_name="S2 单游戏社区帖子流",
         source_type="moment", signal_type="community", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.85,
         base_interval_seconds=300, min_interval_seconds=60, max_interval_seconds=1200,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=30,
         freshness_slo_seconds=900, parser_version="taptap-1.0",
         config={"dataset": "moments", "file": "community/posts.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="tap_community_comments", platform="taptap", source_name="S3 帖子评论流",
         source_type="comment", signal_type="community", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.8,
         base_interval_seconds=300, min_interval_seconds=120, max_interval_seconds=1800,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=30,
         freshness_slo_seconds=900, parser_version="taptap-1.0",
         config={"dataset": "comments", "file": "community/comments.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="tap_user_flow", platform="taptap", source_name="U1 用户社区足迹（暂停：暂无消费方）",
         source_type="moment", signal_type="community", acquisition_mode="internal",
         connector="internal", enabled=0, priority=0.5,
         base_interval_seconds=3600, min_interval_seconds=900, max_interval_seconds=21600,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=10,
         freshness_slo_seconds=21600, parser_version="taptap-1.0",
         config={"dataset": "moments", "file": "user_flow/user_posts.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="tap_reviews", platform="taptap", source_name="评分区评论（带评分/时长）",
         source_type="review", signal_type="community", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.6,
         base_interval_seconds=1800, min_interval_seconds=600, max_interval_seconds=7200,
         timeout_seconds=60, max_concurrency=1, rate_limit_per_minute=20,
         freshness_slo_seconds=3600, parser_version="taptap-1.0",
         config={"dataset": "reviews", "file": "reviews.csv", "batch_size": 3000},
         compliance_config={**DEFAULT_COMPLIANCE, "contains_pii": True}),
    dict(source_id="tap_official", platform="taptap", source_name="官方公告（U1 官方账号）",
         source_type="article", signal_type="official", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.7,
         base_interval_seconds=3600, min_interval_seconds=900, max_interval_seconds=21600,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=10,
         freshness_slo_seconds=7200, parser_version="taptap-1.0",
         config={"dataset": "announcements", "file": "announcements/announcements.jsonl"},
         compliance_config=DEFAULT_COMPLIANCE),

    dict(source_id="bili_game_hot", platform="bilibili", source_name="B站游戏区+全站热门",
         source_type="video", signal_type="content", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.8,
         base_interval_seconds=900, min_interval_seconds=300, max_interval_seconds=3600,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=20,
         freshness_slo_seconds=3600, parser_version="bilibili-1.0",
         config={"dataset": "videos", "file": "hot_videos.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    # ---------- B站：内容信号 ----------
    dict(source_id="bili_search", platform="bilibili", source_name="关键词搜索结果",
         source_type="video", signal_type="content", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.5,
         base_interval_seconds=900, min_interval_seconds=300, max_interval_seconds=3600,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=20,
         freshness_slo_seconds=1800, parser_version="bilibili-1.0",
         config={"dataset": "videos", "file": "videos_sample.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="bili_comments", platform="bilibili", source_name="视频评论",
         source_type="comment", signal_type="content", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.4,
         base_interval_seconds=1800, min_interval_seconds=600, max_interval_seconds=7200,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=20,
         freshness_slo_seconds=3600, parser_version="bilibili-1.0",
         config={"dataset": "comments", "file": "comments_sample.csv"},
         compliance_config=DEFAULT_COMPLIANCE),

    # ---------- 抖音 / 微博 ----------
    dict(source_id="douyin_search", platform="douyin", source_name="关键词搜索结果",
         source_type="video", signal_type="content", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.4,
         base_interval_seconds=1800, min_interval_seconds=600, max_interval_seconds=7200,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=20,
         freshness_slo_seconds=3600, parser_version="douyin-1.0",
         config={"dataset": "videos", "file": "videos_sample.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="weibo_keyword", platform="weibo", source_name="游戏关键词",
         source_type="post", signal_type="social", acquisition_mode="internal",
         connector="internal", enabled=1, priority=0.6,
         base_interval_seconds=600, min_interval_seconds=120, max_interval_seconds=1800,
         timeout_seconds=30, max_concurrency=1, rate_limit_per_minute=30,
         freshness_slo_seconds=900, parser_version="weibo-1.0",
         config={"dataset": "posts", "file": "posts_sample.csv"},
         compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="weibo_hot_search", platform="weibo", source_name="热搜榜",
         source_type="rank", signal_type="social", acquisition_mode="api",
         connector="api", enabled=0, priority=1.0,
         base_interval_seconds=120, min_interval_seconds=60, max_interval_seconds=600,
         timeout_seconds=15, max_concurrency=1, rate_limit_per_minute=60,
         freshness_slo_seconds=180, parser_version="weibo-1.0",
         config={"collect_rank": True}, compliance_config=DEFAULT_COMPLIANCE),

    # ---------- 未接入：显式登记，enabled=0，不假装接了 ----------
    dict(source_id="xiaohongshu_keyword", platform="xiaohongshu", source_name="游戏关键词",
         source_type="post", signal_type="social", acquisition_mode="api",
         connector="api", enabled=0, priority=0.7,
         base_interval_seconds=600, min_interval_seconds=180, max_interval_seconds=3600,
         freshness_slo_seconds=1800, parser_version="xhs-0.1",
         config={"keywords": []}, compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="baidu_index_game", platform="baidu_index", source_name="游戏搜索指数",
         source_type="index", signal_type="search", acquisition_mode="api",
         connector="api", enabled=0, priority=0.8,
         base_interval_seconds=3600, min_interval_seconds=1800, max_interval_seconds=21600,
         freshness_slo_seconds=7200, parser_version="baidu-0.1",
         config={"keywords": []}, compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="news_rss", platform="news", source_name="行业媒体 RSS",
         source_type="article", signal_type="news", acquisition_mode="rss",
         connector="rss", enabled=0, priority=0.7,
         base_interval_seconds=600, min_interval_seconds=300, max_interval_seconds=3600,
         freshness_slo_seconds=900, parser_version="rss-0.1",
         config={"feeds": []}, compliance_config=DEFAULT_COMPLIANCE),
    dict(source_id="steam_reviews", platform="steam", source_name="Steam 评论/榜单",
         source_type="review", signal_type="market", acquisition_mode="api",
         connector="api", enabled=0, priority=0.5,
         base_interval_seconds=3600, min_interval_seconds=1800, max_interval_seconds=21600,
         freshness_slo_seconds=7200, parser_version="steam-0.1",
         config={"app_ids": []}, compliance_config=DEFAULT_COMPLIANCE),
]


class SourceRegistry:
    """数据源注册中心：注册表 + 动态启停 + 优先级调整。"""

    def __init__(self, store: Optional[MetadataStore] = None) -> None:
        self.store = store or MetadataStore()

    def seed(self, force: bool = False) -> int:
        """写入默认 Source。已存在的不覆盖（force=True 才覆盖配置）。"""
        n = 0
        for s in DEFAULT_SOURCES:
            if force or not self.store.get_source(s["source_id"]):
                self.store.upsert_source(s)
                n += 1
        return n

    def list_sources(self, enabled_only: bool = False, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.store.list_sources(enabled_only=enabled_only, platform=platform)

    def get(self, source_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_source(source_id)

    def set_enabled(self, source_id: str, enabled: bool) -> None:
        self.store.conn.execute("UPDATE source_registry SET enabled=?, updated_at=? WHERE source_id=?",
                                (1 if enabled else 0, json.dumps({}), source_id))
        self.store.conn.commit()

    def set_priority(self, source_id: str, priority: float) -> None:
        self.store.conn.execute("UPDATE source_registry SET priority=?, updated_at=? WHERE source_id=?",
                                (priority, "", source_id))
        self.store.conn.commit()

    def summary(self) -> Dict[str, Any]:
        """给 CLI / 第二层看的覆盖概览。"""
        srcs = self.list_sources()
        by_signal: Dict[str, int] = {}
        by_platform: Dict[str, int] = {}
        for s in srcs:
            by_signal[s.get("signal_type", "?")] = by_signal.get(s.get("signal_type", "?"), 0) + 1
            by_platform[s["platform"]] = by_platform.get(s["platform"], 0) + 1
        enabled = [s for s in srcs if s.get("enabled")]
        return {
            "n_sources": len(srcs),
            "n_enabled": len(enabled),
            "by_signal_type": by_signal,
            "platforms_with_sources": by_platform,
            "note": "Platform ≠ Source：一个平台下挂多个 Source，各自独立控制频率与成本",
        }
