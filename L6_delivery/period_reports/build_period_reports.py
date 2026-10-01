#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用已标样本生成样例日/周报（异动结构草稿，非正式全库结论）。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[2]

DEFAULT_GAME = "arknights"  # 游戏档案 key，见 games/<key>.json


def load_game_profile(game_key: str = DEFAULT_GAME) -> dict:
    """加载游戏档案（games/game_profile.py，唯一参数化入口）。"""
    import sys

    sys.path.insert(0, str(ROOT / "games"))
    from game_profile import load as _load  # noqa: PLC0415

    return _load(game_key)


GAME_PROFILE = load_game_profile()
DATA = ROOT / "data/raw/taptap"
ANN = ROOT / "data/annotations"
OUT = ROOT / "L6_delivery/period_reports"
REPORT_DIR = OUT / "reports"

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


def clip(s: object, n: int = 70) -> str:
    return str(s or "").replace("\n", " ").replace("|", "\\|").replace("<br />", " ")[:n]


def load_annotated(ann_path: Path) -> pd.DataFrame:
    clean = pd.read_csv(ROOT / "data/processed/reviews" / "reviews_clean.csv", dtype={"review_id": str})
    ann = pd.read_csv(ann_path, dtype={"review_id": str})
    ann = ann[ann["error"].fillna("") == ""].drop_duplicates("review_id", keep="last")
    ann = ann[~ann["review_id"].astype(str).str.startswith("probe_")]
    cols = [
        "review_id",
        "topic_primary",
        "sentiment",
        "actionable",
        "confidence",
        "rhetoric",
        "rhetoric_confidence",
        "incongruity_cues",
        "reason",
        "prompt_version",
        "model",
        "score_raw",
    ]
    for c in cols:
        if c not in ann.columns:
            ann[c] = ""
    m = ann[cols].merge(
        clean[
            [
                "review_id",
                "text",
                "publish_time_cn",
                "length_bucket",
                "support_count",
            ]
        ],
        on="review_id",
        how="left",
    )
    m["publish_dt"] = pd.to_datetime(m["publish_time_cn"], errors="coerce")
    m["day"] = m["publish_dt"].dt.tz_convert(TZ).dt.date
    m["score_raw"] = m["score_raw"].astype(str)
    return m.dropna(subset=["day"]).copy()


def pct(n: int, d: int) -> str:
    return f"{n / d * 100:.1f}%" if d else "—"


def section_overview(df: pd.DataFrame, title: str) -> list[str]:
    sent = Counter(df["sentiment"])
    topic = Counter(df["topic_primary"])
    score = Counter(df["score_raw"])
    rhe = Counter(df["rhetoric"].fillna("none"))
    act = Counter(df["actionable"])
    n = len(df)
    neg = sent.get("负", 0)
    cue_hit = (df["incongruity_cues"].fillna("none") != "none").sum()
    hi_neg = ((df["score_raw"].isin(["4", "5"])) & (df["sentiment"] == "负")).sum()
    lines = [
        f"## {title}",
        "",
        f"- 样本量：**{n}**",
        f"- 星级：`{dict(sorted(score.items()))}`",
        f"- 情绪：正 {sent.get('正', 0)}（{pct(sent.get('正', 0), n)}） / "
        f"中 {sent.get('中', 0)}（{pct(sent.get('中', 0), n)}） / "
        f"负 {neg}（{pct(neg, n)}）",
        f"- 可行动「是」：{act.get('是', 0)}（{pct(act.get('是', 0), n)}）",
        f"- 修辞≠none：{n - rhe.get('none', 0)}（{pct(n - rhe.get('none', 0), n)}）；"
        f"裂隙 cues≠none：{cue_hit}",
        f"- 高星（4–5）但整体态度负：**{hi_neg}**",
        "",
        "### 主题结构",
        "",
        "| 主题 | 条数 | 占比 | 其中负向 |",
        "|------|------|------|----------|",
    ]
    for k, v in topic.most_common():
        neg_t = int(((df["topic_primary"] == k) & (df["sentiment"] == "负")).sum())
        lines.append(
            f"| `{k}` {TOPIC_CN.get(k, k)} | {v} | {pct(v, n)} | {neg_t}（{pct(neg_t, v)}） |"
        )
    return lines


