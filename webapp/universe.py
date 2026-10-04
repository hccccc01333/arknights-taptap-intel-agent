# -*- coding: utf-8 -*-
"""Universe 数据装配（设计 §C：3D 映射的唯一真源）。

★ 为什么单独一个模块：数据映射规则（百分位 / log / 三级降级）是**纯逻辑**，
  与 Three.js 无关，必须可单测、可复算。所有降级都在这里标出来，
  渲染层只负责画 —— 避免"前端自己猜相关性"这种事。

★ 三级相关性（设计 §C.2）：
  L1 真实  = L4 已分析出的 relevance_score
  L2 推测  = 事件实体命中 games/*.json 档案（game_* 标签）→ 距离按档案距离
  L3 未知  = 都没有 → 放外圈，标注「与我们关系未知」
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------- 游戏档案（代理相关性用）
def _game_profiles() -> Dict[str, Dict[str, Any]]:
    """games/*.json → {key: {name, aliases}}。缓存进程内。"""
    if getattr(_game_profiles, "_cache", None):
        return _game_profiles._cache            # type: ignore[attr-defined]
    out: Dict[str, Dict[str, Any]] = {}
    import glob
    for path in glob.glob(os.path.join(_ROOT, "games", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                p = json.load(f)
        except (ValueError, OSError):
            continue
        key = p.get("key")
        if key:
            out[key] = {"key": key, "name": p.get("name") or key,
                        "aliases": p.get("aliases") or []}
    _game_profiles._cache = out                # type: ignore[attr-defined]
    return out


def _cover_index() -> Dict[str, Dict[str, Any]]:
    """spatial/public/covers/games.json（scripts/fetch_game_covers.py 实抓的真实封面）。

    ★ 只有实抓的封面才进这里；没有就是没有 —— 不用占位色块假装有图。
    """
    if getattr(_cover_index, "_cache", None) is not None:
        return _cover_index._cache                  # type: ignore[attr-defined]
    out: Dict[str, Dict[str, Any]] = {}
    path = os.path.join(_ROOT, "spatial", "public", "covers", "games.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                out = json.load(f)
        except (ValueError, OSError):
            out = {}
    _cover_index._cache = out                        # type: ignore[attr-defined]
    return out


def _game_of(entities: List[str]) -> Optional[str]:
    """事件实体标签 → 游戏 key（取出现最多的 game_*）。"""
    games = [e[5:] for e in (entities or []) if e.startswith("game_")]
    return games[0] if games else None


# L3 实体标签 → TapTap app id（app 详情页 SSR 里直接带 image 字段，最稳）
APP_IDS: Dict[str, int] = {
    "arknights": 167982, "endfield": 2177636, "wuthering-waves": 171704,
    "delta_force": 2353155, "genshin": 168332, "star_rail": 224267,
    "zenless": 254456, "honor_of_kings": 5566, "black_myth_wukong": 2703815,
    "lol": 23976,
}


def _sqlite(db_name: str):
    from paths import STATE
    path = os.path.join(str(STATE), db_name)
    if not os.path.exists(path):
        return None
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- 映射纯函数（可单测）
def percentile_map(values: List[float], v: Optional[float],
                   lo: float = 1.2, hi: float = 5.5) -> float:
    """百分位映射：真实分布挤在窄区间时，只有排名可比（设计 §C1）。

    ⚠️ 不是线性映射：热度 0.133–0.358 直接线性 → 211 颗球几乎一样大。
    """
    vals = [x for x in values if x is not None]
    if v is None or not vals:
        return (lo + hi) / 2
    if len(vals) == 1:
        return (lo + hi) / 2
    below = sum(1 for x in vals if x < v)
    equal = sum(1 for x in vals if x == v)
    rank = (below + equal / 2) / len(vals)      # 并列取中点
    return lo + (hi - lo) * max(0.0, min(1.0, rank))


def log_density(n: Optional[int], cap: int = 60) -> float:
    """粒子密度：内容量中位=1、max=629 → 对数映射（设计 §C1）。"""
    if not n:
        return 0.0
    return max(0.0, min(1.0, math.log(1 + n) / math.log(1 + cap)))


def proxy_relevance(entities: List[str]) -> Tuple[Optional[float], Optional[str]]:
    """L2 代理相关性：命中游戏档案 → 返回 (score, 命中的档案 key)。

    · 主档案（arknights）→ 0.72；对照档案（wuthering-waves）→ 0.42
    · 只有 developer_/genre_ 标签 → 0.30（弱信号）
    · 无匹配 → (None, None) 走 L3
    """
    games = _game_profiles()
    ents = entities or []
    for key in ("arknights", "wuthering-waves"):
        for tag in ents:
            if tag == f"game_{key}" or (tag.startswith("game_") and
                                        tag[5:] == games.get(key, {}).get("key", "\0")):
                return (0.72 if key == "arknights" else 0.42), key
    weak = [e for e in ents if e.startswith(("developer_", "genre_", "publisher_"))]
    if weak:
        return 0.30, None
    return None, None


# ---------------------------------------------------------------- 装配
def build_universe(limit: int = 240, include_ideas: bool = True) -> Dict[str, Any]:
    """产出前端渲染宇宙所需的全部数据（含降级标注）。"""
    l3 = _sqlite("l3_trend.sqlite3")
    l4 = _sqlite("l4_intelligence.sqlite3")
    l5 = _sqlite("l5_memory.sqlite3")
    if l3 is None:
        return {"error": "L3 库不存在：先跑 L3_trend/trend_engine/pipeline.py --run",
                "planets": [], "core": {}}

    events = [dict(r) for r in l3.execute(
        'SELECT * FROM trend_event WHERE status="active" '
        "ORDER BY content_count DESC LIMIT ?", (limit,)).fetchall()]
    if not events:
        return {"error": "没有进行中的话题", "planets": [], "core": {}}

    # --- 真实相关性（L4）
    real_rel: Dict[str, Dict[str, Any]] = {}
    ideas: Dict[str, List[Dict[str, Any]]] = {}
    if l4 is not None:
        for row in l4.execute(
                "SELECT event_id, MAX(relevance_score) AS rel, COUNT(*) n_creatives, "
                "SUM(n_opportunities) n_opp FROM intelligence_analysis "
                "WHERE event_id IS NOT NULL GROUP BY event_id"):
            real_rel[row["event_id"]] = {"rel": row["rel"], "n_creatives": row["n_creatives"],
                                         "n_opp": row["n_opp"]}
        if include_ideas:
            for row in l4.execute(
                    "SELECT c.event_id, c.idea_id, c.idea_name, c.creative_type, c.score, "
                    "c.passed, c.payload FROM intelligence_creative c"):
                ideas.setdefault(row["event_id"], []).append({
                    "id": row["idea_id"], "name": row["idea_name"],
                    "type": row["creative_type"], "score": row["score"],
                    "passed": bool(row["passed"]),
                })

    heats = [e.get("hot_score") or 0 for e in events]
    vels = [e.get("momentum_score") or 0 for e in events]
    covers = _cover_index()
    by_app = {v["app_id"]: v for v in covers.values()} if covers else {}

    planets: List[Dict[str, Any]] = []
    n_l1 = n_l2 = n_l3 = 0
    for e in events:
        eid = e["event_id"]
        ents = json.loads(e.get("entity_ids") or "[]")
        rr = real_rel.get(eid)
        if rr and rr["rel"] is not None:
            rel, rel_level, rel_note = float(rr["rel"]), "L1", "AI 实际分析得出"
            n_l1 += 1
        else:
            proxy, hit = proxy_relevance(ents)
            if proxy is not None:
                rel, rel_level = proxy, "L2"
                rel_note = (f"按游戏档案推测（命中 {hit}）" if hit
                            else "只命中开发方/品类标签，弱信号")
                n_l2 += 1
            else:
                rel, rel_level = None, "L3"
                rel_note = "我们与这件事的关系未知"
                n_l3 += 1
        platforms = json.loads(e.get("platforms") or "[]")
        game = _game_of(ents)
        app_id = str(APP_IDS.get(game or "")) if game else None
        cover_rec = by_app.get(app_id) if app_id else None
        planets.append({
            "id": eid,
            "title": e.get("canonical_title") or eid,
            "lifecycle": e.get("lifecycle"),
            "heat": e.get("hot_score"),
            "velocity": e.get("momentum_score"),
            "confidence": e.get("confidence_score"),
            "contentCount": e.get("content_count") or 0,
            "platformCount": e.get("platform_count") or 1,
            "platforms": platforms,
            "entities": ents,
            "relevance": rel,
            "relevanceLevel": rel_level,
            "relevanceNote": rel_note,
            "nIdeas": len(ideas.get(eid, [])),
            "firstSeen": e.get("first_detected_at"),
            # 预计算渲染量（前端不再算，保证前后端一致）
            "radius": round(percentile_map(heats, e.get("hot_score")), 3),
            "glowRank": round(percentile_map(vels, e.get("momentum_score"), 0.0, 1.0), 3),
            "density": round(log_density(e.get("content_count")), 3),
            # 封面：只有实抓到的才有，没有就是 null（前端画纯色卡，不假装）
            "game": game,
            "gameName": (cover_rec or {}).get("name") if cover_rec else None,
            "cover": f"/{cover_rec['file']}" if cover_rec else None,
            "appId": app_id if cover_rec else None,
        })

    # 平台总量（管道粗细的真实来源）
    pipes: Dict[str, int] = {}
    if l5 is not None:
        try:
            from paths import STATE
            l2 = sqlite3.connect(os.path.join(str(STATE), "l2_processed.sqlite3"))
            for row in l2.execute("SELECT platform, COUNT(*) c FROM content GROUP BY platform"):
                pipes[row[0]] = row[1]
            l2.close()
        except sqlite3.Error:
            pass

    core_games = list(_game_profiles().values())
    return {
        "planets": planets,
        "core": {
            "name": "TapTap",
            "games": core_games,
            "topicCount": len(planets),
            "multiPlatformCount": sum(1 for p in planets if p["platformCount"] > 1),
        },
        "pipes": [{"platform": k, "count": v} for k, v in
                  sorted(pipes.items(), key=lambda kv: -kv[1])],
        "relevanceCensus": {"real": n_l1, "proxy": n_l2, "unknown": n_l3},
        "degradeNotes": [
            f"热度与势头按排名映射（真实分布极窄，绝对值不可视）",
            f"相关性：{n_l1} 个真实 / {n_l2} 个按游戏档案推测 / {n_l3} 个未知",
            f"跨平台话题：{sum(1 for p in planets if p['platformCount'] > 1)}/{len(planets)}",
            f"有真实封面的：{sum(1 for p in planets if p['cover'])}/{len(planets)}"
            f"（其余卡片无图，不放假图）",
        ],
        "games": covers,
        "ideas": ideas,
    }
