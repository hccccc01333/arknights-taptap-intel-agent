#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""评价表预处理：缺失报告、异常标记、长度分桶、去重；不做评分插补。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data/raw/taptap"
INPUT_CSV = DATA_DIR / "reviews.csv"
PROCESSED_DIR = ROOT / "data/processed/reviews"
REPORT_DIR = ROOT / "data/processed/reviews" / "reports"
FIG_DIR = ROOT / "data/processed" / "figures"

TZ_CN = timezone(timedelta(hours=8))
KEY_FIELDS = [
    "review_id",
    "text",
    "score_raw",
    "played_spent_sec",
    "publish_time",
    "publish_time_cn",
    "user_id_hash",
]


def now_tag() -> str:
    return datetime.now(TZ_CN).strftime("%Y%m%d_%H%M%S")


def is_blank(series: pd.Series) -> pd.Series:
    s = series.astype("string")
    return s.isna() | (s.str.strip() == "") | (s.str.lower() == "nan") | (s.str.lower() == "none")


def length_bucket(n: int) -> str:
    if n <= 30:
        return "short"
    if n <= 300:
        return "mid"
    if n <= 1500:
        return "long"
    return "very_long"


def main(args: argparse.Namespace) -> int:
    data_dir = Path(getattr(args, "data_dir", "") or DATA_DIR)
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    input_csv = data_dir / "reviews.csv"
    processed_dir = data_dir / "processed"
    report_dir = data_dir / "reports"
    fig_dir = data_dir / "figures"
    processed_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_csv, dtype=str, keep_default_na=False)
    n0 = len(df)
    drop_rows: list[dict] = []

    # --- missing report (before drop) ---
    miss_rows = []
    for col in df.columns:
        miss = int(is_blank(df[col]).sum())
        miss_rows.append({"field": col, "missing": miss, "missing_rate": round(miss / n0, 4) if n0 else 0})
    miss_df = pd.DataFrame(miss_rows).sort_values("missing", ascending=False)

    # --- drop empty text / missing score ---
    empty_text = is_blank(df["text"])
    for idx in df.index[empty_text]:
        drop_rows.append({"review_id": df.at[idx, "review_id"], "reason": "empty_text"})
    df = df.loc[~empty_text].copy()

    empty_score = is_blank(df["score_raw"])
    for idx in df.index[empty_score]:
        drop_rows.append({"review_id": df.at[idx, "review_id"], "reason": "empty_score"})
    df = df.loc[~empty_score].copy()

    # --- type coerce ---
    df["score_raw_num"] = pd.to_numeric(df["score_raw"], errors="coerce")
    df["played_spent_num"] = pd.to_numeric(df["played_spent_sec"], errors="coerce")
    df["played_hours_num"] = pd.to_numeric(df["played_hours"], errors="coerce")
    df["text_len"] = df["text"].astype(str).str.len()
    df["length_bucket"] = df["text_len"].map(length_bucket)

    # --- anomalies (flag; only drop invalid score / negative playtime) ---
    bad_score = df["score_raw_num"].isna() | (df["score_raw_num"] < 1) | (df["score_raw_num"] > 5)
    for idx in df.index[bad_score]:
        drop_rows.append({"review_id": df.at[idx, "review_id"], "reason": "score_out_of_range"})
    df = df.loc[~bad_score].copy()

    neg_play = df["played_spent_num"].notna() & (df["played_spent_num"] < 0)
    for idx in df.index[neg_play]:
        drop_rows.append({"review_id": df.at[idx, "review_id"], "reason": "negative_played_spent"})
    df = df.loc[~neg_play].copy()

    # --- dedupe by review_id, keep latest last_seen_at ---
    before_dedupe = len(df)
    if "last_seen_at" in df.columns:
        df = df.sort_values("last_seen_at")
    dup_mask = df.duplicated("review_id", keep="last")
    for rid in df.loc[dup_mask, "review_id"]:
        drop_rows.append({"review_id": rid, "reason": "duplicate_review_id"})
    df = df.loc[~dup_mask].copy()
    dup_removed = before_dedupe - len(df)

    # keep played_spent empty as empty string for CSV clarity
    # do NOT impute

    # --- figures ---
    fig_paths = []
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    axes[0].boxplot(df["score_raw_num"].dropna(), vert=True)
    axes[0].set_title("score_raw")
    axes[1].boxplot(df["text_len"].dropna(), vert=True)
    axes[1].set_title("text_len")
    play = df["played_hours_num"].dropna()
    if len(play):
        axes[2].boxplot(play, vert=True)
        axes[2].set_title("played_hours (non-null)")
    else:
        axes[2].text(0.5, 0.5, "no played_hours", ha="center")
        axes[2].set_title("played_hours")
    fig.tight_layout()
    box_path = fig_dir / f"preprocess_box_{now_tag()}.png"
    fig.savefig(box_path, dpi=120)
    plt.close(fig)
    fig_paths.append(box_path)

    fig2, ax = plt.subplots(figsize=(6, 4))
    df["score_raw_num"].astype(int).value_counts().sort_index().plot(kind="bar", ax=ax, color="#0d7377")
    ax.set_title("score_raw distribution")
    ax.set_xlabel("score")
    ax.set_ylabel("count")
    fig2.tight_layout()
    score_path = fig_dir / f"preprocess_score_{now_tag()}.png"
    fig2.savefig(score_path, dpi=120)
    plt.close(fig2)
    fig_paths.append(score_path)

    # --- outputs ---
    out_clean = processed_dir / "reviews_clean.csv"
    out_drop = processed_dir / "dropped_rows.csv"
    # drop helper numeric cols that duplicate; keep useful derived
    save_df = df.copy()
    save_df.to_csv(out_clean, index=False, encoding="utf-8-sig")
    drop_df = pd.DataFrame(drop_rows)
    drop_df.to_csv(out_drop, index=False, encoding="utf-8-sig")

    # IQR notes (EDA only, not deletion)
    def iqr_note(s: pd.Series) -> str:
        s = s.dropna()
        if len(s) < 4:
            return "样本不足"
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outside = int(((s < low) | (s > high)).sum())
        return f"Q1={q1:.2f}, Q3={q3:.2f}, IQR外点数={outside}（仅观察，未因箱线删除）"

    bucket_counts = save_df["length_bucket"].value_counts().to_dict()
    platform_counts = save_df["publish_platform"].value_counts().to_dict() if "publish_platform" in save_df else {}

    report = report_dir / f"preprocess_qc_{now_tag()}.md"
    lines = [
        "# 预处理质检报告",
        "",
        f"- 生成时间：{datetime.now(TZ_CN).isoformat(timespec='seconds')}",
        f"- 输入：`{input_csv.as_posix()}`",
        f"- 原始行数：{n0}",
        f"- 清洗后行数：{len(save_df)}",
        f"- 剔除行数：{len(drop_df)}（含重复 {dup_removed}）",
        f"- 输出：`{out_clean.as_posix()}`",
        "",
        "## 缺失值（清洗前）",
        "",
        "| 字段 | 缺失数 | 缺失率 |",
        "|------|--------|--------|",
    ]
    for _, r in miss_df.head(20).iterrows():
        lines.append(f"| {r['field']} | {r['missing']} | {r['missing_rate']:.2%} |")
    lines += [
        "",
        "### 处理口径",
        "- `text` / 非法 `score_raw`：删除",
        "- `played_spent_sec` 空：保留（隐藏时长），**不做插补**",
        "- 评分：**禁止**均值/众数填充",
        "",
        "## 分布",
        f"- score：{save_df['score_raw_num'].astype(int).value_counts().sort_index().to_dict()}",
        f"- length_bucket：{bucket_counts}",
        f"- publish_platform：{platform_counts}",
        "",
        "## 箱线 / IQR（仅 EDA）",
        f"- score_raw：{iqr_note(save_df['score_raw_num'])}",
        f"- text_len：{iqr_note(save_df['text_len'])}",
        f"- played_hours：{iqr_note(save_df['played_hours_num'])}",
        "",
        "## 图",
    ]
    for p in fig_paths:
        lines.append(f"- `{p.as_posix()}`")
    lines += [
        "",
        "## 关键字段齐全性（清洗后）",
    ]
    for col in KEY_FIELDS:
        if col not in save_df.columns:
            lines.append(f"- {col}：列不存在")
        else:
            m = int(is_blank(save_df[col]).sum()) if col != "played_spent_sec" else int(is_blank(save_df[col]).sum())
            note = "（允许空）" if col == "played_spent_sec" else ""
            lines.append(f"- {col} 空值：{m}{note}")

    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"[done] clean={len(save_df)} dropped={len(drop_df)}")
    print(f"[out] {out_clean}")
    print(f"[qc] {report}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="", help="数据目录（默认 data/raw/taptap；多游戏隔离时按游戏指定）")
    raise SystemExit(main(ap.parse_args()))
