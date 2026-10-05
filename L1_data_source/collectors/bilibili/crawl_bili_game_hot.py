#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/bilibili/crawl_bili_game_hot.py — B站游戏区/全站热门采集。

★ 为什么（2026-10-04 全网热点追踪立项）：
  B站在"创作者开始生产"这一环上，是游戏内容最集中的地方（实况、攻略、二创、
  测评、鬼畜）。社区热点在 B站往往先有"内容化"的信号，再回流到讨论区。

★ 能拿到什么（实测 2026-10-05）：
  /x/web-interface/popular?ps=30          全站热门（信号：什么在被大量看）
  /x/web-interface/ranking/v2?rid=4       游戏区排行 96 条（游戏内容主阵地）
  /x/web-interface/search/type（WBI 签名）  按游戏名/关键词搜（外延发现）
  ⚠ ranking/v2 与 popular 无需登录；search 需要 WBI 签名（复用 crawl_bili_comments 的实现）。

用法：
  python crawl_bili_game_hot.py --sources ranking,popular
  python crawl_bili_game_hot.py --sources search --keywords 明日方舟,原神
  python crawl_bili_game_hot.py --watch --interval 900          # 常驻 15 分钟一轮

输出（data/raw/bilibili/）：
  hot_videos.csv       热门/排行视频快照（时刻, 分区, 标题, 播放/点赞/评论/UP主）
  video_snapshots.csv  按 bvid 的跨轮计数序列（增速 = 正在升温）
  crawl_runs.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
TZ_CN = timezone(timedelta(hours=8))

API = "https://api.bilibili.com"
POPULAR_URL = f"{API}/x/web-interface/popular"
RANKING_URL = f"{API}/x/web-interface/ranking/v2"
RANKING_WBI_URL = f"{API}/x/web-interface/wbi/ranking/v2"   # 需 WBI 签名（-352 = 未签名）
SEARCH_URL = f"{API}/x/web-interface/search/type"

# 游戏区 rid（B站分区）
RID_GAME = 4

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

