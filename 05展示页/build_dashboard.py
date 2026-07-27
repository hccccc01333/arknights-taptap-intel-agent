#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 annotations_v1_4 生成展示页数据，并写出可离线打开的 index.html。"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "02数据"
ANN = ROOT / "03标注结果"
OUT = ROOT / "05展示页"
LAB_OUT = ROOT / "10分析实验室" / "outputs"

TOPIC_CN = {
    "gacha": "抽卡/商业化",
    "balance": "数值/平衡",
    "gameplay": "关卡/玩法",
    "story": "剧情",
    "event": "活动/版本",
    "client": "客户端/性能",
    "ops": "运营/客服",
    "other": "综合/其他",
}

ROLE_BY_TOPIC = {
    "gacha": "运营",
    "balance": "数值策划",
    "gameplay": "产品/战斗",
    "story": "剧情",
    "event": "运营",
    "client": "客户端",
    "ops": "客服/运营",
    "other": "产品",
}

CHURN_RE = re.compile(r"退游|卸载|再也不玩|删了|拜拜|闪退|登不上")
CRASH_RE = re.compile(r"闪退|登不上|无法登录|打不开|崩溃")


def clip(s: object, n: int = 72) -> str:
    t = str(s or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def fmt_day(d) -> str:
    return f"{d.month}/{d.day}"


def load(ann_path: Path) -> pd.DataFrame:
    clean = pd.read_csv(DATA / "processed" / "reviews_clean.csv", dtype={"review_id": str})
    ann = pd.read_csv(ann_path, dtype={"review_id": str})
    ann = ann[ann["error"].fillna("") == ""].drop_duplicates("review_id", keep="last")
    ann = ann[~ann["review_id"].astype(str).str.startswith("probe_")]
    m = ann.merge(
        clean[["review_id", "text", "publish_time_cn"]],
        on="review_id",
        how="left",
    )
    m["publish_dt"] = pd.to_datetime(m["publish_time_cn"], errors="coerce")
    m["day"] = m["publish_dt"].dt.tz_convert(TZ).dt.date
    m["score_raw"] = m["score_raw"].astype(str)
    m["rhetoric"] = m["rhetoric"].fillna("none").replace("", "none")
    m["topic_primary"] = m["topic_primary"].fillna("other").replace("", "other")
    m["sentiment"] = m["sentiment"].fillna("中")
    m["actionable"] = m["actionable"].fillna("否")
    m["text"] = m["text"].fillna("")
    if "confidence" not in m.columns:
        m["confidence"] = 0.5
    m["confidence"] = pd.to_numeric(m["confidence"], errors="coerce").fillna(0.5)
    return m.dropna(subset=["day"]).copy()


def sent_block(df: pd.DataFrame) -> dict:
    c = Counter(df["sentiment"])
    n = len(df)
    return {
        "n": n,
        "正": int(c.get("正", 0)),
        "中": int(c.get("中", 0)),
        "负": int(c.get("负", 0)),
        "neg_rate": round(c.get("负", 0) / n, 4) if n else 0,
    }


def _load_channel_ann(ann_path: Path) -> pd.DataFrame | None:
    if not ann_path.exists():
        return None
    try:
        ann = pd.read_csv(ann_path, dtype={"review_id": str})
    except Exception:  # noqa: BLE001
        return None
    if "error" in ann.columns:
        ann = ann[ann["error"].fillna("") == ""]
    ann = ann.drop_duplicates("review_id", keep="last")
    ann = ann[ann["sentiment"].fillna("").astype(str).str.len() > 0]
    if "review_id" in ann.columns:
        ann = ann[~ann["review_id"].astype(str).str.startswith("probe_")]
    return ann if not ann.empty else None


def _channel_card(
    *,
    key: str,
    label: str,
    role: str,
    ann_path: Path,
    comments_path: Path | None,
    report_glob: str,
    report_dir: Path,
    note: str,
) -> dict | None:
    ann = _load_channel_ann(ann_path)
    if ann is None:
        return None
    sent = sent_block(ann)
    topics = (
        ann["topic_primary"].fillna("other").replace("", "other").value_counts().head(3)
    )
    top_topics = [
        {
            "topic": TOPIC_CN.get(str(k), str(k)),
            "n": int(v),
            "share": round(float(v) / len(ann), 4),
        }
        for k, v in topics.items()
    ]
    n_comments = int(len(ann))
    degraded = False
    degraded_pct = 0.0
    if comments_path and comments_path.exists():
        try:
            com = pd.read_csv(comments_path)
            n_comments = int(len(com))
            if "source" in com.columns:
                src = com["source"].fillna("").astype(str)
                deg_n = int(
                    src.str.contains("fallback_seed|degraded_sample", regex=True).sum()
                )
                degraded_pct = round(100.0 * deg_n / max(n_comments, 1), 1)
                degraded = deg_n / max(n_comments, 1) >= 0.5
        except Exception:  # noqa: BLE001
            pass
    report_path = ""
    if report_dir.exists():
        reports = sorted(report_dir.glob(report_glob), reverse=True)
        if reports:
            report_path = str(reports[0].relative_to(ROOT)).replace("\\", "/")
    mode = "llm"
    if "model" in ann.columns and ann["model"].astype(str).str.contains("weak").any():
        mode = "weak_rules"
    return {
        "key": key,
        "label": label,
        "role": role,
        "n_comments": n_comments,
        "n_annotated": int(len(ann)),
        "sent": sent,
        "top_topics": top_topics,
        "annotate_mode": mode,
        "report_path": report_path,
        "degraded": degraded,
        "degraded_pct": degraded_pct,
        "note": note,
    }


def channel_matrix_block() -> dict:
    """四渠对照矩阵：TapTap 主链 + B站/抖音/微博对照。"""
    cards = []
    specs = [
        {
            "key": "taptap",
            "label": "TapTap",
            "role": "评价主链",
            "ann": ROOT / "03标注结果" / "annotations_v1_4.csv",
            "comments": ROOT / "02数据" / "processed" / "reviews_clean.csv",
            "report_dir": ROOT / "04日报周报" / "reports",
            "report_glob": "weekly_*.md",
            "note": "商店口碑主 KPI",
        },
        {
            "key": "bilibili",
            "label": "B站",
            "role": "传播第一渠",
            "ann": ROOT / "06对照_B站" / "annotations_bili_sample.csv",
            "comments": ROOT / "06对照_B站" / "comments_sample.csv",
            "report_dir": ROOT / "06对照_B站" / "reports",
            "report_glob": "contrast_taptap_bili_*.md",
            "note": "视频评论对照",
        },
        {
            "key": "douyin",
            "label": "抖音",
            "role": "短视频对照",
            "ann": ROOT / "07对照_抖音" / "annotations_douyin_sample.csv",
            "comments": ROOT / "07对照_抖音" / "comments_sample.csv",
            "report_dir": ROOT / "07对照_抖音" / "reports",
            "report_glob": "annotate_douyin_*.md",
            "note": "短视频评论切片",
        },
        {
            "key": "weibo",
            "label": "微博",
            "role": "热议对照",
            "ann": ROOT / "08对照_微博" / "annotations_weibo_sample.csv",
            "comments": ROOT / "08对照_微博" / "comments_sample.csv",
            "report_dir": ROOT / "08对照_微博" / "reports",
            "report_glob": "annotate_weibo_*.md",
            "note": "公开热议切片",
        },
    ]
    for s in specs:
        card = _channel_card(
            key=s["key"],
            label=s["label"],
            role=s["role"],
            ann_path=s["ann"],
            comments_path=s["comments"],
            report_glob=s["report_glob"],
            report_dir=s["report_dir"],
            note=s["note"],
        )
        if card:
            cards.append(card)

    ai_brief = None
    summary_path = ROOT / "09跨渠道AI" / "latest_summary.json"
    facts_path = ROOT / "09跨渠道AI" / "facts_cross_channel.json"
    if summary_path.exists():
        try:
            ai_brief = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            ai_brief = None
    facts_rel = ""
    if facts_path.exists():
        facts_rel = str(facts_path.relative_to(ROOT)).replace("\\", "/")

    return {
        "enabled": len(cards) > 0,
        "channels": cards,
        "ai_brief": ai_brief,
        "facts_path": facts_rel,
        "note": "四渠结构对照，不作全网 KPI；降级样本已在卡片标明。",
    }


def bili_from_matrix(matrix: dict) -> dict | None:
    """兼容旧字段：从四渠矩阵取出 B 站卡。"""
    for c in matrix.get("channels") or []:
        if c.get("key") == "bilibili":
            return {
                "enabled": True,
                "n_comments": c["n_comments"],
                "n_annotated": c["n_annotated"],
                "sent": c["sent"],
                "top_topics": c["top_topics"],
                "annotate_mode": c["annotate_mode"],
                "report_path": c["report_path"],
                "note": "轻量对照，不作跨渠道 KPI；主链仍为 TapTap。",
            }
    return None


def topic_quotes(df: pd.DataFrame, topic: str, limit: int = 3) -> list[dict]:
    neg = df[(df["topic_primary"] == topic) & (df["sentiment"] == "负")].copy()
    if neg.empty:
        return []
    neg["_p"] = (
        neg["text"].map(lambda t: 1 if CHURN_RE.search(str(t)) else 0)
        + (neg["rhetoric"] != "none").astype(int)
        + (neg["actionable"] == "是").astype(int)
        + (neg["score_raw"].isin(["4", "5"])).astype(int)
    )
    neg = neg.sort_values(["_p", "confidence"], ascending=[False, False])
    out = []
    for _, r in neg.head(limit).iterrows():
        out.append(
            {
                "review_id": str(r.review_id),
                "quote": clip(r.text, 90),
                "score": str(r.score_raw),
                "rhetoric": str(r.rhetoric),
            }
        )
    return out


def topic_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for k, v in Counter(df["topic_primary"]).most_common():
        neg = int(((df["topic_primary"] == k) & (df["sentiment"] == "负")).sum())
        rows.append(
            {
                "topic": k,
                "label": TOPIC_CN.get(k, k),
                "n": int(v),
                "neg": neg,
                "neg_rate": round(neg / v, 4) if v else 0,
                "quotes": topic_quotes(df, k, 3),
            }
        )
    return rows


def daily_series(df: pd.DataFrame, end, days: int = 30) -> list[dict]:
    start = end - timedelta(days=days - 1)
    sub = df[(df["day"] >= start) & (df["day"] <= end)]
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        day_df = sub[sub["day"] == d]
        n = len(day_df)
        neg = int((day_df["sentiment"] == "负").sum()) if n else 0
        out.append(
            {
                "day": d.isoformat(),
                "n": n,
                "负": neg,
                "正": int((day_df["sentiment"] == "正").sum()) if n else 0,
                "中": int((day_df["sentiment"] == "中").sum()) if n else 0,
                "neg_rate": round(neg / n, 4) if n else None,
            }
        )
    return out


def pick_signals(df: pd.DataFrame, limit: int = 4) -> list[dict]:
    neg = df[df["sentiment"] == "负"]
    if neg.empty:
        return []
    signals = []
    for topic, cnt in Counter(neg["topic_primary"]).most_common(limit):
        sub = neg[neg["topic_primary"] == topic].copy()
        sub["_p"] = (
            sub["text"].map(lambda t: 1 if CHURN_RE.search(str(t)) else 0)
            + (sub["rhetoric"] != "none").astype(int)
            + (sub["actionable"] == "是").astype(int)
        )
        sub = sub.sort_values(["_p", "confidence"], ascending=[False, False])
        r = sub.iloc[0]
        signals.append(
            {
                "topic": topic,
                "label": TOPIC_CN.get(topic, topic),
                "neg": int(cnt),
                "review_id": str(r.review_id),
                "quote": clip(r.text, 80),
                "score": str(r.score_raw),
                "rhetoric": str(r.rhetoric),
            }
        )
    return signals


def pick_hi_neg(df: pd.DataFrame, limit: int = 8) -> list[dict]:
    sub = df[(df["score_raw"].isin(["4", "5"])) & (df["sentiment"] == "负")].copy()
    if sub.empty:
        return []
    sub["_p"] = (
        (sub["actionable"] == "是").astype(int) * 2
        + (sub["rhetoric"] != "none").astype(int)
        + sub["text"].map(lambda t: 1 if CHURN_RE.search(str(t)) else 0)
    )
    sub = sub.sort_values(["_p", "confidence"], ascending=[False, False])
    out = []
    for _, r in sub.head(limit).iterrows():
        out.append(
            {
                "review_id": str(r.review_id),
                "topic": str(r.topic_primary),
                "label": TOPIC_CN.get(str(r.topic_primary), str(r.topic_primary)),
                "score": str(r.score_raw),
                "rhetoric": str(r.rhetoric),
                "actionable": str(r.actionable),
                "quote": clip(r.text, 100),
            }
        )
    return out


def pick_actionable(df: pd.DataFrame, limit: int = 8) -> list[dict]:
    sub = df[df["actionable"] == "是"].copy()
    if sub.empty:
        return []
    order = {"负": 0, "中": 1, "正": 2}
    sub["_o"] = sub["sentiment"].map(lambda x: order.get(x, 9))
    sub["_p"] = (
        (sub["sentiment"] == "负").astype(int) * 3
        + (sub["score_raw"].isin(["4", "5"])).astype(int)
        + (sub["rhetoric"] != "none").astype(int)
    )
    sub = sub.sort_values(["_o", "_p", "confidence"], ascending=[True, False, False])
    out = []
    for _, r in sub.head(limit).iterrows():
        topic = str(r.topic_primary)
        role = ROLE_BY_TOPIC.get(topic, "产品")
        cn = TOPIC_CN.get(topic, topic)
        out.append(
            {
                "review_id": str(r.review_id),
                "topic": topic,
                "label": cn,
                "sentiment": str(r.sentiment),
                "score": str(r.score_raw),
                "rhetoric": str(r.rhetoric),
                "quote": clip(r.text, 100),
                "suggest": f"建议{role}针对{cn}相关诉求跟进核查",
            }
        )
    return out


FISSURE_RHET = ("sarcasm", "gaoji_hei", "fanchuan")
PRAISE_RHET = ("template_praise",)


def _rhe_samples(sub: pd.DataFrame, limit: int = 6) -> list[dict]:
    out = []
    for _, r in sub.head(limit).iterrows():
        out.append(
            {
                "review_id": str(r.review_id),
                "rhetoric": str(r.rhetoric),
                "sentiment": str(r.sentiment),
                "quote": clip(r.text, 70),
            }
        )
    return out


def rhetoric_block(df: pd.DataFrame) -> dict:
    """全库修辞：裂隙队列（讽刺/高级黑/反串）与模板好评分桶。"""
    n_all = len(df)
    rhe = df[df["rhetoric"] != "none"]
    by = Counter(rhe["rhetoric"]).most_common()
    fissure = rhe[rhe["rhetoric"].isin(FISSURE_RHET)]
    praise = rhe[rhe["rhetoric"].isin(PRAISE_RHET)]
    fissure_by = Counter(fissure["rhetoric"]).most_common()
    return {
        "n": int(len(rhe)),
        "rate": round(len(rhe) / n_all, 4) if n_all else 0,
        "by_type": [{"rhetoric": k, "n": int(v)} for k, v in by],
        "samples": _rhe_samples(rhe, 6),
        "fissure": {
            "n": int(len(fissure)),
            "rate": round(len(fissure) / n_all, 4) if n_all else 0,
            "by_type": [{"rhetoric": k, "n": int(v)} for k, v in fissure_by],
            "samples": _rhe_samples(fissure, 6),
        },
        "template_praise": {
            "n": int(len(praise)),
            "rate": round(len(praise) / n_all, 4) if n_all else 0,
            "samples": _rhe_samples(praise, 6),
        },
    }


def risk_level(df: pd.DataFrame) -> tuple[str, str]:
    n = len(df)
    if not n:
        return "一般关注", "无样本"
    neg = df[df["sentiment"] == "负"]
    neg_n = len(neg)
    client_neg = int((neg["topic_primary"] == "client").sum())
    crash = int(df["text"].map(lambda t: bool(CRASH_RE.search(str(t)))).sum())
    churn = int(df["text"].map(lambda t: bool(CHURN_RE.search(str(t)))).sum())
    top = Counter(neg["topic_primary"]).most_common(1)
    cluster = top[0][1] if top else 0
    hi = int(((df["score_raw"].isin(["4", "5"])) & (df["sentiment"] == "负")).sum())
    act_neg = int(((df["actionable"] == "是") & (df["sentiment"] == "负")).sum())

    if client_neg >= 3 and client_neg / max(neg_n, 1) >= 0.25:
        return "紧急预警", "客户端负向扎堆"
    if crash >= 3 and client_neg >= 2:
        return "紧急预警", "闪退/登录类反馈扎堆"
    if cluster >= 5 and cluster / max(neg_n, 1) >= 0.35:
        return "重点关注", "单簇负向突出"
    if hi >= 1 and act_neg >= 1:
        return "重点关注", "高星负向+可行动并存"
    if churn >= 3 or (neg_n / n >= 0.4 and neg_n >= 10):
        return "重点关注", "负向密度偏高"
    return "一般关注", "结构相对平稳"


def decide_line(df: pd.DataFrame, actions: list[dict]) -> str:
    if actions:
        a0 = actions[0]
        extra = f"；其次跟进{len(actions) - 1}项可行动线索" if len(actions) > 1 else ""
        return f"{a0['suggest']}{extra}"
    neg_tops = Counter(df[df["sentiment"] == "负"]["topic_primary"]).most_common(1)
    if neg_tops:
        cn = TOPIC_CN.get(neg_tops[0][0], neg_tops[0][0])
        return f"本期无可行动=是的强聚类，维持对「{cn}」主题监测即可"
    return "本期无可行动=是的聚类项，维持主题监测即可"


def _review_row(
    r,
    *,
    channel: str,
    degraded: bool = False,
    text_fallback: str = "",
) -> dict:
    text = str(getattr(r, "text", "") or text_fallback or "")
    day = getattr(r, "day", None)
    if hasattr(day, "isoformat"):
        day_s = day.isoformat()
    else:
        day_s = str(day or "")[:10]
    conf = getattr(r, "confidence", 0.5)
    try:
        conf_f = float(conf)
    except (TypeError, ValueError):
        conf_f = 0.5
    return {
        "id": str(getattr(r, "review_id", "")),
        "date": day_s,
        "channel": channel,
        "score": str(getattr(r, "score_raw", "") or ""),
        "sentiment": str(getattr(r, "sentiment", "") or "中"),
        "topic": str(getattr(r, "topic_primary", "") or "other"),
        "rhetoric": str(getattr(r, "rhetoric", "") or "none"),
        "actionable": str(getattr(r, "actionable", "") or "否"),
        "confidence": round(conf_f, 3),
        "text": clip(text, 160),
        "degraded": bool(degraded),
    }


def _channel_reviews_sample(
    *,
    key: str,
    ann_path: Path,
    comments_path: Path,
    id_col: str = "comment_id",
    cap: int = 200,
) -> list[dict]:
    ann = _load_channel_ann(ann_path)
    if ann is None or not comments_path.exists():
        return []
    try:
        com = pd.read_csv(comments_path, dtype={id_col: str})
    except Exception:  # noqa: BLE001
        return []
    if id_col not in com.columns:
        return []
    com = com.drop_duplicates(id_col, keep="last")
    join_cols = [id_col, "text", "publish_time_cn"]
    if "source" in com.columns:
        join_cols.append("source")
    m = ann.merge(
        com[[c for c in join_cols if c in com.columns]],
        left_on="review_id",
        right_on=id_col,
        how="left",
        suffixes=("", "_c"),
    )
    if "text" not in m.columns:
        m["text"] = ""
    m["text"] = m["text"].fillna("")
    m["publish_dt"] = pd.to_datetime(m["publish_time_cn"], errors="coerce")
    m["day"] = m["publish_dt"].dt.tz_convert(TZ).dt.date
    if m["day"].isna().all():
        m["day"] = pd.Timestamp.now(tz=TZ).date()
    else:
        m["day"] = m["day"].fillna(m["day"].dropna().max())
    m["rhetoric"] = m["rhetoric"].fillna("none").replace("", "none")
    m["topic_primary"] = m["topic_primary"].fillna("other").replace("", "other")
    m["sentiment"] = m["sentiment"].fillna("中")
    m["actionable"] = m["actionable"].fillna("否")
    if "score_raw" not in m.columns:
        m["score_raw"] = ""
    m["score_raw"] = m["score_raw"].fillna("").astype(str)
    if "confidence" not in m.columns:
        m["confidence"] = 0.5
    m["confidence"] = pd.to_numeric(m["confidence"], errors="coerce").fillna(0.5)
    if "source" in m.columns:
        src = m["source"].fillna("").astype(str)
        m["_deg"] = src.str.contains("fallback_seed|degraded_sample", regex=True)
    else:
        m["_deg"] = False
    m["_p"] = (
        (m["actionable"] == "是").astype(int) * 3
        + (m["sentiment"] == "负").astype(int) * 2
        + (m["rhetoric"] != "none").astype(int)
    )
    m = m.sort_values(["_p", "confidence"], ascending=[False, False]).head(cap)
    out = []
    for _, r in m.iterrows():
        out.append(_review_row(r, channel=key, degraded=bool(r["_deg"])))
    return out


def build_reviews_sample(
    df: pd.DataFrame,
    *,
    week_start,
    latest,
    roll_days: list,
    cap: int = 1200,
) -> list[dict]:
    """Explorer 样本：本周 + 滚动 + 分层负向/可行动/修辞，并入对照渠，上限约 800–1500。"""
    picked: dict[str, dict] = {}

    def add_rows(sub: pd.DataFrame, channel: str = "taptap") -> None:
        for _, r in sub.iterrows():
            rid = str(r.review_id)
            if rid in picked:
                continue
            picked[rid] = _review_row(r, channel=channel, degraded=False)

    week = df[(df["day"] >= week_start) & (df["day"] <= latest)]
    roll = df[df["day"].isin(roll_days)]
    add_rows(week)
    add_rows(roll)

    buckets = [
        df[df["sentiment"] == "负"],
        df[df["actionable"] == "是"],
        df[df["rhetoric"].isin(FISSURE_RHET)],
        df[df["rhetoric"].isin(PRAISE_RHET)],
        df[(df["score_raw"].isin(["4", "5"])) & (df["sentiment"] == "负")],
    ]
    per_bucket = max(80, (cap - len(picked)) // 8)
    for sub in buckets:
        if sub.empty:
            continue
        add_rows(sub.sample(n=min(per_bucket, len(sub)), random_state=42))

    remain = max(0, min(cap, 1000) - len(picked))
    if remain > 0:
        rest = df[~df["review_id"].astype(str).isin(picked)].sort_values(
            "day", ascending=False
        )
        add_rows(rest.head(remain))

    rows = list(picked.values())

    channel_specs = [
        (
            "bilibili",
            ROOT / "06对照_B站" / "annotations_bili_sample.csv",
            ROOT / "06对照_B站" / "comments_sample.csv",
            "comment_id",
            180,
        ),
        (
            "douyin",
            ROOT / "07对照_抖音" / "annotations_douyin_sample.csv",
            ROOT / "07对照_抖音" / "comments_sample.csv",
            "comment_id",
            150,
        ),
        (
            "weibo",
            ROOT / "08对照_微博" / "annotations_weibo_sample.csv",
            ROOT / "08对照_微博" / "comments_sample.csv",
            "comment_id",
            150,
        ),
    ]
    for key, ann_p, com_p, id_col, ch_cap in channel_specs:
        for row in _channel_reviews_sample(
            key=key, ann_path=ann_p, comments_path=com_p, id_col=id_col, cap=ch_cap
        ):
            rows.append({**row, "id": f"{key}:{row['id']}"})

    if len(rows) > 1500:
        tap = [r for r in rows if r["channel"] == "taptap"]
        other = [r for r in rows if r["channel"] != "taptap"]
        rows = tap[:1200] + other[:300]

    rows.sort(key=lambda r: (r.get("date") or "", r.get("id") or ""), reverse=True)
    return rows


def risk_breakdown(df: pd.DataFrame, baseline: dict | None = None) -> dict:
    """可解释风险分：0–100，附贡献拆解（相对基线）。"""
    n = len(df)
    base_neg = float((baseline or {}).get("neg_rate") or 0.25)
    if not n:
        return {
            "score": 0,
            "level": "一般关注",
            "why": "无样本",
            "formula": "risk = clamp(Σ weighted contributions, 0, 100)",
            "contributions": [],
        }
    neg = df[df["sentiment"] == "负"]
    neg_n = len(neg)
    neg_rate = neg_n / n
    client_neg = int((neg["topic_primary"] == "client").sum())
    crash = int(df["text"].map(lambda t: bool(CRASH_RE.search(str(t)))).sum())
    churn = int(df["text"].map(lambda t: bool(CHURN_RE.search(str(t)))).sum())
    top = Counter(neg["topic_primary"]).most_common(1)
    cluster = top[0][1] if top else 0
    cluster_share = cluster / max(neg_n, 1)
    hi = int(((df["score_raw"].isin(["4", "5"])) & (df["sentiment"] == "负")).sum())
    act_neg = int(((df["actionable"] == "是") & (df["sentiment"] == "负")).sum())
    fissure_n = int(df["rhetoric"].isin(FISSURE_RHET).sum())

    contribs = []
    neg_delta = max(0.0, neg_rate - base_neg)
    c_neg = round(min(30.0, neg_delta / max(base_neg, 0.05) * 18 + neg_rate * 20), 1)
    contribs.append(
        {
            "key": "neg_rate",
            "label": "负向率 vs 基线",
            "value": round(neg_rate, 4),
            "baseline": round(base_neg, 4),
            "points": c_neg,
            "weight": 0.30,
        }
    )
    c_cluster = round(min(22.0, cluster_share * 35 + (5 if cluster >= 5 else 0)), 1)
    contribs.append(
        {
            "key": "cluster",
            "label": "单簇负向集中度",
            "value": round(cluster_share, 4),
            "baseline": 0.25,
            "points": c_cluster,
            "weight": 0.22,
        }
    )
    c_client = round(
        min(18.0, (client_neg / max(neg_n, 1)) * 40 + (8 if client_neg >= 3 else 0)),
        1,
    )
    contribs.append(
        {
            "key": "client",
            "label": "客户端负向份额",
            "value": client_neg,
            "baseline": 0,
            "points": c_client,
            "weight": 0.18,
        }
    )
    c_crash = round(min(12.0, crash * 3.5), 1)
    contribs.append(
        {
            "key": "crash",
            "label": "闪退/登录词命中",
            "value": crash,
            "baseline": 0,
            "points": c_crash,
            "weight": 0.12,
        }
    )
    c_churn = round(min(10.0, churn * 2.5), 1)
    contribs.append(
        {
            "key": "churn",
            "label": "退游/卸载词命中",
            "value": churn,
            "baseline": 0,
            "points": c_churn,
            "weight": 0.10,
        }
    )
    c_hi = round(min(8.0, hi * 2.0 + (3 if hi and act_neg else 0)), 1)
    contribs.append(
        {
            "key": "hi_neg",
            "label": "高星负向 + 可行动",
            "value": hi,
            "baseline": act_neg,
            "points": c_hi,
            "weight": 0.08,
        }
    )
    c_fis = round(min(6.0, fissure_n * 0.8), 1)
    contribs.append(
        {
            "key": "fissure",
            "label": "裂隙修辞密度",
            "value": fissure_n,
            "baseline": 0,
            "points": c_fis,
            "weight": 0.06,
        }
    )

    score = round(min(100.0, sum(c["points"] for c in contribs)), 1)
    level, why = risk_level(df)
    return {
        "score": score,
        "level": level,
        "why": why,
        "formula": (
            "risk_score = clamp(负向率偏离 + 簇集中 + 客户端负向 + 闪退词 + 退游词 "
            "+ 高星负/可行动 + 裂隙修辞, 0, 100)；等级规则与日周报一致"
        ),
        "contributions": contribs,
        "baseline_neg_rate": round(base_neg, 4),
    }


def quality_breakdown(matrix: dict, meta: dict) -> dict:
    """质量分：完整度 / 标注覆盖 / 新鲜度 / 代表性 / 模型桩。"""
    channels = matrix.get("channels") or []
    dims = []
    pool_n = int(meta.get("pool_n") or 0)
    completeness = 100.0 if pool_n >= 500 else round(pool_n / 5.0, 1)
    dims.append(
        {
            "key": "completeness",
            "label": "主链完整度",
            "score": completeness,
            "weight": 0.25,
            "detail": f"TapTap 标注池 n={pool_n}",
        }
    )
    cov_scores = []
    for c in channels:
        if c.get("key") == "taptap":
            cov_scores.append(100.0)
            continue
        n_c = max(1, int(c.get("n_comments") or 0))
        cov = 100.0 * int(c.get("n_annotated") or 0) / n_c
        if c.get("degraded"):
            cov = max(0.0, 100.0 - float(c.get("degraded_pct") or 50))
        cov_scores.append(cov)
    annotate = round(sum(cov_scores) / max(len(cov_scores), 1), 1)
    dims.append(
        {
            "key": "annotate",
            "label": "标注覆盖",
            "score": annotate,
            "weight": 0.25,
            "detail": "主链 + 对照渠标注/采集比",
        }
    )
    freshness = 92.0
    dims.append(
        {
            "key": "freshness",
            "label": "新鲜度",
            "score": freshness,
            "weight": 0.15,
            "detail": f"latest_day={meta.get('latest_day')}",
        }
    )
    deg = [c for c in channels if c.get("degraded")]
    deg_pct = (
        sum(float(c.get("degraded_pct") or 0) for c in deg) / max(len(channels), 1)
        if channels
        else 0
    )
    repre = round(max(0.0, 100.0 - deg_pct * 1.2 - len(deg) * 12), 1)
    dims.append(
        {
            "key": "representativeness",
            "label": "代表性 / 降级",
            "score": repre,
            "weight": 0.25,
            "detail": f"降级渠道 {len(deg)} 个，加权降级率≈{deg_pct:.1f}%",
        }
    )
    modes = {c.get("annotate_mode") for c in channels}
    model_score = 88.0 if modes <= {"llm"} or not modes else 70.0
    if any(c.get("annotate_mode") == "weak_rules" for c in channels):
        model_score = 62.0
    dims.append(
        {
            "key": "model",
            "label": "模型桩可用性",
            "score": model_score,
            "weight": 0.10,
            "detail": "对照渠 annotate_mode；弱规则降权",
        }
    )
    total = round(sum(d["score"] * d["weight"] for d in dims), 1)
    return {
        "score": total,
        "formula": "quality = 0.25·完整度 + 0.25·标注覆盖 + 0.15·新鲜度 + 0.25·代表性 + 0.10·模型桩",
        "dimensions": dims,
    }


def _lab_stub(kind: str) -> dict:
    """Empty lab payload when sibling JSON is missing / unreadable."""
    notes = {
        "anomaly": "尚未生成异动诊断。运行 python 10分析实验室/anomaly_diagnosis.py 后重建看板。",
        "model_eval": "尚未生成模型评估。运行 python 10分析实验室/model_eval.py 后重建看板。",
        "event_impact": "尚未生成事件影响。运行 python 10分析实验室/event_impact.py 后重建看板。",
    }
    if kind == "anomaly":
        return {
            "available": False,
            "comparisons": [],
            "waterfall": [],
            "note": notes[kind],
        }
    if kind == "model_eval":
        return {
            "available": False,
            "mode": None,
            "n_gold": 0,
            "metrics": {},
            "comparison": [],
            "confusion": None,
            "note": notes[kind],
        }
    return {
        "available": False,
        "events": [],
        "note": notes[kind],
    }


def _lab_has_content(kind: str, data: dict) -> bool:
    if kind == "anomaly":
        return bool(
            data.get("comparisons")
            or data.get("table")
            or data.get("waterfall")
            or data.get("contributions")
            or data.get("periods")
        )
    if kind == "model_eval":
        return bool(
            data.get("metrics")
            or data.get("sentiment")
            or data.get("confusion")
            or data.get("confusion_matrix")
            or data.get("comparison")
            or data.get("comparison_table")
            or data.get("baseline_comparison")
        )
    return bool(data.get("events") or data.get("results") or data.get("event_results"))


def _load_lab_json(path: Path, kind: str) -> dict:
    stub = _lab_stub(kind)
    if not path.exists():
        return stub
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {**stub, "note": f"读取失败（{path.name}）：{exc}"}
    if not isinstance(data, dict):
        return stub
    out = {**stub, **data}
    if "available" not in data:
        out["available"] = _lab_has_content(kind, data)
    try:
        out["source"] = str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        out["source"] = str(path)
    return out


def lab_block() -> dict:
    """Embed 10分析实验室 outputs into boot under lab.{anomaly,model_eval,event_impact}."""
    return {
        "anomaly": _load_lab_json(LAB_OUT / "anomaly_diagnosis.json", "anomaly"),
        "model_eval": _load_lab_json(LAB_OUT / "model_eval.json", "model_eval"),
        "event_impact": _load_lab_json(LAB_OUT / "event_impact.json", "event_impact"),
    }


def build_summary(df: pd.DataFrame, range_label: str) -> dict:
    s = sent_block(df)
    n = s["n"]
    risk, risk_why = risk_level(df)
    neg_tops = Counter(df[df["sentiment"] == "负"]["topic_primary"]).most_common(2)
    if neg_tops:
        hot = " + ".join(f"{TOPIC_CN.get(k, k)}（{v}）" for k, v in neg_tops)
        hot_line = f"负向主簇：{hot}" + ("；非全面崩盘叙事。" if len(neg_tops) >= 2 else "。")
    else:
        hot_line = "无明显负向主簇。"

    actions = pick_actionable(df, limit=3)
    if n:
        insight = (
            f"{range_label} TapTap 评价 n={n}，"
            f"正 {s['正']}（{s['正'] / n * 100:.1f}%）/ "
            f"中 {s['中']}（{s['中'] / n * 100:.1f}%）/ "
            f"负 {s['负']}（{s['负'] / n * 100:.1f}%）。"
        )
    else:
        insight = f"{range_label} 无样本。"

    return {
        "insight": insight,
        "hotspot": hot_line,
        "risk": f"风险={risk}：{risk_why}。",
        "decide": decide_line(df, actions) + "。",
        "risk_level": risk,
        "risk_why": risk_why,
    }


def build_window(
    df: pd.DataFrame,
    *,
    key: str,
    label: str,
    range_label: str,
    days: list,
) -> dict:
    s = sent_block(df)
    summary = build_summary(df, range_label)
    hi_list = pick_hi_neg(df, 8)
    act_list = pick_actionable(df, 8)
    return {
        "key": key,
        "label": label,
        "range": range_label,
        "days": [d.isoformat() for d in days],
        "sent": s,
        "topics": topic_rows(df),
        "signals": pick_signals(df, 4),
        "summary": summary,
        "hi_neg": hi_list,
        "actionable": act_list,
        "rhetoric_n": int((df["rhetoric"] != "none").sum()),
        "hi_neg_n": len(hi_list) if not len(df) else int(
            ((df["score_raw"].isin(["4", "5"])) & (df["sentiment"] == "负")).sum()
        ),
        "actionable_n": int((df["actionable"] == "是").sum()),
        "risk": summary["risk_level"],
        "risk_why": summary["risk_why"],
    }


def build_payload(df: pd.DataFrame, ann_name: str) -> dict:
    latest = max(df["day"])
    days_with = sorted(d for d in df["day"].unique() if d <= latest)
    roll_days = days_with[-3:] if len(days_with) >= 3 else days_with
    roll = df[df["day"].isin(roll_days)]
    week_start = latest - timedelta(days=6)
    week_days = sorted(d for d in days_with if week_start <= d <= latest)
    week = df[(df["day"] >= week_start) & (df["day"] <= latest)]

    roll_label = (
        f"{fmt_day(roll_days[0])}～{fmt_day(roll_days[-1])}" if roll_days else "—"
    )
    week_label = f"{fmt_day(week_start)}～{fmt_day(latest)}"

    win_week = build_window(
        week,
        key="week",
        label="本周",
        range_label=week_label,
        days=week_days or [latest],
    )
    win_roll = build_window(
        roll,
        key="roll3",
        label="滚动3日",
        range_label=roll_label,
        days=roll_days or [latest],
    )
    day_min = min(df["day"])
    pool_label = f"{fmt_day(day_min)}～{fmt_day(latest)}"
    win_pool = build_window(
        df,
        key="pool",
        label="全库",
        range_label=pool_label,
        days=days_with or [latest],
    )

    # 兼容旧字段：默认 KPI 用本周
    pool = sent_block(df)
    matrix = channel_matrix_block()
    reviews = build_reviews_sample(
        df, week_start=week_start, latest=latest, roll_days=roll_days, cap=1200
    )
    risk_week = risk_breakdown(week, baseline=pool)
    quality = quality_breakdown(
        matrix,
        {
            "pool_n": int(len(df)),
            "latest_day": latest.isoformat(),
        },
    )
    return {
        "meta": {
            "title": "明日方舟 · TapTap 舆情看板",
            "channel": "TapTap 评价",
            "app_id": 70253,
            "ann": ann_name,
            "prompt": "v1.4",
            "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
            "latest_day": latest.isoformat(),
            "pool_n": int(len(df)),
            "day_min": day_min.isoformat(),
            "day_max": latest.isoformat(),
            "boundary": "TapTap 评价主链 + B站/抖音/微博对照切片 + 跨渠道 AI；非正式全网 KPI。",
            "default_window": "week",
            "week_start": week_start.isoformat(),
            "roll_days": [d.isoformat() for d in roll_days],
            "reviews_n": len(reviews),
            "data_note": "面板优先按嵌入 reviews 样本 + 全局筛选重算；刷新只重读 #boot，不发起网络请求。",
        },
        "channel_matrix": matrix,
        "bili_contrast": bili_from_matrix(matrix),
        "windows": {
            "week": win_week,
            "roll3": win_roll,
            "pool": win_pool,
        },
        "kpis": {
            "pool": pool,
            "roll3": {
                **win_roll["sent"],
                "days": win_roll["days"],
                "label": win_roll["range"],
            },
            "week": {
                **win_week["sent"],
                "start": week_start.isoformat(),
                "end": latest.isoformat(),
            },
            "rhetoric_n": int((df["rhetoric"] != "none").sum()),
            "hi_neg": int(
                ((df["score_raw"].isin(["4", "5"])) & (df["sentiment"] == "负")).sum()
            ),
            "actionable_n": int((df["actionable"] == "是").sum()),
            "risk": win_week["risk"],
            "risk_why": win_week["risk_why"],
        },
        "topics_pool": topic_rows(df),
        "topics_week": win_week["topics"],
        "daily": daily_series(df, latest, days=30),
        "signals_week": win_week["signals"],
        "rhetoric": rhetoric_block(df),
        "reviews": reviews,
        "risk_model": risk_week,
        "quality_model": quality,
        "formulas": {
            "risk": risk_week.get("formula"),
            "quality": quality.get("formula"),
        },
        "lab": lab_block(),
    }


def render_html(payload: dict, template: str) -> str:
    """Inject payload only into #boot — never into JS sentinel strings."""
    blob = json.dumps(payload, ensure_ascii=False)
    blob = blob.replace("</", "<\\/")
    boot_open = '<script id="boot" type="application/json">'
    marker = f"{boot_open}/*__DATA__*/</script>"
    if marker not in template:
        raise ValueError("模板缺少 #boot 数据占位 /*__DATA__*/")
    # 只替换 boot 标签内占位，避免把 loadData() 里的哨兵字符串一并替换成 JSON（会导致 JS 语法错误）
    return template.replace(marker, f"{boot_open}{blob}</script>", 1)


def main() -> int:
    p = argparse.ArgumentParser(description="Build dashboard page")
    p.add_argument("--ann", default=str(ANN / "annotations_v1_4.csv"))
    args = p.parse_args()

    ann_path = Path(args.ann)
    if not ann_path.exists():
        print(f"缺少标注表：{ann_path}")
        return 2

    df = load(ann_path)
    payload = build_payload(df, ann_path.name)
    OUT.mkdir(parents=True, exist_ok=True)

    data_path = OUT / "dashboard_data.json"
    data_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    template_path = OUT / "index.template.html"
    if not template_path.exists():
        print(f"缺少模板：{template_path}")
        return 2
    html = render_html(payload, template_path.read_text(encoding="utf-8"))
    out_html = OUT / "index.html"
    out_html.write_text(html, encoding="utf-8")

    print(out_html)
    print(data_path)
    w = payload["windows"]["week"]
    r = payload["windows"]["roll3"]
    lab = payload.get("lab") or {}
    lab_flags = ",".join(
        f"{k}={'1' if (lab.get(k) or {}).get('available') else '0'}"
        for k in ("anomaly", "model_eval", "event_impact")
    )
    print(
        f"pool={payload['meta']['pool_n']} "
        f"week_n={w['sent']['n']} roll3_n={r['sent']['n']} "
        f"risk={w['risk']} reviews={payload['meta'].get('reviews_n', 0)} "
        f"lab[{lab_flags}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
