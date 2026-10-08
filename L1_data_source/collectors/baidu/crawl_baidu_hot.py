#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/baidu/crawl_baidu_hot.py — 百度热搜榜采集。

★ 为什么是它（2026-10-04 全网热点追踪立项）：
  全网热点追踪里，搜索意图是最早的信号之一 —— 微博/抖音是"讨论起来了"，
  百度是"有人在搜了"。两者时间上互补，而且百度**无需登录、无风控**，
  是成本最低的广度雷达。

★ 能拿到什么（实测 2026-10-04）：
  top.baidu.com/api/board?platform=pc&tab=realtime
    word       热搜词
    hotScore   绝对热度值（百万级）
    hotChange  热度变化率 —— ★趋势信号，比绝对值更能说明"正在升温"
    index      排名
    desc       描述（常含游戏名/IP 名，是游戏相关性判断的主要输入）
    hotTag     0=新 1=热 2=沸 3=爆（百度官方分级）

用法：
  python crawl_baidu_hot.py --out-dir data/raw/baidu
  python crawl_baidu_hot.py --watch --interval 600        # 常驻，每 10 分钟一轮
  python crawl_baidu_hot.py --watch --games-only          # 只留游戏相关（词表粗筛）

输出：
  hot_search.csv        热搜快照（时刻, 排名, 词, 热度, 变化率, 标签, 描述）
  hot_changes.csv       增量（只有两次快照之间**新进榜**的词才记录）
  crawl_runs.jsonl      每轮运行记录
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
BOARD_URL = "https://top.baidu.com/api/board"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

SNAPSHOT_FIELDS = [
    "observed_at", "tab", "rank", "word", "hot_score", "hot_change",
    "hot_tag", "desc", "url", "query",
    # ★ 跨轮派生：与上一次快照比出来的热度变化率（百度自己的 hotChange 只是方向）
    "prev_hot_score", "hot_score_delta", "trend_rate",
]
TREND_DIR = {"up": "↑升", "down": "↓降", "same": "→平", "": ""}
# ★ 游戏相关性粗筛词表（来自 games/*.json 的 name + aliases，见 load_game_terms）
TAG_ZH = {0: "新", 1: "热", 2: "沸", 3: "爆"}


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")







class BaiduHotSearch:
    def __init__(self, sleep: float = 1.0) -> None:
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://top.baidu.com/board",
        })
        self.sleep = sleep

    def fetch(self, tab: str = "realtime") -> List[Dict[str, Any]]:
        """抓一档热搜榜。返回条目列表（word/hotScore/hotChange/...）。"""
        r = self.s.get(BOARD_URL,
                       params={"platform": "pc", "tab": tab},
                       timeout=15)
        r.raise_for_status()
        j = r.json()
        out: List[Dict[str, Any]] = []

        def walk(o: Any) -> None:
            if isinstance(o, list):
                for x in o:
                    walk(x)
            elif isinstance(o, dict):
                if "word" in o and ("hotScore" in o or "index" in o):
                    out.append(o)
                else:
                    for v in o.values():
                        walk(v)
        walk(j)
        return out

    def collect(self, out_dir: Path, tab: str = "realtime",
                games_only: bool = False) -> Dict[str, Any]:
        items = self.fetch(tab)
        stamp = now_iso()
        prev = self._last_scores(out_dir / "hot_search.csv", tab)
        rows: List[Dict[str, Any]] = []
        game_hits: List[str] = []
        rising: List[Dict[str, Any]] = []
        for it in items:
            word = (it.get("word") or "").strip()
            if not word:
                continue
            desc = (it.get("desc") or "").strip()
            # ★ tab=game 时整榜都是游戏，无需再粗筛
            related = is_game_related(f"{word} {desc}") or tab == "game"
            if games_only and not related:
                continue
            if related:
                game_hits.append(word)
            score = int(it.get("hotScore") or 0)
            p_score = prev.get(word)
            delta = (score - p_score) if p_score is not None else None
            rate = (delta / p_score * 100) if (delta is not None and p_score) else None
            rows.append({
                "observed_at": stamp, "tab": tab,
                "rank": it.get("index") or 0, "word": word,
                "hot_score": score,
                "hot_change": TREND_DIR.get(it.get("hotChange") or "", ""),
                "hot_tag": TAG_ZH.get(it.get("hotTag") or 0, ""),
                "desc": desc[:200], "url": it.get("url") or "",
                "query": it.get("query") or "",
                "prev_hot_score": p_score if p_score is not None else "",
                "hot_score_delta": delta if delta is not None else "",
                "trend_rate": round(rate, 2) if rate is not None else "",
            })
            # 上升最快的前几条 = 正在升温的候选
            if rate is not None and rate > 0:
                rising.append({"word": word, "rate": round(rate, 1),
                               "score": score, "desc": desc[:40]})
        self._append_csv(out_dir / "hot_search.csv", SNAPSHOT_FIELDS, rows)
        self._log_run(out_dir, stamp, tab, len(items), len(rows), len(game_hits))
        rising.sort(key=lambda x: -x["rate"])
        return {"observed_at": stamp, "tab": tab, "total": len(items),
                "kept": len(rows), "game_related": len(game_hits),
                "game_words": game_hits[:20],
                "comparable": sum(1 for r in rows if r["trend_rate"] != ""),
                "rising": rising[:10]}

    @staticmethod
    def _last_scores(path: Path, tab: str, min_gap_min: int = 3) -> Dict[str, int]:
        """读**同一 tab** 的上一轮快照（词→热度），用于算变化率。

        ★ 两个坑（2026-10-05 实测踩到）：
          ① 不能跨 tab 比 —— realtime 和 novel 混在一起比会得到
             "掉榜 51 条 / 下一轮新进 51 条"的假变化；
          ② 不能和几秒前的采样比 —— 那不是一轮，等于自己和自己比。
             所以要求与最新一轮至少相隔 min_gap_min 分钟，否则返回空（首轮不算变化）。
        """
        if not path.exists():
            return {}
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("tab") == tab]
        if not rows:
            return {}
        times = sorted({r["observed_at"] for r in rows})
        last_ts = times[-1]
        prev_ts = None
        for t in reversed(times[:-1]):
            try:
                gap = (datetime.fromisoformat(last_ts) - datetime.fromisoformat(t)).total_seconds() / 60
            except ValueError:
                continue
            if gap >= min_gap_min:
                prev_ts = t
                break
        if prev_ts is None:
            return {}          # 没有"上一轮"（首轮或间隔太短）→ 不给变化率
        buckets: Dict[str, int] = {}
        for r in rows:
            if r["observed_at"] == prev_ts:
                try:
                    buckets[r["word"]] = int(r["hot_score"] or 0)
                except ValueError:
                    pass
        return buckets

    # ---------- 落盘 ----------
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
    def _log_run(out_dir: Path, stamp: str, tab: str, total: int,
                 kept: int, game_n: int) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "crawl_runs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"observed_at": stamp, "tab": tab, "total": total,
                                "kept": kept, "game_related": game_n},
                               ensure_ascii=False) + "\n")

    @staticmethod
    def new_entries(prev_csv: Path, words_now: List[str]) -> List[str]:
        """返回"本次新进榜"的词（与上一轮快照做差）—— 新词比在榜老词更值得看。"""
        if not prev_csv.exists():
            return list(words_now)
        last_ts, last_words = "", set()
        with prev_csv.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:                       # 最后一段时间戳的集合
            if r["observed_at"] >= last_ts:
                last_ts = r["observed_at"]
        for r in rows:
            if r["observed_at"] == last_ts:
                last_words.add(r["word"])
        return [w for w in words_now if w not in last_words]


