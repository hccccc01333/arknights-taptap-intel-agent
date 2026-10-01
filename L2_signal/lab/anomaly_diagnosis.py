#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0-1 异动诊断：本期 vs 上期负向率显著性 + 主题贡献瀑布分解。

输入：TapTap `annotations_v1_4.csv` × `reviews_clean.csv`
输出：
  - outputs/anomaly_diagnosis.json  （供看板 lab.anomaly 消费）
  - reports/anomaly_diagnosis_*.md

方法摘要见报告「方法」节；样本不足时诚实标「不显著 / 仅绝对水平」。
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[2]
LAB = Path(__file__).resolve().parent
OUT_DIR = LAB / "outputs"
REPORT_DIR = LAB / "reports"

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

# 与日周报纪律对齐
MIN_N_WARN = 30
ALPHA = 0.05
Z95 = 1.959963984540054  # Φ^{-1}(0.975)


def round4(x: float | None) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), 4)


def round_pp(x: float | None) -> float | None:
    """Percentage points, 2 decimals."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x) * 100.0, 2)


def norm_cdf(z: float) -> float:
    """Standard normal CDF via erf (no scipy)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def wilson_ci(successes: int, n: int, z: float = Z95) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def two_prop_ztest(
    x1: int, n1: int, x0: int, n0: int
) -> tuple[float | None, float | None, bool | None]:
    """Pooled two-proportion z-test (two-sided). Returns z, p_value, significant@alpha."""
    if n1 <= 0 or n0 <= 0:
        return None, None, None
    # 期望计数过低时仍算 z，但调用方会叠 sample_warning
    p1, p0 = x1 / n1, x0 / n0
    pooled = (x1 + x0) / (n1 + n0)
    se2 = pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n0)
    if se2 <= 0:
        # 两端均为 0 或均为 1
        z = 0.0 if abs(p1 - p0) < 1e-15 else math.copysign(math.inf, p1 - p0)
        p_val = 1.0 if z == 0.0 else 0.0
        return z if math.isfinite(z) else None, p_val, p_val < ALPHA
    z = (p1 - p0) / math.sqrt(se2)
    p_val = 2.0 * (1.0 - norm_cdf(abs(z)))
    return z, p_val, p_val < ALPHA


@dataclass
class PeriodStats:
    label: str
    start: date
    end: date
    days: list[date]
    n: int
    neg: int
    pos: int
    neu: int
    neg_rate: float
    wilson_lo: float | None
    wilson_hi: float | None
    topic_n: dict[str, int]
    topic_neg: dict[str, int]


def load_annotated(ann_path: Path) -> pd.DataFrame:
    clean = pd.read_csv(
        ROOT / "data/processed/reviews" / "reviews_clean.csv",
        dtype={"review_id": str},
    )
    ann = pd.read_csv(ann_path, dtype={"review_id": str})
    ann = ann[ann["error"].fillna("") == ""].drop_duplicates("review_id", keep="last")
    ann = ann[~ann["review_id"].astype(str).str.startswith("probe_")]
    keep_ann = [
        "review_id",
        "topic_primary",
        "sentiment",
        "actionable",
        "rhetoric",
        "prompt_version",
        "model",
        "score_raw",
    ]
    for c in keep_ann:
        if c not in ann.columns:
            ann[c] = ""
    m = ann[keep_ann].merge(
        clean[["review_id", "text", "publish_time_cn"]],
        on="review_id",
        how="inner",
    )
    m["publish_dt"] = pd.to_datetime(m["publish_time_cn"], errors="coerce")
    # 统一到东八区日历日
    try:
        m["day"] = m["publish_dt"].dt.tz_convert(TZ).dt.date
    except TypeError:
        m["day"] = m["publish_dt"].dt.tz_localize(TZ).dt.date
    m = m.dropna(subset=["day"]).copy()
    m["topic_primary"] = m["topic_primary"].fillna("other").replace("", "other")
    m["sentiment"] = m["sentiment"].fillna("")
    m["is_neg"] = (m["sentiment"] == "负").astype(int)
    return m


