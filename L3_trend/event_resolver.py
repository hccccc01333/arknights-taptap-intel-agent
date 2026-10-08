#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_trend/event_resolver.py —— 跨平台热点聚类与事件合并。

★ 要解决什么（2026-10-05 用户指出）：
  现在每个热点各出各的创意 —— 但"松岛辉空成史上最年轻世界第一"（百度）
  和"松岛辉空最年轻世界第一"（微博）**是同一件事**，应该合成一个事件、
  出一次创意，而不是两条各出一次。跨平台合并是"全网追踪"相对"单平台抓取"
  唯一的结构性优势，不能浪费。

★ 三段式（召回 → 规则 → LLM 判定），分工和项目纪律一致：
  ① 语义召回（bge）：跨平台两两算相似度，只送 ≥ 阈值的**候选对**给下游
     —— 算法负责"别漏"（recall）
  ② 规则过滤：同一时间窗内、最好同一游戏 —— 挡掉明显不相关的
  ③ LLM 判定：逐对问"这是同一件事吗" —— 模型负责"别错"（precision）
     ★ 为什么必须 LLM：语义相似 ≠ 同一事件。实测 0.838 的
     「缅北电诈回流人员自述被割肾经历」vs「男子讲述在缅北被割肾经历」
     语义高度重合，但是**同一事件还是两个视角**，规则判不了，模型能判。

★ 合并产物：Event 对象（一个真实事件 = 一组跨平台热点），
  带时间轴（各平台首次出现时刻 → 可算传播路径的原料）。

用法：
    python event_resolver.py                 # 跑合并，输出 data/state/hot_events.json
    python event_resolver.py --no-llm        # 只做语义召回+规则（不花模型调用）
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "L1_data_source")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_PATH = os.path.join(_ROOT, "data", "state", "hot_events.json")

# 候选对相似度阈值（召回用，宁可多送给 LLM 判）
SIM_CANDIDATE = 0.80
# 时间窗：超过这个时长不在同一事件（小时）
TIME_WINDOW_H = 48

PLATFORM_LABEL = {"baidu": "百度热搜", "weibo": "微博热搜",
                  "bilibili": "B站", "douyin": "抖音", "taptap": "TapTap"}

import sys as _sys
_sys.path.insert(0, os.path.join(_ROOT, 'L1_data_source', 'collectors'))

SOURCES = [
    ("baidu", os.path.join(_ROOT, "data/raw/baidu_index/hot_search.csv"), "word"),
    ("weibo", os.path.join(_ROOT, "data/raw/weibo/hot_search.csv"), "word"),
    ("bilibili", os.path.join(_ROOT, "data/raw/bilibili/hot_videos.csv"), "title"),
]

MERGE_PROMPT = """你在做**事件消解**（event resolution）：判断两个网络热点是不是同一件事。

【A】{a_title}
     平台：{a_platform}｜热度 {a_hot}｜首次出现 {a_first}

【B】{b_title}
     平台：{b_platform}｜热度 {b_hot}｜首次出现 {b_first}

判断标准（严格按事件本身，不是按字面相似）：
- **同一事件**：同一件事的同一发展阶段（例如"某游戏新角色立绘被改"两边都在讨论这件事）
- **相关但不同**：同一话题的不同侧面 / 不同阶段（例如"缅北电诈回流人员自述"vs"被割肾经历"——同一主题但不同当事人的经历，不是一件事）
- **不同**：只是词面像

严格按 JSON 输出：{{"verdict":"same|related|different","reason":"一句话（中文，≤30字）"}}"""


# ---------------------------------------------------------------- 数据加载
def load_hotspots() -> List[Dict[str, Any]]:
    """三个渠道各取「最新一轮」的条目，带首次出现时刻（用最早采样推）。"""
    out: List[Dict[str, Any]] = []
    for plat, path, name_col in SOURCES:
        if not os.path.exists(path):
            continue
        first_seen: Dict[str, str] = {}
        latest: Dict[str, Dict[str, str]] = {}
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                key = (r.get(name_col) or "").strip()[:80]
                if not key:
                    continue
                ts = r.get("observed_at") or ""
                if key not in first_seen or ts < first_seen[key]:
                    first_seen[key] = ts          # 最早采样 = 首次看到
                if key not in latest or ts > (latest[key].get("observed_at") or ""):
                    latest[key] = r
        for key, r in latest.items():
            # ★ 游戏闸门（2026-10-06）：这个 resolver 喂的是游戏创意链——
            #   泛娱乐热搜（蔡康永/游客宿舍）进事件队列只会占掉创意名额。
            #   与全系统同一个词表入口（game_terms）。
            try:
                from game_terms import get_matcher
                if not get_matcher().is_game_related(key):
                    continue
            except Exception:
                pass
            out.append({
                "id": f"{plat}:{abs(hash(key)) % 10**8}",
                "platform": plat,
                "platform_zh": PLATFORM_LABEL.get(plat, plat),
                "title": key,
                "hot": int(r.get("hot_score") or r.get("play") or 0),
                "rank": int(r.get("rank") or 0),
                "first_seen_at": first_seen.get(key) or r.get("observed_at") or "",
                "latest_at": r.get("observed_at") or "",
            })
    return out


