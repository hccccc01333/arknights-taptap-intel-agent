#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0-3 事件影响分析：事件前后窗对比（before7/after7，稀疏则 ±3d）。

输入：
  - data/annotations/annotations_v1_4.csv
  - data/processed/reviews/reviews_clean.csv
  - 本目录 events.csv（策展事件表）

输出：
  - outputs/event_impact.json
  - reports/event_impact_YYYYMMDD_HHMMSS.md
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[2]
LAB = Path(__file__).resolve().parent
DATA = ROOT / "data/raw/taptap"
ANN = ROOT / "data/annotations"

TOPIC_CN = {
    "gacha": "抽卡/商业化",
    "gameplay": "关卡/玩法",
    "client": "性能/客户端",
    "event": "活动/版本",
    "ops": "运营/客服",
    "story": "剧情",
    "balance": "数值/平衡",
    "other": "综合/其他",
}
FOCUS_TOPICS = ("gacha", "gameplay", "client")

CRASH_RE = re.compile(r"闪退|崩溃|卡死|黑屏|进不去|掉线|卡顿|掉帧")
CHURN_RE = re.compile(r"退坑|弃坑|卸载|删了|不玩了|再也不玩|劝退|跑路|退游")

# Prefer ±7d; fall back to ±3d when either side is thin.
PREF_HALF = 7
FALLBACK_HALF = 3
SPARSE_SIDE_N = 20
MIN_REPORT_N = 8


def clip(s: object, n: int = 72) -> str:
    t = str(s or "").replace("\n", " ").replace("<br />", " ").replace("|", "/").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def load_pool(ann_path: Path, clean_path: Path) -> pd.DataFrame:
    clean = pd.read_csv(
        clean_path,
        dtype={"review_id": str},
        usecols=["review_id", "publish_time_cn", "text", "score_raw"],
    )
    ann = pd.read_csv(ann_path, dtype={"review_id": str})
    ann = ann[ann["error"].fillna("") == ""].drop_duplicates("review_id", keep="last")
    ann = ann[~ann["review_id"].astype(str).str.startswith("probe_")]
    df = ann.merge(clean, on="review_id", how="inner", suffixes=("", "_clean"))
    df["day"] = (
        pd.to_datetime(df["publish_time_cn"], errors="coerce")
        .dt.tz_localize(None)
        .dt.normalize()
    )
    df = df.dropna(subset=["day"]).copy()
    df["sentiment"] = df["sentiment"].fillna("").astype(str).str.strip()
    df["topic_primary"] = df["topic_primary"].fillna("other").astype(str)
    df["text"] = df["text"].fillna("").astype(str)
    df["is_neg"] = (df["sentiment"] == "负").astype(int)
    df["is_crash"] = df["text"].map(lambda t: int(bool(CRASH_RE.search(t))))
    df["is_churn"] = df["text"].map(lambda t: int(bool(CHURN_RE.search(t))))
    for t in FOCUS_TOPICS:
        df[f"is_{t}"] = (df["topic_primary"] == t).astype(int)
    return df


def load_events(path: Path) -> pd.DataFrame:
    ev = pd.read_csv(path)
    ev["event_date"] = pd.to_datetime(ev["event_date"]).dt.normalize()
    if "analyzable" in ev.columns:
        ev = ev[ev["analyzable"].astype(str).str.lower().isin(["1", "true", "yes", "y"])]
    return ev.reset_index(drop=True)


