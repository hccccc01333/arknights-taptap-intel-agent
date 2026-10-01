#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Scoring Engine（§28–§35）—— Hot / Momentum / Confidence 三套分**必须分开**。

    HotScore    有多热（§28 透明规则，MVP 不上 ML）
    Momentum    还在不在继续上升（§34）
    Confidence  我们有多确定（§29/§30）★ "看起来很热，但证据还少"必须有地方表达

    HotScore = 0.22V + 0.20A + 0.15B + 0.15D + 0.10E + 0.08N + 0.05S + 0.05C
    Momentum  = 0.5·Acceleration + 0.3·VelocityGrowth + 0.2·DiffusionGrowth
    Rank      = Hot × Momentum × Confidence（§35；不乘 TapTap relevance —— 那是第四层）
"""

from __future__ import annotations

from typing import Any, Dict, Optional

W_HOT = {"velocity": 0.22, "acceleration": 0.20, "burst": 0.15, "diffusion": 0.15,
         "engagement": 0.10, "novelty": 0.08, "source_diversity": 0.05, "credibility": 0.05}


def hot_score(dims: Dict[str, float]) -> Dict[str, Any]:
    """§28：各维度已归一 0..1。缺的维度按 0 计，但记录 missing（不假装满分）。"""
    used = {k: float(v) for k, v in dims.items() if isinstance(v, (int, float))}
    s = sum(W_HOT.get(k, 0.0) * v for k, v in used.items())
    missing = sorted(set(W_HOT) - set(used))
    return {"hot_score": round(max(0.0, min(1.0, s)), 4), "weights": W_HOT,
            "dimensions": {k: round(used.get(k, 0.0), 3) for k in W_HOT},
            "missing": missing}


def momentum_score(acceleration: float, velocity_growth: float,
                   diffusion_growth: float) -> Dict[str, Any]:
    """§34：0.5·A + 0.3·V_growth + 0.2·D_growth（各量已归一 0..1）。"""
    s = 0.5 * acceleration + 0.3 * velocity_growth + 0.2 * diffusion_growth
    return {"momentum_score": round(max(0.0, min(1.0, s)), 4),
            "parts": {"acceleration": round(acceleration, 3),
                      "velocity_growth": round(velocity_growth, 3),
                      "diffusion_growth": round(diffusion_growth, 3)}}


def confidence_score(content_count: int, platform_count: int,
                     source_diversity: float, avg_quality: float,
                     cluster_coherence: float,
                     baseline_sufficient: bool) -> Dict[str, Any]:
    """§29/§30：Confidence = f(样本量, 平台覆盖, 来源多样, 数据质量, 簇内一致)。"""
    # 样本量：20 条以下证据很薄，100 条以上才算充分
    n = min(1.0, content_count / 100.0)
    p = min(1.0, platform_count / 3.0)
    s = 0.30 * n + 0.20 * p + 0.20 * source_diversity + 0.15 * avg_quality + 0.15 * cluster_coherence
    if not baseline_sufficient:
        s *= 0.8       # baseline 不足 → 相对速度不可信 → 整体降置信
    return {"confidence_score": round(max(0.0, min(1.0, s)), 4),
            "parts": {"sample": round(n, 3), "platform_coverage": round(p, 3),
                      "source_diversity": round(source_diversity, 3),
                      "quality": round(avg_quality, 3),
                      "cluster_coherence": round(cluster_coherence, 3)},
            "penalty": None if baseline_sufficient else "baseline 不足，置信 ×0.8"}


def rank_score(hot: float, momentum: float, confidence: float,
               freshness: Optional[float] = None) -> Dict[str, Any]:
    """§35 PriorityScore = Hot × Momentum × Confidence (× Freshness)。"""
    r = hot * momentum * confidence
    if freshness is not None:
        r *= freshness
    return {"rank_score": round(r, 4), "formula": "hot × momentum × confidence"
            + (" × freshness" if freshness is not None else "")}