# ---------------------------------------------------------------- ① 语义召回
def _embed(texts: List[str]):
    import torch
    import torch.nn.functional as F
    from transformers import AutoModel, AutoTokenizer
    tok = AutoTokenizer.from_pretrained("BAAI/bge-small-zh-v1.5")
    mdl = AutoModel.from_pretrained("BAAI/bge-small-zh-v1.5")
    mdl.eval()
    vecs = []
    with torch.no_grad():
        for i in range(0, len(texts), 32):          # 分批，避免超长序列
            b = tok(texts[i:i + 32], padding=True, truncation=True,
                    max_length=128, return_tensors="pt")
            v = F.normalize(mdl(**b).last_hidden_state[:, 0], dim=-1)
            vecs.append(v)
    return torch.cat(vecs) if vecs else None


def candidate_pairs(hotspots: List[Dict[str, Any]],
                    sim_min: float = SIM_CANDIDATE) -> List[Tuple[float, Dict, Dict]]:
    """跨平台候选对：语义相似度 ≥ 阈值。只跨平台比（同平台内重复由 forming 处理）。"""
    if len(hotspots) < 2:
        return []
    try:
        V = _embed([h["title"] for h in hotspots])
    except Exception as e:
        print(f"[warn] 语义召回不可用（{str(e)[:70]}），跳过候选对", file=sys.stderr)
        return []
    if V is None:
        return []
    n = len(hotspots)
    out = []
    for i in range(n):
        for j in range(i + 1, n):
            if hotspots[i]["platform"] == hotspots[j]["platform"]:
                continue
            s = float(V[i] @ V[j])
            if s >= sim_min:
                out.append((s, hotspots[i], hotspots[j]))
    out.sort(key=lambda x: -x[0])
    return out


# ---------------------------------------------------------------- ② 规则过滤
def _hours_between(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[float]:
    try:
        ta = datetime.fromisoformat(a["first_seen_at"])
        tb = datetime.fromisoformat(b["first_seen_at"])
    except (ValueError, KeyError, TypeError):
        return None
    return abs((ta - tb).total_seconds()) / 3600


def rule_filter(pairs, window_h: int = TIME_WINDOW_H):
    """按时间窗收敛候选。时间跨度太远的即便词面像也不是同一波事件。"""
    kept, dropped = [], []
    for s, a, b in pairs:
        gap = _hours_between(a, b)
        if gap is None or gap <= window_h:
            kept.append((s, a, b, gap))
        else:
            dropped.append((s, a, b, gap))
    return kept, dropped


# ---------------------------------------------------------------- ③ LLM 判定
def judge_pairs(kept, use_llm: bool = True, max_calls: int = 30):
    """逐对问 LLM：同一事件 / 相关但不同 / 不同。"""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_ROOT, ".env"))
        load_dotenv(os.path.join(_ROOT, "common", ".env"))
        from intelligence.llm import ModelRouter
    except ImportError as e:
        print(f"[warn] 拿不到 LLM（{e}），只按语义相似度合并", file=sys.stderr)
        return [(s, a, b, g, "same", "semantic-only（LLM 不可用）") for s, a, b, g in kept]
    if not use_llm:
        return [(s, a, b, g, "same", "语义相似（未过 LLM）") for s, a, b, g in kept]

    router = ModelRouter(enabled=True)
    if not router.enabled:
        print("[warn] LLM 不可用，按语义相似度合并", file=sys.stderr)
        return [(s, a, b, g, "same", "semantic-only（LLM 不可用）") for s, a, b, g in kept]

    # 同一对只判一次（i<j 和 j>i 是同一件事）
    done = set()
    results = []
    for s, a, b, g in kept:
        key = tuple(sorted([a["id"], b["id"]]))
        if key in done:
            continue
        done.add(key)
        prompt = MERGE_PROMPT.format(
            a_title=a["title"][:60], a_platform=a["platform_zh"], a_hot=a["hot"],
            a_first=a["first_seen_at"][:16],
            b_title=b["title"][:60], b_platform=b["platform_zh"], b_hot=b["hot"],
            b_first=b["first_seen_at"][:16])
        try:
            out = router.call_json("relevance", prompt, max_tokens=400)
            v = (out or {}).get("verdict") or "different"
            reason = str((out or {}).get("reason") or "")[:40]
        except Exception:
            v, reason = "different", "LLM 调用失败，保守不合并"
        results.append((s, a, b, g, v, reason))
        if len(results) >= max_calls:
            break
    return results


