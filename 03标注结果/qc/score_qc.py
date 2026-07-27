#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""根据人工金标计算与模型标注的一致率，写出质检报告。"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

TZ = timezone(timedelta(hours=8))
QC = Path(__file__).resolve().parent
REPORTS = QC.parent / "reports"

SENT_OK = {"正", "中", "负"}
RHET_OK = {"none", "sarcasm", "gaoji_hei", "fanchuan", "template_praise"}

# 盲标中文 → 内部英文码（亦兼容直接填英文）
RHET_CN_TO_CODE = {
    "无修辞": "none",
    "无": "none",
    "讽刺": "sarcasm",
    "反讽": "sarcasm",
    "阴阳": "sarcasm",
    "高级黑": "gaoji_hei",
    "反串": "fanchuan",
    "模板好评": "template_praise",
    "模板": "template_praise",
}


def norm_sent(x: object) -> str:
    s = str(x or "").strip()
    return s if s in SENT_OK else ""


def norm_rhe(x: object) -> str:
    raw = str(x or "").strip()
    if not raw or raw.lower() == "nan":
        return ""
    if raw in RHET_CN_TO_CODE:
        return RHET_CN_TO_CODE[raw]
    s = raw.lower().replace(" ", "")
    if s in RHET_CN_TO_CODE:
        return RHET_CN_TO_CODE[s]
    return s if s in RHET_OK else ""


def md_table(ct: pd.DataFrame) -> str:
    if ct.empty:
        return "（空）"
    cols = [str(c) for c in ct.columns]
    header = "| 金标\\\\模型 | " + " | ".join(cols) + " |"
    sep = "|---|" + "|".join(["---"] * len(cols)) + "|"
    rows = []
    for i in ct.index:
        vals = " | ".join(str(int(ct.loc[i, c])) for c in ct.columns)
        rows.append(f"| {i} | {vals} |")
    return "\n".join([header, sep, *rows])


def case_lines(df: pd.DataFrame, blind: pd.DataFrame, title: str, limit: int = 8) -> list[str]:
    out = [f"### {title}", ""]
    if df.empty:
        return out + ["（无）", ""]
    show = df.merge(blind[["review_id", "text"]], on="review_id", how="left")
    for _, r in show.head(limit).iterrows():
        t = str(r.get("text") or "").replace("\n", " ")[:60]
        out.append(
            f"- `{r.review_id}`｜金标 {r.gold_sentiment}/{r.gold_rhetoric} "
            f"vs 模型 {r.model_sentiment}/{r.model_rhetoric}｜{t}…"
        )
    out.append("")
    return out