def period_stats(df: pd.DataFrame, label: str, start: date, end: date) -> PeriodStats:
    sub = df[(df["day"] >= start) & (df["day"] <= end)].copy()
    days = sorted(sub["day"].unique().tolist())
    n = len(sub)
    neg = int(sub["is_neg"].sum())
    pos = int((sub["sentiment"] == "正").sum())
    neu = int((sub["sentiment"] == "中").sum())
    lo, hi = wilson_ci(neg, n)
    topic_n = sub.groupby("topic_primary").size().to_dict()
    topic_neg = sub.groupby("topic_primary")["is_neg"].sum().astype(int).to_dict()
    return PeriodStats(
        label=label,
        start=start,
        end=end,
        days=[d if isinstance(d, date) else d for d in days],
        n=n,
        neg=neg,
        pos=pos,
        neu=neu,
        neg_rate=(neg / n) if n else 0.0,
        wilson_lo=lo,
        wilson_hi=hi,
        topic_n={str(k): int(v) for k, v in topic_n.items()},
        topic_neg={str(k): int(v) for k, v in topic_neg.items()},
    )


def sample_warnings(cur_n: int, prior_n: int) -> list[str]:
    warns: list[str] = []
    if cur_n < MIN_N_WARN:
        warns.append(f"本期 n={cur_n} < {MIN_N_WARN}，负向率点估计不稳，慎用 Δpp 叙事")
    if prior_n < MIN_N_WARN:
        warns.append(f"对照 n={prior_n} < {MIN_N_WARN}，禁止「大幅异动」结论")
    if cur_n > 0 and prior_n < 0.5 * cur_n:
        warns.append(
            f"对照 n={prior_n} < 0.5×本期（{cur_n}），对照窗偏薄，显著性仅供参考"
        )
    if cur_n > 0 and prior_n > 0:
        # 两比例 z 正态近似粗检：期望负向计数
        # 用粗略 pooled 0.3 作提示阈值
        for label, n in (("本期", cur_n), ("对照", prior_n)):
            if n * 0.25 < 5:  # 粗略：期望负向过少
                warns.append(f"{label}期望负向计数可能 <5，z 检验正态近似偏弱")
                break
    return warns


def compare_periods(
    cur: PeriodStats,
    prior: PeriodStats,
    *,
    comparison_id: str,
    label: str,
) -> dict[str, Any]:
    z, p_val, sig = two_prop_ztest(cur.neg, cur.n, prior.neg, prior.n)
    delta = cur.neg_rate - prior.neg_rate
    warns = sample_warnings(cur.n, prior.n)
    allow_delta = (
        cur.n >= MIN_N_WARN
        and prior.n >= MIN_N_WARN
        and prior.n >= 0.5 * cur.n
    )

    if cur.n == 0 or prior.n == 0:
        verdict = "样本为空，无法比较"
        sig_flag = False
    elif not allow_delta:
        verdict = "样本不足：仅报绝对水平，不做「显著异动」认定"
        sig_flag = False
    elif sig is True and delta > 0:
        verdict = "负向率相对对照显著上升（α=0.05）"
        sig_flag = True
    elif sig is True and delta < 0:
        verdict = "负向率相对对照显著下降（α=0.05）"
        sig_flag = True
    elif sig is False:
        verdict = "变化未达统计显著：可能是抽样波动，不宜写成「异动已确认」"
        sig_flag = False
    else:
        verdict = "检验不可用"
        sig_flag = False

    def pack(ps: PeriodStats) -> dict[str, Any]:
        return {
            "label": ps.label,
            "start": ps.start.isoformat(),
            "end": ps.end.isoformat(),
            "n": ps.n,
            "pos": ps.pos,
            "neu": ps.neu,
            "neg": ps.neg,
            "neg_rate": round4(ps.neg_rate),
            "neg_rate_pp": round_pp(ps.neg_rate),
            "wilson_ci": [
                round4(ps.wilson_lo) if ps.wilson_lo is not None else None,
                round4(ps.wilson_hi) if ps.wilson_hi is not None else None,
            ],
            "wilson_ci_pp": [
                round_pp(ps.wilson_lo) if ps.wilson_lo is not None else None,
                round_pp(ps.wilson_hi) if ps.wilson_hi is not None else None,
            ],
            "days": [d.isoformat() for d in ps.days],
        }

    return {
        "id": comparison_id,
        "label": label,
        "period": pack(cur),
        "prior": pack(prior),
        "delta": round4(delta),
        "delta_pp": round_pp(delta),
        "z_stat": round4(z) if z is not None and math.isfinite(z) else None,
        "p_value": round4(p_val) if p_val is not None else None,
        "alpha": ALPHA,
        "significant": bool(sig_flag),
        "allow_delta_narrative": allow_delta,
        "sample_warning": warns,
        "verdict": verdict,
    }