def run_watch(c: BaiduHotSearch, out_dir: Path, tabs: List[str],
              interval: int, games_only: bool, rounds: int = 0) -> int:
    """常驻轮询。interval 秒一轮，rounds=0 表示无限。

    ★ 双层保护：
      内层 —— 单个 tab 失败不中断（原来就有）
      外层 —— 整轮失败也不外溢，交给 robust_watch 退避重试
             （2026-10-05 事故：weibo 采集器一次 ConnectionReset 就死，7 小时零采集）
    """
    def one_round() -> Dict[str, Any]:
        out = {}
        for tab in tabs:
            try:
                r = c.collect(out_dir, tab, games_only)
                out[tab] = {"total": r["total"], "kept": r["kept"],
                            "game_related": r["game_related"]}
                print(f"[{r['observed_at']}] tab={tab} 共{r['total']} 保留{r['kept']} "
                      f"游戏相关{r['game_related']}", flush=True)
                if r["game_words"]:
                    print(f"    游戏话题: {', '.join(r['game_words'][:8])}", flush=True)
            except Exception as e:          # 单档失败不中断整轮
                print(f"[error] tab={tab} {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
                out[tab] = {"error": str(e)[:80]}
            time.sleep(c.sleep)
        return out

    if rounds and rounds > 0:               # 有限轮次：直接跑，不套 watch
        for _ in range(rounds):
            one_round()
            time.sleep(max(10, interval))
        return 0

    # robust_watch.py 在 collectors/ 根下，本采集器在子目录 → 父级路径
    _here = Path(__file__).resolve().parent
    for _p in (_here, _here.parent):
        if _p not in sys.path:
            sys.path.insert(0, str(_p))
    from robust_watch import run_forever
    return run_forever(name="baidu", fn=one_round, interval=interval)
# ★ 游戏词表统一由 collectors/game_terms.py 提供（2026-10-05）：
#   六个采集器原来各写一份 load_game_terms()，彼此不一致 —— 百度认得的游戏
#   微博未必认得。改成共用一份（AC 自动机，2000+ 游戏名一次扫描），改词表全网一次生效。
# game_terms.py 在 collectors/ 根下，本采集器在子目录 → 用 parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from game_terms import get_matcher  # noqa: E402

_MATCHER = get_matcher()
GAME_TERMS = sorted(_MATCHER.game_terms | _MATCHER.industry_terms
                     | _MATCHER.own_markers)


def is_game_related(text: str) -> bool:
    return _MATCHER.is_game_related(text)


def main() -> int:
    ap = argparse.ArgumentParser(description="百度热搜榜采集（全网热点的搜索意图信号）")
    ap.add_argument("--out-dir", default="data/raw/baidu_index",
                    help="输出目录（默认与平台名一致，否则注册表按平台拼路径读不到）")
    ap.add_argument("--tabs", default="realtime,game",
                    help="榜单档：realtime=综合热搜 game=游戏热搜(★垂直信号源) novel/movie/car/health=其他")
    ap.add_argument("--watch", action="store_true", help="常驻轮询")
    ap.add_argument("--interval", type=int, default=600, help="轮询间隔秒")
    ap.add_argument("--rounds", type=int, default=0, help="跑几轮（0=无限）")
    ap.add_argument("--games-only", action="store_true", help="只保留游戏相关（词表粗筛）")
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    c = BaiduHotSearch(args.sleep)
    tabs = [t.strip() for t in args.tabs.split(",") if t.strip()]

    if args.watch:
        return run_watch(c, out_dir, tabs, args.interval, args.games_only, args.rounds)

    for tab in tabs:
        r = c.collect(out_dir, tab, args.games_only)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())