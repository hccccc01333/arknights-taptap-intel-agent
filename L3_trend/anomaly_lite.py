#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L4_decision/anomaly_lite.py — 零依赖异动感知 tool（供每日情报 Agent）。

定位：`L2_signal/lab/anomaly_diagnosis.py` 的 lite 版——统计口径一致
（pooled 两比例 z 检验 + Wilson CI），纯标准库实现，作为 Agent 感知层
在无 pandas 环境下的替代。完整 Kitagawa 三分解仍在分析实验室。

输入：data/annotations/annotations_v1_4.csv × data/processed/reviews/reviews_clean.csv
输出：outputs/anomaly_lite.json
  - primary_comparison_id      情报重点窗（最新 7 天 vs 前 7 天）
  - comparisons[]              最近 SCAN_WINDOWS 个周窗检验（背景表）
  - top_contributors[]         primary 窗主题负向数变化排序（Top）
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
OUT_DIR = LAB / "outputs"
SCAN_WINDOWS = 4  # 展示最近 4 个周窗作为背景
MIN_N = 30

ALPHA = 0.05

# 保证直接运行 / 被测试加载两种场景都能 import 同目录模块
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

from intel_stats import (  # noqa: E402
    allow_delta_narrative,
    sample_warnings,
    two_prop_ztest,
    wilson_ci,
)


