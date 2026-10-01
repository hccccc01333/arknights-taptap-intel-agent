#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Trend Feature Engine（§16–§27）—— 八个信号，全部归一到 0..1。

    Velocity(相对)   §16  相对 baseline 的倍数，比绝对数有意义
    Acceleration     §18  "是不是越来越快" —— 增长团队最该看的
    Burst            §19  Z-score：速度高不一定异常
    Novelty          §20  1 - 与历史事件最大相似度（防止老事件重复报警）
    Diffusion        §21/§22 平台数 + 熵（只有一个平台占 95% 不算扩散）
    Engagement       §24/§25 百分位加权，share/comment 权重高于 like
    SourceDiversity  §26  100 条来自 100 个作者 vs 5 个营销号复制 20 次
    Credibility      §27  官方/独立信源占比（不是判真假，是判"证据可信程度"）

★ 所有信号在样本不足时返回 None 或低值 + `reason`，绝不硬编一个好看的数。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _clip01(x: float) -> float:
    return round(max(0.0, min(1.0, x)), 4)


def velocity_score(rate_now: float, baseline_rate: float, sufficient: bool) -> Dict[str, Any]:
    """§16 相对速度 RV = 当前速率 / baseline 速率。"""
    if baseline_rate <= 0:
        return {"score": 0.5 if rate_now > 0 else 0.0, "rv": None,
                "reason": "无 baseline，按中性处理", "sufficient": sufficient}
    rv = rate_now / baseline_rate
    # log 压缩：RV=15 已经很猛，RV=100 没必要再线性放大
    s = math.log1p(max(0.0, rv)) / math.log1p(20.0)
    return {"score": _clip01(s), "rv": round(rv, 2), "sufficient": sufficient}


def acceleration_score(vel_series: List[float]) -> Dict[str, Any]:
    """§18：Velocity_t - Velocity_{t-1}（归一后）。"""
    if len(vel_series) < 2:
        return {"score": 0.0, "reason": "速度序列不足 2 点"}
    diffs = [vel_series[i] - vel_series[i - 1] for i in range(1, len(vel_series))]
    last = diffs[-1]
    scale = max(1.0, max(abs(d) for d in diffs))
    return {"score": _clip01(0.5 + 0.5 * (last / scale)), "delta": round(last, 3),
            "points": len(vel_series)}


def burst_score(values: List[float], window: int = 10) -> Dict[str, Any]:
    """§19 Z-score：Z = (x_t - μ) / (σ + ε)，Z>3 视为明显异常。"""
    if len(values) < 3:
        return {"score": 0.0, "z": None, "reason": "样本不足（<3）"}
    hist, cur = values[:-1], values[-1]
    mu = sum(hist) / len(hist)
    var = sum((v - mu) ** 2 for v in hist) / len(hist)
    sigma = math.sqrt(var)
    z = (cur - mu) / (sigma + 1e-6)
    return {"score": _clip01(z / 5.0), "z": round(z, 3), "mu": round(mu, 2),
            "sigma": round(sigma, 3), "burst": bool(z > 3)}


def novelty_score(max_sim_history: Optional[float]) -> Dict[str, Any]:
    """§20 Novelty = 1 - max_similarity(与历史事件)。"""
    if max_sim_history is None:
        return {"score": 1.0, "reason": "无历史事件可比，按全新处理"}
    return {"score": _clip01(1.0 - max_sim_history), "max_sim_history": round(max_sim_history, 3)}


def diffusion_score(platform_counts: Dict[str, int], tracked_platforms: int = 5) -> Dict[str, Any]:
    """§21/§22：平台数 + 熵。只有平台数会误判"一平台 95%"的情况。"""
    if not platform_counts:
        return {"score": 0.0, "platform_count": 0, "entropy": 0.0}
    total = sum(platform_counts.values()) or 1
    probs = [v / total for v in platform_counts.values()]
    entropy = -sum(p * math.log(p) for p in probs if p > 0)
    max_entropy = math.log(len(probs)) if len(probs) > 1 else 1.0
    ent_norm = entropy / max_entropy if max_entropy > 0 else 0.0
    cov = min(1.0, len(probs) / max(1, tracked_platforms))
    return {"score": _clip01(0.5 * cov + 0.5 * ent_norm),
            "platform_count": len(probs), "entropy": round(entropy, 3),
            "entropy_norm": round(ent_norm, 3), "coverage": round(cov, 3)}


def engagement_score(percentiles: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """§24/§25：E = 0.15·P(view) + 0.30·P(comment) + 0.40·P(share) + 0.15·P(favorite)。
    share/comment 权重高于 like —— 对传播而言它们更能代表扩散。
    """
    w = {"view": 0.15, "comment": 0.30, "share": 0.40, "favorite": 0.15}
    vals = {k: percentiles.get(k) for k in w}
    present = {k: v for k, v in vals.items() if isinstance(v, (int, float))}
    if not present:
        return {"score": 0.0, "reason": "无百分位特征"}
    # 缺的维度按已有一均值补，但记录下来（不假装都有）
    avg = sum(present.values()) / len(present)
    s = sum(w[k] * (vals.get(k) if isinstance(vals.get(k), (int, float)) else avg) for k in w)
    return {"score": _clip01(s), "used": sorted(present), "filled_by_avg": sorted(set(w) - set(present))}


def diversity_score(unique_authors: int, mentions: int, original_ratio: float) -> Dict[str, Any]:
    """§26：1000 条只有 17 个作者 → 降权。"""
    if mentions <= 0:
        return {"score": 0.0, "unique_authors": 0}
    r = unique_authors / mentions
    return {"score": _clip01(0.6 * min(1.0, r * 3) + 0.4 * original_ratio),
            "unique_authors": unique_authors, "mentions": mentions,
            "author_ratio": round(r, 3), "original_ratio": round(original_ratio, 3)}


def credibility_score(official_ratio: float, independent_sources: int,
                      avg_quality: float) -> Dict[str, Any]:
    """§27：不是判真假，是判"当前证据可信程度"。第四层可进一步核验。"""
    indep = min(1.0, independent_sources / 5.0)
    return {"score": _clip01(0.5 * official_ratio + 0.3 * indep + 0.2 * avg_quality),
            "official_ratio": round(official_ratio, 3),
            "independent_sources": independent_sources, "avg_quality": round(avg_quality, 3)}
