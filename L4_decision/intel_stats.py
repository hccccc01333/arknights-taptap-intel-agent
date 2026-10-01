#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L4_decision/intel_stats.py — 零依赖统计函数（两比例 z 检验 / Wilson CI）。

与 `L2_signal/lab/anomaly_diagnosis.py` 的统计口径一致（pooled SE、双侧 α、
Wilson score interval），但纯标准库实现——让情报 Agent 的感知层
在没有任何第三方依赖的环境下也能完成显著性检验。

口径说明：
  - 检验：two-proportion pooled z-test，双侧，α=0.05
  - 区间：95% Wilson score interval（小样本下不出界）
  - 完整 Kitagawa 三分解仍在 L2_signal/lab（需 pandas）；本模块只提供
    Agent 感知层所需的最小统计集。
"""

from __future__ import annotations

import math

ALPHA = 0.05
Z95 = 1.959963984540054  # Φ^{-1}(0.975)


def norm_sf_two_sided(z: float) -> float:
    """双侧 p 值：P(|Z| >= |z|)，用 erf 实现，无 scipy。"""
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))


def two_prop_ztest(
    x1: int, n1: int, x0: int, n0: int
) -> tuple[float | None, float | None, bool | None]:
    """两比例 pooled z 检验（双侧）。返回 (z, p_value, significant@ALPHA)。

    守卫：任一侧样本量 n<=0 或计数 x<0 / x>n（数据完整性）→ (None, None, None)；
    pooled 方差为 0（两侧同全 0 / 同全 1）时给出退化处理并仍返回判定。
    """
    if n1 <= 0 or n0 <= 0:
        return None, None, None
    if x1 < 0 or x0 < 0 or x1 > n1 or x0 > n0:
        return None, None, None
    p1, p0 = x1 / n1, x0 / n0
    pooled = (x1 + x0) / (n1 + n0)
    se2 = pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n0)
    if se2 <= 0:
        z = 0.0 if abs(p1 - p0) < 1e-15 else math.copysign(math.inf, p1 - p0)
        p_val = 1.0 if z == 0.0 else 0.0
        return (z if math.isfinite(z) else None), p_val, p_val < ALPHA
    z = (p1 - p0) / math.sqrt(se2)
    p_val = norm_sf_two_sided(z)
    return z, p_val, p_val < ALPHA


def wilson_ci(x: int, n: int, z: float = Z95) -> tuple[float | None, float | None]:
    """95% Wilson score interval。n<=0 → (None, None)。

    x<=0 时下界恒为 0、x>=n 时上界恒为 1（浮点显式 clamp，避免 0.999…）。
    """
    if n <= 0:
        return None, None
    p = x / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / denom
    lo, hi = center - margin, center + margin
    if x <= 0:
        lo = 0.0
    if x >= n:
        hi = 1.0
    return max(0.0, lo), min(1.0, hi)


def sample_warnings(cur_n: int, prior_n: int, min_n: int = 30) -> list[str]:
    """样本量警告（与日周报纪律对齐）：任一侧 n 不足或对照窗偏薄。"""
    warns: list[str] = []
    if cur_n < min_n:
        warns.append(f"本期 n={cur_n} < {min_n}，负向率点估计不稳，慎用 Δpp 叙事")
    if prior_n < min_n:
        warns.append(f"对照 n={prior_n} < {min_n}，禁止「大幅异动」结论")
    if cur_n > 0 and 0 < prior_n < 0.5 * cur_n:
        warns.append(f"对照 n={prior_n} < 0.5×本期（{cur_n}），对照窗偏薄，显著性仅供参考")
    return warns


def allow_delta_narrative(cur_n: int, prior_n: int, min_n: int = 30) -> bool:
    """是否允许「显著异动」叙事：两侧样本量门槛 + 对照窗厚度。"""
    return cur_n >= min_n and prior_n >= min_n and prior_n >= 0.5 * cur_n
