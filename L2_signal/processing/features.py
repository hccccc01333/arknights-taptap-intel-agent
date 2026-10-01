#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Feature Engine（§21 / §22 / §23）—— 第三层要的基础特征。

§21：第二层只产出**基础特征**（delta / velocity），
**不判断**"这个 velocity 很异常所以是热点" —— 那是第三层。

§22：平台指标不能直接横向比（微博 1000 赞 ≠ B站 1000 赞）。
所以一律产出**平台内百分位**：like_percentile=0.985 比绝对数有意义得多。

§23：最终把所有散字段收敛成 ContentFeatures，第三层拿一份就够。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from .canonical import ContentFeatures, content_id_of  # noqa: E402

_DB_L1_DEFAULT = os.path.join("data", "state", "l1_source_registry.sqlite3")


def percentile(values: Sequence[float], x: float) -> Optional[float]:
    """x 在这批值里的百分位（0..1）。样本为空返回 None（不猜）。"""
    vs = sorted(v for v in values if v is not None)
    if not vs:
        return None
    below = sum(1 for v in vs if v < x)
    equal = sum(1 for v in vs if v == x)
    return round((below + 0.5 * equal) / len(vs), 4)


class MetricBaseline:
    """平台内指标分布（用于算百分位）。生产版应有独立的 baseline 表按天维护。"""

    def __init__(self) -> None:
        self.by_platform: Dict[str, Dict[str, List[float]]] = {}

    def add(self, platform: str, metrics: Dict[str, Any]) -> None:
        slot = self.by_platform.setdefault(platform, {"views": [], "likes": [], "comments": []})
        for k in ("views", "likes", "comments"):
            v = metrics.get(k)
            if isinstance(v, (int, float)):
                slot[k].append(float(v))

    def pct(self, platform: str, metric: str, value: Any) -> Optional[float]:
        if not isinstance(value, (int, float)):
            return None
        pool = self.by_platform.get(platform, {}).get(metric) or []
        return percentile(pool, float(value))

    def sizes(self) -> Dict[str, int]:
        return {p: len(v.get("views", [])) for p, v in self.by_platform.items()}


def velocity_from_l1(store_db: Optional[str], platform: str, external_id: str,
                     metric: str = "views") -> Optional[float]:
    """从第一层的 MetricSnapshot 算速度：Δmetric / Δt（每分钟）。

    没有快照或只有一个时点 → 返回 None（不编数字）。
    """
    if not store_db or not os.path.exists(store_db):
        return None
    try:
        conn = sqlite3.connect(store_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT observed_at, " + metric + " AS v FROM metric_snapshot "
            "WHERE content_key=? ORDER BY observed_at DESC LIMIT 2",
            (content_id_of(platform, external_id),)).fetchall()
        conn.close()
    except sqlite3.Error:
        return None
    if len(rows) < 2:
        return None
    try:
        t_new = datetime.fromisoformat(rows[0]["observed_at"])
        t_old = datetime.fromisoformat(rows[1]["observed_at"])
    except (ValueError, TypeError):
        return None
    dt = (t_new - t_old).total_seconds()
    if dt <= 0 or rows[0]["v"] is None or rows[1]["v"] is None:
        return None
    return round((float(rows[0]["v"]) - float(rows[1]["v"])) / dt * 60.0, 4)


def build_features(
    content_id: str, platform: str, external_id: str,
    normalized_text: str, metrics: Dict[str, Any],
    classification: Dict[str, Any], quality: Dict[str, Any],
    entities: List[Any], baseline: MetricBaseline,
    l1_db: Optional[str] = None,
    source_features: Optional[Dict[str, Any]] = None,
    duplicate_group: Optional[str] = None,
    embedding_id: Optional[str] = None,
) -> ContentFeatures:
    """组装第三层要的特征集（§23 字段一一对应）。"""
    src = source_features or {}
    return ContentFeatures(
        content_id=content_id,
        text_length=len(normalized_text or ""),
        gaming_probability=float(classification.get("gaming_probability", 0.0)),
        category=classification.get("domain"),
        entities=[getattr(e, "entity_id", "") for e in entities],
        quality_score=float(quality.get("quality_score", 0.0)),
        spam_score=float(quality.get("spam_score", 0.0)),
        is_official=bool(src.get("is_official", False)),
        source_reliability=float(src.get("source_reliability", 0.5)),
        view_percentile=baseline.pct(platform, "views", metrics.get("views")),
        like_percentile=baseline.pct(platform, "likes", metrics.get("likes")),
        comment_percentile=baseline.pct(platform, "comments", metrics.get("comments")),
        view_velocity=velocity_from_l1(l1_db, platform, external_id, "views"),
        comment_velocity=velocity_from_l1(l1_db, platform, external_id, "comments"),
        duplicate_group=duplicate_group,
        embedding_id=embedding_id,
    )