def topic_decomposition(cur: PeriodStats, prior: PeriodStats) -> dict[str, Any]:
    """Kitagawa / 三分解：structure + within + interaction.

    Δp = Σ_k (w1_k - w0_k) * r0_k          # structure（主题占比变化，锁上期主题内负向率）
        + Σ_k w0_k * (r1_k - r0_k)          # within（主题内负向率变化，锁上期结构）
        + Σ_k (w1_k - w0_k) * (r1_k - r0_k) # interaction
    """
    topics = sorted(set(cur.topic_n) | set(prior.topic_n) | set(TOPIC_CN))
    n0, n1 = prior.n, cur.n
    rows: list[dict[str, Any]] = []
    sum_s = sum_w = sum_i = 0.0

    for t in topics:
        c0 = prior.topic_n.get(t, 0)
        c1 = cur.topic_n.get(t, 0)
        neg0 = prior.topic_neg.get(t, 0)
        neg1 = cur.topic_neg.get(t, 0)
        w0 = (c0 / n0) if n0 else 0.0
        w1 = (c1 / n1) if n1 else 0.0
        r0 = (neg0 / c0) if c0 else 0.0
        r1 = (neg1 / c1) if c1 else 0.0
        structure = (w1 - w0) * r0
        within = w0 * (r1 - r0)
        interaction = (w1 - w0) * (r1 - r0)
        total = structure + within + interaction
        sum_s += structure
        sum_w += within
        sum_i += interaction
        if c0 == 0 and c1 == 0:
            continue
        rows.append(
            {
                "topic": t,
                "topic_cn": TOPIC_CN.get(t, t),
                "n0": c0,
                "n1": c1,
                "neg0": neg0,
                "neg1": neg1,
                "share0": round4(w0),
                "share1": round4(w1),
                "neg_rate0": round4(r0),
                "neg_rate1": round4(r1),
                "structure_pp": round_pp(structure),
                "within_pp": round_pp(within),
                "interaction_pp": round_pp(interaction),
                "total_pp": round_pp(total),
                # UI 友好别名
                "delta_pp": round_pp(total),
                "kind_hint": (
                    "within"
                    if abs(within) >= abs(structure) and abs(within) >= abs(interaction)
                    else (
                        "structure"
                        if abs(structure) >= abs(interaction)
                        else "interaction"
                    )
                ),
            }
        )

    rows.sort(key=lambda r: abs(r["total_pp"] or 0), reverse=True)
    delta = cur.neg_rate - prior.neg_rate
    recon = sum_s + sum_w + sum_i

    # 瀑布步骤：基线 → 按 |贡献| 降序的主题合计贡献 → 本期
    # 每步展示「主题合计」（structure+within+interaction），并在 detail 里拆开
    steps: list[dict[str, Any]] = [
        {
            "key": "baseline",
            "label": f"{prior.label}负向率",
            "kind": "baseline",
            "delta_pp": None,
            "value_pp": round_pp(prior.neg_rate),
            "cumulative_pp": round_pp(prior.neg_rate),
        }
    ]
    cum = prior.neg_rate
    # 只展示 |total|>=0.05pp 的主题，其余合并为 residual
    main = [r for r in rows if abs(r["total_pp"] or 0) >= 0.05]
    tiny = [r for r in rows if abs(r["total_pp"] or 0) < 0.05]
    for r in main:
        d = (r["total_pp"] or 0) / 100.0
        cum += d
        steps.append(
            {
                "key": r["topic"],
                "label": f"{r['topic_cn']}（结构{r['structure_pp']:+.2f} / 主题内{r['within_pp']:+.2f} / 交互{r['interaction_pp']:+.2f}）",
                "topic": r["topic"],
                "topic_cn": r["topic_cn"],
                "kind": r["kind_hint"],
                "delta_pp": r["total_pp"],
                "structure_pp": r["structure_pp"],
                "within_pp": r["within_pp"],
                "interaction_pp": r["interaction_pp"],
                "cumulative_pp": round_pp(cum),
            }
        )
    if tiny:
        tiny_sum = sum((r["total_pp"] or 0) for r in tiny) / 100.0
        cum += tiny_sum
        steps.append(
            {
                "key": "other_small",
                "label": f"其余主题合计（{len(tiny)}）",
                "kind": "residual",
                "delta_pp": round_pp(tiny_sum),
                "cumulative_pp": round_pp(cum),
            }
        )
    steps.append(
        {
            "key": "current",
            "label": f"{cur.label}负向率",
            "kind": "current",
            "delta_pp": None,
            "value_pp": round_pp(cur.neg_rate),
            "cumulative_pp": round_pp(cur.neg_rate),
        }
    )

    return {
        "formula": (
            "Δneg_rate = Σ(Δshare·r0) + Σ(share0·Δr) + Σ(Δshare·Δr) "
            "= structure + within + interaction"
        ),
        "delta_pp": round_pp(delta),
        "reconstructed_pp": round_pp(recon),
        "recon_error_pp": round_pp(delta - recon),
        "totals": {
            "structure_pp": round_pp(sum_s),
            "within_pp": round_pp(sum_w),
            "interaction_pp": round_pp(sum_i),
        },
        "by_topic": rows,
        "steps": steps,
    }


