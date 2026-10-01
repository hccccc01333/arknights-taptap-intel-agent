#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L5_generation/platform_insight.py — 平台级发现流洞察（S4/S5/S6 → facts）。

与 community_insight.py 的分工：
  community_insight.py  单游戏社区（S2/S3）——「这款游戏的玩家在社区里说什么」
  platform_insight.py   全平台发现流（S4/S5/S6）——「整个社区现在在聊什么事件、哪个游戏在产内容」

输入（默认 data/raw/taptap/，由 L1_data_source/collectors/taptap/crawl_taptap_discovery.py 产出）：
  hot_hashtags.csv        S4 话题热榜
  discovery_posts.csv     S5 发现流 + S6 话题下帖子
  discovery_comments.csv  上述帖子的评论

输出：
  outputs/platform_insight.json
  reports/platform_insight_latest.md

全部由代码计算，零 LLM；LLM 只能引用本文件产出的 facts（锁数纪律）。
事件信号为**规则**判定（阈值写在 EVENT_RULES，可复算），不是模型主观判断。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
OUT_DIR = LAB / "outputs"
REPORT_DIR = LAB / "reports"
DEFAULT_DATA_DIR = "data/raw/taptap"

# 事件规则（阈值集中在这里，改口径改这一处）
EVENT_RULES = {
    "hot_board_pv": 10000,      # 热榜话题浏览量 ≥ 此值 → 平台级热事件
    "topic_dig_interaction": 500,  # 话题下帖子互动总量 ≥ 此值 → 深挖热度事件
    "hot_board_top_n": 3,       # 热榜前 N 名无条件记为事件
    "delisted_game_label": "该游戏已下架",  # TapTap 对下架游戏的展示名
}


def _int(v: Any) -> int:
    try:
        return int(str(v or 0).strip())
    except (TypeError, ValueError):
        return 0


def interaction_score(p: dict[str, Any]) -> int:
    return _int(p.get("comments")) + _int(p.get("supports")) + _int(p.get("ups")) + _int(p.get("pv_total")) // 100


def load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_profiles() -> list[dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "games"))
    try:
        from game_profile import available, load  # noqa: PLC0415
    except ImportError:
        return []
    out = []
    for key in available():
        try:
            out.append(load(key))
        except Exception:
            continue
    return out


def match_games(text: str, profiles: list[dict[str, Any]]) -> list[str]:
    """文本命中哪些已建档游戏（别名匹配）。用于「平台事件 → 我关注的游戏」关联。"""
    hits = []
    for p in profiles:
        name = str(p.get("name") or "")
        aliases = [a for a in (p.get("aliases") or []) if a]
        for a in [name] + aliases:
            if a and a in text:
                hits.append(name)
                break
    return hits


