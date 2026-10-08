#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/tieba/crawl_tieba_hot.py — 贴吧热点话题采集。

★ 接口（用户 2026-10-05 提供，已实测）：
  https://tieba.baidu.com/hottopic/browse/topicList?res_type=1
  → 服务端渲染，页面里有 topic_id + topic_name（URL 编码）
  → **无需登录、无 cookie**（对比：单游戏吧页面 /f?kw=xxx 无 cookie 时 403）

★ 为什么是它：
  贴吧热点是**论坛体裁的真实讨论**（不是媒体报道、不是搜索意图）。
  「369打瓦被喷，撸友一致对外」这种就是玩家间的争议发酵，
  和 TapTap 社区的讨论结构最接近 —— 可直接喂给相关性判定和创意生成。

★ 已知边界：
  - 主榜 30 条，游戏/电竞占比约 2/30（泛论坛，游戏内容需词表过滤）
  - 分类 tab（页面有"游戏"分类）是前端控制，静态抓取拿不到独立榜
  - res_type=2 返回同批内容，按 topic_id 去重即可

用法：
    python crawl_tieba_hot.py                       # 单轮
    python crawl_tieba_hot.py --watch --interval 600 # 常驻（10 分钟）

输出：data/raw/tieba/hot_topics.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote

import requests

ROOT = Path(__file__).resolve().parents[3]
TZ_CN = timezone(timedelta(hours=8))
OUT_DIR = ROOT / "data" / "raw" / "tieba"

TOPIC_LIST_URL = "https://tieba.baidu.com/hottopic/browse/topicList"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "Chrome/138.0.0.0 Safari/537.36",
      "Referer": "https://tieba.baidu.com/"}

FIELDS = ["observed_at", "rank", "topic_id", "topic_name", "url",
           "game_related", "matched_game", "is_new"]


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")






# ★ 游戏词表统一由 collectors/game_terms.py 提供（2026-10-05）：
#   六个采集器原来各写一份 load_game_terms()，彼此不一致 —— 百度认得的游戏
#   微博未必认得。改成共用一份（AC 自动机，2000+ 游戏名一次扫描），改词表全网一次生效。
# game_terms.py 在 collectors/ 根下，本采集器在子目录 → 用 parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from game_terms import get_matcher  # noqa: E402

_MATCHER = get_matcher()
def match_game(text: str) -> str:
    hits = _MATCHER.matched_games(text)
    return hits[0] if hits else ""


def fetch(res_type: int = 1) -> List[Dict[str, Any]]:
    try:
        r = requests.get(TOPIC_LIST_URL, params={"res_type": res_type},
                         headers=UA, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[warn] 贴吧热点抓取失败：{type(e).__name__}: {str(e)[:60]}", file=sys.stderr)
        return []
    hits = re.findall(r"hottopic\?topic_id=(\d+)&amp;topic_name=([^&\"]+)", r.text)
    seen, out = set(), []
    for tid, tname in hits:
        if tid in seen:
            continue
        seen.add(tid)
        name = unquote(tname)
        out.append({"topic_id": tid, "topic_name": name,
                    "url": f"https://tieba.baidu.com/hottopic/browse/hottopic?topic_id={tid}"})
    return out


def collect(games_only: bool = False) -> Dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()
    items = fetch(res_type=1) + fetch(res_type=2)

    # 按 topic_id 去重（两个 res_type 返回同批）
    uniq: Dict[str, Dict[str, Any]] = {}
    for it in items:
        uniq.setdefault(it["topic_id"], it)

    path = OUT_DIR / "hot_topics.csv"
    known = set()
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            known = {r["topic_id"] for r in csv.DictReader(f)}

    rows, new_n = [], 0
    for i, (tid, it) in enumerate(uniq.items(), 1):
        g = match_game(it["topic_name"])
        if games_only and not g:
            continue
        is_new = tid not in known
        rows.append({"observed_at": stamp, "rank": i, "topic_id": tid,
                     "topic_name": it["topic_name"], "url": it["url"],
                     "game_related": "true" if g else "false",
                     "matched_game": g, "is_new": "true" if is_new else "false"})
        if is_new:
            new_n += 1

    if rows:
        with path.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            if not path.exists() or not known:
                w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})

    return {"observed_at": stamp, "fetched": len(uniq), "saved": len(rows),
            "game_related": sum(1 for r in rows if r["game_related"] == "true"),
            "new_topics": new_n}
# ★ 游戏词表统一由 collectors/game_terms.py 提供（2026-10-05）：
#   六个采集器原来各写一份 load_game_terms()，彼此不一致 —— 百度认得的游戏
#   微博未必认得。改成共用一份，改词表全网一次生效。
from game_terms import get_matcher  # noqa: E402

_MATCHER = get_matcher()
GAME_TERMS = sorted(_MATCHER.game_terms | _MATCHER.industry_terms
                     | _MATCHER.own_markers)


def is_game_related(text: str) -> bool:
    return _MATCHER.is_game_related(text)


def main() -> int:
    ap = argparse.ArgumentParser(description="贴吧热点话题采集（无需登录）")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=600)
    ap.add_argument("--games-only", action="store_true")
    args = ap.parse_args()

    rounds = 0
    while True:
        r = collect(args.games_only)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        rounds += 1
        if not args.watch:
            return 0
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
