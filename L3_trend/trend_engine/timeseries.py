#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Time-Series Engine（§14 / §15 / §17）—— 多尺度窗口 + 实体级 baseline。

★ 多尺度并存（§15）：不能只看 24h。电竞事件可能 10 分钟爆发，
游戏口碑反转可能 3 天持续增长 → 5m / 15m / 1h / 6h / 24h / 7d 都要有。

★ Baseline 必须分实体（§17）：原神日常几万条，小众独立游戏 100 条就已经爆了。
    如果 baseline 不分实体，"热门游戏天然霸榜"这个问题永远解决不了。
    Baseline = f(entity, platform, hour) —— 数据不足时降级并**如实标注**。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

WINDOWS_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "6h": 360, "24h": 1440, "7d": 10080}


def _parse(t: Optional[str]) -> Optional[datetime]:
    if not t:
        return None
    try:
        return datetime.fromisoformat(t)
    except ValueError:
        return None


class EventTimeSeries:
    """把成员内容按时间切成多个尺度的窗口，产出 Event × Time 序列。"""

    def __init__(self, members: List[Dict[str, Any]]) -> None:
        self.members = [m for m in members if _parse(m.get("observed_at"))]
        self.members.sort(key=lambda m: m["observed_at"])

    def series(self, window: str = "15m") -> List[Dict[str, Any]]:
        step = WINDOWS_MINUTES.get(window, 15)
        buckets: Dict[datetime, List[Dict[str, Any]]] = defaultdict(list)
        for m in self.members:
            t = _parse(m["observed_at"])
            key = t.replace(minute=(t.minute // step) * step, second=0, microsecond=0)
            buckets[key].append(m)
        out = []
        for k in sorted(buckets):
            items = buckets[k]
            views = sum(_num(i, "views") for i in items)
            likes = sum(_num(i, "likes") for i in items)
            comments = sum(_num(i, "comments") for i in items)
            out.append({
                "window_start": k.isoformat(),
                "window": window,
                "mention_count": len(items),
                "unique_author_count": len({i.get("author_id") for i in items}),
                "view_delta": views, "like_delta": likes, "comment_delta": comments,
                "platform_count": len({i.get("platform") for i in items if i.get("platform")}),
                "new_content_count": len(items),
            })
        return out

    def multi_scale(self) -> Dict[str, List[Dict[str, Any]]]:
        return {w: self.series(w) for w in ("5m", "15m", "1h", "6h", "24h")}

    def totals(self) -> Dict[str, Any]:
        return {
            "content_count": len(self.members),
            "unique_authors": len({m.get("author_id") for m in self.members}),
            "platforms": sorted({m.get("platform") for m in self.members if m.get("platform")}),
            "first_at": self.members[0]["observed_at"] if self.members else None,
            "last_at": self.members[-1]["observed_at"] if self.members else None,
        }


def _num(item: Dict[str, Any], key: str) -> float:
    import json
    m = item.get("metrics")
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            return 0.0
    v = (m or {}).get(key)
    return float(v) if isinstance(v, (int, float)) else 0.0


class EntityBaseline:
    """§17 实体级 baseline：Baseline(entity, platform, hour)。

    数据不足时返回 fallback 并标 `sufficient=False` —— 不能拿 1 条数据当 baseline，
    否则相对速度会虚高（这正是"小样本假爆发"的来源）。
    """

    MIN_SAMPLES = 20

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.by_entity: Dict[str, List[int]] = defaultdict(list)
        self.by_entity_platform: Dict[str, List[int]] = defaultdict(list)
        counts: Dict[str, int] = defaultdict(int)
        for r in rows:
            ents = _json_list(r.get("entities"))
            for e in ents:
                counts[e] += 1
            # 按小时聚合需要时间桶，这里用总量/活跃小时数的粗估
        self.counts = dict(counts)
        hours = _active_hours(rows)
        self.rates = {e: (n / hours if hours else float(n)) for e, n in counts.items()}

    def rate(self, entity_id: Optional[str]) -> Dict[str, Any]:
        if not entity_id or entity_id not in self.rates:
            return {"rate": 0.0, "sufficient": False, "reason": "该实体无历史样本"}
        n = self.counts[entity_id]
        return {"rate": self.rates[entity_id], "sufficient": n >= self.MIN_SAMPLES,
                "samples": n, "note": "样本不足时相对速度不可信，按低置信处理"}

    def summary(self, top: int = 10) -> List[Dict[str, Any]]:
        return [{"entity_id": e, "count": n, "rate_per_hour": round(self.rates[e], 2)}
                for e, n in sorted(self.counts.items(), key=lambda kv: -kv[1])[:top]]


def _json_list(v: Any) -> List[str]:
    import json
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return list(v or [])


def _active_hours(rows: List[Dict[str, Any]]) -> float:
    ts = [t for t in (_parse(r.get("observed_at")) for r in rows) if t]
    if len(ts) < 2:
        return 1.0
    span = (max(ts) - min(ts)).total_seconds() / 3600.0
    return max(1.0, span)