# ---------------------------------------------------------------- ④ 建事件
def build_events(hotspots, judged, min_sim: float = 0.90) -> List[Dict[str, Any]]:
    """用并查集把判定为 same 的热点并成事件。

    注意：只有 verdict=same 且相似度 ≥ min_sim 才合并（高门槛，宁可不合）；
    related 的对会在结果里单独列出，供人参考。
    """
    parent: Dict[str, str] = {h["id"]: h["id"] for h in hotspots}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    merged_pairs, related_pairs = [], []
    for s, a, b, g, verdict, reason in judged:
        if verdict == "same" and s >= min_sim:
            union(a["id"], b["id"])
            merged_pairs.append({"sim": round(s, 3), "gap_h": round(g, 1) if g else None,
                                 "a": a["title"], "b": b["title"], "reason": reason})
        elif verdict == "related":
            related_pairs.append({"sim": round(s, 3), "a": a["title"], "b": b["title"],
                                  "reason": reason})

    groups: Dict[str, List[Dict]] = {}
    for h in hotspots:
        groups.setdefault(find(h["id"]), []).append(h)

    events: List[Dict[str, Any]] = []
    for members in groups.values():
        cross = len({m["platform"] for m in members}) > 1
        if not cross:
            continue                     # 只输出跨平台事件（单平台不算"合并"）
        members.sort(key=lambda m: m["first_seen_at"])
        platforms = []
        for p in {m["platform"] for m in members}:
            first = min((m["first_seen_at"] for m in members if m["platform"] == p), default="")
            platforms.append({"platform": p, "platform_zh": PLATFORM_LABEL.get(p, p),
                              "first_seen_at": first,
                              "items": [{"title": m["title"], "hot": m["hot"], "rank": m["rank"]}
                                        for m in members if m["platform"] == p]})
        platforms.sort(key=lambda x: x["first_seen_at"])
        events.append({
            "event_id": f"evt_{abs(hash(members[0]['id'])) % 10**8}",
            "title": members[0]["title"][:70],
            "cross_platform": True,
            "platform_count": len(platforms),
            "member_count": len(members),
            "platforms": platforms,
            "first_seen_at": members[0]["first_seen_at"],
            "timeline": [{"platform": m["platform_zh"], "at": m["first_seen_at"]}
                         for m in members],
        })
    events.sort(key=lambda e: (-e["platform_count"], -e["member_count"]))
    return events, merged_pairs, related_pairs


def resolve(use_llm: bool = True, min_sim: float = 0.90) -> Dict[str, Any]:
    """跑完整条链路，返回合并结果。"""
    hotspots = load_hotspots()
    pairs = candidate_pairs(hotspots)
    kept, dropped = rule_filter(pairs)
    judged = judge_pairs(kept, use_llm=use_llm)
    events, merged, related = build_events(hotspots, judged, min_sim=min_sim)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "hotspot_count": len(hotspots),
        "candidate_pairs": len(pairs),
        "after_time_filter": len(kept),
        "dropped_by_time": len(dropped),
        "events": events,
        "merged_pairs": merged,
        "related_pairs": related,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="跨平台热点聚类与事件合并")
    ap.add_argument("--no-llm", action="store_true", help="只用语义+规则（不调模型）")
    ap.add_argument("--min-sim", type=float, default=0.90, help="合并所需的最低相似度")
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()

    res = resolve(use_llm=not args.no_llm, min_sim=args.min_sim)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(f"热点 {res['hotspot_count']} 条 → 候选对 {res['candidate_pairs']} → "
          f"过时间窗 {res['after_time_filter']}")
    print(f"合并出跨平台事件 {len(res['events'])} 个")
    for e in res["events"]:
        print(f"【{e['title'][:40]}】{e['platform_count']} 个平台 / {e['member_count']} 条")
        for p in e["platforms"]:
            its = " / ".join(i["title"][:26] for i in p["items"][:2])
            print(f"   {p['platform_zh']:<6} {p['first_seen_at'][11:16]}  {its}")
        print()
    if res["related_pairs"]:
        print("相关但未合并（供人工判断）:")
        for r in res["related_pairs"][:5]:
            print(f"   {r['sim']} {r['a'][:26]} ⟷ {r['b'][:26]}  ← {r['reason']}")
    print(f"结果已存 {args.out}")
