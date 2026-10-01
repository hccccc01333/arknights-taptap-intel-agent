#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Feedback Engine（§40 / §41）+ Publisher（§46 / §47）。

★ Active Intelligence Loop（§40）：第三层发现某事件在加速 → 给第一层发事件
  （不是 API 调用）→ 第一层提频 → 更多数据 → Confidence 上升 → 闭环。

★ 但不能无限加速（§41）：否则一个热点会吞掉全部采集资源。
    priority budget：P0 ≤ 10、P1 ≤ 50，TTL = 2h，到期自动恢复。

★ Gate（§47）：第四层不需要消费所有 update，只消费
    trend.event.emerging / trend.event.high_priority / trend.event.early_alert。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from bus.event_bus import EventBus  # noqa: E402

# §41 budget
BUDGET = {"P0": 10, "P1": 50}
TTL_SECONDS = 7200

# §47 触发第四层的门槛
GATE_HIGH_PRIORITY = {"hot": 0.65, "momentum": 0.70, "confidence": 0.60}
GATE_EARLY_ALERT = {"acceleration": 0.95}


class FeedbackEngine:
    def __init__(self, bus: Optional[EventBus] = None) -> None:
        self.bus = bus or EventBus()
        self.issued: Dict[str, Dict[str, Any]] = {}    # source_id → {level, expire_at}

    # ---------- 闭环：给第一层发调频指令 ----------
    def request_priority(self, event: Dict[str, Any], sources: List[str],
                         level: str = "P1") -> List[str]:
        """按 budget 发指令，超预算就拒绝（并说明原因）。"""
        cap = BUDGET.get(level, BUDGET["P1"])
        active = [k for k, v in self.issued.items()
                  if v["level"] == level and v["expire_at"] > datetime.now().isoformat()]
        room = cap - len(active)
        if room <= 0:
            return []
        sent = []
        for sid in sources[:room]:
            self.bus.publish("acquisition.priority.command", {
                "source_id": sid, "priority": "high" if level == "P0" else "normal",
                "ttl_seconds": TTL_SECONDS,
                "issued_at": datetime.now().isoformat(),
                "expire_at": datetime.now().isoformat(),
                "reason": f"event:{event.get('event_id')} {event.get('lifecycle')} "
                          f"hot={event.get('hot_score')} accel={event.get('acceleration_score')}",
            })
            self.issued[sid] = {"level": level, "expire_at": datetime.now().isoformat()}
            sent.append(sid)
        return sent

    # ---------- 发布给第四层 ----------
    def publish_event(self, event: Dict[str, Any], members: List[str]) -> List[str]:
        """按 §47 Gate 决定发哪些 topic。返回发出的 topic 列表。"""
        payload = {
            "event_id": event.get("event_id"),
            "canonical_title": event.get("canonical_title"),
            "event_type": event.get("event_type"),
            "entities": event.get("entity_ids") or [],
            "lifecycle": event.get("lifecycle"),
            "hot_score": event.get("hot_score"),
            "momentum_score": event.get("momentum_score"),
            "confidence_score": event.get("confidence_score"),
            "opportunity_score": event.get("opportunity_score"),
            "signals": {
                "velocity": event.get("velocity_score"),
                "acceleration": event.get("acceleration_score"),
                "burst": event.get("burst_score"),
                "diffusion": event.get("diffusion_score"),
                "engagement": event.get("engagement_score"),
                "novelty": event.get("novelty_score"),
            },
            "platforms": event.get("platforms") or [],
            "started_at": event.get("started_at"),
            "first_detected_at": event.get("first_detected_at"),
            "evidence_content_ids": members[:20],
        }
        topics = ["trend.event.updated"]
        hot = float(event.get("hot_score") or 0)
        mom = float(event.get("momentum_score") or 0)
        conf = float(event.get("confidence_score") or 0)
        accel = float(event.get("acceleration_score") or 0)

        if event.get("lifecycle") == "EMERGING":
            topics.append("trend.event.emerging")
        if (hot >= GATE_HIGH_PRIORITY["hot"] and mom >= GATE_HIGH_PRIORITY["momentum"]
                and conf >= GATE_HIGH_PRIORITY["confidence"]):
            topics.append("trend.event.high_priority")
        if accel >= GATE_EARLY_ALERT["acceleration"] and event.get("lifecycle") == "EMERGING":
            # ★ 早期预警：HotScore 可能还不高，但值得让第四层提前研究
            topics.append("trend.event.early_alert")
        for t in topics:
            self.bus.publish(t, payload)
        return topics

    def publish_lifecycle(self, event_id: str, frm: Optional[str], to: str) -> None:
        topic = f"trend.event.{to.lower()}"
        try:
            self.bus.publish(topic, {"event_id": event_id, "from": frm, "to": to,
                                     "changed_at": datetime.now().isoformat()})
        except KeyError:
            pass      # 未登记的生命周期 topic 不发布
