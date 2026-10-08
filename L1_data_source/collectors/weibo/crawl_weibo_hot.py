#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/weibo/crawl_weibo_hot.py — 微博热搜榜采集。

★ 为什么是它（2026-10-05 全网热点追踪立项）：
  微博热搜是**破圈话题最早出现的地方** —— 玩家还没在任何游戏社区讨论，
  微博可能已经在推了。对 TapTap 来说，微博涨了而站内没动静 = 早期信号。

★ 接口（2026-10-05 实测可用）：
  GET https://weibo.com/ajax/side/hotSearch
    · **不需要微博登录**：任意登录态 cookie 即可（实测百度 cookie 也能过，
      服务端只校验"有登录态"，不校验是哪个站）
    · 无 cookie 时 403（这是它唯一的门槛）
  返回 data.realtime[]：word / num(热度) / rank / label_name(新/热/沸/爆)
  另有 data.hotgov（置顶政府话题）与 band_list（广告位），都要过滤掉。

★ 关于「趋势指数」：
  微博网页端没有公开的趋势指数 API。可获得的最接近信号是
  **跨轮次热度变化率**（本模块自己算，见 hot_search.csv 的 trend_rate）
  + label_name 的「新」标记（= 该词是本轮新进榜）。
  真要官方趋势指数需要非公开接口，这里如实标注，不假装拿到。

用法：
  python crawl_weibo_hot.py --watch --interval 600
  python crawl_weibo_hot.py --games-only

Cookie 放环境变量 WEIBO_COOKIE（见 README），
或放在 L1_data_source/collectors/weibo/.env（已 gitignore）。

输出（data/raw/weibo/）：
  hot_search.csv    热搜快照（时刻, 排名, 词, 热度, 变化率, 新/热/沸/爆）
  crawl_runs.jsonl
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
CRAWLER_DIR = Path(__file__).resolve().parent
TZ_CN = timezone(timedelta(hours=8))

HOT_SEARCH_URL = "https://weibo.com/ajax/side/hotSearch"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

FIELDS = ["observed_at", "rank", "word", "hot_score", "prev_hot_score",
          "hot_score_delta", "trend_rate", "is_new", "label", "category",
          "url", "raw_hot"]

# 广告/政务/辟谣位：不是内容热点，采了会污染信号
JUNK_WORDS = ("广告", "推广", "商务", "合作")


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def load_cookie() -> str:
    """从环境变量或本地 .env 取登录态。"""
    try:
        from dotenv import load_dotenv
        load_dotenv(CRAWLER_DIR / ".env")
    except ImportError:
        p = CRAWLER_DIR / ".env"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "WEIBO_COOKIE":
                    os.environ.setdefault(k.strip(), v.strip())
    return os.environ.get("WEIBO_COOKIE", "").strip()







