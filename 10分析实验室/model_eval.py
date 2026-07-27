#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0-2 模型评估：人工金标（优先）或星级映射 proxy vs LLM v1.4。

用法:
  python 10分析实验室/model_eval.py
  python 10分析实验室/model_eval.py --force-proxy   # 即使有金标也跑 proxy（调试）
  python 10分析实验室/model_eval.py --proxy-n 200 --seed 42
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

TZ = timezone(timedelta(hours=8))
LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
QC = ROOT / "03标注结果" / "qc"
ANN_PATH = ROOT / "03标注结果" / "annotations_v1_4.csv"
OUT_DIR = LAB / "outputs"
REPORT_DIR = LAB / "reports"

SENT_OK = {"正", "中", "负"}
SENT_ORDER = ["正", "中", "负"]
RHET_OK = {"none", "sarcasm", "gaoji_hei", "fanchuan", "template_praise"}
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def now_ts() -> str:
    return datetime.now(TZ).strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


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


def star_to_sentiment(score: object) -> str:
    """星级→情绪弱基线：4–5 正，3 中，1–2 负（与 annotate prompt 模糊兜底一致）。"""
    try:
        s = int(float(str(score).strip()))
    except (TypeError, ValueError):
        return ""
    if s >= 4:
        return "正"
    if s == 3:
        return "中"
    if s in (1, 2):
        return "负"
    return ""


def load_blind(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        xl = pd.ExcelFile(path)
        sheet = "盲标" if "盲标" in xl.sheet_names else xl.sheet_names[-1]
        return pd.read_excel(path, sheet_name=sheet, dtype={"review_id": str})
    return pd.read_csv(path, dtype={"review_id": str})


def resolve_blind_path() -> Path | None:
    xlsx = QC / "qc_blind_to_label.xlsx"
    csv = QC / "qc_blind_to_label.csv"
    if xlsx.exists():
        return xlsx
    if csv.exists():
        return csv
    return None


def accuracy(y_true: list[str], y_pred: list[str]) -> float:
    if not y_true:
        return float("nan")
    return sum(a == b for a, b in zip(y_true, y_pred)) / len(y_true)


def macro_f1(y_true: list[str], y_pred: list[str], labels: list[str]) -> float:
    """未加权宏平均 F1；某类无金标时该标签 F1 记 0。"""
    if not y_true:
        return float("nan")
    scores: list[float] = []
    for lab in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p != lab)
        if tp == 0 and fp == 0 and fn == 0:
            scores.append(0.0)
            continue
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        scores.append(f1)
    return sum(scores) / len(labels) if labels else float("nan")


def confusion(y_true: list[str], y_pred: list[str], labels: list[str]) -> dict[str, dict[str, int]]:
    idx = {lab: i for i, lab in enumerate(labels)}
    mat = [[0 for _ in labels] for _ in labels]
    for t, p in zip(y_true, y_pred):
        if t not in idx or p not in idx:
            continue
        mat[idx[t]][idx[p]] += 1
    return {labels[i]: {labels[j]: mat[i][j] for j in range(len(labels))} for i in range(len(labels))}


def md_confusion(ct: dict[str, dict[str, int]], labels: list[str]) -> str:
    header = "| 金标\\\\预测 | " + " | ".join(labels) + " |"
    sep = "|---|" + "|".join(["---"] * len(labels)) + "|"
    rows = []
    for lab in labels:
        vals = " | ".join(str(ct.get(lab, {}).get(c, 0)) for c in labels)
        rows.append(f"| {lab} | {vals} |")
    return "\n".join([header, sep, *rows])


def metrics_block(
    y_true: list[str],
    y_pred: list[str],
    labels: list[str],
) -> dict[str, Any]:
    return {
        "n": len(y_true),
        "accuracy": round(accuracy(y_true, y_pred), 6) if y_true else None,
        "macro_f1": round(macro_f1(y_true, y_pred, labels), 6) if y_true else None,
        "confusion": confusion(y_true, y_pred, labels) if y_true else {},
        "support": {lab: sum(1 for t in y_true if t == lab) for lab in labels},
    }


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------