def select_week_windows(df: pd.DataFrame, week_end: date | None) -> tuple[PeriodStats, PeriodStats]:
    end = week_end or max(df["day"])
    start = end - timedelta(days=6)
    cur = period_stats(df, "本周", start, end)
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=6)
    prior = period_stats(df, "上周", prior_start, prior_end)
    return cur, prior


def select_rolling3(df: pd.DataFrame, anchor: date | None) -> tuple[PeriodStats, PeriodStats]:
    """近 3 个有数据自然日 vs 再往前 3 个有数据自然日。"""
    a = anchor or max(df["day"])
    days_with = sorted(d for d in df["day"].unique() if d <= a)
    cur_days = days_with[-3:] if len(days_with) >= 3 else days_with
    before = [d for d in days_with if d < min(cur_days)] if cur_days else []
    prior_days = before[-3:] if len(before) >= 3 else before
    if not cur_days:
        empty = period_stats(df.iloc[0:0], "滚动3日", a, a)
        return empty, empty
    cur = period_stats(df[df["day"].isin(cur_days)], "滚动3日", min(cur_days), max(cur_days))
    cur.days = list(cur_days)
    if prior_days:
        prior = period_stats(
            df[df["day"].isin(prior_days)], "对照滚动3日", min(prior_days), max(prior_days)
        )
        prior.days = list(prior_days)
    else:
        prior = period_stats(df.iloc[0:0], "对照滚动3日", a, a)
    return cur, prior