VIDEO_FIELDS = [
    "observed_at", "source", "rid", "rank", "title", "bvid", "aid", "author", "url",
    "play", "danmaku", "like", "reply", "favorite", "coin", "share", "pubdate",
    "description", "duration",
]
SNAPSHOT_FIELDS = ["bvid", "observed_at", "play", "like", "reply", "danmaku",
                   "prev_play", "play_delta", "play_rate"]


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def _n(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def load_game_terms() -> List[str]:
    """游戏名 + 别名（与百度采集器同源，用于游戏相关性标记）。"""
    terms: set[str] = set()
    gdir = ROOT / "games"
    if gdir.is_dir():
        for fn in gdir.glob("*.json"):
            try:
                d = json.loads(fn.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for k in ("name", "key"):
                if d.get(k):
                    terms.add(str(d[k]).lower())
            for a in (d.get("aliases") or []):
                if a:
                    terms.add(str(a).lower())
    return sorted(terms, key=len, reverse=True)


GAME_TERMS = load_game_terms()


def guess_game(title: str, desc: str = "") -> str:
    """标题/简介里命中的游戏名（长词优先）。命中不了返回空。"""
    low = f"{title} {desc}".lower()
    for t in GAME_TERMS:
        if t in low:
            return t
    return ""


class BiliHot:
    def __init__(self, sleep: float = 1.0, use_wbi: bool = True) -> None:
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        self.sleep = sleep
        self.sleep_min, self.sleep_max = max(0.3, sleep), max(1.0, sleep + 0.5)
        self._wbi_error = ""
        self._crawler = None
        if use_wbi:
            try:
                from crawl_bili_comments import BiliClient, DEFAULT_UA  # 复用 WBI 签名实现
                ua = os.environ.get("BILI_UA", DEFAULT_UA).strip() or DEFAULT_UA
                self._crawler = BiliClient(ua=ua, cookie=os.environ.get("BILI_COOKIE", "").strip(),
                                           sleep_min=self.sleep_min, sleep_max=self.sleep_max)
            except Exception as e:      # 拿不到就只跑 popular，并记下原因（静默吞异常会让人以为没有 cookie 问题）
                self._wbi_error = f"{type(e).__name__}: {str(e)[:120]}"
                print(f"[warn] WBI 客户端不可用：{self._wbi_error}", file=sys.stderr)
                self._crawler = None

    def _get(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        r = self.s.get(url, params=params, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get("code") != 0:
            raise RuntimeError(f"{url.rsplit('/', 1)[-1]} code={j.get('code')} {str(j.get('message'))[:60]}")
        return j.get("data") or {}

    # ---------- 三个源 ----------
    def popular(self, ps: int = 30) -> List[Dict[str, Any]]:
        d = self._get(POPULAR_URL, {"ps": ps, "pn": 1})
        return [v for v in (d.get("list") or []) if v.get("bvid")]

    def ranking_game(self) -> List[Dict[str, Any]]:
        """游戏区排行 96 条。

        ★ 实测（2026-10-05）：裸调返回 -352，**加上 WBI 签名参数后同一个路径就通**
          （不存在 wbi/ 前缀的独立路由）。没有 BiliClient 时退回裸调。
        """
        if self._crawler:
            data = self._crawler._get(
                RANKING_URL, self._crawler._encode_wbi({"rid": RID_GAME, "type": "all"}))
            if int(data.get("code", -1)) == 0:
                return [v for v in ((data.get("data") or {}).get("list") or []) if v.get("bvid")]
        d = self._get(RANKING_URL, {"rid": RID_GAME, "type": "all"})
        return [v for v in (d.get("list") or []) if v.get("bvid")]

    def search(self, keyword: str, page: int = 1) -> List[Dict[str, Any]]:
        if not self._crawler:
            return []
        try:
            return self._crawler.search_videos(keyword, page=page, page_size=20)
        except Exception:
            return []

    # ---------- 落盘 ----------
    @staticmethod
    def _row(v: Dict[str, Any], source: str, rid: int, stamp: str,
             rank: int = 0) -> Dict[str, Any]:
        title = str(v.get("title") or "").strip()
        desc = str(v.get("desc") or v.get("description") or "")[:200]
        stat = v.get("stat") or v
        return {
            "observed_at": stamp, "source": source, "rid": rid,
            "rank": v.get("rank") or rank,     # ★ 排名变化是热点形成的关键信号
            "title": title[:80],
            "bvid": v.get("bvid") or "", "aid": str(v.get("aid") or ""),
            "author": str((v.get("owner") or {}).get("name") or v.get("author") or "")[:30],
            "url": f"https://www.bilibili.com/video/{v.get('bvid')}" if v.get("bvid") else "",
            "play": _n(stat.get("view") or stat.get("play")),
            "danmaku": _n(stat.get("danmaku")),
            "like": _n(stat.get("like")),
            "reply": _n(stat.get("reply")),
            "favorite": _n(stat.get("favorite")),
            "coin": _n(stat.get("coin")),
            "share": _n(stat.get("share")),
            "pubdate": v.get("pubdate") or v.get("senddate") or "",
            "description": desc,
            "duration": str(v.get("duration") or ""),
        }

    def collect(self, out_dir: Path, sources: List[str],
                keywords: List[str]) -> Dict[str, Any]:
        stamp = now_iso()
        rows: List[Dict[str, Any]] = []
        got: Dict[str, int] = {}
        if "ranking" in sources:
            try:
                vids = self.ranking_game()
                rows += [self._row(v, "game_ranking", RID_GAME, stamp, i + 1)
                         for i, v in enumerate(vids)]
                got["game_ranking"] = len(vids)
            except Exception as e:
                got["game_ranking"] = f"失败:{str(e)[:40]}"
            time.sleep(self.sleep)
        if "popular" in sources:
            try:
                vids = self.popular()
                rows += [self._row(v, "popular", 0, stamp, i + 1)
                         for i, v in enumerate(vids)]
                got["popular"] = len(vids)
            except Exception as e:
                got["popular"] = f"失败:{str(e)[:40]}"
            time.sleep(self.sleep)
        if "search" in sources and keywords:
            for kw in keywords:
                vids = self.search(kw)
                rows += [self._row(v, f"search:{kw}", 0, stamp) for v in vids]
                got[f"search:{kw}"] = len(vids)
                time.sleep(self.sleep)

        rows = [r for r in rows if r["bvid"]]
        self._append_csv(out_dir / "hot_videos.csv", VIDEO_FIELDS, rows)
        snaps = self._write_snapshots(out_dir / "thread_snapshots.csv", rows, stamp)
        game_hits = sorted({r["title"][:24] for r in rows if guess_game(r["title"], r["description"])})
        self._log(out_dir / "crawl_runs.jsonl",
                  {"observed_at": stamp, "got": got, "kept": len(rows),
                   "game_related": len(game_hits)})
        return {"observed_at": stamp, "got": got, "kept": len(rows),
                "game_related": game_hits[:15], "rising_play": snaps}

    def _write_snapshots(self, path: Path, rows: List[Dict[str, Any]],
                         stamp: str) -> List[Dict[str, Any]]:
        """跨轮计数快照 + 播放增速（升温信号）。"""
        prev: Dict[str, int] = {}
        if path.exists():
            last_ts = ""
            with path.open(encoding="utf-8-sig", newline="") as f:
                hist = list(csv.DictReader(f))
            for r in hist:
                last_ts = max(last_ts, r.get("observed_at") or "")
            for r in hist:
                if r.get("observed_at") == last_ts:
                    prev[r["bvid"]] = _n(r.get("play"))
        out, rising = [], []
        for r in rows:
            p = prev.get(r["bvid"])
            delta = (r["play"] - p) if p is not None else None
            rate = round(delta / 3600, 1) if (delta and delta > 0) else None   # 播放/小时
            out.append({"bvid": r["bvid"], "observed_at": stamp, "play": r["play"],
                        "like": r["like"], "reply": r["reply"], "danmaku": r["danmaku"],
                        "prev_play": p if p is not None else "",
                        "play_delta": delta if delta is not None else "",
                        "play_rate": rate if rate is not None else ""})
            if rate:
                rising.append({"bvid": r["bvid"], "play_per_h": rate,
                               "title": r["title"][:30]})
        rising.sort(key=lambda x: -x["play_per_h"])
        self._append_csv(path, SNAPSHOT_FIELDS, out)
        return rising[:10]

    @staticmethod
    def _append_csv(path: Path, fields: List[str], rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if new:
                w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})

    @staticmethod
    def _log(path: Path, rec: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="B站游戏区/热门采集（创作者生产信号）")
    ap.add_argument("--out-dir", default="data/raw/bilibili")
    ap.add_argument("--sources", default="ranking,popular", help="ranking,popular,search")
    ap.add_argument("--keywords", default="明日方舟", help="search 时的关键词（逗号分隔）")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=900)
    ap.add_argument("--rounds", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    c = BiliHot(args.sleep)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    kws = [k.strip() for k in args.keywords.split(",") if k.strip()]

    rounds = 0
    while True:
        r = c.collect(out_dir, sources, kws)
        print(json.dumps(r, ensure_ascii=False, indent=2), flush=True)
        rounds += 1
        if not args.watch or (args.rounds and rounds >= args.rounds):
            return 0
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
