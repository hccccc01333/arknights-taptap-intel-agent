#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 TapTap vs B 站 传播对照报告（B 站为第一社交对照渠）。"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

MOD_DIR = Path(__file__).resolve().parent
ROOT = MOD_DIR.parent
ANN_TAPTAP = ROOT / "03标注结果" / "annotations_v1_4.csv"
REVIEWS = ROOT / "02数据" / "processed" / "reviews_clean.csv"
ANN_BILI = MOD_DIR / "annotations_bili_sample.csv"
COMMENTS = MOD_DIR / "comments_sample.csv"
REPORT_DIR = MOD_DIR / "reports"

TZ_CN = timezone(timedelta(hours=8))
TOPIC_CN = {
    "gacha": "抽卡",
    "balance": "强度/平衡",
    "gameplay": "玩法",
    "story": "剧情",
    "event": "活动",
    "client": "客户端",
    "ops": "运营",
    "other": "其他",
}


def pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def sentiment_dist(rows: list[dict], key: str = "sentiment") -> dict[str, float]:
    c = Counter((r.get(key) or "").strip() for r in rows if (r.get(key) or "").strip())
    total = sum(c.values())
    return {k: pct(c.get(k, 0), total) for k in ("正", "中", "负")} | {"n": total, "counts": dict(c)}


def topic_dist(rows: list[dict]) -> list[tuple[str, int, float]]:
    c = Counter((r.get("topic_primary") or "other").strip() or "other" for r in rows)
    total = sum(c.values()) or 1
    return [(k, v, pct(v, total)) for k, v in c.most_common()]


def rhetoric_rate(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    n = sum(1 for r in rows if (r.get("rhetoric") or "none") not in ("", "none"))
    return pct(n, len(rows))


def load_taptap_window(days: int = 7, end: str | None = None) -> tuple[list[dict], str, str]:
    reviews = {r["review_id"]: r for r in load_csv(REVIEWS)}
    anns = load_csv(ANN_TAPTAP)
    dated = []
    for a in anns:
        rev = reviews.get(a["review_id"])
        if not rev:
            continue
        d = (rev.get("publish_time_cn") or "")[:10]
        if not d:
            continue
        dated.append((d, a))
    if not dated:
        return [], "", ""
    max_d = end or max(d for d, _ in dated)
    end_dt = datetime.strptime(max_d, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=days - 1)
    start = start_dt.strftime("%Y-%m-%d")
    rows = [a for d, a in dated if start <= d <= max_d]
    # if window too thin, use full corpus
    used = f"{start}~{max_d}"
    if len(rows) < 30:
        rows = [a for _, a in dated]
        used = f"全库近窗不足，改用全库 n={len(rows)}（日期 {min(d for d,_ in dated)}~{max(d for d,_ in dated)}）"
    return rows, used, max_d


def top_examples(comments: list[dict], anns: list[dict], sentiment: str, k: int = 2) -> list[str]:
    by_id = {c["comment_id"]: c for c in comments}
    scored: list[tuple[float, str]] = []
    for a in anns:
        if a.get("sentiment") != sentiment:
            continue
        try:
            conf = float(a.get("confidence") or 0)
        except ValueError:
            conf = 0.0
        c = by_id.get(a["review_id"])
        if not c:
            continue
        text = (c.get("text") or "").replace("\n", " ").strip()
        if len(text) < 12:
            continue
        if len(text) > 80:
            text = text[:80] + "…"
        topic_cn = TOPIC_CN.get(a.get("topic_primary", ""), a.get("topic_primary"))
        scored.append((conf, f'"{text}"（主题 {topic_cn}）'))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:k]]