def build_payload(
    df: pd.DataFrame,
    ann_name: str,
    week_end: date | None,
    include_rolling3: bool,
) -> dict[str, Any]:
    week_cur, week_prior = select_week_windows(df, week_end)
    week_cmp = compare_periods(
        week_cur, week_prior, comparison_id="week_vs_prev", label="本周 vs 上周"
    )
    week_decomp = topic_decomposition(week_cur, week_prior)

    comparisons = [week_cmp]
    decomps = {"week_vs_prev": week_decomp}

    if include_rolling3:
        r_cur, r_prior = select_rolling3(df, week_end or max(df["day"]))
        r_cmp = compare_periods(
            r_cur,
            r_prior,
            comparison_id="rolling3_vs_prior",
            label="滚动3日 vs 对照滚动3日",
        )
        r_decomp = topic_decomposition(r_cur, r_prior)
        comparisons.append(r_cmp)
        decomps["rolling3_vs_prior"] = r_decomp

    primary = "week_vs_prev"
    primary_cmp = week_cmp
    primary_decomp = week_decomp

    # 一句话 headline：诚实
    dpp = primary_cmp.get("delta_pp")
    if primary_cmp["period"]["n"] == 0:
        headline = "本期无样本，无法做异动诊断。"
    elif not primary_cmp["allow_delta_narrative"]:
        headline = (
            f"本周负向率 {primary_cmp['period']['neg_rate_pp']}% "
            f"（n={primary_cmp['period']['n']}，Wilson "
            f"{primary_cmp['period']['wilson_ci_pp']}），"
            f"对照不足或样本偏薄——{primary_cmp['verdict']}"
        )
    elif primary_cmp["significant"]:
        top = primary_decomp["by_topic"][0] if primary_decomp["by_topic"] else None
        top_bit = (
            f"；主贡献 `{top['topic']}` {top['topic_cn']} "
            f"{top['total_pp']:+.2f}pp（偏{ '主题内' if top['kind_hint']=='within' else '结构' }）"
            if top
            else ""
        )
        headline = (
            f"本周 vs 上周 Δneg={dpp:+.2f}pp，"
            f"z={primary_cmp['z_stat']}，p={primary_cmp['p_value']}，"
            f"{primary_cmp['verdict']}{top_bit}"
        )
    else:
        top = primary_decomp["by_topic"][0] if primary_decomp["by_topic"] else None
        top_bit = (
            f"结构上可见 `{top['topic']}` 贡献 {top['total_pp']:+.2f}pp，但整体未显著。"
            if top and abs(top["total_pp"] or 0) >= 1.0
            else "整体波动可归因于抽样噪声。"
        )
        headline = (
            f"本周 vs 上周 Δneg={dpp:+.2f}pp（p={primary_cmp['p_value']}），"
            f"未达显著。{top_bit}"
        )

    # 扁平 waterfall：UI 直接读 steps；并附 totals / by_topic
    waterfall_ui = [
        {
            "label": s["label"],
            "delta_pp": s.get("delta_pp"),
            "value_pp": s.get("value_pp"),
            "cumulative_pp": s.get("cumulative_pp"),
            "kind": s.get("kind"),
            "key": s.get("key"),
        }
        for s in primary_decomp["steps"]
    ]

    now = datetime.now(TZ)
    return {
        "schema_version": "1.0",
        "available": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "channel": "taptap",
        "headline": headline,
        "primary_comparison_id": primary,
        "source": {
            "annotations": f"data/annotations/{ann_name}",
            "reviews": "data/processed/reviews/reviews_clean.csv",
            "n_pool": int(len(df)),
            "day_min": min(df["day"]).isoformat() if len(df) else None,
            "day_max": max(df["day"]).isoformat() if len(df) else None,
            "prompt_version": (
                str(df["prompt_version"].mode().iloc[0]) if len(df) else None
            ),
            "model": str(df["model"].mode().iloc[0]) if len(df) else None,
        },
        "methods": {
            "neg_definition": "sentiment == '负'（v1.4 intended/整体态度）",
            "wilson_ci": "95% Wilson score interval for binomial proportion",
            "significance_test": "two-proportion z-test (pooled SE), two-sided, alpha=0.05",
            "min_n_warn": MIN_N_WARN,
            "allow_delta_rule": (
                f"cur_n>={MIN_N_WARN} and prior_n>={MIN_N_WARN} "
                "and prior_n >= 0.5 * cur_n"
            ),
            "decomposition": (
                "Kitagawa three-factor: structure Σ(Δw·r0) + within Σ(w0·Δr) "
                "+ interaction Σ(Δw·Δr); units = percentage points on overall neg_rate"
            ),
            "week_definition": "calendar 7-day window ending at week_end (inclusive)",
            "rolling3_definition": "last 3 calendar days with any annotated volume vs prior 3",
        },
        "comparisons": comparisons,
        # UI 主瀑布（对应 primary week）
        "waterfall": waterfall_ui,
        "contributions": primary_decomp["by_topic"],
        "decomposition": {
            "comparison_id": primary,
            **{k: v for k, v in primary_decomp.items() if k != "steps"},
            "steps": primary_decomp["steps"],
        },
        "decompositions": decomps,
        "table": [
            {
                "metric": "负向率",
                "current": c["period"]["neg_rate_pp"],
                "prior": c["prior"]["neg_rate_pp"],
                "delta_pp": c["delta_pp"],
                "wilson_current": c["period"]["wilson_ci_pp"],
                "wilson_prior": c["prior"]["wilson_ci_pp"],
                "p_value": c["p_value"],
                "significant": c["significant"],
                "verdict": c["verdict"],
                "comparison_id": c["id"],
                "n_current": c["period"]["n"],
                "n_prior": c["prior"]["n"],
            }
            for c in comparisons
        ],
    }


