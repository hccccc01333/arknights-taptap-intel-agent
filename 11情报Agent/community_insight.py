#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/community_insight.py — 社区话题流分析（感知层第二数据源）。

输入：<data_dir>/community/posts.csv + comments.csv（社区通道爬虫产出）
输出：outputs/community_insight_<key>.json + reports/community_insight_<key>_latest.md

分析（第一版，全部代码计算，零 LLM）：
  - 总量与互动：帖子数 / 评论数 / 赞 / 浏览 / 时间跨度
  - 热帖榜 Top N（互动分 = comments + supports + pv_total/100）
  - spam 疑似启发式：代练/开户/兑换码话术模式 → 标「疑似」并给比例
    （边界：官方活动打卡可能误中——2026-09-28 实测教训，报告必须带人工抽检提示）
  - 发布节奏：近 14 天逐日帖量

CLI：python community_insight.py --game wuthering-waves
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
OUT_DIR = LAB / "outputs"
REPORT_DIR = LAB / "reports"
DEFAULT_GAME = "arknights"

SPAM_PATTERNS = [
    r"开户", r"代练", r"兑换码", r"签到领", r"加[Vv]", r"薇信", r"威信",
    r"https?://", r"\d{6,}\s*[领 points分]*\s*[码券币]", r"返利", r"折扣充值",
]
SPAM_RE = re.compile("|".join(SPAM_PATTERNS))


def round2(x: float) -> float:
    return round(float(x), 2)


def interaction_score(p: dict[str, Any]) -> int:
    """互动分：评论 + 赞 + 浏览/100（热帖排序用）。"""
    return int(p.get("comments") or 0) + int(p.get("supports") or 0) + int(p.get("ups") or 0) + int(p.get("pv_total") or 0) // 100


def load_posts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_comments(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def analyze(posts: list[dict[str, Any]], comments: list[dict[str, Any]], game_name: str) -> dict[str, Any]:
    n = len(posts)
    total_comments = sum(int(p.get("comments") or 0) for p in posts)
    total_supports = sum(int(p.get("supports") or 0) for p in posts)
    total_pv = sum(int(p.get("pv_total") or 0) for p in posts)
    n_loaded_comments = len(comments)

    def date_of(p: dict[str, Any]) -> str:
        pt = str(p.get("publish_time") or "")
        return datetime.fromtimestamp(int(pt), TZ).strftime("%Y-%m-%d") if pt.isdigit() else ""

    ranked = sorted(posts, key=interaction_score, reverse=True)

    def hot_row(p: dict[str, Any]) -> dict[str, Any]:
        return {
            "moment_id": p.get("moment_id"),
            "summary": (p.get("summary") or p.get("title") or "")[:80],
            "date": date_of(p),
            "source_type": p.get("source_type") or "feed",
            "interaction_score": interaction_score(p),
            "comments": int(p.get("comments") or 0),
            "supports": int(p.get("supports") or 0),
            "pv_total": int(p.get("pv_total") or 0),
        }

    hot = [hot_row(p) for p in ranked[:10]]

    # 近 30 天热榜（总互动的热榜偏向历史积累，当期视角单独给）
    cutoff = (datetime.now(TZ).timestamp()) - 30 * 86400
    recent_ranked = [p for p in ranked if str(p.get("publish_time") or "").isdigit() and int(p["publish_time"]) >= cutoff]
    hot_recent = [hot_row(p) for p in recent_ranked[:10]]

    spam_rows = [p for p in posts if SPAM_RE.search(p.get("summary") or "")]
    spam_comments = [c for c in comments if SPAM_RE.search(c.get("content") or "")]
    spam_rate = round2(len(spam_rows) / n * 100) if n else None

    # 近 14 天逐日帖量
    day_counter: dict[str, int] = {}
    for p in posts:
        pt = str(p.get("publish_time") or "")
        if pt.isdigit():
            d = datetime.fromtimestamp(int(pt), TZ).strftime("%Y-%m-%d")
            day_counter[d] = day_counter.get(d, 0) + 1
    today = datetime.now(TZ).date()
    daily = [
        {"date": (today - timedelta(days=i)).isoformat(), "posts": day_counter.get((today - timedelta(days=i)).isoformat(), 0)}
        for i in range(13, -1, -1)
    ]

    zero_comment = sum(1 for p in posts if int(p.get("comments") or 0) == 0)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "game": game_name,
        "totals": {
            "posts": n,
            "loaded_comments": n_loaded_comments,
            "comments_declared": total_comments,
            "supports": total_supports,
            "pv_total": total_pv,
            "zero_comment_posts": zero_comment,
        },
        "hot_posts": hot,
        "hot_posts_recent_30d": hot_recent,
        "spam_heuristic": {
            "note": "启发式疑似，需人工抽检（官方活动打卡可能误中）",
            "suspected_posts": len(spam_rows),
            "suspected_rate_pp": spam_rate,
            "suspected_comments": len(spam_comments),
        },
        "daily_posts_14d": daily,
        "caliber": "社区话题流（feed 帖子 + 热评 + 帖子评论），非评分区评论；与评分层分析口径独立",
    }