def section_alerts(df: pd.DataFrame, baseline: pd.DataFrame | None) -> list[str]:
    """相对基线的简易异动（样本小，仅作结构演示）。"""
    lines = ["## 异动与关注点", ""]
    n = len(df)
    neg_rate = (df["sentiment"] == "负").mean() if n else 0
    alerts: list[str] = []

    base_n = len(baseline) if baseline is not None else 0
    allow_delta = base_n >= 30 and (n == 0 or base_n >= 0.5 * n)
    if baseline is not None and base_n and allow_delta:
        base_neg = (baseline["sentiment"] == "负").mean()
        delta = neg_rate - base_neg
        alerts.append(
            f"- 负面占比：本期 {neg_rate * 100:.1f}% vs 对照 {base_neg * 100:.1f}% "
            f"（Δ {delta * 100:+.1f}pp）"
        )
        t0 = Counter(df["topic_primary"]).most_common(1)
        if t0:
            k, v = t0[0]
            share = v / n if n else 0
            bshare = (baseline["topic_primary"] == k).mean()
            alerts.append(
                f"- 主主题 `{k}` 占比：本期 {share * 100:.1f}% vs 对照 {bshare * 100:.1f}%"
            )
    elif baseline is not None and base_n:
        alerts.append(
            f"- 负面占比：{neg_rate * 100:.1f}%（对照 n={base_n} 不足，仅绝对水平，不做 Δpp）"
        )
    else:
        alerts.append(f"- 负面占比：{neg_rate * 100:.1f}%（无对照窗，仅绝对水平）")

    rhe_n = (df["rhetoric"].fillna("none") != "none").sum()
    if rhe_n:
        alerts.append(f"- 修辞命中 {rhe_n} 条（讽刺/高级黑/模板等），建议人工扫一眼原文")
    hi_neg = df[(df["score_raw"].isin(["4", "5"])) & (df["sentiment"] == "负")]
    if len(hi_neg):
        alerts.append(f"- 「高星负向意图」{len(hi_neg)} 条（星级与态度冲突，适合异动下钻）")

    # top negative topics
    neg = df[df["sentiment"] == "负"]
    if len(neg):
        top = Counter(neg["topic_primary"]).most_common(3)
        alerts.append(
            "- 负向主题 Top："
            + "、".join(f"`{k}`×{v}" for k, v in top)
        )

    if not alerts:
        alerts.append("- 本期无明显结构信号（样本过小）")
    lines.extend(alerts)
    lines.append("")
    lines.append("> 样本期标注量有限，异动阈值仅演示口径，不可外推全站。")
    return lines


def section_excerpts(df: pd.DataFrame, limit_per_topic: int = 2) -> list[str]:
    lines = ["## 原文摘录（分主题，负向/修辞优先）", ""]
    # order topics by negative count then volume
    neg_c = Counter(df.loc[df["sentiment"] == "负", "topic_primary"])
    vol = Counter(df["topic_primary"])
    order = sorted(vol.keys(), key=lambda k: (-neg_c.get(k, 0), -vol[k]))
    for topic in order:
        sub = df[df["topic_primary"] == topic].copy()
        sub["_pri"] = (
            (sub["sentiment"] == "负").astype(int) * 3
            + (sub["rhetoric"].fillna("none") != "none").astype(int) * 2
            + (sub["actionable"] == "是").astype(int)
        )
        sub = sub.sort_values(["_pri", "confidence"], ascending=[False, False])
        lines.append(f"### {TOPIC_CN.get(topic, topic)} (`{topic}`)")
        lines.append("")
        for _, r in sub.head(limit_per_topic).iterrows():
            rhe = r.get("rhetoric") or "none"
            cues = r.get("incongruity_cues") or "none"
            lines.append(
                f"- **{r.review_id}**｜星{r.score_raw}｜{r.sentiment}｜rhe=`{rhe}`｜"
                f"可行动={r.actionable}"
            )
            lines.append(f"  - 理由：{r.reason}")
            if cues != "none":
                lines.append(f"  - 裂隙：`{cues}`")
            lines.append(f"  - 摘录：{clip(r.text, 90)}")
        lines.append("")
    return lines