def analyze(
    tags: list[dict[str, Any]],
    posts: list[dict[str, Any]],
    comments: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    # ---------- S4 热榜 ----------
    board = sorted(tags, key=lambda r: _int(r.get("sort")) or 999)
    hot_board = [
        {
            "sort": _int(r.get("sort")),
            "title": r.get("title", ""),
            "hashtag_id": r.get("hashtag_id", ""),
            "page_view": _int(r.get("page_view")),
            "comment_count": _int(r.get("comment_count")),
            "matched_games": match_games(f"{r.get('title','')} {r.get('description','')}", profiles),
        }
        for r in board
    ]

    # ---------- S6 话题深挖 ----------
    dig: dict[str, dict[str, Any]] = {}
    for p in posts:
        if p.get("source_type") != "hashtag":
            continue
        tid = str(p.get("hashtag_id") or "")
        tname = str(p.get("hashtag_title") or "")
        d = dig.setdefault(
            tid,
            {"hashtag_id": tid, "title": tname, "posts": 0, "comments_declared": 0,
             "supports": 0, "ups": 0, "pv_total": 0, "top_posts": []},
        )
        d["posts"] += 1
        d["comments_declared"] += _int(p.get("comments"))
        d["supports"] += _int(p.get("supports"))
        d["ups"] += _int(p.get("ups"))
        d["pv_total"] += _int(p.get("pv_total"))
        d["top_posts"].append(p)
    topic_dig = []
    for d in dig.values():
        d["interaction_total"] = d["comments_declared"] + d["supports"] + d["ups"] + d["pv_total"] // 100
        tops = sorted(d["top_posts"], key=interaction_score, reverse=True)[:3]
        d["top_posts"] = [
            {
                "summary": (t.get("title") or t.get("summary") or "")[:60],
                "app_title": t.get("app_title", ""),
                "comments": _int(t.get("comments")),
                "pv_total": _int(t.get("pv_total")),
            }
            for t in tops
        ]
        d["matched_games"] = match_games(d["title"], profiles)
        topic_dig.append(d)
    topic_dig.sort(key=lambda d: d["interaction_total"], reverse=True)

    # ---------- S5 发现流生态 ----------
    disc = [p for p in posts if p.get("source_type") == "discover"]
    games_counter: dict[str, int] = {}
    for p in disc:
        g = (p.get("app_title") or "（未标注来源）").strip()
        games_counter[g] = games_counter.get(g, 0) + 1
    games_top = sorted(games_counter.items(), key=lambda kv: kv[1], reverse=True)[:10]
    delisted = games_counter.get(EVENT_RULES["delisted_game_label"], 0)
    inter_top = [
        {
            "summary": (p.get("title") or p.get("summary") or "")[:60],
            "app_title": p.get("app_title", ""),
            "comments": _int(p.get("comments")),
            "ups": _int(p.get("ups")),
            "pv_total": _int(p.get("pv_total")),
            "interaction_score": interaction_score(p),
        }
        for p in sorted(disc, key=interaction_score, reverse=True)[:10]
    ]

    # ---------- 事件信号（规则） ----------
    signals: list[dict[str, Any]] = []
    for h in hot_board:
        s = h["sort"]
        pv = h["page_view"]
        if (s and s <= EVENT_RULES["hot_board_top_n"]) or pv >= EVENT_RULES["hot_board_pv"]:
            signals.append({
                "kind": "hot_topic",
                "title": h["title"],
                "score": pv,
                "evidence": f"热榜第 {s} 名 / 浏览 {pv}",
                "matched_games": h["matched_games"],
            })
    for d in topic_dig:
        if d["interaction_total"] >= EVENT_RULES["topic_dig_interaction"]:
            signals.append({
                "kind": "topic_dig_heat",
                "title": d["title"],
                "score": d["interaction_total"],
                "evidence": f"话题下 {d['posts']} 帖 / 评论 {d['comments_declared']} / 浏览 {d['pv_total']}",
                "matched_games": d["matched_games"],
            })
    signals.sort(key=lambda s: s["score"], reverse=True)

    n_loaded_comments = len(comments)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "scope": "platform",
        "inputs": {
            "hot_hashtags": len(tags),
            "posts": len(posts),
            "posts_discover": len(disc),
            "posts_hashtag": len(posts) - len(disc),
            "loaded_comments": n_loaded_comments,
        },
        "hot_board": hot_board,
        # 实测：S4 的 comment_count 多数话题回填为 0（接口侧未统计），热度排序以 page_view 为准
        "board_note": "S4 的 comment_count 实测多为 0（接口未回填），话题热度排序以 page_view 为准；"
                      "评论热度要看 S6 深挖后的 comments_declared",
        "topic_dig": topic_dig,
        "discover_ecosystem": {
            "posts": len(disc),
            "games_top": [{"game": g, "posts": c} for g, c in games_top],
            "delisted_game_posts": delisted,
            "delisted_note": "TapTap 对下架游戏的展示名；下架游戏仍在产生 UGC 属平台生态事实，分析时勿当成正常在运营游戏",
            "interaction_top": inter_top,
        },
        "event_signals": signals[:10],
        "event_rules": EVENT_RULES,
        "caliber": "平台级发现流（S4 话题热榜 + S5 发现页跨游戏流 + S6 话题下帖子），非单游戏社区；样本为推荐流采样，不代表全站全量",
    }


