#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/gamemedia/crawl_gamemedia.py — 游戏媒体事件源。

★ 为什么（2026-10-05 发现）：
  全网通用热搜里游戏占比极低（百度游戏榜 30 条 / 微博 0 条），
  因为微博/抖音的热榜反映的是**大众话题**，游戏话题天然小众。
  但「游戏事件」有专门的垂直媒体 —— 它们的标题本身就是结构化事件：
    「《战争机器：事变日》因发售故障 向玩家发放15000金币」
    「多个游戏工作室获巨额投资后 扩大项目开发规模」
    「WF2026首次曝光INART《影之刃零》主角魂1:1雕像」
  这类内容比从通用热搜猜准得多，且**天然免过滤**（整站都是游戏）。

★ 三个已验证可采的源（2026-10-05 实测）：
  机核网   https://www.gcores.com/rss        ← RSS，最稳，20 条/次
  3DM 游戏网 https://www.3dmgame.com/news/  ← 网页解析，25-41 条/次
  游民星空  https://www.gamersky.com/news/   ← 网页解析，169 条/次（需显式 utf-8）
  游研社 / 米游社 / 篝火营地 —— 前端渲染或风控，暂不采（见 UNSUPPORTED）

★ 信号定位：**事件源（news）**，不是热度榜。
  它们回答「游戏行业正在发生什么」，不回答「玩家在讨论什么」。
  前者喂给创意生成的「机会识别」，后者喂给相关性判定。

用法：
    python crawl_gamemedia.py                    # 单轮
    python crawl_gamemedia.py --watch --interval 1800   # 常驻（30 分钟，媒体更新不频繁）
    python crawl_gamemedia.py --games-only        # 只留与关注的游戏相关

输出：data/raw/gamemedia/news.csv
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
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parents[3]
TZ_CN = timezone(timedelta(hours=8))
OUT_DIR = ROOT / "data" / "raw" / "gamemedia"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "Chrome/138.0.0.0 Safari/537.36"}

GCORES_RSS = "https://www.gcores.com/rss"
THREEDM_NEWS = "https://www.3dmgame.com/news/"
GAMERSKY_NEWS = "https://www.gamersky.com/news/"

FIELDS = ["observed_at", "source", "title", "summary", "url", "published_at",
           "game_related", "matched_game"]

UNSUPPORTED = {
    "游研社": "首页 200 但文章链接未匹配到（结构待摸清，可能是前端渲染）",
    "米游社": "米哈游旗下，仅米系内容，与 TapTap 全社区视角不匹配",
}


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
    """长词优先匹配（"崩坏：星穹铁道" 要先于 "崩坏" 命中）。"""
    hits = _MATCHER.matched_games(text)
    return hits[0] if hits else ""


# ---------------------------------------------------------------- 机核 RSS
def fetch_gcores() -> List[Dict[str, Any]]:
    try:
        r = requests.get(GCORES_RSS, headers=UA, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[warn] 机核 RSS 失败：{type(e).__name__}", file=sys.stderr)
        return []
    out = []
    for item in re.findall(r"<item>(.*?)</item>", r.text, re.DOTALL):
        def g(tag):
            m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>",
                          item, re.DOTALL)
            return m.group(1).strip() if m else ""
        title = re.sub(r"<[^>]+>", "", g("title")).strip()
        desc = re.sub(r"<[^>]+>", " ", g("description")).strip()[:200]
        if not title:
            continue
        out.append({"source": "gcores", "title": title, "summary": desc,
                    "url": g("link"), "published_at": g("pubDate")})
    return out