def render_report(p: dict[str, Any]) -> str:
    t = p["totals"]
    lines = [
        f"# 社区话题流洞察 · {p['game']}",
        "",
        f"> 生成：{p['generated_at']} · {p['caliber']}",
        "",
        "## 总量",
        "",
        f"- 帖子 **{t['posts']}** 条；声明评论 **{t['comments_declared']}** 条（实载 {t['loaded_comments']}）；赞 {t['supports']}；浏览 {t['pv_total']}",
        f"- 零评论帖子：{t['zero_comment_posts']} 条",
        "",
        "## 热帖榜 · 近 30 天（当期舆情视角）",
        "",
        "| # | 日期 | 帖子 | 互动分 | 评论 | 浏览 |",
        "|---|------|------|--------|------|------|",
    ]
    for i, h in enumerate(p["hot_posts_recent_30d"], 1):
        lines.append(f"| {i} | {h['date']} | {h['summary'][:36] or '（无题）'} | {h['interaction_score']} | {h['comments']} | {h['pv_total']} |")
    if not p["hot_posts_recent_30d"]:
        lines.append("| — | — | 近 30 天无帖子 | — | — | — |")
    lines += [
        "",
        "## 热帖榜 · 全时段 Top10（含历史精华，注意时间）",
        "",
        "| # | 日期 | 帖子 | 互动分 | 评论 | 浏览 |",
        "|---|------|------|--------|------|------|",
    ]
    for i, h in enumerate(p["hot_posts"], 1):
        lines.append(f"| {i} | {h['date']} | {h['summary'][:36] or '（无题）'} | {h['interaction_score']} | {h['comments']} | {h['pv_total']} |")
    s = p["spam_heuristic"]
    lines += [
        "",
        "## spam 疑似（启发式，需人工抽检）",
        "",
        f"- 疑似帖：**{s['suspected_posts']}** 条（{s['suspected_rate_pp']}%）；疑似评论 {s['suspected_comments']} 条",
        f"- ⚠ 边界：官方活动打卡（兑换码回复）可能被误中——抽检后再定论",
        "",
        "## 近 14 天发布节奏",
        "",
        "| 日期 | 帖量 |",
        "|------|------|",
    ]
    for d in p["daily_posts_14d"]:
        lines.append(f"| {d['date']} | {d['posts']} |")
    lines += ["", "---", "", "*报告结束*", ""]
    return "\n".join(lines)


def main() -> None:
    sys.path.insert(0, str(ROOT / "games"))
    from game_profile import load as load_profile  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="社区话题流分析（感知层第二数据源）")
    ap.add_argument("--game", default=DEFAULT_GAME)
    ap.add_argument("--data-dir", default="", help="数据目录（默认取档案 data_dir）")
    args = ap.parse_args()

    prof = load_profile(args.game)
    data_dir = Path(args.data_dir or prof.get("data_dir") or "")
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    community_dir = data_dir / "community"

    posts = load_posts(community_dir / "posts.csv")
    comments = load_comments(community_dir / "comments.csv")
    if not posts:
        print(f"[stop] 无社区帖子数据：{community_dir / 'posts.csv'}（先跑 crawl_taptap_community.py --game {args.game}）", file=sys.stderr)
        return

    payload = analyze(posts, comments, str(prof.get("name") or args.game))
    payload["profile_key"] = prof.get("key")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    jout = OUT_DIR / f"community_insight_{prof.get('key')}.json"
    mout = REPORT_DIR / f"community_insight_{prof.get('key')}_latest.md"
    jout.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    mout.write_text(render_report(payload), encoding="utf-8")
    print(jout)
    print(mout)


if __name__ == "__main__":
    main()