def render_report(p: dict[str, Any]) -> str:
    inp = p["inputs"]
    lines = [
        "# 平台级发现流洞察",
        "",
        f"> 生成：{p['generated_at']} · {p['caliber']}",
        "",
        f"- 话题热榜 **{inp['hot_hashtags']}** 条；发现流帖子 **{inp['posts']}** 条（S5 发现页 {inp['posts_discover']} / S6 话题下 {inp['posts_hashtag']}）；评论 {inp['loaded_comments']} 条",
        "",
        "## 事件信号（规则判定，非模型主观）",
        "",
        "| # | 类型 | 话题 | 热度 | 依据 | 命中建档游戏 |",
        "|---|------|------|------|------|--------------|",
    ]
    if p["event_signals"]:
        for i, s in enumerate(p["event_signals"], 1):
            mg = "/".join(s["matched_games"]) or "—"
            lines.append(f"| {i} | {s['kind']} | {s['title'][:24]} | {s['score']} | {s['evidence']} | {mg} |")
    else:
        lines.append("| — | — | 未触发阈值 | — | — | — |")

    lines += [
        "",
        "## 话题热榜（S4）",
        "",
        f"> {p['board_note']}",
        "",
        "| # | 话题 | 浏览 | 评论 | 命中游戏 |",
        "|---|------|------|------|----------|",
    ]
    for h in p["hot_board"]:
        mg = "/".join(h["matched_games"]) or "—"
        lines.append(f"| {h['sort']} | {h['title'][:26]} | {h['page_view']} | {h['comment_count']} | {mg} |")

    if p["topic_dig"]:
        lines += [
            "",
            "## 话题深挖（S6：热榜 → 话题下帖子）",
            "",
            "| 话题 | 帖数 | 评论 | 浏览 | 互动总量 | 代表帖 |",
            "|------|------|------|------|----------|--------|",
        ]
        for d in p["topic_dig"]:
            top = d["top_posts"][0]["summary"] if d["top_posts"] else "—"
            lines.append(
                f"| {d['title'][:22]} | {d['posts']} | {d['comments_declared']} | {d['pv_total']} | "
                f"{d['interaction_total']} | {top[:28]} |"
            )

    eco = p["discover_ecosystem"]
    lines += [
        "",
        "## 发现流生态（S5）",
        "",
        f"- 发现页帖子 {eco['posts']} 条；其中「{EVENT_RULES['delisted_game_label']}」{eco['delisted_game_posts']} 条（下架游戏残留 UGC，勿按在运营游戏解读）",
        "",
        "| 来源游戏 | 帖数 |",
        "|----------|------|",
    ]
    for g in eco["games_top"]:
        lines.append(f"| {g['game']} | {g['posts']} |")

    lines += [
        "",
        "| 帖子 | 来源 | 评论 | 赞 | 浏览 |",
        "|------|------|------|----|------|",
    ]
    for t in eco["interaction_top"][:8]:
        lines.append(f"| {t['summary'][:30] or '（无题）'} | {t['app_title'][:10]} | {t['comments']} | {t['ups']} | {t['pv_total']} |")

    lines += ["", "---", "", "*报告结束*", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="平台级发现流洞察（S4/S5/S6 → facts）")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    tags = load_csv(data_dir / "hot_hashtags.csv")
    posts = load_csv(data_dir / "discovery_posts.csv")
    comments = load_csv(data_dir / "discovery_comments.csv")
    if not tags and not posts:
        print(f"[stop] 无发现流数据：{data_dir}（先跑 L1_data_source/collectors/taptap/crawl_taptap_discovery.py）", file=sys.stderr)
        return

    payload = analyze(tags, posts, comments, load_profiles())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    jout = OUT_DIR / "platform_insight.json"
    mout = REPORT_DIR / "platform_insight_latest.md"
    jout.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    mout.write_text(render_report(payload), encoding="utf-8")
    print(jout)
    print(mout)


if __name__ == "__main__":
    main()