def section_actionable(df: pd.DataFrame, n: int = 8) -> list[str]:
    sub = df[df["actionable"] == "是"].copy()
    if sub.empty:
        return ["## 可行动线索", "", "- 本期无可行动=是 的样本", ""]
    sub = sub.sort_values(
        by=["sentiment", "confidence"],
        ascending=[True, False],
    )  # 负优先（负字序在中正前? 中<正<负 in unicode - bad)
    # explicit order
    order_map = {"负": 0, "中": 1, "正": 2}
    sub["_o"] = sub["sentiment"].map(lambda x: order_map.get(x, 9))
    sub = sub.sort_values(["_o", "confidence"], ascending=[True, False])
    lines = ["## 可行动线索（产品/运营可跟进）", ""]
    for _, r in sub.head(n).iterrows():
        lines.append(
            f"- `{r.topic_primary}`｜{r.sentiment}｜星{r.score_raw}｜{r.review_id}：{r.reason}"
        )
        lines.append(f"  - {clip(r.text, 80)}")
    lines.append("")
    return lines


def meta_block(
    kind: str,
    window: str,
    ann_name: str,
    prompt_ver: str,
    model: str,
    n_total_pool: int,
) -> list[str]:
    now = datetime.now(TZ)
    return [
        f"# TapTap《{GAME_PROFILE.get('name', '')}》评价 · 样例{kind}",
        "",
        "## 元数据",
        "",
        "| 项 | 内容 |",
        "|----|------|",
        f"| 生成时间 | {now.isoformat(timespec='seconds')} |",
        "| 生成软件 | Cursor Agent + `L6_delivery/period_reports/build_period_reports.py` |",
        f"| 用途 | 用已标样本验证日/周异动报告结构与口径；**非正式全库结论。** |",
        f"| 窗口 | {window} |",
        f"| 标注表 | `{ann_name}`（prompt `{prompt_ver}`） |",
        f"| 模型 | `{model}` |",
        f"| 池内已标总量 | {n_total_pool} 条（本报告为窗口切片） |",
        "| 指标口径 | 主情绪=`sentiment`；修辞=`rhetoric`/`incongruity_cues`；可行动=`actionable` |",
        "",
    ]


