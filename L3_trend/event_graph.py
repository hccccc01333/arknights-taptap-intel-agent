#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_trend/event_graph.py —— 事件之上的图（GraphRAG）。

★ 解决什么（承接 event_resolver）：
  event_resolver 只做「**同一件事**」的合并 —— 把各平台对同一事件的重复上报并成一个点。
  但事件之间还有别的关系：谁引发谁、谁在哪个阶段、哪些属于同一波连锁反应、
  哪些发生在同一个游戏上。**这些关系是"全网追踪"真正的情报**：
  知道「A 事件导致 B 事件」比知道「A 在三个平台上榜」更有运营价值。

★ 图的形状（节点/边都是事实，不是猜测）：
    节点
      external_event  站外事件（跨平台合并后的）
      taptap_topic    站内话题（L3 已聚类的事件）
      game            游戏（从 games/*.json 的别名识别）
    边（关系类型由 LLM 判定，规则只负责召回）
      same_event      同一件事（event_resolver 已并过，这里兜底）
      derived_from    衍生/引发：A 引出了 B
      escalation      升级：A 是 B 的早期阶段
      same_cluster    同一波连锁（不直接因果，但明显相关）
      verified_in     站外热点在站内话题里出现了 → ★跨平台验证
      about_game      事件属于某个游戏

★ 为什么要 LLM 判关系类型（不能只用相似度）：
  「某游戏新角色立绘被改」和「某主播吐槽立绘」相似度很高，但关系是 derived_from；
  和「新版本上线」也相似，关系是 same_cluster。
  相似度分不出"这俩是同一件事的不同阶段"和"这俩互相无关只是都在讲立绘"。

用法：
    python event_graph.py                 # 建图 + LLM 判关系
    python event_graph.py --no-llm        # 只用规则边
输出：data/state/event_graph.json（webapp 可直接渲染）
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "L1_data_source"),
           os.path.join(_ROOT, "L4_intelligence"), os.path.join(_ROOT, "L6_execution")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

EVENTS_JSON = os.path.join(_ROOT, "data", "state", "hot_events.json")
OUT_PATH = os.path.join(_ROOT, "data", "state", "event_graph.json")
L3_DB = os.path.join(_ROOT, "data", "state", "l3_trend.sqlite3")

REL_ZH = {
    "same_event": "同一件事",
    "derived_from": "衍生/引发",
    "escalation": "升级演化",
    "same_cluster": "同一波连锁",
    "verified_in": "站内已验证",
    "about_game": "归属游戏",
}
# 图算法只把"有关系"的边计入传播分析；归属游戏是弱属性边
STRUCTURAL_RELS = {"derived_from", "escalation", "same_cluster", "verified_in"}

REL_PROMPT = """你在做**事件关系判定**（event relation extraction）。

【事件A】{a_title}
  平台：{a_platform}｜首次出现 {a_at}｜关联游戏：{a_game}

【事件B】{b_title}
  平台：{b_platform}｜首次出现 {b_at}｜关联游戏：{b_game}

判断 A 与 B 的关系（选一个）：
- same_event      同一件事（只是不同平台/不同措辞的上报）
- derived_from    A 引出了/导致了 B（A 是 B 的起因）
- escalation      同一事件的两个阶段（A 是早期、B 是升级或恶化）
- same_cluster    明显相关但不直接因果（属于同一波话题/同一个作品/同类争议）
- unrelated       没关系（只是碰巧都上了榜）

注意：
- 时间上 A 更早且 B 像是被 A 带起来的 → derived_from
- 只是都在讲同一款游戏/同一部作品，不构成事件关联 → unrelated
- 拿不准就 unrelated（宁可漏，不要编因果）

严格按 JSON：{{"relation":"...","reason":"一句话（中文，≤30字）"}}"""


# ---------------------------------------------------------------- 节点
def game_aliases() -> Dict[str, str]:
    out: Dict[str, str] = {}
    gdir = os.path.join(_ROOT, "games")
    if not os.path.isdir(gdir):
        return out
    for fn in os.listdir(gdir):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(gdir, fn), encoding="utf-8") as f:
                d = json.load(f)
        except (ValueError, OSError):
            continue
        name = d.get("name")
        if not name:
            continue
        for k in [name, d.get("key")] + list(d.get("aliases") or []):
            if k:
                out[str(k).lower()] = name
    return out


# ★ 游戏识别不能只靠 games/*.json —— 那里只有我们建了档案的（目前只有方舟/鸣潮），
#   但站外榜上出现的是全行业游戏（绝区零/永劫/三角洲/原神…）。所以：
#     ① 档案别名（权威，但不全）
#     ② 常见在榜游戏的别名补充表（覆盖行业面，只做识别不做业务判断）
_COMMON_GAME_ALIASES = {
    "绝区零": ["绝区零", "zenless", "绝区零游戏"],
    "永劫无间": ["永劫无间", "永劫", "naraka"],
    "三角洲行动": ["三角洲行动", "三角洲", "delta force", "dfm"],
    "原神": ["原神", "genshin"],
    "崩坏：星穹铁道": ["崩铁", "星穹铁道", "星铁", "honkai star rail", "hsr"],
    "崩坏3": ["崩坏3", "崩坏三"],
    "王者荣耀": ["王者荣耀", "王者", "honor of kings"],
    "和平精英": ["和平精英"],
    "蛋仔派对": ["蛋仔派对", "蛋仔"],
    "第五人格": ["第五人格"],
    "光遇": ["光遇", "光·遇", "sky"],
    "明日方舟": ["明日方舟", "方舟", "arknights"],
    "鸣潮": ["鸣潮", "wuthering waves", "鸣潮"],
    "塞尔达": ["塞尔达", "zelda"],
    "黑神话": ["黑神话悟空", "黑神话", "black myth"],
    "我的世界": ["我的世界", "minecraft"],
    "炉石传说": ["炉石传说", "炉石"],
    "csgo": ["cs2", "csgo", "cs"],
}
GAME_ALIASES = game_aliases()
for _canon, _aliases in _COMMON_GAME_ALIASES.items():
    for _a in _aliases:
        GAME_ALIASES.setdefault(_a.lower(), _canon)


def detect_game(text: str) -> str:
    low = (text or "").lower()
    for a in sorted(GAME_ALIASES, key=len, reverse=True):
        if a and a in low:
            return GAME_ALIASES[a]
    return ""


def load_channel_nodes() -> List[Dict[str, Any]]:
    """★ 各渠道的**原始**热点条目作为节点（不只跨平台合并结果）。

    为什么：合并结果只保留"同一件事被多平台上报"的那几条
    （实测大部分社会新闻会合并掉），但**游戏类热点常常只在一个平台出现**
    （这次 B站 21 条游戏热点，合并后剩不下）—— 所以图要用原始条目，
    合并结果作为"同事件归并"的附加属性。
    """
    nodes: List[Dict[str, Any]] = []
    for plat, path, col in (
        ("bilibili", os.path.join(_ROOT, "data/raw/bilibili/hot_videos.csv"), "title"),
        ("weibo", os.path.join(_ROOT, "data/raw/weibo/hot_search.csv"), "word"),
        ("baidu", os.path.join(_ROOT, "data/raw/baidu_index/hot_search.csv"), "word"),
    ):
        if not os.path.exists(path):
            continue
        first, latest = {}, {}
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                k = (r.get(col) or "").strip()[:80]
                if not k:
                    continue
                ts = r.get("observed_at") or ""
                if k not in first or ts < first[k]:
                    first[k] = ts
                if k not in latest or ts > (latest.get(k) or {}).get("observed_at", ""):
                    latest[k] = r
        for k, r in latest.items():
            game = detect_game(k)
            if not game:
                continue          # ★ 只要游戏相关的（图的语义是游戏舆情）
            nodes.append({
                "id": f"{plat}:{abs(hash(k)) % 10**8}",
                "type": "external_event", "title": k[:80],
                "platforms": [{"platform": plat, "platform_zh": plat}],
                "platform_count": 1,
                "at": first.get(k) or r.get("observed_at") or "",
                "game": game, "all_titles": [k],
                "hot": int(r.get("hot_score") or r.get("play") or 0),
            })
    return nodes


def load_external_nodes() -> List[Dict[str, Any]]:
    if not os.path.exists(EVENTS_JSON):
        return []
    with open(EVENTS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    nodes = []
    for e in data.get("events") or []:
        titles = [i.get("title") for p in (e.get("platforms") or []) for i in (p.get("items") or [])]
        game = ""
        for t in titles:
            game = detect_game(t or "")
            if game:
                break
        nodes.append({
            "id": e.get("event_id"),
            "type": "external_event",
            "title": (e.get("title") or "")[:80],
            "platforms": [p.get("platform_zh") for p in (e.get("platforms") or [])],
            "platform_count": e.get("platform_count"),
            "at": e.get("first_seen_at"),
            "game": game,
            "all_titles": titles[:6],
        })
    return nodes


def load_taptap_nodes(limit: int = 40) -> List[Dict[str, Any]]:
    """站内话题（L3 聚类出的话题）—— 用来验证站外热点在社区里有没有讨论。"""
    if not os.path.exists(L3_DB):
        return []
    con = sqlite3.connect(L3_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT event_id, canonical_title, entity_ids, first_detected_at, last_updated_at, "
        "content_count, hot_score, lifecycle FROM trend_event "
        "WHERE status='active' ORDER BY content_count DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    out = []
    for r in rows:
        title = (r["canonical_title"] or "").strip()
        ents = []
        try:
            ents = json.loads(r["entity_ids"] or "[]")
        except ValueError:
            pass
        games = [e.split("_", 1)[1] for e in ents
                 if e.startswith("game_") and e.split("_", 1)[1] in
                 {v.lower() for v in GAME_ALIASES.values()} or e.startswith("game_")]
        out.append({
            "id": r["event_id"],
            "type": "taptap_topic",
            "title": title[:80],
            "content_count": r["content_count"],
            "hot_score": r["hot_score"],
            "lifecycle": r["lifecycle"],
            "at": r["first_detected_at"] or r["last_updated_at"],
            "game": detect_game(title) or (GAME_ALIASES.get((games[0] or "").lower()) if games else ""),
            "all_titles": [title] if title else [],
        })
    return out


# ---------------------------------------------------------------- 召回：候选边
def candidate_edges(nodes: List[Dict[str, Any]], sim_min: float = 0.62
                    ) -> List[Tuple[float, Dict, Dict]]:
    """候选边：语义相似度（bge）。规则只负责召回，不负责下结论。"""
    ext = [n for n in nodes if n["type"] == "external_event"]
    tap = [n for n in nodes if n["type"] == "taptap_topic"]
    pairs = []
    if len(ext) < 1 or (len(ext) + len(tap)) < 2:
        return pairs
    try:
        import torch
        import torch.nn.functional as F
        from transformers import AutoModel, AutoTokenizer
        tok = AutoTokenizer.from_pretrained("BAAI/bge-small-zh-v1.5")
        mdl = AutoModel.from_pretrained("BAAI/bge-small-zh-v1.5")
        mdl.eval()

        def enc(items):
            vs = []
            with torch.no_grad():
                for i in range(0, len(items), 32):
                    b = tok([x["title"][:64] or "" for x in items[i:i + 32]], padding=True,
                             truncation=True, max_length=96, return_tensors="pt")
                    v = F.normalize(mdl(**b).last_hidden_state[:, 0], dim=-1)
                    vs.append(v)
            return torch.cat(vs) if vs else None

        Ve = enc(ext)
        Vt = enc(tap) if tap else None
        for i, a in enumerate(ext):
            # 站外 ↔ 站内（验证关系）
            if Vt is not None:
                sims = (Vt @ Ve[i]).tolist()
                for j, s in enumerate(sims):
                    if s >= sim_min:
                        pairs.append((float(s), a, tap[j]))
            # 站外 ↔ 站外（衍生/连锁）
            for j in range(i + 1, len(ext)):
                s = float(Ve[i] @ Ve[j])
                if s >= sim_min + 0.05:      # 事件之间门槛更高
                    pairs.append((s, a, ext[j]))
    except Exception as e:
        print(f"[warn] 语义召回不可用（{str(e)[:70]}），只用游戏/实体规则边", file=sys.stderr)
        pairs = []
        # 降级：同游戏 + 时间邻近
        for i, a in enumerate(ext):
            for b in ext[i + 1:]:
                if a.get("game") and a["game"] == b.get("game"):
                    pairs.append((0.75, a, b))
    return pairs


# ---------------------------------------------------------------- LLM 判关系
def judge_relations(pairs, use_llm: bool = True, max_calls: int = 20
                    ) -> List[Dict[str, Any]]:
    try:
        if use_llm:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(_ROOT, ".env"))
            load_dotenv(os.path.join(_ROOT, "common", ".env"))
            from intelligence.llm import ModelRouter
        else:
            raise ImportError("--no-llm")
    except ImportError as e:
        print(f"[warn] LLM 不可用（{e}），关系只按游戏/相似度粗判", file=sys.stderr)
        return [{"sim": round(s, 3), "source": a["id"], "target": b["id"],
                 "relation": "about_game" if (a.get("game") and a["game"] == b.get("game")) else "unrelated",
                 "reason": "规则粗判（LLM 不可用）"} for s, a, b in pairs]

    router = ModelRouter(enabled=True)
    if not router.enabled:
        return [{"sim": round(s, 3), "source": a["id"], "target": b["id"],
                 "relation": "about_game" if (a.get("game") and a["game"] == b.get("game")) else "unrelated",
                 "reason": "规则粗判（LLM 不可用）"} for s, a, b in pairs]

    edges = []
    for s, a, b in pairs[:max_calls]:
        # 站外 ↔ 站内：天然就是"验证"关系，但也要确认是不是同一件事
        if a["type"] != b["type"]:
            rel = "verified_in" if s >= 0.80 else "unrelated"
            reason = "站外热点在站内话题里出现" if rel == "verified_in" else "语义相似但对不上"
        else:
            prompt = REL_PROMPT.format(
                a_title=a["title"][:60], a_platform="、".join(p.get("platform_zh", p.get("platform","")) for p in (a.get("platforms") or [])),
                a_at=(a.get("at") or "")[:16], a_game=a.get("game") or "未识别",
                b_title=b["title"][:60],
                b_platform="、".join(p.get("platform_zh", p.get("platform","")) for p in (b.get("platforms") or [])) if b["type"] == "external_event" else "TapTap站内",
                b_at=(b.get("at") or "")[:16], b_game=b.get("game") or "未识别")
            try:
                out = router.call_json("relevance", prompt, max_tokens=400)
                rel = (out or {}).get("relation") or "unrelated"
                reason = str((out or {}).get("reason") or "")[:40]
            except Exception:
                rel, reason = "unrelated", "LLM 调用失败，保守判无关"
        if rel in STRUCTURAL_RELS:
            edges.append({"sim": round(s, 3), "source": a["id"], "target": b["id"],
                          "relation": rel, "reason": reason})
    return edges


# ---------------------------------------------------------------- 簇与传播路径
def build_clusters(nodes, edges) -> List[Dict[str, Any]]:
    """并查集：把有结构关系的事件并成「事件簇」（一波连锁反应）。"""
    parent = {n["id"]: n["id"] for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for e in edges:
        if e["relation"] in STRUCTURAL_RELS and e["source"] in parent and e["target"] in parent:
            union(e["source"], e["target"])

    groups: Dict[str, List[str]] = defaultdict(list)
    for nid in parent:
        groups[find(nid)].append(nid)
    by_id = {n["id"]: n for n in nodes}
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue                       # 单点不算簇
        ms = sorted((by_id[m] for m in members), key=lambda n: n.get("at") or "")
        games = sorted({n["game"] for n in ms if n.get("game")})
        clusters.append({
            "cluster_id": f"clu_{abs(hash(ms[0]['id'])) % 10**8}",
            "title": ms[0]["title"][:60],
            "size": len(ms),
            "games": games,
            "external": sum(1 for n in ms if n["type"] == "external_event"),
            "taptap": sum(1 for n in ms if n["type"] == "taptap_topic"),
            "start_at": ms[0].get("at"),
            "timeline": [{"id": n["id"], "title": n["title"][:50],
                          "type": n["type"], "at": n.get("at")} for n in ms],
        })
    clusters.sort(key=lambda c: -c["size"])
    return clusters


def propagation_paths(clusters, edges) -> List[Dict[str, Any]]:
    """簇内按时间排序 → 传播路径的素材（谁先起、多久跟进）。"""
    out = []
    for c in clusters:
        tl = [t for t in c["timeline"] if t.get("at")]
        if len(tl) < 2:
            continue
        path = []
        prev = None
        for t in tl:
            gap_h = None
            if prev:
                try:
                    gap_h = round(
                        (datetime.fromisoformat(t["at"]) - datetime.fromisoformat(prev["at"])
                         ).total_seconds() / 3600, 1)
                except (ValueError, TypeError):
                    pass
            path.append({"title": t["title"][:40], "type": t["type"],
                         "at": (t["at"] or "")[:16], "gap_h": gap_h})
            prev = t
        out.append({"cluster_id": c["cluster_id"], "title": c["title"], "path": path})
    return out


def build(use_llm: bool = True) -> Dict[str, Any]:
    ext = load_channel_nodes()
    tap = load_taptap_nodes()
    nodes = ext + tap
    # 游戏节点（被事件引用才建）
    games = sorted({n["game"] for n in nodes if n.get("game")})
    for g in games:
        nodes.append({"id": f"game:{g}", "type": "game", "title": g,
                      "at": None, "game": g, "platforms": [], "platform_count": 0})

    pairs = candidate_edges(nodes)
    edges = judge_relations(pairs, use_llm=use_llm)
    # 归属游戏边（弱属性，不入簇）
    game_edges = [{"sim": 1.0, "source": n["id"], "target": f"game:{n['game']}",
                   "relation": "about_game", "reason": "事件归属该游戏"}
                  for n in nodes if n.get("game") and n["type"] != "game"]
    all_edges = edges + game_edges
    clusters = build_clusters(nodes, edges)
    paths = propagation_paths(clusters, edges)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "nodes": nodes, "edges": all_edges,
        "structural_edges": edges,
        "clusters": clusters, "propagation_paths": paths,
        "stats": {
            "external_events": len(ext), "taptap_topics": len(tap),
            "games": len(games), "candidate_pairs": len(pairs),
            "structural_edges": len(edges), "game_edges": len(game_edges),
            "clusters": len(clusters),
        },
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="事件图（GraphRAG：事件间关系 + 传播路径）")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()

    res = build(use_llm=not args.no_llm)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    st = res["stats"]
    print(f"节点 {st['external_events']} 站外 + {st['taptap_topics']} 站内 + {st['games']} 游戏")
    print(f"候选对 {st['candidate_pairs']} → 结构边 {st['structural_edges']} + 归属边 {st['game_edges']}")
    print(f"事件簇 {st['clusters']} 个")
    for e in res["structural_edges"][:8]:
        src = next((n["title"][:34] for n in res["nodes"] if n["id"] == e["source"]), "?")
        tgt = next((n["title"][:34] for n in res["nodes"] if n["id"] == e["target"]), "?")
        print(f"  [{REL_ZH.get(e['relation'], e['relation'])}] {src}")
        print(f"        → {tgt}   {e['reason']}")
    for c in res["clusters"][:3]:
        print(f"簇【{c['title'][:36]}】{c['size']} 个节点 / {c['games']}")
        for p in res["propagation_paths"]:
            if p["cluster_id"] == c["cluster_id"]:
                for i, s in enumerate(p["path"]):
                    gap = f" +{s['gap_h']}h" if s.get("gap_h") is not None else ""
                    print(f"    {i+1}. [{s['type']}] {s['title'][:34]} @{s['at']}{gap}")
    print(f"结果已存 {args.out}")
