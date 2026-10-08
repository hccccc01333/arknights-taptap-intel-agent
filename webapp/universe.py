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

import hashlib
import re
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



# ★ 呈现层中文名（2026-10-06）：trend_engine 的 canonical_title 是实体 key
#   （arknights/honor of kings），直接当标题又英文又重复。keys 覆盖 APP_IDS。
_GAME_CN: Dict[str, str] = {
    "arknights": "明日方舟", "endfield": "明日方舟：终末地", "wuthering-waves": "鸣潮",
    "delta_force": "三角洲行动", "genshin": "原神", "star_rail": "崩坏：星穹铁道",
    "zenless": "绝区零", "honor_of_kings": "王者荣耀", "black_myth_wukong": "黑神话：悟空",
    "lol": "英雄联盟",
}
_TITLE_KEY_ALIASES = {
    "honor of kings": "honor_of_kings", "genshin impact": "genshin", "genshin": "genshin",
    "wuthering waves": "wuthering-waves", "black myth": "black_myth_wukong",
    "zenless zone zero": "zenless", "star rail": "star_rail",
    "delta force": "delta_force", "league of legends": "lol", "arknights": "arknights",
}


def _display_title(title: str, game_key: Optional[str]) -> str:
    """把实体 key 泄漏的英文标题换成中文游戏名；保留中文原题。"""
    if not game_key or game_key not in _GAME_CN:
        return title
    cn = _GAME_CN[game_key]
    t = title or ""
    for v in {game_key, game_key.replace("-", " "), game_key.replace("_", " ")}:
        if v and v.lower() in t.lower():
            return re.sub(re.escape(v), cn, t, flags=re.I).strip()
    return cn if t.strip().lower() == game_key.lower() else t


def _game_of(entities: List[str], title: str = "") -> Optional[str]:
    """事件实体标签 → 游戏 key（取出现最多的 game_*）。

    ★ 兜底：老事件实体里没有 game_* 时，从标题的英文别名解析
      （"honor of kings" → honor_of_kings）——否则详情卡显示"未归属游戏"。
    """
    from collections import Counter
    games = Counter(e[5:] for e in (entities or []) if e.startswith("game_"))
    if games:
        return games.most_common(1)[0][0]
    tl = (title or "").lower()
    for frag, key in _TITLE_KEY_ALIASES.items():
        if frag in tl:
            return key
    return None


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


_FORMING_LIFECYCLE = {"forming": "EMERGING", "rising": "GROWING",
                      "steady": "PEAKING", "peaking": "PEAKING",
                      "fading": "DECLINING", "baseline": "DORMANT"}
_FORMING_PLATFORM = {"微博热搜": "weibo", "微博": "weibo", "百度热搜": "baidu",
                     "百度": "baidu", "B站投稿流": "bilibili", "B站游戏区": "bilibili",
                     "B站": "bilibili", "贴吧游戏吧": "tieba", "贴吧": "tieba",
                     "游戏媒体": "media", "媒体": "media"}


def _forming_velocity(rate_text: str, trend_rate) -> float:
    """展示分（0-1），映射自跨轮变化率——不是造数，是标度换算。

    新进榜/无变化率 = 0.65 基准；有变化率按 +r% → 0.55 + r/300 封顶 0.95。
    """
    if isinstance(trend_rate, (int, float)) and trend_rate:
        return round(min(0.95, 0.55 + abs(trend_rate) / 300.0), 3)
    m = re.search(r"([+-]?\d+(?:\.\d+)?)%", rate_text or "")
    if m:
        return round(min(0.95, 0.55 + abs(float(m.group(1))) / 300.0), 3)
    return 0.65