def build_report(taptap_rows: list[dict], bili_rows: list[dict], window_desc: str, annotate_mode: str) -> str:
    ts = sentiment_dist(taptap_rows)
    bs = sentiment_dist(bili_rows)
    tt = topic_dist(taptap_rows)
    bt = topic_dist(bili_rows)
    comments = load_csv(COMMENTS) if COMMENTS.exists() else []

    # observations
    obs = []
    # 1 sentiment structure
    if abs(ts.get("负", 0) - bs.get("负", 0)) >= 8:
        who = "B 站" if bs.get("负", 0) > ts.get("负", 0) else "TapTap"
        obs.append(
            f"负向占比上 {who} 更高（TapTap {ts.get('负')}% vs B站 {bs.get('负')}%）。"
            "差异可能来自场景：商店评价偏「是否推荐/是否继续玩」，视频评论偏「弹幕式即时反应」。"
        )
    else:
        obs.append(
            f"两侧负向占比接近（TapTap {ts.get('负')}% / B站 {bs.get('负')}%），"
            "至少在本样本窗内未见渠道级崩盘信号；更宜看主题结构而非单一情绪率。"
        )

    # 2 topic
    t_top = tt[0][0] if tt else "other"
    b_top = bt[0][0] if bt else "other"
    if t_top != b_top:
        obs.append(
            f"主题主簇不同：TapTap 以「{TOPIC_CN.get(t_top, t_top)}」为主，"
            f"B 站样本以「{TOPIC_CN.get(b_top, b_top)}」为主——对照时勿直接跨渠道同比同一主题份额。"
        )
    else:
        obs.append(
            f"两侧主簇同为「{TOPIC_CN.get(t_top, t_top)}」，可在该主题上做定性互证，但仍受样本与场景偏差约束。"
        )

    # 3 gacha share
    tg = next((p for k, _, p in tt if k == "gacha"), 0.0)
    bg = next((p for k, _, p in bt if k == "gacha"), 0.0)
    if bg > tg + 5:
        gacha_note = "本样本中 B 站抽卡声量更高，常见于活动 PV/抽卡录播，不必然等于商店口碑恶化。"
    elif tg > bg + 5:
        gacha_note = "本样本中 TapTap 抽卡份额更高；若 B 站未覆盖抽卡向视频，对照会低估该主题。"
    else:
        gacha_note = "两侧抽卡份额接近；仍须结合视频选题，避免把场景偏差写成口碑结论。"
    obs.append(f"抽卡主题份额：TapTap {tg}% / B站 {bg}%。{gacha_note}")

    # 4 rhetoric
    tr = rhetoric_rate(taptap_rows)
    br = rhetoric_rate(bili_rows)
    obs.append(
        f"修辞非 none 占比：TapTap {tr}% / B站 {br}%。"
        "B 站梗与表情更多，弱规则/小样本下 rhetoric 召回不稳定，只作复核线索。"
    )

    # 5 volume caveat
    obs.append(
        f"样本量不对称：TapTap 窗 {int(ts['n'])} 条 vs B站标注 {int(bs['n'])} 条"
        f"（原始评论采集 {len(comments)} 条）。本对照只用于方法演示与方向感，不作 KPI。"
    )
    obs = obs[:5]

    # conclusion restrained
    conclusion = (
        "在「TapTap 评价主链 + B 站传播对照第一渠」框架下，B 站样本适合回答："
        "「视频场景里玩家在吵什么、即时情绪是否与商店评价同向」。"
        "它不适合替代 TapTap 做留存/推荐决策；跨渠道百分比不宜直接当作异动幅度。"
    )

    neg_ex = top_examples(comments, bili_rows, "负", 2)
    pos_ex = top_examples(comments, bili_rows, "正", 1)

    lines = [
        f"# TapTap × B 站传播对照（{datetime.now(TZ_CN).strftime('%Y-%m-%d')}）",
        "",
        "## 0. 范围与边界",
        "",
        f"- TapTap：`annotations_v1_4`，窗口说明：{window_desc}",
        f"- B 站：`annotations_bili_sample.csv`（标注模式：{annotate_mode}），源评论 `comments_sample.csv`",
        "- B 站是**传播对照第一渠**（非全网舆情中台）；禁止把 B 站比例直接写进对标 KPI。",
        "",
        "## 1. 情绪结构",
        "",
        "| 渠道 | n | 正 | 中 | 负 |",
        "|------|---|----|----|----|",
        f"| TapTap | {int(ts['n'])} | {ts.get('正',0)}% | {ts.get('中',0)}% | {ts.get('负',0)}% |",
        f"| B 站样本 | {int(bs['n'])} | {bs.get('正',0)}% | {bs.get('中',0)}% | {bs.get('负',0)}% |",
        "",
        "## 2. 主题结构（Top）",
        "",
        "### TapTap",
        "",
    ]
    for k, v, p in tt[:6]:
        lines.append(f"- {TOPIC_CN.get(k,k)}：{v}（{p}%）")
    lines += ["", "### B 站样本", ""]
    for k, v, p in bt[:6]:
        lines.append(f"- {TOPIC_CN.get(k,k)}：{v}（{p}%）")

    lines += [
        "",
        "## 3. 观察（3–5 条）",
        "",
    ]
    for i, o in enumerate(obs, 1):
        lines.append(f"{i}. {o}")

    if neg_ex or pos_ex:
        lines += ["", "## 4. B 站原话摘录（示意）", ""]
        if neg_ex:
            lines.append("- 负向：" + "；".join(neg_ex))
        if pos_ex:
            lines.append("- 正向：" + "；".join(pos_ex))

    lines += [
        "",
        "## 5. 克制结论",
        "",
        conclusion,
        "",
        "## 6. 复现",
        "",
        "```bash",
        "python 06对照_B站/crawl_bili_comments.py --target 350",
        "python 06对照_B站/annotate_bili_sample.py --limit 150",
        "python 06对照_B站/build_contrast_report.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def detect_annotate_mode(rows: list[dict]) -> str:
    if not rows:
        return "无标注"
    models = Counter((r.get("model") or "") for r in rows)
    versions = Counter((r.get("prompt_version") or "") for r in rows)
    if any("weak" in m for m in models):
        return f"弱规则（model={models.most_common(1)[0][0]}）"
    return f"LLM schema {versions.most_common(1)[0][0] if versions else 'v1.4'} / {models.most_common(1)[0][0]}"


def main(args: argparse.Namespace) -> int:
    if not ANN_BILI.exists():
        print(f"缺少 {ANN_BILI}")
        return 2
    bili_rows = [r for r in load_csv(ANN_BILI) if not r.get("error") and r.get("sentiment")]
    taptap_rows, window_desc, _ = load_taptap_window(days=args.days)
    mode = detect_annotate_mode(bili_rows)
    text = build_report(taptap_rows, bili_rows, window_desc, mode)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"contrast_taptap_bili_{datetime.now(TZ_CN).strftime('%Y%m%d')}.md"
    out.write_text(text, encoding="utf-8")
    print(f"[out] {out}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7, help="TapTap 近窗天数；不足则回退全库")
    raise SystemExit(main(p.parse_args()))
