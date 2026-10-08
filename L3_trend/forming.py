#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_trend/forming.py —— 「正在形成」的热点判定。

★ 为什么单独一层（2026-10-05 审查发现）：
  百度/微博/B站的热榜是**存量榜**——词上榜时，讨论量通常已经过了峰值。
  我们做的是热点**追踪**，要的是"正在形成"，不是"已经火了"。
  把榜上词直接当热点报出去，报的全是昨天的新闻（实测首轮跑出来就是）。

★ 口径（四个阶段，对齐运营的真实动作）：
    forming  形成中 —— ★这才是要报的：刚进榜 / 热度在涨 / 排名在升
    rising   上升中 —— 连续多轮涨，但已在榜一段时间
    peaking  高位滞涨—— 长期在榜且热度横盘，爆发已过
    fading   降温中 —— 热度下滑

  判据（全部来自我们自己跨轮采集的快照，不含主观判断）：
    · 新进榜      —— 上一轮不在榜，本轮在
    · 热度变化率  —— (本轮 - 上轮) / 上轮
    · 在榜时长    —— 连续在榜轮数 × 采样间隔
    · 官方「新」标记（微博 label_name）
"""

from __future__ import annotations

import csv
import os
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

TZ_CN = timezone(timedelta(hours=8))

# 形成中的门槛（可调，先给拍脑袋值，跑几天有体感再改）
TH_RISING_RATE = 8.0      # 单轮热度涨幅 ≥8% 算在涨
TH_NEW_ONBOARD = 1.0      # 上升 ≥1 名算排名上升
TH_HOT_PCT = 60.0         # 热度在榜内前 60% 位置算高位

STAGE_LABEL = {
    "forming": "形成中",
    "rising": "上升中",
    "peaking": "高位滞涨",
    "fading": "降温中",
    "baseline": "基线观察",
    "steady": "持续高热",
    "unknown": "数据不足",
}

# 贴吧水楼/灌水帖：常驻聊天楼，回帖数几万但与热点无关，不参与 forming 判定
WATER_RE = re.compile(r"水楼|灌水|水氵|氵水|氺|建个.{0,8}楼|水贴|闲聊楼|日常水")


def _read(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    # ★ 不同来源的"条目名"字段不同：热榜是 word，B站视频是 title+bvid。
    #   统一成一个 word 键，下面的分析逻辑就不用到处判断表类型。
    for r in rows:
        if not r.get("word"):
            r["word"] = (r.get("title") or r.get("bvid") or "").strip()[:40]
        if not r.get("hot_score"):
            r["hot_score"] = r.get("play") or "0"
        if not r.get("rank"):
            r["rank"] = r.get("rank") or "0"
    return [r for r in rows if r.get("word")]


def _parse_ts(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def analyze_hotlist(csv_path: str, platform: str,
                    min_gap_min: int = 3,
                    source_filter: tuple = ()) -> Dict[str, Any]:
    """分析一个热榜 csv，输出每个词的阶段判定 + 整体"正在形成"的清单。

    source_filter：只分析这些来源的行（前缀匹配，空 = 不过滤）。
      B站的 hot_videos.csv 混了 game_ranking（游戏区榜）和 popular（全站热门），
      不滤的话 forming 档会被绘画/美妆视频占据 —— 泛热门渠道微博已有，B站这边
      只取游戏区。
    """
    rows = _read(csv_path)
    if source_filter:
        rows = [r for r in rows
                if r.get("source") in source_filter
                or any(r.get("source", "").startswith(s) for s in source_filter)]
    if not rows:
        return {"platform": platform, "rounds": 0, "stages": {}, "forming": [],
                "note": "无数据"}

    # 按轮次分组（过滤掉间隔过短的采样轮）
    by_round: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_round[r["observed_at"]].append(r)
    times = sorted(by_round)
    # 只保留与上一轮至少间隔 min_gap_min 的采样点
    kept: List[str] = []
    for i, t in enumerate(times):
        if i == 0:
            kept.append(t)
            continue
        gap = (_parse_ts(t) - _parse_ts(kept[-1])).total_seconds() / 60
        if gap >= min_gap_min:
            kept.append(t)

    if len(kept) < 2:
        return {"platform": platform, "rounds": len(kept), "stages": {},
                "forming": [],
                "note": f"只采了 {len(kept)} 轮有效快照，还算不出趋势（至少要 2 轮）"}

    prev_t, cur_t = kept[-2], kept[-1]
    prev = {r["word"]: r for r in by_round[prev_t]}
    cur = {r["word"]: r for r in by_round[cur_t]}
    # 在榜时长（连续在榜轮数）
    presence: Dict[str, int] = defaultdict(int)
    for t in kept:
        for w in {r["word"] for r in by_round[t]}:
            presence[w] += 1
    # 采样间隔（分钟），用于把"轮"换算成时间
    gaps: List[float] = []
    for a, b in zip(kept[:-1], kept[1:]):
        d = (_parse_ts(b) - _parse_ts(a)).total_seconds() / 60
        if d > 0:
            gaps.append(d)
    interval_min = sum(gaps) / len(gaps) if gaps else 0

    stages: Dict[str, Dict[str, Any]] = {}
    for w, r in cur.items():
        p = prev.get(w)
        score_now = float(r.get("hot_score") or 0)
        rank_now = float(r.get("rank") or 999)
        prev_score = float(p.get("hot_score")) if p else None
        prev_rank = float(p.get("rank") or 999) if p else None

        new_onboard = (p is None) or (r.get("is_new") == "true") or (r.get("label") == "新")
        rate = ((score_now - prev_score) / prev_score * 100) if prev_score else None
        rank_delta = (prev_rank - rank_now) if prev_rank is not None else None
        on_board_min = round(presence[w] * interval_min, 1)

        # —— 四档判定 ——
        if new_onboard:
            stage = "forming"
        elif rate is not None and rate >= TH_RISING_RATE:
            stage = "forming" if on_board_min <= 90 else "rising"
        elif rate is not None and rate <= -TH_RISING_RATE:
            stage = "fading"
        else:
            # 无明显变化：在榜越久越可能是高位滞涨（爆发已过）
            stage = "peaking" if on_board_min >= 60 else "forming"

        # 热度相对位置（判断是不是高位词）
        top = float(max((float(x.get("hot_score") or 0) for x in cur.values()), default=1))
        rel_pct = round(score_now / top * 100, 1) if top else 0

        stages[w] = {
            "word": w, "stage": stage, "stage_label": STAGE_LABEL[stage],
            "new_onboard": new_onboard,
            "trend_rate": round(rate, 1) if rate is not None else None,
            "rank": int(rank_now), "rank_delta": int(rank_delta) if rank_delta is not None else None,
            "hot_score": int(score_now), "rel_pct": rel_pct,
            "on_board_min": on_board_min,
            "label": r.get("label") or "",
            "url": r.get("url") or "",
        }

    by_stage: Dict[str, int] = defaultdict(int)
    for v in stages.values():
        by_stage[v["stage"]] += 1
    forming = sorted(
        [v for v in stages.values() if v["stage"] == "forming"],
        key=lambda x: (-(x["trend_rate"] or 0), x["rank"]))

    return {
        "platform": platform,
        "rounds": len(kept),
        "last_round": cur_t,
        "prev_round": prev_t,
        "interval_min": round(interval_min, 1),
        "stages": dict(by_stage),
        "stage_labels": STAGE_LABEL,
        "forming": forming[:30],
        "rising": [v for v in stages.values() if v["stage"] == "rising"][:15],
        "note": "" if len(kept) >= 3 else f"只有 {len(kept)} 轮快照，趋势判断还不稳（建议≥3 轮）",
    }



# ---------------------------------------------------------------- 贴吧游戏吧
def analyze_tieba_forums(root: str) -> Dict[str, Any]:
    """贴吧游戏吧：**回帖数增速** = 帖子级 forming 信号。

    ★ 为什么单独一套逻辑（2026-10-05）：
      贴吧帖子的"热度"是 `reply_count`（绝对值，像 TapTap 的评论数），
      不是百度热搜那种相对热度值。所以判 forming 的判据完全不同：
        首轮见到（rounds_seen==1）      → baseline（基线观察，不算 forming）
        回帖爆发（单轮 +50% 且 +20 评）  → forming（讨论正在引爆）
        回帖在涨                        → rising
        无变化                          → peaking

    ★ 两个实测修正（2026-10-05）：
      ① 首轮见到 ≠ 新帖子 —— 列表页只给"最后活动时间"，帖子可能是三年前的老楼。
         只有跨轮回帖增速才是"正在发生讨论"的证据，首轮一律算基线。
      ② 水楼/灌水楼是常驻聊天楼（回帖几万但与热点无关），直接过滤。
    """
    base = os.path.join(root, "data", "raw", "tieba", "forums")
    if not os.path.isdir(base):
        return {"rounds": 0}
    # post_id → 按时间排序的 (observed_at, reply_count)
    # ★ 数据源（2026-10-05）：优先 history.csv（append-only 时间序列），
    #   posts.csv 是每轮覆盖的快照，只有一轮 —— 用它算不出回帖增速。
    hist: Dict[str, List[tuple]] = defaultdict(list)
    forums = set()
    skipped_water = 0
    for gw in os.listdir(base):
        d = os.path.join(base, gw)
        fp = os.path.join(d, "history.csv")
        if not os.path.exists(fp):
            fp = os.path.join(d, "posts.csv")
        if not os.path.exists(fp):
            continue
        forums.add(gw)
        # (pid, observed_at) 去重：正文补写会 append 同轮重复行，取回帖数最大的一条
        best: Dict[tuple, tuple] = {}
        with open(fp, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                pid = r.get("post_id")
                if not pid:
                    continue
                if WATER_RE.search(r.get("title") or ""):
                    skipped_water += 1
                    continue
                try:
                    rc = int(r.get("reply_count") or 0)
                except ValueError:
                    rc = 0
                key = (pid, r.get("observed_at") or "")
                if key not in best or rc > best[key][1]:
                    best[key] = (r.get("observed_at") or "", rc, gw,
                                 (r.get("title") or "")[:40], r.get("activity_time") or "")
        for key, row in best.items():
            hist[key[0]].append(row)
    if not hist:
        return {"rounds": 0}

    stages: Dict[str, Dict[str, Any]] = {}
    for pid, series in hist.items():
        series.sort()
        rounds_seen = len(series)
        cur_t, cur_rc, forum, title, act = series[-1]
        prev_rc = series[-2][1] if rounds_seen >= 2 else None
        if rounds_seen == 1:
            stage, label = "baseline", "首轮基线"
        elif prev_rc is not None and prev_rc > 0:
            growth = (cur_rc - prev_rc) / prev_rc * 100
            if growth >= 50 and (cur_rc - prev_rc) >= 20:
                stage, label = "forming", f"回帖 {prev_rc}→{cur_rc} (+{growth:.0f}%)"
            elif cur_rc > prev_rc:
                stage, label = "rising", f"回帖 {prev_rc}→{cur_rc} (+{growth:.1f}%)"
            elif cur_rc < prev_rc:
                stage, label = "fading", f"回帖 {prev_rc}→{cur_rc}"
            else:
                stage, label = "peaking", "无变化"
        else:
            # prev_rc == 0：上轮零回帖，本轮有回帖 —— 讨论刚起。
            # ★ 2026-10-07 收紧：≥20 才算 forming（原来 ≥10，实测王者荣耀吧
            #   水帖 0→10 就能上榜，垃圾帖混进信号卡）
            if cur_rc >= 20:
                stage, label = "forming", f"回帖 0→{cur_rc}"
            elif cur_rc >= 5:
                stage, label = "rising", f"回帖 0→{cur_rc}"
            else:
                stage, label = "peaking", "无变化"
        stages[pid] = {"post_id": pid, "forum": forum, "title": title,
                       "reply_count": cur_rc, "prev_reply": prev_rc,
                       "stage": stage, "label": label, "activity_time": act,
                       "rounds_seen": rounds_seen}

    by_stage: Dict[str, int] = defaultdict(int)
    for v in stages.values():
        by_stage[v["stage"]] += 1
    # forming（回帖爆发）排前，rising 按增速排后 —— 两档都报，forming 才是重点
    forming = [v for v in stages.values() if v["stage"] == "forming"]
    rising = [v for v in stages.values() if v["stage"] == "rising"]
    def _growth(v):
        p, c = v.get("prev_reply") or 0, v.get("reply_count") or 0
        return ((c - p) / p * 100) if p else float("inf")
    forming.sort(key=_growth, reverse=True)
    rising.sort(key=_growth, reverse=True)
    all_times = sorted({t for s in hist.values() for t, *_ in s})
    rounds = len(all_times)
    interval = None
    if rounds >= 2:
        try:
            from datetime import datetime as _dt
            t0 = _dt.fromisoformat(all_times[-2])
            t1 = _dt.fromisoformat(all_times[-1])
            interval = round((t1 - t0).total_seconds() / 60, 1)
        except (ValueError, TypeError):
            pass
    note = "贴吧=玩家讨论原现场；回帖数是绝对热度（可跨轮比增速）"
    if rounds < 2:
        note += f"；首轮基线 {len(stages)} 帖（含已滤水楼 {skipped_water}），趋势从第 2 轮起算"
    return {"platform": "tieba", "rounds": rounds, "interval_min": interval,
            "forums": len(forums), "posts": len(stages),
            "stages": dict(by_stage), "stage_labels": STAGE_LABEL,
            "forming": forming[:30],
            "rising": rising[:15],
            "note": note}



# ---------------------------------------------------------------- B站投稿流
def analyze_bili_search(root: str) -> Dict[str, Any]:
    """B站投稿流：关键词搜索（pubdate 倒序）的**新视频出现速率** = 投稿速率。

    ★ 为什么不用榜单做趋势（2026-10-05 实测）：
      B站榜单 API 的 stat 字段是缓存的 —— 96 个在榜视频 1 小时里 reply 只动了
      1 个、danmaku/coin 完全不动，跨轮"增速"全是 0。那是数据假象，不是热点平稳。
      投稿速率才是真信号（对齐 S3 的发帖速率口径）：一个游戏关键词下新视频
      突然变多 = 玩家在产出内容 = 话题正在形成。

    判定（按轮差分 bvid）：
      首轮                          → baseline
      上轮静默(≤1条)，本轮 ≥5 条      → forming（讨论刚起）
      环比 +50% 且本轮 ≥5 条          → forming
      环比 +10%~50% 且本轮 ≥3 条      → rising
      其余                          → peaking
    注意：每轮每关键词只抓 20 条（单页上限），20 = 速率下限被封顶。
    """
    path = os.path.join(root, "data", "raw", "bilibili", "hot_videos.csv")
    if not os.path.exists(path):
        return {"rounds": 0}
    kw_seen: Dict[str, Dict[str, set]] = defaultdict(dict)   # kw → round → {bvid}
    kw_title: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(dict)  # kw → round → {bvid: 标题}
    for r in _read(path):
        src = r.get("source") or ""
        if not src.startswith("search:"):
            continue
        kw = src[len("search:"):]
        t = r.get("observed_at") or ""
        bv = r.get("bvid")
        kw_seen[kw].setdefault(t, set()).add(bv)
        if r.get("word"):
            # 搜索接口返回的标题带高亮 <em class="keyword">，剥掉再给人看
            kw_title[kw].setdefault(t, {})[bv] = re.sub(r"</?em[^>]*>", "", r["word"])

    stages: Dict[str, Dict[str, Any]] = {}
    for kw, rounds in kw_seen.items():
        times = sorted(rounds)
        if not times:
            continue
        cur_t = times[-1]
        prev_t = times[-2] if len(times) >= 2 else None
        new_now = len(rounds[cur_t])
        new_prev = len(rounds[prev_t]) if prev_t else None
        # 样本标题优先取本轮**新增**的视频 —— 报告里要让人一眼看到"什么新内容在爆"
        new_bvs = rounds[cur_t] - rounds.get(prev_t, set()) if prev_t else set()
        titles = kw_title.get(kw, {}).get(cur_t, {})
        samples = [titles[bv] for bv in new_bvs if bv in titles][:3]             or list(titles.values())[:3]
        if prev_t is None:
            stage, label = "baseline", "首轮基线"
        else:
            growth = ((new_now - new_prev) / new_prev * 100) if new_prev else None
            if new_prev is not None and new_prev <= 1 and new_now >= 5:
                stage, label = "forming", f"{new_prev or 0}→{new_now} 条/轮"
            elif growth is not None and growth >= 50 and new_now >= 5:
                stage, label = "forming", f"{new_prev}→{new_now} 条/轮 (+{growth:.0f}%)"
            elif growth is not None and growth >= 10 and new_now >= 3:
                stage, label = "rising", f"{new_prev}→{new_now} 条/轮 (+{growth:.0f}%)"
            else:
                stage = "peaking"
                label = f"{new_prev}→{new_now} 条/轮"
                # ★ 持续高热 ≠ 高位滞涨：头部游戏天天顶在封顶线（50 条/轮），
                #   标 peaking 会让 29 个关键词常年"滞涨"，语义误导（2026-10-06）
                if new_now >= 20 and (growth is None or abs(growth) < 10):
                    stage = "steady"
                if new_now >= 20:
                    label += "（封顶）"
        stages[kw] = {"word": kw, "stage": stage, "label": label,
                      "new_now": new_now, "new_prev": new_prev,
                      "rounds_seen": len(times),
                      "samples": samples,
                      "last_round": cur_t}

    by_stage: Dict[str, int] = defaultdict(int)
    for v in stages.values():
        by_stage[v["stage"]] += 1
    def _new_now(v):
        return v.get("new_now") or 0
    forming = sorted([v for v in stages.values() if v["stage"] == "forming"],
                     key=_new_now, reverse=True)
    all_times = sorted({t for rounds in kw_seen.values() for t in rounds})
    interval = None
    if len(all_times) >= 2:
        try:
            t0, t1 = _parse_ts(all_times[-2]), _parse_ts(all_times[-1])
            if t0 and t1:
                interval = round((t1 - t0).total_seconds() / 60, 1)
        except (ValueError, TypeError):
            pass
    return {"platform": "bili_search", "rounds": len(all_times),
            "interval_min": interval, "keywords": len(stages),
            "stages": dict(by_stage), "stage_labels": STAGE_LABEL,
            "forming": forming[:20],
            "note": "B站投稿流=玩家内容产出速率；榜单 stat 字段是缓存的，趋势只有这里有"
                    if len(all_times) >= 2 else
                    "B站投稿流首轮基线；下一轮起按新视频差分算投稿速率"}

# ---------------------------------------------------------------- 游戏媒体
def analyze_gamemedia(root: str) -> Dict[str, Any]:
    """游戏媒体：**新条目出现** = 行业事件发生。

    ★ 为什么逻辑不同：媒体没有热度值，只有"发没发"。所以判据是
      这一轮出现的、上一轮没有的条目 = 新事件。
      媒体是**事件源**（游戏行业在发生什么），不是热度榜 ——
      它的作用是给创意生成提供"机会线索"，不参与 forming 排名。
    """
    path = os.path.join(root, "data", "raw", "gamemedia", "news.csv")
    if not os.path.exists(path):
        return {"rounds": 0}
    seen: Dict[str, str] = {}       # url -> 首次出现时刻
    by_src: Dict[str, int] = defaultdict(int)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            u = r.get("url") or ""
            if not u:
                continue
            by_src[r.get("source") or "?"] += 1
            if u not in seen:
                seen[u] = r.get("observed_at") or ""
    with open(path, encoding="utf-8-sig", newline="") as f2:
        times = sorted({r.get("observed_at", "") for r in csv.DictReader(f2)})
    latest = times[-1] if times else ""
    new_items = [{"url": u, "first_seen": t, "source": "gamemedia"}
                 for u, t in seen.items() if t == latest]
    return {"platform": "gamemedia", "items": len(seen), "by_source": dict(by_src),
            "rounds": len(times), "new_count": len(new_items),
            "stage": "news", "note": "游戏媒体=事件源（行业在发生什么），不参与热度排名"}


def analyze_all(root: str) -> Dict[str, Any]:
    out = {}
    for label, path, plat, sf in (
        ("百度热搜", os.path.join(root, "data/raw/baidu_index/hot_search.csv"), "baidu", ()),
        ("微博热搜", os.path.join(root, "data/raw/weibo/hot_search.csv"), "weibo", ()),
    ):
        r = analyze_hotlist(path, plat, source_filter=sf)
        if r["rounds"]:
            out[label] = r
    # 游戏垂类源（判定逻辑不同，单独分析器）
    bs = analyze_bili_search(root)
    if bs.get("keywords"):
        out["B站投稿流"] = bs
    tb = analyze_tieba_forums(root)
    if tb.get("posts"):
        out["贴吧游戏吧"] = tb
    gm = analyze_gamemedia(root)
    if gm.get("items"):
        out["游戏媒体"] = gm
    return out


if __name__ == "__main__":
    import json
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    res = analyze_all(root)
    for plat, r in res.items():
        print(f"\n===== {plat}（{r['rounds']} 轮，间隔 {r.get('interval_min')} 分钟）=====")
        print(f"  阶段分布: {r['stages']}")
        if r.get("note"):
            print(f"  ⚠ {r['note']}")
        if r["forming"]:
            print("  ★ 正在形成：")
            for f in r["forming"][:8]:
                rate = f"{f['trend_rate']:+.1f}%" if f["trend_rate"] is not None else "新进榜"
                print(f"     #{f['rank']:<3} {f['word'][:22]:<24} {rate:>9}  在榜{f['on_board_min']}分")
    if not res:
        print("还没有热榜数据，先跑一轮采集。")