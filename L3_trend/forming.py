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
    "unknown": "数据不足",
}


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
                    min_gap_min: int = 3) -> Dict[str, Any]:
    """分析一个热榜 csv，输出每个词的阶段判定 + 整体"正在形成"的清单。"""
    rows = _read(csv_path)
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


def analyze_all(root: str) -> Dict[str, Any]:
    out = {}
    for label, path, plat in (
        ("百度热搜", os.path.join(root, "data/raw/baidu_index/hot_search.csv"), "baidu"),
        ("微博热搜", os.path.join(root, "data/raw/weibo/hot_search.csv"), "weibo"),
        ("B站热门", os.path.join(root, "data/raw/bilibili/hot_videos.csv"), "bilibili"),
    ):
        r = analyze_hotlist(path, plat)
        if r["rounds"]:
            out[label] = r
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