def load_human_gold() -> tuple[pd.DataFrame, Path | None, str]:
    """返回 (labeled_df, blind_path, note)。labeled 至少需 gold_sentiment。"""
    path = resolve_blind_path()
    if path is None:
        return pd.DataFrame(), None, "未找到 qc_blind_to_label.xlsx/csv"

    blind = load_blind(path)
    blind = blind.copy()
    blind["review_id"] = blind["review_id"].astype(str)
    blind["gold_sentiment_raw"] = blind.get("gold_sentiment", pd.Series(dtype=str)).fillna("").astype(str)
    blind["gold_rhetoric_raw"] = blind.get("gold_rhetoric", pd.Series(dtype=str)).fillna("").astype(str)
    blind["gold_sentiment"] = blind["gold_sentiment_raw"].map(norm_sent)
    blind["gold_rhetoric"] = blind["gold_rhetoric_raw"].map(norm_rhe)

    # 情绪必填；修辞可选（有则算修辞指标）
    labeled = blind[blind["gold_sentiment"] != ""].copy()
    note = f"金标源={path.name}；已填情绪 {len(labeled)} / 盲标表 {len(blind)}"
    return labeled, path, note


def _normalize_pred_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["review_id"] = out["review_id"].astype(str)
    if "pred_sentiment" not in out.columns and "sentiment" in out.columns:
        out["pred_sentiment"] = out["sentiment"]
    if "pred_rhetoric" not in out.columns and "rhetoric" in out.columns:
        out["pred_rhetoric"] = out["rhetoric"]
    out["pred_sentiment"] = out.get("pred_sentiment", pd.Series(dtype=str)).map(norm_sent)
    rhe = out.get("pred_rhetoric", pd.Series(["none"] * len(out))).fillna("none").astype(str)
    out["pred_rhetoric"] = rhe.map(lambda x: norm_rhe(x) if norm_rhe(x) else "none")
    out.loc[out["pred_rhetoric"] == "", "pred_rhetoric"] = "none"
    out["star_sentiment"] = out["score_raw"].map(star_to_sentiment)
    return out


def load_model_preds(review_ids: list[str] | None = None) -> pd.DataFrame:
    """qc_with_model 优先，再用 annotations_v1_4 补齐缺失 id。"""
    pieces: list[pd.DataFrame] = []

    qc_model = QC / "qc_with_model.csv"
    if qc_model.exists():
        m = pd.read_csv(qc_model, dtype={"review_id": str})
        m = m.rename(
            columns={
                "model_sentiment": "pred_sentiment",
                "model_rhetoric": "pred_rhetoric",
            }
        )
        pieces.append(_normalize_pred_frame(m))

    ann = pd.read_csv(ANN_PATH, dtype={"review_id": str})
    ann = ann[ann["error"].fillna("") == ""].drop_duplicates("review_id", keep="last")
    ann = ann[~ann["review_id"].astype(str).str.startswith("probe_")].copy()
    pieces.append(_normalize_pred_frame(ann))

    if not pieces:
        raise FileNotFoundError("找不到 qc_with_model.csv 或 annotations_v1_4.csv")

    base = pieces[0]
    for extra in pieces[1:]:
        missing = extra[~extra["review_id"].isin(set(base["review_id"]))]
        if len(missing):
            # 对齐列
            for col in base.columns:
                if col not in missing.columns:
                    missing = missing.copy()
                    missing[col] = pd.NA
            base = pd.concat([base, missing[base.columns]], ignore_index=True)

    if review_ids is not None:
        base = base[base["review_id"].isin(set(review_ids))].copy()
    return base


