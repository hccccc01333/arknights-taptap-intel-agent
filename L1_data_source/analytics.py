#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快照分析：把「时间 × 指标」变成速度 / 加速度 —— 这是 L1 交给第三层的最终数据产品。

    Velocity     = Δviews / Δt
    Acceleration = ΔVelocity / Δt

为什么必须靠 Snapshot（而不是 UPDATE 当前值）：
只存"当前 views"的系统，永远算不出加速度，也就识别不了"正在起飞"和"已经饱和"。
第三层判断热点生命周期（Search→Social→Content→Community）靠的就是这两个量。

★ 数字纪律：这里所有数字都从 metric_snapshot 表里读，不估算、不插值、不编。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

_L1 = os.path.dirname(os.path.abspath(__file__))
if _L1 not in sys.path:
    sys.path.insert(0, _L1)

from storage.metadata_db import MetadataStore  # noqa: E402


def _ts(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def series(store: MetadataStore, content_key: Optional[str] = None,
           source_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """取一条内容的快照序列（按时间升序）。"""
    if content_key:
        rows = store.snapshots_of(content_key, limit=limit)
    else:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT * FROM metric_snapshot WHERE source_id=? ORDER BY observed_at LIMIT ?",
            (source_id, limit)).fetchall()]
    return rows


def velocity(rows: List[Dict[str, Any]], metric: str = "views") -> List[Dict[str, Any]]:
    """逐点算速度与加速度：需要至少 2 / 3 个时点。"""
    out: List[Dict[str, Any]] = []
    pts = []
    for r in rows:
        t = _ts(r.get("observed_at") or "")
        v = r.get(metric)
        if t is None or v is None:
            continue
        pts.append((t, float(v), r.get("observed_at")))
    for i in range(1, len(pts)):
        dt = (pts[i][0] - pts[i - 1][0]).total_seconds()
        if dt <= 0:
            continue
        dv = pts[i][1] - pts[i - 1][1]
        vel = dv / dt          # 单位/秒
        acc = None
        if i >= 2:
            dt2 = (pts[i - 1][0] - pts[i - 2][0]).total_seconds()
            if dt2 > 0:
                prev_vel = (pts[i - 1][1] - pts[i - 2][1]) / dt2
                acc = (vel - prev_vel) / dt
        out.append({
            "observed_at": pts[i][2], "dt_seconds": round(dt, 1),
            metric: pts[i][1], "delta": dv,
            "velocity_per_min": round(vel * 60, 3),
            "acceleration": None if acc is None else round(acc * 3600, 4),
        })
    return out


def freshness(store: MetadataStore, source_id: str) -> Dict[str, Any]:
    """FreshnessLag = now - latest observed_at（热点系统的核心 SLO）。"""
    r = store.conn.execute(
        "SELECT MAX(observed_at) AS latest FROM metric_snapshot WHERE source_id=?",
        (source_id,)).fetchone()
    latest = r["latest"] if r else None
    t = _ts(latest) if latest else None
    lag = None
    if t:
        lag = (datetime.now(t.tzinfo) - t).total_seconds()
    return {"source_id": source_id, "latest_observed_at": latest,
            "freshness_lag_seconds": None if lag is None else round(lag, 1)}
