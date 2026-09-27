#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/risk_insight.py — 发声用户流失风险分层（舆情侧信号，非流失预测）。

输入：
  02数据/processed/reviews_clean.csv   （TapTap 评价清洗切片）
  03标注结果/annotations_v1_4.csv      （LLM 标注 v1.4，error 为空 + 去重 + 排除 probe_）

输出：
  outputs/risk_insight.json        （供每日情报 Agent / 看板消费）
  reports/risk_insight_latest.md

风险信号定义（诚实口径）：
  high_investment_negative   高投入不满：played_hours_num >= 100 且 sentiment == 负
  high_investment_not_rec    高投入不推荐：played_hours_num >= 100 且 is_recommend 为假
  rhetoric_disguised         修辞伪装（极性分离）：rhetoric ∈ {sarcasm, gaoji_hei, fanchuan}
                             —— 字面与真实态度分离，单字段正负抓不住
  actionable_negative        可行动差评：sentiment == 负 且 actionable == 是
  传播维度：support_count 在本切片全为 0 时，显式标注「不可用」，不编造。

边界：
  - 本模块输出的是舆情侧风险信号分层，不是用户流失预测模型。
  - 干预建议为方向性规则映射，不承诺效果。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
LAB = Path(__file__).resolve().parent
OUT_DIR = LAB / "outputs"
REPORT_DIR = LAB / "reports"

HIGH_HOURS = 100.0  # 高投入阈值：明日方舟存量游戏语境下 100h+ 视为深度投入

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
RHETORIC_CN = {
    "none": "无修辞",
    "sarcasm": "讽刺/阴阳",
    "gaoji_hei": "高级黑",
    "fanchuan": "反串",
    "template_praise": "模板好评",
}
RHETORIC_DISGUISED = {"sarcasm", "gaoji_hei", "fanchuan"}

# 主题 → 干预建议方向映射（规则，供 Agent 行动层引用；只给方向，不承诺效果）
INTERVENTION_MAP = {
    "gacha": "概率公示与保底沟通；新卡池发布前预期管理",
    "balance": "数值调整公开说明与补偿预告",
    "gameplay": "关卡难度/引导问题转关卡组；置顶反馈收集帖",
    "story": "剧情争议点整理给文案组；避免官方下场争论细节",
    "event": "活动节奏与奖励力度说明；发版前预期管理",
    "client": "性能问题转技术组并公示修复排期",
    "ops": "规则/客服争议个案升级；公示处理口径",
    "other": "常规监测；聚集时人工复核主题归类",
}


def round4(x: float | None) -> float | None:
    if x is None:
        return None
    try:
        if math.isnan(x) or math.isinf(x):
            return None
    except TypeError:
        return None
    return round(float(x), 4)


def pp(x: float | None) -> float | None:
    """比例 → 百分点（2 位）。"""
    if x is None:
        return None
    return round(float(x) * 100.0, 2)


def parse_hours(v: str | None) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def parse_recommend(v: str | None) -> bool | None:
    s = str(v).strip().lower() if v is not None else ""
    if s in ("true", "1", "是"):
        return True
    if s in ("false", "0", "否"):
        return False
    return None