class WeiboHotSearch:
    def __init__(self, cookie: str = "", sleep: float = 1.0) -> None:
        self.cookie = cookie
        self.sleep = sleep
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://weibo.com/",
            "X-Requested-With": "XMLHttpRequest",
        })
        if cookie:
            self.s.headers["Cookie"] = cookie

    def auth_check(self) -> Dict[str, Any]:
        """登录态自检（无 cookie 会 403）。"""
        try:
            r = self.s.get(HOT_SEARCH_URL, timeout=15)
        except Exception as e:
            return {"ok": False, "reason": f"{type(e).__name__}"}
        if r.status_code != 200:
            return {"ok": False, "reason": f"HTTP {r.status_code}",
                    "advice": "微博热搜需要登录态 cookie，请配置 WEIBO_COOKIE"}
        return {"ok": True, "cookie": bool(self.cookie)}

    def fetch(self) -> List[Dict[str, Any]]:
        r = self.s.get(HOT_SEARCH_URL, timeout=15)
        r.raise_for_status()
        j = r.json()
        data = j.get("data") or {}
        out: List[Dict[str, Any]] = []
        for it in (data.get("realtime") or []):
            word = (it.get("word") or "").strip()
            if not word:
                continue
            if any(b in word for b in JUNK_WORDS):
                continue
            out.append(it)
        return out

    def collect(self, out_dir: Path, games_only: bool = False) -> Dict[str, Any]:
        items = self.fetch()
        stamp = now_iso()
        prev = self._last_scores(out_dir / "hot_search.csv")
        rows, game_hits, new_words = [], [], []
        for i, it in enumerate(items):
            word = it["word"]
            score = int(it.get("num") or 0)
            p = prev.get(word)
            delta = (score - p) if p is not None else None
            rate = round(delta / p * 100, 2) if (delta is not None and p) else None
            label = (it.get("label_name") or "").strip()
            related = is_game_related(word)
            if games_only and not related:
                continue
            if related:
                game_hits.append(word)
            is_new = bool(label == "新")
            if is_new:
                new_words.append(word)
            rows.append({
                "observed_at": stamp, "rank": it.get("rank") or i + 1, "word": word,
                "hot_score": score,
                "prev_hot_score": p if p is not None else "",
                "hot_score_delta": delta if delta is not None else "",
                "trend_rate": rate if rate is not None else "",
                "is_new": "true" if is_new else "false",
                "label": label,
                "category": it.get("category") or "",
                "url": f"https://s.weibo.com/weibo?q=%23{word}%23",
                "raw_hot": it.get("raw_hot") or "",
            })
        self._append_csv(out_dir / "hot_search.csv", FIELDS, rows)
        self._log(out_dir / "crawl_runs.jsonl",
                  {"observed_at": stamp, "total": len(items), "kept": len(rows),
                   "game_related": len(game_hits), "new_words": len(new_words)})
        return {"observed_at": stamp, "total": len(items), "kept": len(rows),
                "game_related": len(game_hits), "game_words": game_hits[:15],
                "new_words": new_words[:15],
                "comparable": sum(1 for r in rows if r["trend_rate"] != ""),
                "rising": [{"word": r["word"], "rate": r["trend_rate"]}
                           for r in sorted((x for x in rows if x["trend_rate"] != ""),
                                           key=lambda x: -x["trend_rate"])[:8]]}

    @staticmethod
    def _last_scores(path: Path, min_gap_min: int = 3) -> Dict[str, int]:
        """上一轮（≥3 分钟前）的 词→热度。

        ★ 几秒内连采两次不算一轮 —— 那会算出"掉榜 51 条/新进 51 条"的假变化。
        """
        if not path.exists():
            return {}
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
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
            return {}
        buckets: Dict[str, int] = {}
        for r in rows:
            if r["observed_at"] == prev_ts:
                try:
                    buckets[r["word"]] = int(r["hot_score"] or 0)
                except ValueError:
                    pass
        return buckets

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
# ★ 游戏词表统一由 collectors/game_terms.py 提供（2026-10-05）：
#   六个采集器原来各写一份 load_game_terms()，彼此不一致 —— 百度认得的游戏
#   微博未必认得。改成共用一份，改词表全网一次生效。
# game_terms.py 在 collectors/ 根下，本采集器在子目录 → 用 parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from game_terms import get_matcher  # noqa: E402

_MATCHER = get_matcher()
GAME_TERMS = sorted(_MATCHER.game_terms | _MATCHER.industry_terms
                     | _MATCHER.own_markers)


def is_game_related(text: str) -> bool:
    return _MATCHER.is_game_related(text)


def main() -> int:
    ap = argparse.ArgumentParser(description="微博热搜榜采集（全网破圈话题的早期信号）")
    ap.add_argument("--out-dir", default="data/raw/weibo")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=600)
    ap.add_argument("--rounds", type=int, default=0)
    ap.add_argument("--games-only", action="store_true")
    ap.add_argument("--auth-check", action="store_true", help="只检查登录态")
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    c = WeiboHotSearch(load_cookie(), args.sleep)

    chk = c.auth_check()
    print(f"[auth] {'可用' if chk['ok'] else '不可用：' + str(chk.get('reason'))}"
          f"{'（带 cookie）' if chk.get('cookie') else '（无 cookie）'}", flush=True)
    if not chk["ok"]:
        print("[hint] 微博热搜需要登录态：配置环境变量 WEIBO_COOKIE 后重试", file=sys.stderr)
        return 3
    if args.auth_check:
        return 0

    rounds = 0
    if not args.watch:
        return 0
    # ★ 健壮循环：异常不外溢 + 退避重试（2026-10-05 事故：
    #   一次 ConnectionReset 就让进程死掉，7 小时零采集且无人察觉）
    # robust_watch.py 在 collectors/ 根下，本采集器在子目录 → 父级路径
    _here = Path(__file__).resolve().parent
    for _p in (_here, _here.parent):
        if _p not in sys.path:
            sys.path.insert(0, str(_p))
    from robust_watch import run_forever
    return run_forever(
        name="weibo",
        fn=lambda: c.collect(out_dir, args.games_only),
        interval=args.interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())