# ---------------------------------------------------------------- 3DM 新闻
def fetch_3dm() -> List[Dict[str, Any]]:
    try:
        r = requests.get(THREEDM_NEWS, headers=UA, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[warn] 3DM 失败：{type(e).__name__}", file=sys.stderr)
        return []
    import html as H
    hits = re.findall(
        r'<a[^>]+href="(https://www\.3dmgame\.com/news/\d{6}/\d+\.html)"[^>]*>(.{6,80}?)</a>',
        r.text, re.DOTALL)
    # CDN 偶尔返回旧缓存（2023 的页面），只要最近 3 个月的内容
    import datetime as _dt
    try:
        cutoff = _dt.datetime.now(_dt.timezone(timedelta(hours=8))).strftime("%Y%m")
        fresh_urls = {u for u in re.findall(r"/news/(\d{6})/\d+\.html", r.text)
                      if u >= cutoff}
    except Exception:
        fresh_urls = None
    out, seen = [], set()
    for url, title in hits:
        if fresh_urls is not None:
            m = re.search(r"/news/(\d{6})/", url)
            if m and m.group(1) not in fresh_urls:
                continue
        clean = H.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        if not clean or url in seen:
            continue
        # 过滤掉游戏分类导航链接（标题太短/像分类名）
        if len(clean) < 6 or clean.startswith("/news/"):
            continue
        seen.add(url)
        out.append({"source": "3dm", "title": clean, "summary": "",
                    "url": url, "published_at": ""})
    return out


# ---------------------------------------------------------------- 游民星空
def fetch_gamersky() -> List[Dict[str, Any]]:
    """游民星空新闻（2026-10-05 实测 169 条标题）。

    ⚠ 编码坑：页面是 UTF-8，requests 按 header 猜错成 latin-1/GBK 会乱码，
       这里显式指定 utf-8。
    """
    import html as H
    try:
        r = requests.get(GAMERSKY_NEWS, headers=UA, timeout=20)
        r.raise_for_status()
        r.encoding = "utf-8"           # ★ 必须显式设，否则中文乱码
    except Exception as e:
        print(f"[warn] 游民星空失败：{type(e).__name__}", file=sys.stderr)
        return []
    hits = re.findall(
        r'<a[^>]+href="[^"]*/news/(\d{6})/(\d+)\.shtml"[^>]*>(.{6,80}?)</a>',
        r.text, re.DOTALL)
    out, seen = [], set()
    for ym, nid, title in hits:
        url = f"https://www.gamersky.com/news/{ym}/{nid}.shtml"
        if url in seen:
            continue
        clean = H.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        if len(clean) < 6:
            continue
        seen.add(url)
        out.append({"source": "gamersky", "title": clean, "summary": "",
                    "url": url, "published_at": ym})
    return out


# ---------------------------------------------------------------- 落盘
def collect(games_only: bool = False) -> Dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()
    items = fetch_gcores() + fetch_3dm() + fetch_gamersky()
    for it in items:
        g = match_game(f"{it['title']} {it['summary']}")
        it["observed_at"] = stamp
        it["game_related"] = "true" if g else "false"
        it["matched_game"] = g
    if games_only:
        items = [i for i in items if i["game_related"] == "true"]

    path = OUT_DIR / "news.csv"
    new_file = not path.exists()
    # 去重：同一 URL 已存在则跳过
    seen_urls = set()
    if not new_file:
        with path.open(encoding="utf-8-sig", newline="") as f:
            seen_urls = {r.get("url") for r in csv.DictReader(f)}
    fresh = [i for i in items if i["url"] not in seen_urls]

    if fresh:
        with path.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            if new_file:
                w.writeheader()
            for r in fresh:
                w.writerow({k: r.get(k, "") for k in FIELDS})

    gc = [i for i in items if i["source"] == "gcores"]
    dm = [i for i in items if i["source"] == "3dm"]
    gs = [i for i in items if i["source"] == "gamersky"]
    return {
        "observed_at": stamp,
        "fetched": len(items),
        "gcores": len(gc), "3dm": len(dm), "gamersky": len(gs),
        "game_related": sum(1 for i in items if i["game_related"] == "true"),
        "new_saved": len(fresh),
        "unsupported": UNSUPPORTED,
    }
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
    ap = argparse.ArgumentParser(description="游戏媒体事件源（机核 RSS + 3DM 新闻）")
    ap.add_argument("--games-only", action="store_true", help="只留与游戏相关的")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=1800, help="常驻间隔秒（默认 30 分钟）")
    ap.add_argument("--rounds", type=int, default=0)
    args = ap.parse_args()

    rounds = 0
    while True:
        r = collect(args.games_only)
        print(json.dumps(r, ensure_ascii=False, indent=2), flush=True)
        rounds += 1
        if not args.watch or (args.rounds and rounds >= args.rounds):
            return 0
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
