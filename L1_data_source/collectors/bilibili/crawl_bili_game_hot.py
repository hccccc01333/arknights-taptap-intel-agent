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
TZ_CN = timezone(timedelta(hours=8))

API = "https://api.bilibili.com"
POPULAR_URL = f"{API}/x/web-interface/popular"
RANKING_URL = f"{API}/x/web-interface/ranking/v2"
RANKING_WBI_URL = f"{API}/x/web-interface/wbi/ranking/v2"   # 需 WBI 签名（-352 = 未签名）
SEARCH_URL = f"{API}/x/web-interface/search/type"

# 游戏区 rid（B站分区）
RID_GAME = 4

# ★ 投稿流监控的关键词（2026-10-05）：与贴吧 29 个游戏吧同源 ——
#   人工维护的"当下真有人在玩"名单。search 模式按 pubdate 倒序抓每轮最新投稿，
#   跨轮 bvid 差分 = 各游戏的投稿速率（B站榜单 stat 是缓存的，速率测不了，见 forming.py 注释）
DEFAULT_KEYWORDS = [
    "明日方舟", "鸣潮", "原神", "崩坏星穹铁道", "绝区零", "王者荣耀",
    "和平精英", "光遇", "恋与深空", "第五人格", "蛋仔派对", "我的世界",
    "英雄联盟", "金铲铲之战", "永劫无间", "三角洲行动", "燕云十六声",
    "碧蓝航线", "阴阳师", "炉石传说", "无期迷途", "少女前线", "无限暖暖",
    "明日方舟终末地", "暗区突围", "迷你世界", "csgo", "黑神话悟空", "塞尔达",
]

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






# ★ 游戏词表统一由 collectors/game_terms.py 提供（2026-10-05）：
#   六个采集器原来各写一份 load_game_terms()，彼此不一致 —— 百度认得的游戏
#   微博未必认得。改成共用一份（AC 自动机，2000+ 游戏名一次扫描），改词表全网一次生效。
# game_terms.py 在 collectors/ 根下，本采集器在子目录 → 用 parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from game_terms import get_matcher  # noqa: E402

_MATCHER = get_matcher()
def guess_game(title: str, desc: str = "") -> str:
    """标题/简介里命中的游戏名（长词优先）。命中不了返回空。"""
    hits = _MATCHER.matched_games(f"{title} {desc}")
    return hits[0] if hits else ""


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

    def search(self, keyword: str, page: int = 1,
               order: str = "totalrank") -> List[Dict[str, Any]]:
        if not self._crawler:
            return []
        try:
            # ★ page_size=50（API 上限，2026-10-06）：20 条/轮的页容量把头部游戏
            #   全部顶到封顶（实测 13/29 个关键词永远 20 条/轮），大游戏爆发时
            #   增速反而测不出来。50 也只能扛到 200 投/时，饱和本身即强信号。
            return self._crawler.search_videos(keyword, page=page, page_size=50,
                                               order=order)
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
        # ★ 每轮重读 agent 提案（原来只在 main() 启动时读一次——采集器长跑
        #   几天不重启，提案要等几天才生效。现在下一轮就用上）
        for extra in _agent_watchlist("bili_keyword"):
            if extra not in keywords:
                keywords.append(extra)
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
            # ★ order=pubdate：按发布时间倒序，本轮抓到的就是"最新投稿"
            for kw in keywords:
                vids = self.search(kw, order="pubdate")
                rows += [self._row(v, f"search:{kw}", 0, stamp) for v in vids]
                got[f"search:{kw}"] = len(vids)
                time.sleep(self.sleep)

        rows = [r for r in rows if r["bvid"]]
        self._append_csv(out_dir / "hot_videos.csv", VIDEO_FIELDS, rows)
        snaps = self._write_snapshots(out_dir / "thread_snapshots.csv", rows, stamp)
        # ★ 三档标注而不是硬过滤（2026-10-05 修正）：
        #   ranking/popular 是 B站**游戏区官方榜**，内容天然是游戏；
        #   但实测只用标题去判，只有 29% 命中 —— 因为大量视频标题写得跟游戏
        #   半点关系没有（"海龟汤""大合集""射手，我们该算算旧账了"）。
        #   叠上 description + author（官方号名就是游戏名）能到 95%。
        #   所以：官方榜来源直接标 confirmed，词表命中的额外标 named，
        #   两档都收进 game_hits —— 宁可多召回，交给上层语义召回和 LLM 分级。
        game_hits = []
        for r in rows:
            from_official_rank = r["source"] in ("game_ranking", "popular")
            named = guess_game(r["title"], r.get("description") or "")
            if from_official_rank or named:
                game_hits.append({
                    "title": r["title"][:40],
                    "game": named or "",
                    "by": "official_rank" if from_official_rank else "term_match",
                })
        self._log(out_dir / "crawl_runs.jsonl",
                  {"observed_at": stamp, "got": got, "kept": len(rows),
                   "game_related": len(game_hits),
                   "named_by_term": sum(1 for g in game_hits if g["game"])})
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


def is_game_related(text: str) -> bool:
    return _MATCHER.is_game_related(text)


def _agent_watchlist(kind: str) -> list:
    """搜索 agent 的监控提案（48h 过期）。agent 不在时静默为空。"""
    try:
        d = json.loads((ROOT / "data" / "state" / "agent_watchlist.json")
                       .read_text(encoding="utf-8"))
        return [x["name"] for x in d.get("proposals", [])
                if x.get("type") == kind and x.get("expires_at", "") >
                datetime.now(TZ_CN).isoformat(timespec="seconds")]
    except Exception:
        return []



def _single_instance_lock(marker: str) -> bool:
    """★ 单实例锁：已有同脚本 --watch 实例在跑就退出（防人工重启与看门狗
    自动重启赛跑产生双实例——双实例会双写 CSV + 双倍请求（2026-10-06 事故）。"""
    import psutil
    me = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (not (p.info["name"] or "").lower().startswith("python")
                    or p.info["pid"] == me):
                continue
            cl = " ".join(p.info["cmdline"] or [])
            if marker in cl and "--watch" in cl:
                print(f"[lock] 已有 watch 实例 pid={p.info['pid']}，本实例退出")
                return False
        except Exception:
            pass
    return True

def main() -> int:
    ap = argparse.ArgumentParser(description="B站游戏区/热门采集（创作者生产信号）")
    ap.add_argument("--out-dir", default="data/raw/bilibili")
    ap.add_argument("--sources", default="ranking,popular", help="ranking,popular,search")
    ap.add_argument("--keywords", default="",
                help="search 时的关键词（逗号分隔）；留空 = 用 DEFAULT_KEYWORDS（29 游戏名单）")
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
    if "search" in sources and not kws:
        kws = DEFAULT_KEYWORDS
    # ★ 搜索 agent 的临时关键词并入（agent 发现的话题词盯 48h）
    for extra in _agent_watchlist("bili_keyword"):
        if extra not in kws:
            kws.append(extra)

    if not args.watch:
        return 0
    if not _single_instance_lock("crawl_bili_game_hot"):
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
        name="bilibili",
        fn=lambda: c.collect(out_dir, sources, kws),
        interval=args.interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())