def load_merged(ann_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """读取标注 × 清洗评论，on review_id 内连接；过滤 error / probe_ / 去重。"""
    ann_rows: dict[str, dict[str, Any]] = {}
    with ann_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rid = (row.get("review_id") or "").strip()
            if not rid or rid.startswith("probe_"):
                continue
            if (row.get("error") or "").strip():
                continue
            ann_rows[rid] = row  # 去重：keep last

    clean_path = ROOT / "02数据" / "processed" / "reviews_clean.csv"
    clean_rows: dict[str, dict[str, Any]] = {}
    with clean_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rid = (row.get("review_id") or "").strip()
            if rid:
                clean_rows[rid] = row

    merged: list[dict[str, Any]] = []
    for rid, ann in ann_rows.items():
        rv = clean_rows.get(rid)
        if rv is None:
            continue
        hours = parse_hours(rv.get("played_hours_num"))
        rec = parse_recommend(rv.get("is_recommend"))
        try:
            support = int(float(rv.get("support_count") or 0))
        except (ValueError, TypeError):
            support = 0
        try:
            replies = int(float(rv.get("reply_preview_count") or 0))
        except (ValueError, TypeError):
            replies = 0
        merged.append(
            {
                "review_id": rid,
                "topic": (ann.get("topic_primary") or "other").strip() or "other",
                "sentiment": (ann.get("sentiment") or "").strip(),
                "rhetoric": (ann.get("rhetoric") or "none").strip() or "none",
                "actionable": (ann.get("actionable") or "").strip(),
                "played_hours": hours,
                "is_recommend": rec,
                "support_count": support,
                "reply_count": replies,
                "text": (rv.get("text") or "").strip(),
                "score_norm": parse_hours(rv.get("score_norm")),
                "publish_time_cn": (rv.get("publish_time_cn") or "").strip(),
            }
        )
    availability = {
        "played_hours_available": any(m["played_hours"] is not None for m in merged),
        "recommend_available": any(m["is_recommend"] is not None for m in merged),
        "support_available": any(m["support_count"] > 0 for m in merged),
    }
    return merged, availability


def topic_risk_table(m: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """分主题风险表：负向率、负向中高投入占比、修辞伪装占比、可行动占比。"""
    rows: list[dict[str, Any]] = []
    for t in sorted(TOPIC_CN):
        sub = [r for r in m if r["topic"] == t]
        n = len(sub)
        if n == 0:
            continue
        neg = [r for r in sub if r["sentiment"] == "负"]
        n_neg = len(neg)
        neg_rate = (n_neg / n) if n else None
        with_hours = [r for r in neg if r["played_hours"] is not None]
        high_inv_share = (
            (sum(1 for r in with_hours if r["played_hours"] >= HIGH_HOURS) / len(with_hours))
            if with_hours
            else None
        )
        disguised_share = (
            (sum(1 for r in neg if r["rhetoric"] in RHETORIC_DISGUISED) / n_neg)
            if n_neg
            else None
        )
        actionable_share = (
            (sum(1 for r in neg if r["actionable"] == "是") / n_neg) if n_neg else None
        )
        rows.append(
            {
                "topic": t,
                "topic_cn": TOPIC_CN[t],
                "n": n,
                "n_neg": n_neg,
                "neg_rate": round4(neg_rate),
                "neg_rate_pp": pp(neg_rate),
                "neg_high_hours_share": round4(high_inv_share),
                "neg_high_hours_share_pp": pp(high_inv_share),
                "rhetoric_disguised_share": round4(disguised_share),
                "rhetoric_disguised_share_pp": pp(disguised_share),
                "actionable_share": round4(actionable_share),
                "actionable_share_pp": pp(actionable_share),
                "intervention": INTERVENTION_MAP.get(t, INTERVENTION_MAP["other"]),
            }
        )
    rows.sort(key=lambda r: (r["neg_rate"] or 0), reverse=True)
    return rows


def top_samples(layer: list[dict[str, Any]], k: int = 3) -> list[dict[str, Any]]:
    """层内代表样例：按投入时长降序；只输出脱敏摘要（review_id + 数字 + 截断文本）。"""
    if not layer:
        return []
    sub = sorted(
        layer,
        key=lambda r: (r["played_hours"] if r["played_hours"] is not None else -1.0),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for r in sub[:k]:
        hours = r["played_hours"]
        text = r["text"]
        out.append(
            {
                "review_id": r["review_id"],
                "topic": TOPIC_CN.get(r["topic"], r["topic"]),
                "played_hours": round(hours, 1) if hours is not None else None,
                "sentiment": r["sentiment"],
                "rhetoric": RHETORIC_CN.get(r["rhetoric"], r["rhetoric"]),
                "text_excerpt": text[:60] + ("…" if len(text) > 60 else ""),
            }
        )
    return out


def build_payload(m: list[dict[str, Any]], availability: dict[str, bool], ann_name: str) -> dict[str, Any]:
    n_total = len(m)
    neg = [r for r in m if r["sentiment"] == "负"]
    n_neg = len(neg)
    neg_rate = (n_neg / n_total) if n_total else 0.0

    hours_all = [r["played_hours"] for r in m if r["played_hours"] is not None]
    hours_median = sorted(hours_all)[len(hours_all) // 2] if hours_all else None
    # 分位数参考（供业务校准阈值）：P75 = 排序后 75% 位置的值
    hours_p75 = None
    if hours_all:
        s = sorted(hours_all)
        hours_p75 = s[min(len(s) - 1, int(round(0.75 * (len(s) - 1))))]

    high_inv_neg = [
        r for r in neg if r["played_hours"] is not None and r["played_hours"] >= HIGH_HOURS
    ]
    high_inv_not_rec = [
        r
        for r in m
        if r["played_hours"] is not None
        and r["played_hours"] >= HIGH_HOURS
        and r["is_recommend"] is False
    ]
    rhetoric_disguised = [r for r in neg if r["rhetoric"] in RHETORIC_DISGUISED]
    actionable_neg = [r for r in neg if r["actionable"] == "是"]

    high_inv_share_of_neg = (
        (len(high_inv_neg) / n_neg) if n_neg else None
    )

    # headline（诚实：字段不可用或样本不足时显式说明）
    if n_total == 0:
        headline = "合并后无样本，无法输出风险分层。"
    elif not availability["played_hours_available"]:
        headline = f"负向 {n_neg}/{n_total}（{pp(neg_rate)}%）；本切片缺游戏时长字段，高投入分层不可用。"
    elif n_neg < 30:
        headline = f"负向样本 n={n_neg} < 30，风险分层仅供观察，不做占比叙事。"
    else:
        headline = (
            f"负向 {n_neg} 条中 {len(high_inv_neg)} 条（{pp(high_inv_share_of_neg)}%）"
            f"来自 ≥{HIGH_HOURS:.0f}h 高投入玩家；另有 {len(rhetoric_disguised)} 条修辞伪装负向"
            f"（字面/意图极性分离）、{len(actionable_neg)} 条可行动差评。"
        )

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "available": n_total > 0,
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "channel": "taptap",
        "caliber": "舆情侧流失风险信号（发声用户），非用户流失预测",
        "headline": headline,
        "thresholds": {
            "high_hours": HIGH_HOURS,
            "note": "展示口径阈值，非业务定标；可按分位点（如 P75）通过 --high-hours 配置",
        },
        "availability": availability,
        "facts": {
            "n_total": n_total,
            "n_neg": n_neg,
            "neg_rate": round4(neg_rate),
            "neg_rate_pp": pp(neg_rate),
            "played_hours_median": round4(hours_median),
            "played_hours_p75": round4(hours_p75),
            "n_high_investment_negative": len(high_inv_neg),
            "high_investment_share_of_neg": round4(high_inv_share_of_neg),
            "high_investment_share_of_neg_pp": pp(high_inv_share_of_neg),
            "n_high_investment_not_recommend": len(high_inv_not_rec),
            "n_rhetoric_disguised": len(rhetoric_disguised),
            "n_actionable_negative": len(actionable_neg),
        },
        "layers": {
            "high_investment_negative": {
                "n": len(high_inv_neg),
                "definition": f"played_hours_num >= {HIGH_HOURS:.0f} 且 sentiment == 负",
                "top_samples": top_samples(high_inv_neg),
            },
            "high_investment_not_recommend": {
                "n": len(high_inv_not_rec),
                "definition": f"played_hours_num >= {HIGH_HOURS:.0f} 且 is_recommend == False",
                "top_samples": top_samples(high_inv_not_rec),
            },
            "rhetoric_disguised": {
                "n": len(rhetoric_disguised),
                "definition": "rhetoric ∈ {sarcasm, gaoji_hei, fanchuan}（字面/意图极性分离）",
                "top_samples": top_samples(rhetoric_disguised),
            },
            "actionable_negative": {
                "n": len(actionable_neg),
                "definition": "sentiment == 负 且 actionable == 是",
                "top_samples": top_samples(actionable_neg),
            },
        },
        "topic_risk_table": topic_risk_table(m),
        "intervention_map": INTERVENTION_MAP,
        "source": {
            "annotations": f"03标注结果/{ann_name}",
            "reviews": "02数据/processed/reviews_clean.csv",
        },
        "boundaries": [
            "舆情侧风险信号分层，非用户流失预测；无留存/回流数据前不做因果与转化归因。",
            "support_count 本切片全为 0：传播风险维度不可用，已显式标注而非估算。",
            "干预建议为方向性规则映射，不承诺效果。",
        ],
    }
    return payload


def render_report(p: dict[str, Any]) -> str:
    f = p["facts"]
    lines: list[str] = [
        "# TapTap 流失风险分层（11情报Agent）",
        "",
        "## 元数据",
        "",
        "| 项 | 内容 |",
        "|----|------|",
        f"| 生成时间 | {p['generated_at']} |",
        f"| 口径 | {p['caliber']} |",
        f"| 高投入阈值 | ≥{p['thresholds']['high_hours']:.0f}h（{p['thresholds']['note']}） |",
        f"| 标注源 | `{p['source']['annotations']}` |",
        f"| 字段可用性 | 时长={'可用' if p['availability']['played_hours_available'] else '不可用'} · 推荐={'可用' if p['availability']['recommend_available'] else '不可用'} · 点赞={'可用' if p['availability']['support_available'] else '本切片全 0，不可用'} |",
        "",
        "## 一句话结论",
        "",
        p["headline"],
        "",
        "## 核心数字",
        "",
        "| 指标 | 值 |",
        "|------|----|",
        f"| 样本 n | {f['n_total']} |",
        f"| 负向 n | {f['n_neg']}（{f['neg_rate_pp']}%） |",
        f"| 高投入负向 n | {f['n_high_investment_negative']}"
        + (
            f"（占负向 {f['high_investment_share_of_neg_pp']}%）"
            if f["high_investment_share_of_neg_pp"] is not None
            else ""
        )
        + " |",
        f"| 高投入不推荐 n | {f['n_high_investment_not_recommend']} |",
        f"| 修辞伪装负向 n | {f['n_rhetoric_disguised']} |",
        f"| 可行动差评 n | {f['n_actionable_negative']} |",
        f"| 时长中位数 | {f['played_hours_median'] if f['played_hours_median'] is not None else '—'} h |",
        f"| 时长 P75（阈值校准参考） | {f['played_hours_p75'] if f['played_hours_p75'] is not None else '—'} h |",
        "",
        "## 分主题风险表（按负向率降序）",
        "",
        "| 主题 | n | 负向 n | 负向率 | 负向中高投入占比 | 修辞伪装占比 | 可行动占比 | 干预方向 |",
        "|------|---|--------|--------|------------------|--------------|------------|----------|",
    ]
    for r in p["topic_risk_table"]:
        hh = (
            f"{r['neg_high_hours_share_pp']:.1f}%"
            if r["neg_high_hours_share_pp"] is not None
            else "—"
        )
        rd = (
            f"{r['rhetoric_disguised_share_pp']:.1f}%"
            if r["rhetoric_disguised_share_pp"] is not None
            else "—"
        )
        ac = (
            f"{r['actionable_share_pp']:.1f}%"
            if r["actionable_share_pp"] is not None
            else "—"
        )
        lines.append(
            f"| {r['topic_cn']} | {r['n']} | {r['n_neg']} | {r['neg_rate_pp']}% | {hh} | {rd} | {ac} | {r['intervention']} |"
        )
    lines += ["", "## 分层样例（脱敏：review_id + 数字 + 截断文本）", ""]
    for k, v in p["layers"].items():
        lines += [f"### {k}（n={v['n']}）", "", f"- 定义：{v['definition']}", ""]
        if v["top_samples"]:
            for s in v["top_samples"]:
                hours = f"{s['played_hours']:.0f}h" if s["played_hours"] is not None else "—"
                lines.append(
                    f"- `{s['review_id']}` {s['topic']} · {hours} · "
                    f"{s['sentiment']} · {s['rhetoric']}：{s['text_excerpt']}"
                )
        else:
            lines.append("- （本层无样本）")
        lines.append("")
    lines += ["## 边界", ""]
    for b in p["boundaries"]:
        lines.append(f"- {b}")
    lines += ["", "---", "", "*报告结束*", ""]
    return "\n".join(lines)


def main() -> None:
    global HIGH_HOURS
    ap = argparse.ArgumentParser(description="11情报Agent risk insight layering")
    ap.add_argument(
        "--ann",
        default=str(ROOT / "03标注结果" / "annotations_v1_4.csv"),
        help="annotations CSV path",
    )
    ap.add_argument(
        "--high-hours",
        type=float,
        default=HIGH_HOURS,
        help=f"高投入阈值（小时），默认 {HIGH_HOURS:.0f}；展示口径，可按业务分位点（如 P75）配置",
    )
    args = ap.parse_args()
    HIGH_HOURS = float(args.high_hours)

    ann_path = Path(args.ann)
    merged, availability = load_merged(ann_path)
    payload = build_payload(merged, availability, ann_path.name)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "risk_insight.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path = REPORT_DIR / "risk_insight_latest.md"
    report_path.write_text(render_report(payload), encoding="utf-8")

    print(json_path)
    print(report_path)
    print(f"headline={payload['headline']}")


if __name__ == "__main__":
    main()
