#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Crawl Planner —— 采集策略中心（不是 cron）。

不要写 `crawl_bilibili_every_5_min()` 这种硬编码。频率应该算出来：

    NextInterval = f(SourcePriority, ChangeRate, SourceHealth, TrendFeedback, Cost)

★ Closed-loop Adaptive Acquisition（强烈建议做的功能）：
第三层发现某话题在爆发 → 往 `acquisition.priority.command` 发事件：
    {"topic": "black_myth_dlc", "priority": "high", "ttl": 7200}
采集系统自动把对应 source 从 10 min 提到 2 min，两小时后恢复。
**用事件而不是 API 调用**，第一层和第三层就不强耦合。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

_L1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _L1 not in sys.path:
    sys.path.insert(0, _L1)

from schema.content_event import now_cn, to_iso  # noqa: E402
from bus.event_bus import EventBus  # noqa: E402

# priority 关键词 → 频率乘子（越小越勤）
PRIORITY_MULTIPLIER = {"high": 0.35, "urgent": 0.25, "normal": 1.0, "low": 1.6}

# 默认基础频率（秒）——注册表里的 base_interval_seconds 优先，这张表只作兜底说明
DEFAULT_BASE_INTERVAL = {
    "weibo_hot_search": 120, "bili_popular": 300, "tap_hot_hashtags": 300,
    "keyword": 600, "reddit": 600, "news_rss": 600, "long_tail": 1800,
}


class CrawlPlanner:
    def __init__(self, store: Any, bus: Optional[EventBus] = None) -> None:
        self.store = store
        self.bus = bus or EventBus()

    # ---------- 第三层 → 第一层：调频指令 ----------
    def read_priority_commands(self) -> List[Dict[str, Any]]:
        """读未过期的调频指令（事件驱动，不是 RPC）。"""
        now = now_cn()
        active: List[Dict[str, Any]] = []
        for rec in self.bus.consume("acquisition.priority.command"):
            p = rec.get("payload", {})
            expire = p.get("expire_at")
            if expire and to_iso(expire) and now.isoformat() > to_iso(expire):   # type: ignore[operator]
                continue
            active.append(p)
        return active

    def request_priority(self, source_id: str, priority: str = "high",
                         ttl_seconds: int = 7200, reason: str = "") -> None:
        """供第三层（或人工）调用：把某个 source 的频率提上去，ttl 后自动恢复。"""
        from datetime import timedelta
        self.bus.publish("acquisition.priority.command", {
            "source_id": source_id,
            "priority": priority,
            "ttl_seconds": ttl_seconds,
            "issued_at": now_cn().isoformat(),
            "expire_at": (now_cn() + timedelta(seconds=ttl_seconds)).isoformat(),
            "reason": reason,
        })
        # 只写事件，不直接改注册表 —— 保证调频是可回溯的（谁在什么时候要求加速）

    # ---------- 频率计算 ----------
    def next_interval(self, src: Dict[str, Any], health: Dict[str, Any],
                      change_rate: float = 0.0,
                      commands: Optional[List[Dict[str, Any]]] = None) -> int:
        base = int(src.get("base_interval_seconds") or 600)
        lo = int(src.get("min_interval_seconds") or 60)
        hi = int(src.get("max_interval_seconds") or 3600)

        mult = 1.0

        # 1) 优先级：priority ∈ [0,1]，越高越勤
        prio = float(src.get("priority") or 0.5)
        mult *= (1.6 - 1.2 * max(0.0, min(1.0, prio)))      # 0 → 1.6 倍；1 → 0.4 倍

        # 2) 变化率：内容在动就多采（change_rate = 近期指标相对变化）
        if change_rate > 0.5:
            mult *= 0.5
        elif change_rate > 0.2:
            mult *= 0.75

        # 3) 健康度：越不健康越慢（避免对一个已经挂掉的源猛砸）
        failures = int(health.get("consecutive_failures") or 0)
        if failures >= 3:
            mult *= 2.0
        elif failures >= 1:
            mult *= 1.4
        if health.get("circuit_state") == "open":
            mult *= 3.0
        if (health.get("rate_limit_count") or 0) > 0:
            mult *= 1.3

        # 4) 第三层反馈（闭环自适应采集）
        for c in (commands or []):
            tgt = c.get("source_id")
            if tgt in (src.get("source_id"), "*"):
                mult *= PRIORITY_MULTIPLIER.get(str(c.get("priority", "normal")).lower(), 1.0)

        interval = int(base * mult)
        return max(lo, min(hi, interval))

    def plan(self, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """给一批 source 算出下次该什么时候采 + 间隔。"""
        cmds = self.read_priority_commands()
        out = []
        for s in sources:
            health = self.store.get_health(s["source_id"])
            interval = self.next_interval(s, health, change_rate=self.change_rate(s["source_id"]), commands=cmds)
            ckpt = self.store.get_checkpoint(s["source_id"])
            last = ckpt.get("last_success_at")
            out.append({
                "source_id": s["source_id"],
                "platform": s["platform"],
                "interval_seconds": interval,
                "base_interval_seconds": s.get("base_interval_seconds"),
                "last_success_at": last,
                "priority": s.get("priority"),
                "due": self._is_due(last, interval),
            })
        return out

    def _is_due(self, last_success_at: Optional[str], interval: int) -> bool:
        if not last_success_at:
            return True
        try:
            last = datetime.fromisoformat(last_success_at)
        except ValueError:
            return True
        return (now_cn() - last).total_seconds() >= interval

    def change_rate(self, source_id: str) -> float:
        """用最近两次指标快照估算变化率：Δviews / max(views, 1)。没数据 → 0。"""
        rows = self.store.conn.execute(
            "SELECT views, observed_at FROM metric_snapshot WHERE source_id=? "
            "ORDER BY observed_at DESC LIMIT 2", (source_id,)).fetchall()
        if len(rows) < 2:
            return 0.0
        new, old = rows[0]["views"], rows[1]["views"]
        if new is None or old is None or old == 0:
            return 0.0
        return abs(new - old) / float(old)