def _load_risk_module():
    spec = importlib.util.spec_from_file_location(
        "risk_insight", str(LAB / "risk_insight.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def daily_counts(rows: list[dict[str, Any]]) -> dict[date, list[int]]:
    """按东八区日历日聚合 (n, neg)。"""
    out: dict[date, list[int]] = {}
    for r in rows:
        t = r.get("publish_time_cn") or ""
        if len(t) < 10:
            continue
        try:
            d = datetime.strptime(t[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        cell = out.setdefault(d, [0, 0])
        cell[0] += 1
        if r.get("sentiment") == "负":
            cell[1] += 1
    return out


def agg_window(counts: dict[date, list[int]], start: date, end: date) -> tuple[int, int]:
    n = x = 0
    for d, (dn, dx) in counts.items():
        if start <= d <= end:
            n += dn
            x += dx
    return x, n


def compare_week(counts: dict[date, list[int]], end: date) -> dict[str, Any] | None:
    """end 结尾的 7 天 vs 其前 7 天。"""
    s1 = end - timedelta(days=6)
    e0 = s1 - timedelta(days=1)
    s0 = e0 - timedelta(days=6)
    x1, n1 = agg_window(counts, s1, end)
    x0, n0 = agg_window(counts, s0, e0)
    if n1 == 0 and n0 == 0:
        return None

    z, p_val, sig = two_prop_ztest(x1, n1, x0, n0)
    delta = (x1 / n1 - x0 / n0) if (n1 and n0) else None
    lo1, hi1 = wilson_ci(x1, n1)
    lo0, hi0 = wilson_ci(x0, n0)
    allow = allow_delta_narrative(n1, n0, MIN_N)
    warns = sample_warnings(n1, n0, MIN_N)

    if n1 == 0 or n0 == 0:
        verdict = "样本为空，无法比较"
        sig_flag = False
    elif not allow:
        verdict = "样本不足：仅报绝对水平，不做「显著异动」认定"
        sig_flag = False
    elif sig and delta is not None and delta > 0:
        verdict = "负向率相对对照显著上升（α=0.05）"
        sig_flag = True
    elif sig and delta is not None and delta < 0:
        verdict = "负向率相对对照显著下降（α=0.05）"
        sig_flag = True
    elif sig is False:
        verdict = "变化未达统计显著：可能是抽样波动，不宜写成「异动已确认」"
        sig_flag = False
    else:
        verdict = "检验不可用"
        sig_flag = False

    def pp(x: float | None) -> float | None:
        return round(x * 100.0, 2) if x is not None else None

    return {
        "id": "week_vs_prev",
        "label": f"本周({s1.isoformat()}~{end.isoformat()}) vs 上周({s0.isoformat()}~{e0.isoformat()})",
        "period": {
            "start": s1.isoformat(),
            "end": end.isoformat(),
            "n": n1,
            "neg": x1,
            "neg_rate": (x1 / n1) if n1 else None,
            "neg_rate_pp": pp((x1 / n1) if n1 else None),
            "wilson_ci_pp": [pp(lo1), pp(hi1)],
        },
        "prior": {
            "start": s0.isoformat(),
            "end": e0.isoformat(),
            "n": n0,
            "neg": x0,
            "neg_rate": (x0 / n0) if n0 else None,
            "neg_rate_pp": pp((x0 / n0) if n0 else None),
            "wilson_ci_pp": [pp(lo0), pp(hi0)],
        },
        "delta_pp": pp(delta) if delta is not None else None,
        "z_stat": round(z, 4) if z is not None and math.isfinite(z) else None,
        "p_value": round(p_val, 4) if p_val is not None else None,
        "alpha": ALPHA,
        "significant": bool(sig_flag),
        "allow_delta_narrative": allow,
        "sample_warning": warns,
        "verdict": verdict,
    }


def topic_contributions(
    rows: list[dict[str, Any]], start: date, end: date, prior_start: date, prior_end: date
) -> list[dict[str, Any]]:
    """primary 窗各主题：两窗负向数与负向率变化，按负向数变化绝对值排序。"""
    TOPIC_CN = {
        "gacha": "抽卡/商业化",
        "balance": "数值/平衡/养成",
        "gameplay": "关卡/玩法",
        "story": "剧情/世界观",
        "event": "活动/版本",
        "client": "性能/客户端",
        "ops": "运营/客服/规则",
        "other": "综合/其他",
    }
    cur: dict[str, list[int]] = {}
    prior: dict[str, list[int]] = {}
    for r in rows:
        t = r.get("publish_time_cn") or ""
        if len(t) < 10:
            continue
        try:
            d = datetime.strptime(t[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        topic = r.get("topic") or "other"
        if start <= d <= end:
            cell = cur.setdefault(topic, [0, 0])
        elif prior_start <= d <= prior_end:
            cell = prior.setdefault(topic, [0, 0])
        else:
            continue
        cell[0] += 1
        if r.get("sentiment") == "负":
            cell[1] += 1
    out: list[dict[str, Any]] = []
    for topic in set(cur) | set(prior):
        n1, x1 = cur.get(topic, [0, 0])
        n0, x0 = prior.get(topic, [0, 0])
        if n1 == 0 and n0 == 0:
            continue
        r1 = (x1 / n1) if n1 else None
        r0 = (x0 / n0) if n0 else None
        out.append(
            {
                "topic": topic,
                "topic_cn": TOPIC_CN.get(topic, topic),
                "n0": n0,
                "n1": n1,
                "neg0": x0,
                "neg1": x1,
                "neg_delta": x1 - x0,
                "rate0_pp": round(r0 * 100, 2) if r0 is not None else None,
                "rate1_pp": round(r1 * 100, 2) if r1 is not None else None,
                "rate_delta_pp": (
                    round((r1 - r0) * 100, 2) if (r1 is not None and r0 is not None) else None
                ),
            }
        )
    out.sort(key=lambda r: abs(r["neg_delta"]), reverse=True)
    return out[:6]


def main() -> None:
    ap = argparse.ArgumentParser(description="零依赖异动感知（lite）")
    ap.add_argument(
        "--ann",
        default=str(ROOT / "data/annotations" / "annotations_v1_4.csv"),
        help="annotations CSV path",
    )
    args = ap.parse_args()

    ri = _load_risk_module()
    rows, _avail = ri.load_merged(Path(args.ann))
    counts = daily_counts(rows)
    if not counts:
        payload: dict[str, Any] = {
            "available": False,
            "reason": "无带发布日期的样本",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
    else:
        latest = max(counts)
        comparisons: list[dict[str, Any]] = []
        end = latest
        for _ in range(SCAN_WINDOWS):
            c = compare_week(counts, end)
            if c is not None:
                comparisons.append(c)
            end = end - timedelta(days=7)
        primary = comparisons[0] if comparisons else None
        top: list[dict[str, Any]] = []
        if primary:
            p = primary["period"]
            pr = primary["prior"]
            top = topic_contributions(
                rows,
                date.fromisoformat(p["start"]),
                date.fromisoformat(p["end"]),
                date.fromisoformat(pr["start"]),
                date.fromisoformat(pr["end"]),
            )
        headline = (
            f"{primary['label']}：Δneg={primary['delta_pp']:+.2f}pp，"
            f"p={primary['p_value']}，{primary['verdict']}"
            if primary
            else "样本不足，无法做异动检验。"
        )
        payload = {
            "schema_version": "1.0",
            "available": primary is not None,
            "engine": "intel_stats (stdlib)",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "channel": "taptap",
            "headline": headline,
            "primary_comparison_id": (primary or {}).get("id"),
            "comparisons": comparisons,
            "top_contributors": [
                {
                    "topic": t["topic_cn"],
                    "total_pp": t["rate_delta_pp"],
                    "neg_delta": t["neg_delta"],
                    "kind": "topic_delta",
                }
                for t in top
            ],
            "methods": {
                "test": "two-proportion z-test (pooled SE), two-sided, alpha=0.05",
                "ci": "95% Wilson score interval",
                "windows": f"latest {SCAN_WINDOWS} calendar-week comparisons",
                "note": "完整 Kitagawa 分解见 L2_signal/lab（需 pandas）",
            },
        }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "anomaly_lite.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(out_path)
    print(f"headline={payload.get('headline')}")


if __name__ == "__main__":
    main()