def load_blind(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        # 优先读「盲标」sheet；否则第一张有 gold_sentiment 的表
        xl = pd.ExcelFile(path)
        sheet = "盲标" if "盲标" in xl.sheet_names else xl.sheet_names[-1]
        df = pd.read_excel(path, sheet_name=sheet, dtype={"review_id": str})
    else:
        df = pd.read_csv(path, dtype={"review_id": str})
    return df


def main() -> int:
    p = argparse.ArgumentParser(description="Score QC gold vs model")
    p.add_argument(
        "--blind",
        default="",
        help="金标文件；默认优先 qc_blind_to_label.xlsx，否则 csv",
    )
    p.add_argument("--model", default=str(QC / "qc_with_model.csv"))
    p.add_argument("--out", default="")
    args = p.parse_args()

    if args.blind:
        blind_path = Path(args.blind)
    else:
        xlsx = QC / "qc_blind_to_label.xlsx"
        csv = QC / "qc_blind_to_label.csv"
        blind_path = xlsx if xlsx.exists() else csv

    if not blind_path.exists():
        print(f"找不到金标文件：{blind_path}")
        return 2

    blind = load_blind(blind_path)
    model = pd.read_csv(args.model, dtype={"review_id": str})

    # 保留中文原值，再映射为英文码
    blind["gold_sentiment_raw"] = blind["gold_sentiment"].fillna("").astype(str)
    blind["gold_rhetoric_raw"] = blind["gold_rhetoric"].fillna("").astype(str)
    blind["gold_sentiment"] = blind["gold_sentiment_raw"].map(norm_sent)
    blind["gold_rhetoric"] = blind["gold_rhetoric_raw"].map(norm_rhe)

    labeled = blind[
        (blind["gold_sentiment"] != "") & (blind["gold_rhetoric"] != "")
    ].copy()
    if labeled.empty:
        print(f"尚未填写金标：请在 {blind_path.name} 的「盲标」表用下拉填写")
        print("情绪：正 / 中 / 负")
        print("修辞：无修辞 / 讽刺 / 高级黑 / 反串 / 模板好评")
        return 1

    # 转化产物：中文金标 → 英文码（与模型字段对齐）
    norm_path = QC / "qc_gold_normalized.csv"
    cols_norm = [
        c
        for c in [
            "qc_id",
            "review_id",
            "gold_sentiment_raw",
            "gold_rhetoric_raw",
            "gold_sentiment",
            "gold_rhetoric",
            "gold_note",
        ]
        if c in labeled.columns
    ]
    labeled[cols_norm].to_csv(norm_path, index=False, encoding="utf-8-sig")
    print(f"已转化：{norm_path}（中文 → 英文码）")

    m = labeled.merge(
        model[["review_id", "model_sentiment", "model_rhetoric", "strata"]],
        on="review_id",
        how="inner",
    )
    if m.empty:
        print("金标与对照表无法按 review_id 对齐")
        return 2

    n = len(m)
    sent_ok = int((m["gold_sentiment"] == m["model_sentiment"]).sum())
    rhe_ok = int((m["gold_rhetoric"] == m["model_rhetoric"]).sum())
    m["gold_rhe_bin"] = m["gold_rhetoric"].ne("none")
    m["model_rhe_bin"] = m["model_rhetoric"].fillna("none").ne("none")
    rhe_bin_ok = int((m["gold_rhe_bin"] == m["model_rhe_bin"]).sum())

    sent_ct = pd.crosstab(m["gold_sentiment"], m["model_sentiment"])
    rhe_ct = pd.crosstab(m["gold_rhetoric"], m["model_rhetoric"])
    miss_rhe = m[(m["gold_rhetoric"] != "none") & (m["model_rhetoric"] == "none")]
    false_rhe = m[(m["gold_rhetoric"] == "none") & (m["model_rhetoric"] != "none")]
    sent_miss = m[m["gold_sentiment"] != m["model_sentiment"]]

    detail = m[
        [
            "review_id",
            "gold_sentiment",
            "model_sentiment",
            "gold_rhetoric",
            "model_rhetoric",
            "strata",
        ]
    ].copy()
    detail["sent_match"] = detail["gold_sentiment"] == detail["model_sentiment"]
    detail["rhe_match"] = detail["gold_rhetoric"] == detail["model_rhetoric"]
    detail = detail.merge(
        blind[["review_id", "score_raw", "text", "gold_note"]],
        on="review_id",
        how="left",
    )
    detail_path = QC / "qc_scored_detail.csv"
    detail.to_csv(detail_path, index=False, encoding="utf-8-sig")

    ts = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else REPORTS / f"qc_v1_{ts}.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# TapTap 标注质检报告（人工金标 vs v1.4）",
        "",
        "## 元数据",
        "",
        "| 项 | 内容 |",
        "|----|------|",
        f"| 生成时间 | {datetime.now(TZ).isoformat(timespec='seconds')} |",
        f"| 已标条数 | **{n}** / 盲标表 {len(blind)} |",
        "| 模型侧 | `annotations_v1_4.csv` |",
        "| 金标文件 | `{blind_path.name}` |",
        f"| 明细 | `qc/{detail_path.name}` |",
        "",
        "## 一致率（主结果）",
        "",
        "| 指标 | 一致数 | 一致率 |",
        "|------|--------|--------|",
        f"| 情绪 `sentiment` 精确一致 | {sent_ok} | **{sent_ok / n * 100:.1f}%** |",
        f"| 修辞 `rhetoric` 精确一致 | {rhe_ok} | **{rhe_ok / n * 100:.1f}%** |",
        f"| 修辞二分类（有/无修辞） | {rhe_bin_ok} | **{rhe_bin_ok / n * 100:.1f}%** |",
        "",
        "> 修辞在样本中过采样，精确一致率通常低于情绪；二分类更能反映漏检/误报风险。",
        "",
        "## 情绪混淆（行=金标，列=模型）",
        "",
        md_table(sent_ct),
        "",
        "## 修辞混淆（行=金标，列=模型）",
        "",
        md_table(rhe_ct),
        "",
        "## 错例摘要",
        "",
        f"- 情绪不一致：**{len(sent_miss)}** 条",
        f"- 金标有修辞、模型 `none`（漏检）：**{len(miss_rhe)}** 条",
        f"- 金标 `none`、模型有修辞（误报）：**{len(false_rhe)}** 条",
        "",
    ]
    lines += case_lines(sent_miss, blind, "情绪不一致样例")
    lines += case_lines(miss_rhe, blind, "修辞漏检样例")
    lines += case_lines(false_rhe, blind, "修辞误报样例")
    lines += [
        "## 对外口径（金标填完后选用）",
        "",
        f"在 n={n} 人工金标抽检上，情绪一致率 {sent_ok / n * 100:.1f}%，"
        f"修辞二分类一致率 {rhe_bin_ok / n * 100:.1f}%"
        "（修辞类过采样，非全库无偏估计）。",
        "",
        "## 边界",
        "",
        "- 单人金标，无双人一致性（IAA）。",
        "- 修辞过采样 → 全库修辞精确率不可直接外推。",
        "- 情绪全库分布与本样本不完全同构。",
        "",
        "---",
        "",
        "*由 `qc/score_qc.py` 自动生成*",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)
    print(detail_path)
    print(
        f"n={n} sent={sent_ok / n * 100:.1f}% "
        f"rhe={rhe_ok / n * 100:.1f}% rhe_bin={rhe_bin_ok / n * 100:.1f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