def _forming_planets(limit: int = 12) -> List[Dict[str, Any]]:
    """forming_report.json 的实时「正在形成」信号 → planets。

    ★ 为什么（2026-10-06）：trend_event 是 trend_engine 老管线的产物
      （两天前、大量同题重复），而 forming_report 每 10 分钟刷新、
      是当前唯一活着的热点信号源。forming 信号排前面，老事件垫后。
    """
    from paths import STATE
    path = os.path.join(str(STATE), "forming_report.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
    except (ValueError, OSError):
        return []
    generated = report.get("generated_at") or ""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for f in (report.get("forming_game") or [])[:limit]:
        word = str(f.get("word") or "").strip()
        if not word or word in seen:
            continue
        seen.add(word)
        plat_key = _FORMING_PLATFORM.get(f.get("platform") or "", "weibo")
        rate_text = str(f.get("rate") or "")
        extra = str(f.get("extra") or "")
        velocity = _forming_velocity(rate_text, f.get("trend_rate"))
        stage = str(f.get("stage") or "forming")
        out.append({
            "id": f"forming:{plat_key}:{hashlib.md5(word.encode('utf-8')).hexdigest()[:10]}",
            "title": word,
            "lifecycle": _FORMING_LIFECYCLE.get(stage, "EMERGING"),
            "heat": None,
            "velocity": velocity,
            "confidence": None,
            "contentCount": 0,
            "platformCount": 1,
            "platforms": [plat_key],
            "entities": [],
            "relevance": None,
            "relevanceLevel": "L2",
            "relevanceNote": "词表判定游戏相关（forming 实时信号）",
            "nIdeas": 0,
            "firstSeen": generated or None,
            "radius": 0.42,
            "glowRank": velocity,
            "density": 0.4,
            "game": None,
            "gameName": word[:18],
            "cover": None,
            "appId": None,
            "description": " · ".join(x for x in (rate_text, extra) if x),
            # forming 专属：卡片 <p> 显示 title 第一段之后的内容
            "isForming": True,
            "formingStage": stage,
        })
    return out




def _representative_titles(rows: List[Dict[str, Any]]) -> Dict[str, tuple]:
    """trend_event.representative_content_id → (标题, 平台)。

    ★ 为什么（2026-10-07 用户反馈"标题语义看不出来任何东西"）：
      canonical_title 是实体+通用词的碎片（"明日方舟 庆典"），没有事件语义；
      而每条事件的代表内容标题就是"人们实际在说什么"
      （"最速详评！六星削弱者灵知，干员使用指南"）。100% 可解析（实测 60/60）。
    """
    ids = [e.get("representative_content_id") for e in rows
           if e.get("representative_content_id")]
    if not ids:
        return {}
    try:
        from paths import STATE
        con = sqlite3.connect(os.path.join(str(STATE), "l2_processed.sqlite3"))
        con.row_factory = sqlite3.Row
        out: Dict[str, tuple] = {}
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            q = ",".join("?" * len(chunk))
            for r in con.execute(
                    f"SELECT content_id, raw_title, normalized_title, platform "
                    f"FROM content WHERE content_id IN ({q})", chunk):
                t = (r["raw_title"] or r["normalized_title"] or "").strip()
                t = re.sub(r"<[^>]+>", "", t)
                out[r["content_id"]] = (t[:60], r["platform"] or "")
        con.close()
        return out
    except sqlite3.Error:
        return {}




def _event_evidence(event_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """事件工作区的证据 = event_content 关联的**全部汇聚内容**（最新优先）。

    ★ 用户 2026-10-07 点破的本质：热点是几十条内容汇聚，不是一条代表视频。
      工作区证据就该摊开这一批。
    """
    try:
        from paths import STATE
        l3 = sqlite3.connect(os.path.join(str(STATE), "l3_trend.sqlite3"))
        ids = [r[0] for r in l3.execute(
            "SELECT content_id FROM event_content WHERE event_id=? "
            "ORDER BY similarity_score DESC LIMIT ?", (event_id, limit * 2))]
        l3.close()
        if not ids:
            return []
        l2 = sqlite3.connect(os.path.join(str(STATE), "l2_processed.sqlite3"))
        l2.row_factory = sqlite3.Row
        q = ",".join("?" * len(ids))
        out: List[Dict[str, Any]] = []
        for r in l2.execute(
                f"SELECT raw_title, normalized_title, normalized_text, platform, "
                f"published_at FROM content WHERE content_id IN ({q})", ids):
            title = re.sub(r"<[^>]+>", "", r["raw_title"] or r["normalized_title"] or "").strip()
            if not title:
                continue
            body = re.sub(r"<[^>]+>|\s+", " ", r["normalized_text"] or "").strip()
            out.append({
                "title": title[:80],
                "text": (body[:180] or title),
                "platform": r["platform"] or "",
                "url": None,
                "published_at": r["published_at"],
            })
            if len(out) >= limit:
                break
        l2.close()
        return out
    except sqlite3.Error:
        return []


def _forming_evidence(word: str, limit: int = 12) -> List[Dict[str, Any]]:
    """forming 信号的证据 = agent 围绕该话题的跨平台搜索结果。"""
    from paths import STATE
    csv_path = os.path.join(os.path.dirname(str(STATE)), "raw", "agent", "search_results.csv")
    if not os.path.exists(csv_path):
        return []
    import csv as _csv
    out: List[Dict[str, Any]] = []
    seen: set = set()
    plat_cn = {"bili": "bilibili", "weibo": "weibo", "tieba": "tieba"}
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for r in _csv.DictReader(f):
                if (r.get("trigger_word") or r.get("query") or "").strip() != word:
                    continue
                title = re.sub(r"</?em[^>]*>", "", r.get("title") or "").strip()
                key = (r.get("item_id") or title)
                if not title or key in seen:
                    continue
                seen.add(key)
                out.append({
                    "title": title[:80],
                    "text": title,
                    "platform": plat_cn.get(r.get("platform") or "", r.get("platform") or ""),
                    "url": r.get("url") or None,
                    "published_at": None,
                })
                if len(out) >= limit:
                    break
    except OSError:
        pass
    return out

def forming_workspace(event_id: str) -> Optional[Dict[str, Any]]:
    """forming 信号的最小 workspace（诚实版）：

    摘要 = 信号本身（词 + 回帖变化 + 来源吧）；evidence 给空 —— 帖子正文
    和跨平台证据还没接进 forming 层，不造假摘要。
    """
    for p in _forming_planets(limit=30):
        if p.get("id") == event_id:
            title = p.get("title") or ""
            game = p.get("gameName") or ""
            plat_cn = {"weibo": "微博", "baidu": "百度", "bilibili": "哔哩哔哩",
                       "tieba": "贴吧", "media": "游戏媒体", "agent": "搜索"}
            plats = "、".join(plat_cn.get(x, x) for x in (p.get("platforms") or []))
            lead = f"「{title}」" if title != game else f"「{game}」的"
            return {
                "event_id": event_id,
                "forming": True,
                "title": title,
                "summary": (f"{lead}相关讨论正在形成，信号来自{plats}，传播势头 "
                            f"{round((p.get('velocity') or 0) * 100)}/100。"),
                "evidence": _forming_evidence(title),
                "ideas": [],
            }
    return None


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
        "ORDER BY momentum_score DESC LIMIT ?", (limit,)).fetchall()]
    # ★ 同题去重（2026-10-06）：老管线同题碎片成灾（arknights×5 同分重复），
    #   卡片列表一片重复。同 canonical_title 只留 momentum 最高的一条。
    _dedup: Dict[str, Dict[str, Any]] = {}
    for e in events:
        k = re.sub(r"\s+", "", (e.get("canonical_title") or "").lower())
        if k and (k not in _dedup or (e.get("momentum_score") or 0)
                  > (_dedup[k].get("momentum_score") or 0)):
            _dedup[k] = e
    events = list(_dedup.values())
    # ★ 碎片事件闸门：canonical_title 是"上线/版本"这类通用词碎片、
    #   且内容量和势头都垫底的，是老管线切词的残渣——不进前端。
    events = [e for e in events
              if len((e.get("canonical_title") or "").strip()) > 2
              or (e.get("content_count") or 0) >= 10]
    events = [e for e in events
              if (e.get("content_count") or 0) >= 5
              or (e.get("momentum_score") or 0) >= 0.15]
    if not events:
        return {"error": "没有进行中的话题", "planets": [], "core": {}}
    rep_titles = _representative_titles(events)

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

    planets: List[Dict[str, Any]] = _forming_planets()
    n_forming = len(planets)
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
        game = _game_of(ents, e.get("canonical_title") or "")
        app_id = str(APP_IDS.get(game or "")) if game else None
        cover_rec = by_app.get(app_id) if app_id else None
        cover_rec = None
        rep = rep_titles.get(e.get("representative_content_id") or "")
        planets.append({
            "id": eid,
            "title": _display_title(e.get("canonical_title") or eid, game),
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
            # ★ 代表内容标题 = 卡片上真正有语义的那行
            "description": (rep[0] if rep and rep[0] else None),
            "representativePlatform": (rep[1] if rep and rep[1] else None),
            "gameName": (cover_rec or {}).get("name") or _GAME_CN.get(game or ""),
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
        "degradeNotes": ([
            f"含 {n_forming} 条 forming 实时信号（每 10 分钟刷新）"
        ] if n_forming else []) + [
            f"热度与势头按排名映射（真实分布极窄，绝对值不可视）",
            f"相关性：{n_l1} 个真实 / {n_l2} 个按游戏档案推测 / {n_l3} 个未知",
            f"跨平台话题：{sum(1 for p in planets if p['platformCount'] > 1)}/{len(planets)}",
            f"有真实封面的：{sum(1 for p in planets if p['cover'])}/{len(planets)}"
            f"（其余卡片无图，不放假图）",
        ],
        "games": covers,
        "ideas": ideas,
    }