def write_daily(df_all: pd.DataFrame, day, ann_name: str) -> Path:
    day_df = df_all[df_all["day"] == day].copy()
    # baseline: previous calendar day with any data, else previous 3 days pool
    prev_days = sorted(d for d in df_all["day"].unique() if d < day)
    baseline = None
    if prev_days:
        baseline = df_all[df_all["day"] == prev_days[-1]]
    prompt_ver = str(day_df["prompt_version"].iloc[0]) if len(day_df) else ""
    model = str(day_df["model"].iloc[0]) if len(day_df) else ""
    lines = meta_block(
        "日报",
        f"{day}（发布日）",
        ann_name,
        prompt_ver,
        model,
        len(df_all),
    )
    lines += section_overview(day_df, "1. 当日概览")
    lines += [""]
    lines += section_alerts(day_df, baseline)
    lines += [""]
    if baseline is not None and len(baseline):
        lines.append(f"对照日：{prev_days[-1]}（n={len(baseline)}）")
        lines.append("")
    lines += section_actionable(day_df)
    lines += section_excerpts(day_df)
    lines += [
        "## 产物说明",
        "",
        "- 本日报由窗口内已标注评价聚合；未覆盖当日全量 TapTap 评论。",
        "- 全量后可将对照改为「昨日全量 / 近7日均值」并加阈值。",
        "",
        "---",
        "",
        "*报告结束*",
    ]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"daily_sample_{day.isoformat().replace('-', '')}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_weekly(df_all: pd.DataFrame, end_day, ann_name: str) -> Path:
    start = end_day - timedelta(days=6)
    week = df_all[(df_all["day"] >= start) & (df_all["day"] <= end_day)].copy()
    # baseline: previous 7 days
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)
    baseline = df_all[(df_all["day"] >= prev_start) & (df_all["day"] <= prev_end)]
    prompt_ver = str(week["prompt_version"].mode().iloc[0]) if len(week) else ""
    model = str(week["model"].mode().iloc[0]) if len(week) else ""
    lines = meta_block(
        "周报",
        f"{start} ~ {end_day}（含首尾，共 7 日）",
        ann_name,
        prompt_ver,
        model,
        len(df_all),
    )
    lines += section_overview(week, "1. 本周概览")
    lines += ["", "### 分日量与负面占比", ""]
    lines.append("| 日期 | 条数 | 负面占比 | 主主题 |")
    lines.append("|------|------|----------|--------|")
    for d in sorted(week["day"].unique()):
        sub = week[week["day"] == d]
        neg_r = (sub["sentiment"] == "负").mean() * 100 if len(sub) else 0
        top = Counter(sub["topic_primary"]).most_common(1)
        top_s = f"`{top[0][0]}`×{top[0][1]}" if top else "—"
        lines.append(f"| {d} | {len(sub)} | {neg_r:.0f}% | {top_s} |")
    lines += [""]
    lines += section_alerts(week, baseline if len(baseline) else None)
    lines += [""]
    if len(baseline):
        lines.append(f"对照周：{prev_start} ~ {prev_end}（n={len(baseline)}）")
        lines.append("")
    # rhetoric summary
    rhe = week[week["rhetoric"].fillna("none") != "none"]
    lines += ["## 修辞与裂隙（本周）", ""]
    if rhe.empty:
        lines.append("- 无 rhetoric≠none")
    else:
        lines.append(f"- rhetoric≠none：**{len(rhe)}** / {len(week)}")
        for k, v in Counter(rhe["rhetoric"]).most_common():
            lines.append(f"  - `{k}`：{v}")
        cue_c: Counter[str] = Counter()
        for raw in rhe["incongruity_cues"].fillna("none"):
            for c in str(raw).split(","):
                c = c.strip()
                if c and c != "none":
                    cue_c[c] += 1
        if cue_c:
            lines.append("- 裂隙线索频次：")
            for k, v in cue_c.most_common():
                lines.append(f"  - `{k}`：{v}")
    lines += [""]
    lines += section_actionable(week, n=10)
    lines += section_excerpts(week, limit_per_topic=2)
    lines += [
        "## 产物说明",
        "",
        "- 样例周报用于演示「量级 → 情绪/主题结构 → 异动对照 → 可行动 → 摘录」链路。",
        "- 全量标注后，建议固定自然周（周一至周日）并加入阈值告警。",
        "",
        "---",
        "",
        "*报告结束*",
    ]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"weekly_sample_{start.isoformat().replace('-', '')}_{end_day.isoformat().replace('-', '')}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    p = argparse.ArgumentParser(description="Build sample daily/weekly reports from annotations")
    p.add_argument(
        "--ann",
        default=str(ANN / "annotations_v1_4.csv"),
        help="标注 CSV（默认 v1.4）",
    )
    p.add_argument("--day", default="", help="日报日期 YYYY-MM-DD；默认库内最新发布日")
    p.add_argument("--week-end", default="", help="周报结束日 YYYY-MM-DD；默认取样本最新日")
    args = p.parse_args()

    ann_path = Path(args.ann)
    if not ann_path.exists():
        print(f"缺少标注表：{ann_path}")
        return 2
    df = load_annotated(ann_path)
    if df.empty:
        print("无可用标注行")
        return 1

    # 默认最新日（禁止默认最密日；终稿请用 build_skill_reports.py）
    if args.day:
        day = datetime.strptime(args.day, "%Y-%m-%d").date()
    else:
        day = max(df["day"])
    if args.week_end:
        end_day = datetime.strptime(args.week_end, "%Y-%m-%d").date()
    else:
        end_day = max(df["day"])

    daily_path = write_daily(df, day, ann_path.name)
    weekly_path = write_weekly(df, end_day, ann_path.name)

    print(daily_path)
    print(weekly_path)
    print(
        f"day={day} n_day={int((df['day']==day).sum())} "
        f"week_end={end_day} "
        f"n_week={int(((df['day']>=end_day-timedelta(days=6))&(df['day']<=end_day)).sum())}"
    )
    print("提示：样例底稿已写出；Skill 终稿请运行 build_skill_reports.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
