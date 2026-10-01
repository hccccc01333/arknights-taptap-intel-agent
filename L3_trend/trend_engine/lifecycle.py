#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lifecycle Engine（§31 / §32）—— 状态机：Emerging → Growing → Peaking → Declining → Dormant → Reactivated。

★ 不是按时间判断，是按**速度与加速度**判断：
    Emerging   量低 + 加速度高
    Growing    速度高 + 加速度为正 + 扩散在增
    Peaking    量高 + 加速度≈0
    Declining  速度下降 + 加速度为负
    Reactivated Dormant 后突然再次增长

★ §33：对增长团队最有价值的是 Emerging，不是 Peaking。
  等它成为热搜 Top1，创意窗口可能已经关了 —— 所以 Dashboard 默认应按
  "Opportunity Window" 排序，而不是 Hot Score。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

STATES = ("EMERGING", "GROWING", "PEAKING", "DECLINING", "DORMANT", "REACTIVATED")

# 合法迁移（§32 状态机）
TRANSITIONS = {
    "EMERGING": {"GROWING", "DECLINING", "DORMANT"},
    "GROWING":  {"PEAKING", "DECLINING", "DORMANT"},
    "PEAKING":  {"DECLINING", "DORMANT"},
    "DECLINING": {"DORMANT", "REACTIVATED", "GROWING"},
    "DORMANT":  {"REACTIVATED"},
    "REACTIVATED": {"GROWING", "PEAKING", "DECLINING", "DORMANT"},
}


def classify(volume_norm: float, velocity: float, acceleration: float,
             diffusion_growth: float, current: Optional[str] = None,
             dormant_since_hours: Optional[float] = None) -> Dict[str, Any]:
    """返回 {lifecycle, reason, allowed_transition}。"""
    # Reactivated：Dormant 后又出现明显增长
    if current == "DORMANT" and velocity > 0.35 and acceleration > 0.55:
        return _mk("REACTIVATED", "休眠后再次增长", current)

    if acceleration > 0.60 and velocity < 0.55 and volume_norm < 0.5:
        state = "EMERGING"
        reason = "加速度高但体量尚小（最有价值的窗口）"
    elif velocity >= 0.55 and acceleration > 0.05 and diffusion_growth >= 0.0:
        state = "GROWING"
        reason = "速度快、加速度为正、扩散未减"
    elif volume_norm >= 0.6 and abs(acceleration - 0.5) <= 0.12:
        state = "PEAKING"
        reason = "体量高但加速度趋零"
    elif velocity < 0.4 and acceleration < 0.45:
        state = "DECLINING"
        reason = "速度下降、加速度为负"
    elif volume_norm < 0.15 and velocity < 0.2:
        state = "DORMANT"
        reason = "体量与速度都接近零"
    else:
        state = current or "EMERGING"
        reason = "信号不明确，维持当前状态"

    return _mk(state, reason, current)


def _mk(state: str, reason: str, current: Optional[str]) -> Dict[str, Any]:
    legal = True
    if current and state != current:
        legal = state in TRANSITIONS.get(current, set())
    return {"lifecycle": state, "reason": reason, "from": current,
            "transition_legal": legal}


def opportunity_window(hot: float, acceleration: float, lifecycle: str,
                       confidence: float) -> Dict[str, Any]:
    """§33：Opportunity Window 排序分 —— 早于爆发才有行动价值。"""
    boost = {"EMERGING": 1.35, "GROWING": 1.15, "REACTIVATED": 1.20,
             "PEAKING": 0.9, "DECLINING": 0.6, "DORMANT": 0.3}.get(lifecycle, 1.0)
    s = hot * (0.5 + 0.5 * acceleration) * boost * (0.4 + 0.6 * confidence)
    return {"opportunity_score": round(max(0.0, min(1.0, s)), 4),
            "lifecycle_boost": boost, "note": "Emerging 加权最高：窗口还在"}
