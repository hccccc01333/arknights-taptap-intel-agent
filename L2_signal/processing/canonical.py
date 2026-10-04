#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Canonical Content Model —— 第二层最重要的设计。

第三层以后只认识 CanonicalContent，绝不写 `if platform == "bilibili": ...`。
第一层各平台字段各异（B站 view/danmaku/coin、微博 repost/like、Reddit score），
第二层统一收敛成这一个模型 + 一份 metrics（公共指标归一，平台特有指标原样保留）。

★ 与项目既有纪律的一致性：
- `raw_text` 与 `normalized_text` **两个版本都留**（规格 §7：永远保留原文）。
  清洗只产生新字段，不销毁原文 —— 与本项目"素材必须可溯源"同源。
- 缺失 = None，不填 0。
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))           # processing/
_L2 = os.path.dirname(_HERE)                                  # L2_signal/
_ROOT = os.path.dirname(_L2)
_L1 = os.path.join(_ROOT, "L1_data_source")
for p in (_L1, _L2):
    if p not in sys.path:
        sys.path.insert(0, p)

PROCESSING_VERSION = "2026.10.1"      # 处理链版本；任何 processor 变更都应体现在这里

# 处理状态机（§27：以后问"为什么这条热点没被发现"，能追到是哪一步挂的）
STATE_ORDER = ["RECEIVED", "VALIDATED", "CLEANED", "DEDUPED", "ENRICHED", "EMBEDDED", "READY", "FAILED"]


def content_id_of(platform: str, external_id: str) -> str:
    """内容身份：与第一层的 content_key 对齐（platform:external_id）。"""
    return f"{platform}:{external_id}"


@dataclass
class Entity:
    """实体（抽取出来的一句话里的游戏/角色/厂商…）。"""

    entity_id: str
    entity_type: str          # GAME / GAME_IP / CHARACTER / PUBLISHER / DEVELOPER / PLATFORM / EVENT …
    canonical_name: str
    mention: str              # 原文里的说法（"黑猴"）
    confidence: float = 1.0
    resolved_by: str = "rule"  # rule / llm_fallback


@dataclass
class CanonicalContent:
    """统一内容模型。第三层只认这个。"""

    content_id: str = ""
    platform: str = ""
    source_id: str = ""
    external_id: str = ""
    content_type: str = "post"
    parent_id: str = ""

    # ★ 两版文本：原文永存，清洗版用于计算
    raw_title: Optional[str] = None
    raw_text: str = ""
    normalized_title: Optional[str] = None
    normalized_text: str = ""

    author_id: str = "unknown"
    published_at: Optional[str] = None
    observed_at: Optional[str] = None

    language: str = "unknown"          # zh-CN / en / ja / ko …

    entities: List[Entity] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)      # hashtag / 话题标签
    category: Optional[str] = None                        # gaming / ai / tech …
    subcategory: List[str] = field(default_factory=list)  # 游戏细分：fps / moba / anime …

    embedding_ref: Optional[str] = None                   # 指向 content_embedding（模型可换）

    quality_score: float = 0.0
    spam_score: float = 0.0
    gaming_probability: float = 0.0

    metrics: Dict[str, Any] = field(default_factory=dict)          # 公共指标（归一后）
    platform_metrics: Dict[str, Any] = field(default_factory=dict)  # 平台特有指标（原样保留）

    extracted: Dict[str, Any] = field(default_factory=dict)   # hashtags / mentions / urls
    features: Dict[str, Any] = field(default_factory=dict)    # ContentFeatures 扁平化结果
    source_features: Dict[str, Any] = field(default_factory=dict)  # §20

    fingerprint: str = ""              # §9 跨平台转载指纹
    dedup_group: Optional[str] = None  # 近似重复组
    is_duplicate: bool = False

    raw_ref: str = ""
    processing_version: str = PROCESSING_VERSION
    processor_versions: Dict[str, str] = field(default_factory=dict)  # §28
    state: str = "RECEIVED"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entities"] = [asdict(e) for e in self.entities]
        return d


@dataclass
class ContentFeatures:
    """★ 第三层最终消费的特征集（§23）。第三层不该再被一堆散字段折磨。"""

    content_id: str = ""

    # text
    text_length: int = 0
    # semantic
    gaming_probability: float = 0.0
    category: Optional[str] = None
    entities: List[str] = field(default_factory=list)
    # quality
    quality_score: float = 0.0
    spam_score: float = 0.0
    # source
    is_official: bool = False
    source_reliability: float = 0.5
    # engagement（平台内百分位，跨平台可比）
    view_percentile: Optional[float] = None
    like_percentile: Optional[float] = None
    comment_percentile: Optional[float] = None
    # temporal
    view_velocity: Optional[float] = None
    comment_velocity: Optional[float] = None
    # dedup
    duplicate_group: Optional[str] = None
    # embedding
    embedding_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def text_fingerprint(normalized_text: str, urls: Optional[List[str]] = None) -> str:
    """§9 内容指纹：由归一化文本 + 规范 URL 组成，用于识别跨平台转载。

    （图片感知哈希需要多媒体模型，本机没有 → 只做文本+URL 两部分，如实标注。）
    """
    base = (normalized_text or "").strip().lower()
    base = "".join(ch for ch in base if not ch.isspace())
    canonical_urls = sorted({u.split("?")[0].rstrip("/") for u in (urls or [])})
    raw = base + "|" + "|".join(canonical_urls)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