def render_report(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    src = payload["source"]
    lines += [
        "# TapTap 异动诊断（P0-1）",
        "",
        "## 元数据",
        "",
        "| 项 | 内容 |",
        "|----|------|",
        f"| 生成时间 | {payload['generated_at']} |",
        f"| 标注 | `{src['annotations']}` |",
        f"| 评论 | `{src['reviews']}` |",
        f"| 池内样本 | n={src['n_pool']}（{src['day_min']} ~ {src['day_max']}） |",
        f"| prompt / model | `{src.get('prompt_version')}` / `{src.get('model')}` |",
        "",
        "## 一句话结论",
        "",
        payload["headline"],
        "",
        "## 方法",
        "",
        "- **负向定义**：`sentiment == '负'`（v1.4 整体态度 = intended）。",
        "- **Wilson 95% CI**：二项比例 Wilson score interval，避免小样本下 Wald 区间出界。",
        "- **两比例 z 检验**：pooled SE，双侧 α=0.05；报告 z 与 p。",
        f"- **样本警告**：任一侧 n<{MIN_N_WARN}，或对照 < 0.5×本期 → "
        "**禁止「显著异动 / 大幅恶化」叙事**，只报绝对水平与 CI。",
        "- **主题贡献分解（Kitagawa）**：",
        "  - 结构效应 `structure`：主题占比变化 × 上期主题内负向率",
        "  - 主题内效应 `within`：上期主题占比 × 主题内负向率变化",
        "  - 交互 `interaction`：占比变化 × 主题内率变化",
        "  - 三者之和应还原整体 Δneg_rate（允许浮点误差）。",
        "- **披露原则**：p≥0.05 或不满足样本量门槛时，明确写「未显著 / 样本不足」，",
        "  不把点估计涨跌表述为已确认异动。",
        "",
        "## 对照表",
        "",
        "| 对照 | 本期窗 | n | 负向率 (Wilson) | 对照窗 | n | 负向率 (Wilson) | Δpp | z | p | 显著? | 判断 |",
        "|------|--------|---|---------------|--------|---|---------------|-----|---|---|-------|------|",
    ]
    for c in payload["comparisons"]:
        pe, pr = c["period"], c["prior"]
        sig = "是" if c["significant"] else "否"
        warns = "；".join(c["sample_warning"]) if c["sample_warning"] else "—"
        lines.append(
            f"| {c['label']} | {pe['start']}~{pe['end']} | {pe['n']} | "
            f"{pe['neg_rate_pp']}% {pe['wilson_ci_pp']} | "
            f"{pr['start']}~{pr['end']} | {pr['n']} | "
            f"{pr['neg_rate_pp']}% {pr['wilson_ci_pp']} | "
            f"{c['delta_pp']:+.2f} | {c['z_stat']} | {c['p_value']} | {sig} | "
            f"{c['verdict']} |"
        )
        if c["sample_warning"]:
            lines.append(f"| ↳ 样本警告 | colspan | | | | | | | | | | {warns} |")

    # Cleaner warning block instead of broken table row
    lines += ["", "### 样本警告明细", ""]
    any_warn = False
    for c in payload["comparisons"]:
        if c["sample_warning"]:
            any_warn = True
            lines.append(f"- **{c['label']}**：")
            for w in c["sample_warning"]:
                lines.append(f"  - {w}")
    if not any_warn:
        lines.append("- 无（两侧均达到样本量门槛）")

    decomp = payload["decomposition"]
    lines += [
        "",
        f"## 主题贡献瀑布（primary = `{payload['primary_comparison_id']}`）",
        "",
        f"- 整体 Δneg_rate = **{decomp['delta_pp']:+.2f} pp**",
        f"- 分解合计：结构 {decomp['totals']['structure_pp']:+.2f} / "
        f"主题内 {decomp['totals']['within_pp']:+.2f} / "
        f"交互 {decomp['totals']['interaction_pp']:+.2f} "
        f"（还原 {decomp['reconstructed_pp']:+.2f}，误差 {decomp['recon_error_pp']:+.2f} pp）",
        "",
        "### 瀑布步骤",
        "",
        "| 步骤 | 类型 | Δpp | 累计负向率 pp |",
        "|------|------|-----|---------------|",
    ]
    for s in decomp["steps"]:
        d = "—" if s.get("delta_pp") is None else f"{s['delta_pp']:+.2f}"
        lines.append(
            f"| {s['label']} | {s.get('kind','')} | {d} | {s.get('cumulative_pp')} |"
        )

    lines += [
        "",
        "### 分主题明细",
        "",
        "| 主题 | n0→n1 | 占比0→1 | 主题内负向率0→1 | 结构pp | 主题内pp | 交互pp | 合计pp |",
        "|------|-------|---------|-----------------|--------|----------|--------|--------|",
    ]
    for r in decomp["by_topic"]:
        lines.append(
            f"| `{r['topic']}` {r['topic_cn']} | {r['n0']}→{r['n1']} | "
            f"{(r['share0'] or 0)*100:.1f}%→{(r['share1'] or 0)*100:.1f}% | "
            f"{(r['neg_rate0'] or 0)*100:.1f}%→{(r['neg_rate1'] or 0)*100:.1f}% | "
            f"{r['structure_pp']:+.2f} | {r['within_pp']:+.2f} | "
            f"{r['interaction_pp']:+.2f} | {r['total_pp']:+.2f} |"
        )

    lines += [
        "",
        "## JSON schema（看板消费）",
        "",
        "```",
        "lab.anomaly.available          bool",
        "lab.anomaly.headline           str   # 一句话",
        "lab.anomaly.comparisons[]      # id/label/period/prior/delta_pp/z/p/significant/verdict/sample_warning",
        "lab.anomaly.table[]            # 扁平行，方便直接画表",
        "lab.anomaly.waterfall[]        # {label, delta_pp, cumulative_pp, kind}",
        "lab.anomaly.contributions[]    # 分主题 structure/within/interaction",
        "lab.anomaly.decomposition      # 完整分解对象",
        "```",
        "",
        "## 边界",
        "",
        "- 本诊断基于已标注 TapTap 切片，**非正式全站 KPI**。",
        "- 未覆盖未标注评论；显著性不等于业务重要性。",
        "- 主题「结构 vs 主题内」是会计恒等式分解，不是因果识别。",
        "",
        "---",
        "",
        "*报告结束*",
        "",
    ]
    # Fix accidental bad table row from earlier draft if any — regenerate clean
    # Remove the broken colspan line if present
    cleaned = []
    for ln in lines:
        if ln.startswith("| ↳ 样本警告"):
            continue
        cleaned.append(ln)
    return "\n".join(cleaned)


def main() -> None:
    p = argparse.ArgumentParser(description="P0-1 TapTap anomaly diagnosis")
    p.add_argument(
        "--ann",
        default=str(ROOT / "data/annotations" / "annotations_v1_4.csv"),
        help="annotations CSV path",
    )
    p.add_argument("--week-end", default="", help="YYYY-MM-DD；默认=库内最新发布日")
    p.add_argument(
        "--no-rolling3",
        action="store_true",
        help="跳过滚动3日对照（默认会算）",
    )
    args = p.parse_args()

    ann_path = Path(args.ann)
    df = load_annotated(ann_path)
    week_end = (
        datetime.strptime(args.week_end, "%Y-%m-%d").date() if args.week_end else None
    )
    payload = build_payload(
        df,
        ann_path.name,
        week_end=week_end,
        include_rolling3=not args.no_rolling3,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "anomaly_diagnosis.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # report name from primary week window
    primary = next(c for c in payload["comparisons"] if c["id"] == "week_vs_prev")
    tag = (
        f"{primary['period']['start'].replace('-', '')}_"
        f"{primary['period']['end'].replace('-', '')}"
    )
    report_path = REPORT_DIR / f"anomaly_diagnosis_{tag}.md"
    report_path.write_text(render_report(payload), encoding="utf-8")
    # also write a stable latest pointer name
    latest = REPORT_DIR / "anomaly_diagnosis_latest.md"
    latest.write_text(render_report(payload), encoding="utf-8")

    print(json_path)
    print(report_path)
    print(f"headline={payload['headline']}")
    c = primary
    print(
        f"week Δpp={c['delta_pp']} p={c['p_value']} significant={c['significant']} "
        f"n={c['period']['n']}/{c['prior']['n']}"
    )


if __name__ == "__main__":
    main()