def window_slice(
    df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    return df[(df["day"] >= start) & (df["day"] <= end)].copy()


def pick_windows(
    df: pd.DataFrame, event_date: pd.Timestamp
) -> dict[str, Any]:
    """before = [D-half, D-1], after = [D, D+half-1]. Prefer half=7."""
    day_min = df["day"].min()
    day_max = df["day"].max()

    def build(half: int) -> dict[str, Any]:
        before_start = event_date - pd.Timedelta(days=half)
        before_end = event_date - pd.Timedelta(days=1)
        after_start = event_date
        after_end = event_date + pd.Timedelta(days=half - 1)
        # clip to data range (honesty: truncated windows)
        clipped = False
        if before_start < day_min:
            before_start = day_min
            clipped = True
        if after_end > day_max:
            after_end = day_max
            clipped = True
        b = window_slice(df, before_start, before_end)
        a = window_slice(df, after_start, after_end)
        return {
            "half_days": half,
            "before_start": before_start,
            "before_end": before_end,
            "after_start": after_start,
            "after_end": after_end,
            "before_n": int(len(b)),
            "after_n": int(len(a)),
            "clipped_to_data": clipped,
            "before_df": b,
            "after_df": a,
        }

    pref = build(PREF_HALF)
    note = "before7_vs_after7"
    used = pref
    if pref["before_n"] < SPARSE_SIDE_N or pref["after_n"] < SPARSE_SIDE_N:
        fb = build(FALLBACK_HALF)
        # Use fallback if it improves the thinner side or if pref after is truncated tiny
        if fb["before_n"] >= MIN_REPORT_N and fb["after_n"] >= MIN_REPORT_N:
            if (
                min(fb["before_n"], fb["after_n"]) > min(pref["before_n"], pref["after_n"])
                or pref["after_n"] < SPARSE_SIDE_N
                or pref["before_n"] < SPARSE_SIDE_N
            ):
                used = fb
                note = "sparse_fallback_pm3d"
    return {
        **{k: v for k, v in used.items() if k not in ("before_df", "after_df")},
        "window_rule": note,
        "before_df": used["before_df"],
        "after_df": used["after_df"],
        "data_day_min": str(day_min.date()),
        "data_day_max": str(day_max.date()),
    }


def rate(x: int, n: int) -> float | None:
    if n <= 0:
        return None
    return round(x / n, 4)


def two_proportion_ztest(x1: int, n1: int, x2: int, n2: int) -> dict[str, Any]:
    """Two-sided two-proportion z-test (pooled). Returns z, p, delta_pp."""
    if n1 <= 0 or n2 <= 0:
        return {
            "test": "two_proportion_z",
            "ok": False,
            "reason": "empty_side",
            "z": None,
            "p_value": None,
            "delta_pp": None,
        }
    p1, p2 = x1 / n1, x2 / n2
    delta_pp = round((p2 - p1) * 100, 2)
    pooled = (x1 + x2) / (n1 + n2)
    if pooled <= 0 or pooled >= 1:
        return {
            "test": "two_proportion_z",
            "ok": True,
            "z": 0.0,
            "p_value": 1.0,
            "delta_pp": delta_pp,
            "note": "pooled_rate_boundary",
            "n1": n1,
            "n2": n2,
            "x1": x1,
            "x2": x2,
            "p1": round(p1, 4),
            "p2": round(p2, 4),
        }
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = (p2 - p1) / se if se > 0 else 0.0
    # two-sided via normal CDF
    p_value = math.erfc(abs(z) / math.sqrt(2))
    return {
        "test": "two_proportion_z",
        "ok": True,
        "z": round(z, 4),
        "p_value": round(float(p_value), 4),
        "delta_pp": delta_pp,
        "n1": n1,
        "n2": n2,
        "x1": x1,
        "x2": x2,
        "p1": round(p1, 4),
        "p2": round(p2, 4),
    }


def mann_whitney_u(a: list[int], b: list[int]) -> dict[str, Any]:
    """Mann-Whitney U with normal approximation (tie-corrected). Prefer scipy if present."""
    n1, n2 = len(a), len(b)
    if n1 < 3 or n2 < 3:
        return {
            "test": "mannwhitney_u",
            "ok": False,
            "reason": "n_lt_3",
            "U": None,
            "p_value": None,
        }
    try:
        from scipy import stats  # type: ignore

        res = stats.mannwhitneyu(a, b, alternative="two-sided")
        return {
            "test": "mannwhitney_u",
            "ok": True,
            "engine": "scipy",
            "U": float(res.statistic),
            "p_value": round(float(res.pvalue), 4),
            "n1": n1,
            "n2": n2,
        }
    except Exception:
        pass

    # Pure-Python normal approx with tie correction
    combined = [("a", float(v), i) for i, v in enumerate(a)] + [
        ("b", float(v), i) for i, v in enumerate(b)
    ]
    combined.sort(key=lambda t: t[1])
    n = len(combined)
    rank_sum_a = 0.0
    i = 0
    tie_term = 0.0
    while i < n:
        j = i
        while j < n and combined[j][1] == combined[i][1]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0  # 1-based ranks
        tie_n = j - i
        if tie_n > 1:
            tie_term += tie_n**3 - tie_n
        for k in range(i, j):
            if combined[k][0] == "a":
                rank_sum_a += avg_rank
        i = j
    U1 = rank_sum_a - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    sigma2 = n1 * n2 * (n + 1) / 12.0
    if tie_term:
        sigma2 -= n1 * n2 * tie_term / (12.0 * n * (n - 1))
    if sigma2 <= 0:
        return {
            "test": "mannwhitney_u",
            "ok": True,
            "engine": "pure_python",
            "U": round(U1, 4),
            "p_value": 1.0,
            "n1": n1,
            "n2": n2,
            "note": "zero_variance",
        }
    z = (U1 - mu) / math.sqrt(sigma2)
    p_value = math.erfc(abs(z) / math.sqrt(2))
    return {
        "test": "mannwhitney_u",
        "ok": True,
        "engine": "pure_python",
        "U": round(U1, 4),
        "z": round(z, 4),
        "p_value": round(float(p_value), 4),
        "n1": n1,
        "n2": n2,
    }


def metric_block(before: pd.DataFrame, after: pd.DataFrame, col: str) -> dict[str, Any]:
    x1 = int(before[col].sum()) if len(before) else 0
    x2 = int(after[col].sum()) if len(after) else 0
    n1, n2 = len(before), len(after)
    prop = two_proportion_ztest(x1, n1, x2, n2)
    mw = mann_whitney_u(before[col].astype(int).tolist(), after[col].astype(int).tolist())
    return {
        "before": {"n": n1, "count": x1, "rate": rate(x1, n1)},
        "after": {"n": n2, "count": x2, "rate": rate(x2, n2)},
        "two_proportion": prop,
        "mann_whitney": mw,
        "honesty": _honesty(n1, n2, x1, x2),
    }


def _honesty(n1: int, n2: int, x1: int = 0, x2: int = 0) -> str:
    notes = []
    if n1 < SPARSE_SIDE_N or n2 < SPARSE_SIDE_N:
        notes.append(f"薄样本（before n={n1}, after n={n2}；阈值侧<{SPARSE_SIDE_N}），显著性仅作参考")
    if n1 < MIN_REPORT_N or n2 < MIN_REPORT_N:
        notes.append("单侧 n 过低，禁止强因果叙事")
    if x1 + x2 < 5:
        notes.append("事件计数合计 <5，比例波动易被单条评价放大")
    return "；".join(notes) if notes else "两侧样本量尚可，仍属观察性前后对照，非因果识别"


def topic_volume_block(before: pd.DataFrame, after: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for t in FOCUS_TOPICS:
        out[t] = metric_block(before, after, f"is_{t}")
        out[t]["label_cn"] = TOPIC_CN.get(t, t)
    return out


def analyze_event(df: pd.DataFrame, row: pd.Series) -> dict[str, Any]:
    event_date = pd.Timestamp(row["event_date"]).normalize()
    win = pick_windows(df, event_date)
    before, after = win["before_df"], win["after_df"]
    neg = metric_block(before, after, "is_neg")
    crash = metric_block(before, after, "is_crash")
    churn = metric_block(before, after, "is_churn")
    topics = topic_volume_block(before, after)

    # headline pick: largest |delta_pp| among neg / focus topics / crash / churn with n ok
    candidates = []
    for name, blk in [
        ("neg_rate", neg),
        ("crash_kw", crash),
        ("churn_kw", churn),
        *[(f"topic_{k}", v) for k, v in topics.items()],
    ]:
        dpp = (blk.get("two_proportion") or {}).get("delta_pp")
        if dpp is None:
            continue
        candidates.append((abs(dpp), dpp, name, blk))
    candidates.sort(reverse=True)
    headline = None
    if candidates:
        _, dpp, name, blk = candidates[0]
        p = (blk.get("two_proportion") or {}).get("p_value")
        headline = {
            "metric": name,
            "delta_pp": dpp,
            "p_value": p,
            "before_rate": blk["before"]["rate"],
            "after_rate": blk["after"]["rate"],
            "before_n": blk["before"]["n"],
            "after_n": blk["after"]["n"],
        }

    # sample quotes after window (neg preferred)
    quotes = []
    pool = after[after["is_neg"] == 1]
    if pool.empty:
        pool = after
    for _, r in pool.head(5).iterrows():
        quotes.append(
            {
                "review_id": str(r["review_id"]),
                "day": str(pd.Timestamp(r["day"]).date()),
                "topic": str(r["topic_primary"]),
                "sentiment": str(r["sentiment"]),
                "text": clip(r["text"], 100),
            }
        )

    neg_dpp = (neg.get("two_proportion") or {}).get("delta_pp")
    neg_p = (neg.get("two_proportion") or {}).get("p_value")
    sig = bool(
        neg_p is not None
        and neg_p < 0.05
        and win["before_n"] >= SPARSE_SIDE_N
        and win["after_n"] >= SPARSE_SIDE_N
    )
    # Flat headline string for dashboard cards
    if headline:
        hl_str = (
            f"{headline['metric']}: {headline['before_rate']}→{headline['after_rate']} "
            f"(Δ{headline['delta_pp']}pp, p={headline['p_value']}; "
            f"n={headline['before_n']}/{headline['after_n']})"
        )
    else:
        hl_str = "样本量不足，未形成头条指标"

    return {
        "event_id": str(row["event_id"]),
        "name": str(row.get("title", "")),
        "title": str(row.get("title", "")),
        "event_date": str(event_date.date()),
        "date": str(event_date.date()),
        "event_type": str(row.get("event_type", "")),
        "anchor_source": str(row.get("anchor_source", "")),
        "assumption": str(row.get("assumption", "")),
        "expected_signals": str(row.get("expected_signals", "")),
        "window": {
            "rule": win["window_rule"],
            "half_days": win["half_days"],
            "before": f"{win['before_start'].date()}~{win['before_end'].date()}",
            "after": f"{win['after_start'].date()}~{win['after_end'].date()}",
            "before_n": win["before_n"],
            "after_n": win["after_n"],
            "clipped_to_data": win["clipped_to_data"],
            "data_day_min": win["data_day_min"],
            "data_day_max": win["data_day_max"],
            "label": win["window_rule"],
        },
        "window_label": win["window_rule"],
        "before": {
            "n": win["before_n"],
            "neg_rate": neg["before"]["rate"],
            "neg_count": neg["before"]["count"],
        },
        "after": {
            "n": win["after_n"],
            "neg_rate": neg["after"]["rate"],
            "neg_count": neg["after"]["count"],
        },
        "delta_pp": neg_dpp,
        "neg_rate_delta_pp": neg_dpp,
        "significant": sig,
        "metrics": {
            "neg_rate": neg,
            "topic_volumes": topics,
            "crash_keyword_rate": crash,
            "churn_keyword_rate": churn,
        },
        "headline_metric": headline,
        "headline": hl_str,
        "sample_quotes_after": quotes,
        "analyzable_ok": win["before_n"] >= MIN_REPORT_N and win["after_n"] >= MIN_REPORT_N,
    }


def star_report(payload: dict[str, Any]) -> str:
    gen = payload["generated_at"]
    lines = [
        "# P0-3 事件影响分析报告",
        "",
        "## Situation（问题）",
        "",
        "策展若干与明日方舟相关的版本 / 抽卡 / 活动 / 热更节点后，TapTap 评价在事件前后窗内，",
        "负向率、主题声量（抽卡/玩法/客户端）与流失·崩溃关键词率是否出现可观测位移？",
        "位移幅度与不确定性如何——能否支撑值班跟进，而非把噪声当事故？",
        "",
        "## Task / 口径",
        "",
        "| 项 | 内容 |",
        "|----|------|",
        f"| 标注 | `annotations_v1_4.csv` |",
        f"| 时间 | `reviews_clean.publish_time_cn` → 自然日 |",
        f"| 事件表 | `L2_signal/lab/events.csv`（策展 + 假设已写明） |",
        "| 情绪 | `sentiment`（= intended；负向率 = 负 / n） |",
        "| 主题声量 | `topic_primary ∈ {gacha, gameplay, client}` 占比 |",
        "| 崩溃词 | 正则：闪退/崩溃/卡死/黑屏/进不去/掉线/卡顿/掉帧 |",
        "| 流失词 | 正则：退坑/弃坑/卸载/删了/不玩了/再也不玩/劝退/跑路/退游 |",
        "| 窗规则 | 默认 before7=[D-7,D-1] vs after7=[D,D+6]；任一侧 n<20 则降级 ±3d |",
        "| 检验 | 两比例 z 检验（主）；Mann-Whitney U 作用在 0/1 指标上（辅） |",
        f"| 生成 | `{gen}` |",
        "",
        "> 本分析是**观察性前后对照**，不是因果识别；事件日期部分由标注声量/主题推断，见各事件 assumption。",
        "",
        "## Action（方法）",
        "",
        "1. 合并标注与清洗表，滤掉 `error` 非空与 probe 行。",
        "2. 按 `events.csv` 逐事件切 before/after 窗（稀疏自动降级）。",
        "3. 计算负向率、三主题占比、崩溃/流失词率；两侧做两比例 z + Mann-Whitney。",
        "4. 披露 n、窗截断、薄样本；禁止在 n 过低时写「显著崩盘」。",
        "",
        f"- 池规模：n={payload['pool_n']}；日界 {payload['day_min']} ~ {payload['day_max']}",
        f"- 事件数：{len(payload['events'])}（可分析 {sum(1 for e in payload['events'] if e.get('analyzable_ok'))}）",
        "",
        "## Result（结果）",
        "",
    ]

    # summary table
    lines += [
        "### 总览",
        "",
        "| 事件 | 日期 | 类型 | 窗 | before n | after n | 主指标 Δpp | p(z) |",
        "|------|------|------|----|----------|---------|------------|------|",
    ]
    for e in payload["events"]:
        h = e.get("headline_metric") or {}
        win = e["window"]
        lines.append(
            "| {title} | {date} | {typ} | {rule} | {bn} | {an} | {dpp} | {p} |".format(
                title=e["title"][:22],
                date=e["event_date"],
                typ=e["event_type"],
                rule=win["rule"],
                bn=win["before_n"],
                an=win["after_n"],
                dpp=h.get("delta_pp"),
                p=h.get("p_value"),
            )
        )
    lines.append("")

    for e in payload["events"]:
        win = e["window"]
        neg = e["metrics"]["neg_rate"]
        lines += [
            f"### {e['event_id']} · {e['title']}",
            "",
            f"- **类型**：`{e['event_type']}` · **日期**：{e['event_date']}",
            f"- **锚定**：{e['anchor_source']}",
            f"- **假设**：{e['assumption']}",
            f"- **窗**：{win['rule']}（half={win['half_days']}）"
            f" before `{win['before']}` n={win['before_n']}；"
            f" after `{win['after']}` n={win['after_n']}"
            + ("；**已截断到数据边界**" if win.get("clipped_to_data") else ""),
            f"- **负向率**：{neg['before']['rate']} → {neg['after']['rate']}"
            f"（Δ {neg['two_proportion'].get('delta_pp')} pp；"
            f"z={neg['two_proportion'].get('z')}，p={neg['two_proportion'].get('p_value')}；"
            f"MW p={neg['mann_whitney'].get('p_value')}）",
            f"- **局限说明**：{neg['honesty']}",
            "",
            "| 指标 | before rate (count/n) | after rate (count/n) | Δpp | p(z) | p(MW) |",
            "|------|----------------------|----------------------|-----|------|-------|",
        ]
        rows = [
            ("负向率", neg),
            ("崩溃词率", e["metrics"]["crash_keyword_rate"]),
            ("流失词率", e["metrics"]["churn_keyword_rate"]),
        ]
        for t, blk in e["metrics"]["topic_volumes"].items():
            rows.append((f"{TOPIC_CN.get(t, t)}占比", blk))
        for label, blk in rows:
            b, a = blk["before"], blk["after"]
            zp = blk["two_proportion"]
            mw = blk["mann_whitney"]
            lines.append(
                f"| {label} | {b['rate']} ({b['count']}/{b['n']}) | "
                f"{a['rate']} ({a['count']}/{a['n']}) | "
                f"{zp.get('delta_pp')} | {zp.get('p_value')} | {mw.get('p_value')} |"
            )
        lines.append("")
        if e.get("sample_quotes_after"):
            lines.append("**After 窗原话抽样（偏负向）**")
            lines.append("")
            for q in e["sample_quotes_after"][:3]:
                lines.append(
                    f"- `{q['review_id']}` [{q['day']} · {q['topic']} · {q['sentiment']}] {q['text']}"
                )
            lines.append("")

    # pick global headline (from headline_metric dict)
    best = None
    for e in payload["events"]:
        h = e.get("headline_metric")
        if not h or not e.get("analyzable_ok"):
            continue
        score = abs(h.get("delta_pp") or 0)
        bonus = 0
        if (h.get("p_value") is not None) and h["p_value"] < 0.05:
            bonus += 10
        if min(h.get("before_n", 0), h.get("after_n", 0)) >= SPARSE_SIDE_N:
            bonus += 5
        score += bonus
        if best is None or score > best[0]:
            best = (score, e, h)

    lines += [
        "## Uncertainty（不确定性）",
        "",
        "1. **事件日期策展**：部分节点由声量峰 + 主题/关键词推断，可能与官方维护公告差 0–2 天。",
        "2. **样本非面板**：TapTap 评价非日活全量；高峰日可能混入「补评/翻车补刀」，非纯增量用户。",
        "3. **关键词漏检**：崩溃/流失靠正则，反讽与缩写会漏；rhetoric 裂隙未计入本 P0-3。",
        "4. **多重比较**：多事件 × 多指标未做 FWER 校正；p 值仅作排序线索。",
        "5. **尾部截断**：数据 day_max 限制 after 窗，近期末事件更易降级为 ±3d。",
        "",
        "## 建议",
        "",
    ]
    if best:
        e, h = best[1], best[2]
        lines += [
            f"**头条结果**：`{e['title']}`（{e['event_date']}）上指标 `{h['metric']}` "
            f"由 {h['before_rate']} → {h['after_rate']}（Δ {h['delta_pp']} pp，"
            f"before n={h['before_n']} / after n={h['after_n']}，z 检验 p={h['p_value']}）。",
            "",
            "1. 对 **显著抬升且主题可行动** 的事件（玩法/客户端），值班按主题拆线：产品复现 + 客户端日志，勿一锅安抚。",
            "2. 对 **抽卡/商业化** 位移，优先看是否与卡池周期重合；口碑监测即可，慎发补偿承诺。",
            "3. 薄样本事件只进「观察清单」，等滚动 3 日 n 补齐再升格风险等级。",
            "4. 下一迭代：把官方维护日历 CSV 对齐后重跑，减少日期推断误差。",
        ]
    else:
        lines.append("本期无足够样本支撑头条结论；请扩充近窗评价或放宽事件筛选后重跑。")

    lines += [
        "",
        "---",
        f"产物：`outputs/event_impact.json` · 脚本 `event_impact.py` · 生成 {gen}",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="P0-3 事件影响分析")
    p.add_argument("--ann", default=str(ANN / "annotations_v1_4.csv"))
    p.add_argument("--clean", default=str(ROOT / "data/processed/reviews" / "reviews_clean.csv"))
    p.add_argument("--events", default=str(LAB / "events.csv"))
    p.add_argument("--out-json", default=str(LAB / "outputs" / "event_impact.json"))
    p.add_argument("--out-report-dir", default=str(LAB / "reports"))
    args = p.parse_args()

    df = load_pool(Path(args.ann), Path(args.clean))
    events = load_events(Path(args.events))
    results = [analyze_event(df, row) for _, row in events.iterrows()]

    now = datetime.now(TZ)

    # Global summary headline for dashboard + report
    best = None
    for e in results:
        h = e.get("headline_metric")
        if not h or not e.get("analyzable_ok"):
            continue
        score = abs(h.get("delta_pp") or 0)
        if (h.get("p_value") is not None) and h["p_value"] < 0.05:
            score += 10
        if min(h.get("before_n", 0), h.get("after_n", 0)) >= SPARSE_SIDE_N:
            score += 5
        if best is None or score > best[0]:
            best = (score, e, h)
    if best:
        e, h = best[1], best[2]
        summary = (
            f"{e['title']}（{e['event_date']}）：{h['metric']} "
            f"{h['before_rate']}→{h['after_rate']}（Δ{h['delta_pp']}pp，"
            f"n={h['before_n']}/{h['after_n']}，p={h['p_value']}）"
        )
    else:
        summary = "无足够样本形成头条结论"

    payload = {
        "available": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "prompt_version": "v1.4",
        "events_path": "L2_signal/lab/events.csv",
        "pool_n": int(len(df)),
        "day_min": str(df["day"].min().date()),
        "day_max": str(df["day"].max().date()),
        "window_policy": {
            "prefer": "before7_vs_after7",
            "fallback": "pm3d_when_side_n_lt_20",
            "sparse_side_n": SPARSE_SIDE_N,
            "min_report_n": MIN_REPORT_N,
        },
        "summary": summary,
        "headline": summary,
        "events": results,
        "note": "观察性前后对照；事件日期含标注推断，详见 events.csv assumption。",
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report_dir = Path(args.out_report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"event_impact_{stamp}.md"
    report_path.write_text(star_report(payload), encoding="utf-8")

    # also write a stable latest pointer (overwrite)
    latest = report_dir / "event_impact_latest.md"
    latest.write_text(star_report(payload), encoding="utf-8")

    print(f"events={len(results)} json={out_json} report={report_path}")
    print(f"HEADLINE: {summary}")
    for e in results:
        h = e.get("headline_metric") or {}
        print(
            f"  {e['event_id']} {e['event_date']} {e['window']['rule']} "
            f"n={e['window']['before_n']}/{e['window']['after_n']} "
            f"head={h.get('metric')} Δpp={h.get('delta_pp')} p={h.get('p_value')}"
        )


if __name__ == "__main__":
    main()
