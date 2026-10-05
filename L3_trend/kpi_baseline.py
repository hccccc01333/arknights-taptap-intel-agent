#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_trend/kpi_baseline.py —— 创意目标的基线来源。

★ 问题（2026-10-05 用户提问"kpi_target 怎么定"）：
  LLM 不知道该给多少目标，只能留空 —— 留空是对的（不编数字），
  但运营还是需要一个可参考的数。所以这里给**分位数基线**：
  从我们自己采集的社区数据算出"同类内容通常能做到多少"，
  让目标值有据可依，而不是拍脑袋。

★ 为什么不从「历史活动效果」取：
  L6 的 experiment / experiment_metric 表结构齐全（有 baseline_value /
  relative_lift / p_value），但**一行数据都没有** —— 从没跑过真实活动。
  所以现阶段基线只能来自社区内容分布，等 L6 有实验数据后，
  这里应该优先读 experiment_metric 的 baseline_value（留了接口）。

★ 关键纪律：
  基线是「参考」，不是「承诺」。输出里带 n（样本量）和分位，
  样本不足时明说不足，不给数。
"""

from __future__ import annotations

import csv
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

POSTS_CSV = os.path.join(_ROOT, "data", "raw", "taptap", "community", "posts.csv")
L6_DB = os.path.join(_ROOT, "data", "state", "l6_execution.sqlite3")

MIN_SAMPLE = 30          # 少于这个样本量不给基线


def _n(v: Any) -> int:
    try:
        return int(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0


def _pct(sorted_xs: List[float], q: float) -> float:
    """分位数（线性插值）。"""
    if not sorted_xs:
        return 0.0
    if len(sorted_xs) == 1:
        return float(sorted_xs[0])
    pos = q * (len(sorted_xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return float(sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo))


def community_baseline(csv_path: str = POSTS_CSV) -> Dict[str, Any]:
    """从社区帖数据算互动基线（点赞/评论/浏览/互动率）。"""
    if not os.path.exists(csv_path):
        return {"ok": False, "reason": f"找不到 {csv_path}"}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    likes = [_n(r.get("ups")) for r in rows if _n(r.get("ups")) > 0]
    cmts = [_n(r.get("comments")) for r in rows if _n(r.get("comments")) > 0]
    views = [_n(r.get("pv_total")) for r in rows if _n(r.get("pv_total")) > 0]
    ers = [(_n(r.get("ups")) + _n(r.get("comments"))) / _n(r.get("pv_total"))
           for r in rows
           if _n(r.get("pv_total")) >= 20 and (_n(r.get("ups")) + _n(r.get("comments"))) > 0]
    n = len(rows)
    out: Dict[str, Any] = {"ok": n >= MIN_SAMPLE, "n_posts": n, "source": "TapTap 社区帖"}
    if not out["ok"]:
        out["reason"] = f"样本不足（{n} < {MIN_SAMPLE}），不给基线"
        return out
    for name, xs, unit in (("likes", likes, ""), ("comments", cmts, ""),
                           ("views", views, ""), ("engagement_rate", ers, "%")):
        if not xs:
            continue
        xs_s = sorted(xs)
        out[name] = {
            "median": round(_pct(xs_s, 0.5), 4 if unit else 0),
            "p75": round(_pct(xs_s, 0.75), 4 if unit else 0),
            "p90": round(_pct(xs_s, 0.90), 4 if unit else 0),
            "max": round(max(xs), 4 if unit else 0),
            "unit": unit,
        }
    return out


def experiment_baseline(metric_name: str = "engagement_rate") -> Dict[str, Any]:
    """★ 优先读 L6 实验的 baseline_value（真实活动效果）——没有则如实说没有。"""
    if not os.path.exists(L6_DB):
        return {"ok": False, "reason": "L6 实验库不存在"}
    import sqlite3
    con = sqlite3.connect(L6_DB)
    try:
        rows = con.execute(
            "SELECT metric_name, baseline_value, treatment_value, relative_lift, sample_size "
            "FROM experiment_metric WHERE metric_name=? AND baseline_value IS NOT NULL "
            "ORDER BY rowid DESC LIMIT 20", (metric_name,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    if not rows:
        return {"ok": False,
                "reason": "L6 还没有任何实验数据（从没跑过真实活动），"
                          "kpi 基线暂时只能取社区内容分位数"}
    vals = sorted(float(r[1]) for r in rows)
    return {"ok": True, "n": len(vals), "source": "L6 实验 baseline_value",
            "median": round(_pct(vals, 0.5), 4), "p75": round(_pct(vals, 0.75), 4),
            "relative_lift_samples": [r[3] for r in rows[:5]]}


# 指标 → (基线字段, 目标档位含义)
_METRIC_MAP = {
    "engagement_rate": ("engagement_rate", "互动率（赞+评/浏览）"),
    "engagement": ("comments", "评论数"),
    "reach": ("views", "曝光量"),
    "views": ("views", "浏览量"),
    "install": (None, "下载量（社区数据无参照，需产品侧基线）"),
    "game_follow": (None, "关注数（社区数据无参照）"),
    "ugc": (None, "UGC 投稿数（需历史活动基线）"),
}


def suggest_kpi(primary_metric: str, baseline: Optional[Dict[str, Any]] = None,
                level: str = "p75") -> Dict[str, Any]:
    """给一条创意填 kpi_target。

    level=p50 保守（做到社区中位就算成功）
    level=p75 进取（做到社区 P75 就算成功）
    ★ 只给「参照值」，措辞上不承诺 —— 样本不足时明确留空。
    """
    field, label = _METRIC_MAP.get(primary_metric or "",
                                   (None, "未知指标"))
    if not field or not baseline or not baseline.get("ok"):
        return {"value": "", "basis": "",
                "reason": (baseline or {}).get("reason") or "该指标在社区数据里无参照，需产品侧基线"}
    b = baseline.get(field) or {}
    val = b.get(level if level in b else "median")
    if val is None:
        return {"value": "", "basis": "", "reason": "基线字段缺失"}
    # 互动率存的是 0~1 比值，标 % 时要 ×100，否则 6.91% 会被显示成 0.0691%
    shown = f"{val * 100:.2f}%" if b.get("unit") == "%" else f"{val:.0f}"
    return {
        "value": shown,
        "basis": f"{label} 的社区{ {'median': '中位', 'p75': 'P75', 'p90': 'P90'}[level] }"
                 f"（n={baseline.get('n_posts')} 篇社区帖）",
        "level": level,
        "reference_only": True,     # ★ 这是参照不是承诺
        "median": b.get("median"),
        "p75": b.get("p75"),
        "p90": b.get("p90"),
    }


def fill_creatives(creatives: List[Dict[str, Any]], level: str = "p75") -> Dict[str, Any]:
    """批量给创意填 kpi_target（原地修改 + 落盘）。"""
    bl = community_baseline()
    filled = 0
    for c in creatives:
        kpi = suggest_kpi(c.get("primary_metric") or "", bl, level)
        c["kpi_target"] = kpi.get("value") or ""
        c["kpi_basis"] = kpi.get("basis") or ""
        c["kpi_note"] = kpi.get("reason") or ""
        if c["kpi_target"]:
            filled += 1
    return {"baseline": bl, "filled": filled, "total": len(creatives), "level": level}


if __name__ == "__main__":
    import json
    bl = community_baseline()
    print("社区内容基线：")
    for k in ("likes", "comments", "views", "engagement_rate"):
        if k in bl:
            b = bl[k]
            f = (lambda v: f"{v * 100:.2f}%") if b.get("unit") == "%" else (lambda v: f"{v:.0f}")
        print(f"  {k:<16} 中位 {f(b['median'])}  P75 {f(b['p75'])}  "
              f"P90 {f(b['p90'])}  最大 {f(b['max'])}")
    exp = experiment_baseline()
    print(f"\n实验基线：{exp.get('reason') or exp}")

    path = os.path.join(_ROOT, "data", "state", "growth_creatives.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        res = fill_creatives(data.get("creatives") or [])
        data["baseline"] = res["baseline"]
        data["meta"] = {"kpi_level": res["level"], "kpi_filled": res["filled"],
                        "kpi_total": res["total"],
                        "note": "kpi_target 是社区分位数参照值，不是承诺"}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n已填充 {res['filled']}/{res['total']} 条创意的 kpi_target → {path}")
        for c in (data.get("creatives") or [])[:4]:
            print(f"  [{c.get('primary_metric')}] 目标={c.get('kpi_target') or '—'}  ← {c.get('kpi_basis') or c.get('kpi_note')}")