def attach_text(df: pd.DataFrame) -> pd.DataFrame:
    """若缺 text，从 reviews_clean / 盲标表补齐。"""
    out = df.copy()
    if "text" in out.columns and out["text"].fillna("").astype(str).str.strip().ne("").any():
        return out
    clean_path = ROOT / "02数据" / "processed" / "reviews_clean.csv"
    if clean_path.exists():
        clean = pd.read_csv(clean_path, dtype={"review_id": str}, usecols=["review_id", "text"])
        out = out.drop(columns=["text"], errors="ignore").merge(clean, on="review_id", how="left")
    return out


def build_proxy_slice(n: int, seed: int) -> pd.DataFrame:
    """从 annotations 抽 held-out 切片，以星级映射为弱金标（proxy）。"""
    preds = load_model_preds()
    preds = preds[
        (preds["pred_sentiment"] != "")
        & (preds["star_sentiment"] != "")
    ].copy()

    # 尽量避开尚未填金标的 QC 表，方便日后真金标评估独立；若全库不够则回退
    qc_ids: set[str] = set()
    blind_path = resolve_blind_path()
    if blind_path is not None:
        try:
            blind = load_blind(blind_path)
            qc_ids = set(blind["review_id"].astype(str))
        except Exception:
            qc_ids = set()

    pool = preds[~preds["review_id"].isin(qc_ids)]
    if len(pool) < max(30, n // 2):
        pool = preds

    # 分层：按星级映射情绪抽样，保证三类都有
    parts: list[pd.DataFrame] = []
    per = max(1, n // 3)
    rng = seed
    for lab in SENT_ORDER:
        sub = pool[pool["star_sentiment"] == lab]
        take_n = min(len(sub), per)
        if take_n:
            parts.append(sub.sample(n=take_n, random_state=rng))
            rng += 1
    got = pd.concat(parts, ignore_index=True).drop_duplicates("review_id") if parts else pd.DataFrame()
    remain = n - len(got)
    if remain > 0:
        rest = pool[~pool["review_id"].isin(got["review_id"])]
        if len(rest):
            got = pd.concat(
                [got, rest.sample(n=min(remain, len(rest)), random_state=seed + 99)],
                ignore_index=True,
            )

    got = got.head(n).copy()
    got["gold_sentiment"] = got["star_sentiment"]
    got["gold_rhetoric"] = ""  # proxy 无修辞金标
    got["gold_source"] = "proxy_star_map"
    return attach_text(got)


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


def eval_sentiment(df: pd.DataFrame, pred_col: str) -> dict[str, Any]:
    sub = df[(df["gold_sentiment"] != "") & (df[pred_col] != "")].copy()
    y_true = sub["gold_sentiment"].tolist()
    y_pred = sub[pred_col].tolist()
    return metrics_block(y_true, y_pred, SENT_ORDER)


def eval_rhetoric(df: pd.DataFrame) -> dict[str, Any]:
    """修辞：精确一致 + 二分类（有/无）。无金标修辞时返回 empty。"""
    if "gold_rhetoric" not in df.columns:
        return {"n": 0, "exact": None, "binary": None}
    sub = df[df["gold_rhetoric"].fillna("").astype(str).str.strip() != ""].copy()
    if sub.empty:
        return {"n": 0, "exact": None, "binary": None}

    sub["pred_rhetoric"] = sub["pred_rhetoric"].fillna("none")
    y_t = sub["gold_rhetoric"].tolist()
    y_p = sub["pred_rhetoric"].tolist()
    labels = sorted(set(y_t) | set(y_p) | {"none"})
    exact = metrics_block(y_t, y_p, labels)

    y_tb = [("rhe" if t != "none" else "none") for t in y_t]
    y_pb = [("rhe" if p != "none" else "none") for p in y_p]
    binary = metrics_block(y_tb, y_pb, ["none", "rhe"])
    return {"n": len(sub), "exact": exact, "binary": binary}


def eval_topic(df: pd.DataFrame) -> dict[str, Any] | None:
    if "gold_topic" not in df.columns:
        return None
    sub = df[
        (df["gold_topic"].fillna("").astype(str).str.strip() != "")
        & (df["topic_primary"].fillna("").astype(str).str.strip() != "")
    ].copy()
    if sub.empty:
        return None
    labels = sorted(set(sub["gold_topic"]) | set(sub["topic_primary"]))
    return metrics_block(sub["gold_topic"].tolist(), sub["topic_primary"].tolist(), labels)


def comparison_table(llm: dict[str, Any], star: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name, m in [("LLM_v1.4", llm), ("star_map_baseline", star)]:
        rows.append(
            {
                "system": name,
                "n": m.get("n"),
                "accuracy": m.get("accuracy"),
                "macro_f1": m.get("macro_f1"),
            }
        )
    return rows


def export_errors(df: pd.DataFrame, mode: str, out_path: Path) -> int:
    """导出情绪错例（LLM ≠ gold）。"""
    sub = df[(df["gold_sentiment"] != "") & (df["pred_sentiment"] != "")].copy()
    err = sub[sub["gold_sentiment"] != sub["pred_sentiment"]].copy()
    cols = [
        c
        for c in [
            "review_id",
            "score_raw",
            "gold_sentiment",
            "pred_sentiment",
            "star_sentiment",
            "gold_rhetoric",
            "pred_rhetoric",
            "topic_primary",
            "strata",
            "text",
            "gold_note",
            "gold_source",
        ]
        if c in err.columns
    ]
    err = err[cols]
    err.insert(0, "eval_mode", mode)
    err.insert(1, "error_type", "sentiment_mismatch")
    # 截断文本
    if "text" in err.columns:
        err["text"] = err["text"].astype(str).str.replace("\n", " ", regex=False).str.slice(0, 200)
    err.to_csv(out_path, index=False, encoding="utf-8-sig")
    return len(err)


def write_report(
    payload: dict[str, Any],
    path: Path,
    instructions: list[str],
) -> None:
    mode = payload["eval_mode"]
    is_proxy = mode.startswith("proxy")
    llm = payload["sentiment"]["llm"]
    star = payload["sentiment"]["star_baseline"]
    rhe = payload.get("rhetoric") or {}

    lines = [
        f"# 模型评估报告（P0-2）{'【PROXY】' if is_proxy else ''}",
        "",
        "## 元数据",
        "",
        "| 项 | 内容 |",
        "|----|------|",
        f"| 生成时间 | {payload['generated_at']} |",
        f"| 评估模式 | **`{mode}`** |",
        f"| n_gold | **{payload['n_gold']}** |",
        f"| 金标说明 | {payload.get('gold_note', '')} |",
        f"| 模型侧 | `{payload.get('model_source', '')}` |",
        f"| 明细 JSON | `outputs/model_eval.json` |",
        f"| 错例 | `outputs/{payload.get('error_cases_file', 'error_cases.csv')}` |",
        "",
    ]

    if is_proxy:
        lines += [
            "> **PROXY 声明**：当前人工金标为空/不足，本报告以「星级→情绪映射」为弱参照，"
            "指标**不可**当作正式模型精度。填完 QC 盲标后请重跑本脚本得到正式结果。",
            "",
        ]

    lines += [
        "## 情绪：LLM vs 星级基线",
        "",
        "| 系统 | n | Accuracy | Macro-F1 |",
        "|------|---|----------|----------|",
    ]
    for row in payload["comparison_table"]:
        acc = row["accuracy"]
        f1 = row["macro_f1"]
        acc_s = f"{acc * 100:.1f}%" if isinstance(acc, (int, float)) and acc == acc else "—"
        f1_s = f"{f1:.4f}" if isinstance(f1, (int, float)) and f1 == f1 else "—"
        lines.append(f"| {row['system']} | {row['n']} | {acc_s} | {f1_s} |")

    lines += [
        "",
        "### LLM 情绪混淆矩阵（行=金标/弱金标，列=预测）",
        "",
        md_confusion(llm.get("confusion") or {}, SENT_ORDER),
        "",
        "### 星级基线混淆矩阵",
        "",
        md_confusion(star.get("confusion") or {}, SENT_ORDER),
        "",
    ]

    if rhe.get("n"):
        exact = rhe.get("exact") or {}
        binary = rhe.get("binary") or {}
        lines += ["## 修辞", "", f"- 有修辞金标条数：**{rhe['n']}**"]
        if exact.get("accuracy") is not None:
            lines.append(
                f"- 精确一致 Accuracy：**{exact['accuracy'] * 100:.1f}%**；"
                f"Macro-F1：**{exact['macro_f1']:.4f}**"
            )
        else:
            lines.append("- 精确一致：—")
        if binary.get("accuracy") is not None:
            lines.append(
                f"- 二分类（有/无修辞）Accuracy：**{binary['accuracy'] * 100:.1f}%**；"
                f"Macro-F1：**{binary['macro_f1']:.4f}**"
            )
        else:
            lines.append("- 二分类：—")
        lines.append("")
        if exact.get("confusion"):
            labels = list(exact["confusion"].keys())
            lines += ["### 修辞精确混淆", "", md_confusion(exact["confusion"], labels), ""]
    else:
        lines += [
            "## 修辞",
            "",
            "- 无可用修辞金标（正式评估需在盲标表填写 `gold_rhetoric`）。",
            "",
        ]

    topic = payload.get("topic")
    if topic and topic.get("n"):
        lines += [
            "## 主题（可选）",
            "",
            f"- n={topic['n']}；Acc={(topic.get('accuracy') or 0)*100:.1f}%；"
            f"Macro-F1={topic.get('macro_f1'):.4f}",
            "",
        ]

    lines += [
        "## 错例摘要",
        "",
        f"- 情绪错例导出：**{payload.get('n_errors', 0)}** 条 → `outputs/{payload.get('error_cases_file')}`",
        "",
        "## 金标填完后如何重跑（正式评估）",
        "",
    ]
    lines += [f"{i}. {s}" for i, s in enumerate(instructions, 1)]
    lines += [
        "",
        "## 边界",
        "",
        "- 修辞 QC 过采样 → 修辞指标不可直接外推全库。",
        "- 星级基线在星文冲突样本上会系统性偏乐观/偏悲观；LLM 价值正在于此。",
        "- PROXY 模式下 Acc/F1 衡量的是「相对星级映射的一致度」，不是人工正确率。",
        "",
        "---",
        "",
        "*由 `10分析实验室/model_eval.py` 自动生成*",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def post_qc_instructions() -> list[str]:
    return [
        "打开 `03标注结果/qc/qc_blind_to_label.xlsx`「盲标」页，用下拉填 `gold_sentiment` / `gold_rhetoric`（勿看 `qc_with_model.csv`）。",
        "可选：同步备份到 `qc_blind_to_label.csv`；也可只维护 xlsx（本脚本优先读 xlsx）。",
        "运行：`python 10分析实验室/model_eval.py` → 自动切到 `human_gold` 模式，写出 Acc / Macro-F1 / 混淆矩阵 / 错例。",
        "亦可跑 `python 03标注结果/qc/score_qc.py` 生成质检一致率报告（与本评估互补）。",
        "勿清空或覆盖已填盲标表；重新抽样前请先备份。",
    ]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="P0-2 model evaluation")
    ap.add_argument("--force-proxy", action="store_true", help="强制 proxy 模式")
    ap.add_argument("--proxy-n", type=int, default=200, help="proxy held-out 条数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-gold", type=int, default=1, help="至少多少条金标才走正式评估")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    gold_df, blind_path, gold_note = load_human_gold()
    n_gold = len(gold_df)
    use_proxy = args.force_proxy or n_gold < args.min_gold

    if use_proxy:
        eval_mode = "proxy_star_weak_gold"
        slice_df = build_proxy_slice(args.proxy_n, args.seed)
        # 合并模型预测（slice 已含 pred）
        df = slice_df.copy()
        if "pred_sentiment" not in df.columns or df["pred_sentiment"].isna().all():
            preds = load_model_preds(df["review_id"].tolist())
            df = df.drop(
                columns=[c for c in ["pred_sentiment", "pred_rhetoric", "star_sentiment", "topic_primary", "text", "score_raw"] if c in df.columns],
                errors="ignore",
            ).merge(preds, on="review_id", how="left")
            df["star_sentiment"] = df["score_raw"].map(star_to_sentiment)
            df["gold_sentiment"] = df["star_sentiment"]
        gold_note = (
            f"PROXY：人工金标 n={n_gold}；以星级映射为弱金标，held-out n={len(df)} "
            f"(seed={args.seed})。{gold_note}"
        )
        model_source = "annotations_v1_4.csv (+ qc_with_model 若有)"
    else:
        eval_mode = "human_gold"
        preds = load_model_preds(gold_df["review_id"].tolist())
        df = gold_df.merge(preds, on="review_id", how="inner", suffixes=("", "_pred"))
        # 文本/星级：优先盲标表
        if "score_raw" not in df.columns or df["score_raw"].isna().all():
            df["score_raw"] = df.get("score_raw_pred")
        df["star_sentiment"] = df["score_raw"].map(star_to_sentiment)
        df["gold_source"] = "human_qc"
        if "gold_note" not in df.columns:
            df["gold_note"] = ""
        df = attach_text(df)
        model_source = "qc_with_model.csv / annotations_v1_4.csv"
        gold_note = f"{gold_note}；对齐后 n={len(df)}"

    # 指标
    llm_sent = eval_sentiment(df, "pred_sentiment")
    star_sent = eval_sentiment(df, "star_sentiment")
    # 正式模式下星级基线 vs 真金标；proxy 下星级 vs 自身 → Acc=1，仍写入对照表说明
    rhe = eval_rhetoric(df)
    topic = eval_topic(df)

    err_name = "error_cases.csv"
    n_errors = export_errors(df, eval_mode, OUT_DIR / err_name)

    payload: dict[str, Any] = {
        "generated_at": now_iso(),
        "eval_mode": eval_mode,
        "n_gold": int(n_gold),
        "n_eval": int(llm_sent.get("n") or 0),
        "gold_note": gold_note,
        "gold_path": (
            str(blind_path.resolve().relative_to(ROOT)).replace("\\", "/")
            if blind_path
            else None
        ),
        "model_source": model_source,
        "is_proxy": use_proxy,
        "sentiment": {
            "llm": llm_sent,
            "star_baseline": star_sent,
            "labels": SENT_ORDER,
        },
        "rhetoric": rhe,
        "topic": topic,
        "comparison_table": comparison_table(llm_sent, star_sent),
        "n_errors": n_errors,
        "error_cases_file": err_name,
        "post_qc_instructions": post_qc_instructions(),
        "star_map_rule": "4-5→正, 3→中, 1-2→负",
    }

    json_path = OUT_DIR / "model_eval.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = REPORT_DIR / f"model_eval_{now_ts()}.md"
    write_report(payload, report_path, post_qc_instructions())
    # 稳定别名，方便 README 链接
    latest = REPORT_DIR / "model_eval_latest.md"
    latest.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"eval_mode={eval_mode}")
    print(f"n_gold={n_gold} n_eval={payload['n_eval']} n_errors={n_errors}")
    acc = llm_sent.get("accuracy")
    f1 = llm_sent.get("macro_f1")
    if acc is not None:
        print(f"LLM sentiment Acc={acc * 100:.1f}% Macro-F1={f1:.4f}")
    sacc = star_sent.get("accuracy")
    sf1 = star_sent.get("macro_f1")
    if sacc is not None:
        print(f"star baseline Acc={sacc * 100:.1f}% Macro-F1={sf1:.4f}")
    print(json_path)
    print(report_path)
    print(OUT_DIR / err_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
