#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Graph-based Community Detection（设计：替代相似度聚类的碎片化问题）。

★ 为什么不用相似度聚类：3811 条内容按两两相似度分，产出 211 个碎片事件
  （26 个都叫 arknights，每个 2-3 条），运营无法阅读。
  根因：**聚类的单位是"一条内容"，而运营关心的单位是"一个讨论社群"。**

★ 做法：
  ① 从 L2 内容建实体共现图：同一条内容里的两个实体连一条边，边权 = 共现次数
  ② 贪心模块度社区检测（stdlib networkx，零额外依赖）
  ③ 每个社区 = 一组关联实体 = 一个讨论群
  ④ 内容按其实体的社区归属分组

★ 诚实边界：
  · networkx 的 greedy_modularity 不是 Leiden（效果略差但零依赖，够 MVP）
  · 粒子密度极低的内容（只有 1 条）没有足够信号分社区 → 归入"杂项"
  · 时间窗口约束（跨 6h 分开）等连续采集后加，当前单日快照无意义
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx

# ★ 泛事件实体不进共现图：它们几乎每条内容都出现，
#   会把不相干的游戏（战双/艾希/鸣潮）全拉进同一个"社群"。
#   事件本身仍保留这些实体标注，只是不作为连边依据。
GENERIC_EVENT_LABELS = {
    "联动", "上线", "开服", "公测", "直播", "更新", "版本", "复刻",
    "周年庆", "庆典", "测试", "内测", "活动", "公告", "卡池", "爆料",
    "DLC", "dlc", "补偿", "福利", "抽卡",
}


def _is_generic_entity(e: str) -> bool:
    if not e.startswith("event_"):
        return False
    return e.split("_", 1)[1] in GENERIC_EVENT_LABELS


def build_entity_graph(contents: List[Dict[str, Any]]) -> nx.Graph:
    """从内容列表建实体共现图。

    每条内容的实体列表里，任意两个实体连一条边（权重=共现次数）。
    只有 game_ 和 event_ 前缀的实体参与建图（genre/developer 太泛，会把不相关内容连在一起）。
    """
    G = nx.Graph()
    edge_weights: Dict[Tuple[str, str], int] = defaultdict(int)
    node_meta: Dict[str, Dict] = {}

    for c in contents:
        ents = c.get("entities") or []
        if isinstance(ents, str):
            try:
                ents = json.loads(ents)
            except (ValueError, TypeError):
                ents = []
        # 只用有区分力的实体：排除平台前缀与泛事件词
        useful = [e for e in ents
                  if len(e) > 4 and not e.startswith("platform_") and not _is_generic_entity(e)]
        if len(useful) < 1:
            continue
        for e in useful:
            node_meta.setdefault(e, {"label": e.split("_", 1)[1], "count": 0})
            node_meta[e]["count"] += 1
        # 两两连边
        for i in range(len(useful)):
            for j in range(i + 1, len(useful)):
                a, b = sorted([useful[i], useful[j]])
                edge_weights[(a, b)] += 1

    for (a, b), w in edge_weights.items():
        G.add_edge(a, b, weight=w)
    for node, meta in node_meta.items():
        G.add_node(node, **meta)

    return G


def detect_communities(G: nx.Graph, min_size: int = 2) -> List[List[str]]:
    """社区检测：greedy modularity（stdlib），过滤太小的社区。"""
    if not G.nodes:
        return []
    comms = nx.algorithms.community.greedy_modularity_communities(G, weight="weight")
    return [sorted(c) for c in comms if len(c) >= min_size]


def assign_contents_to_communities(
    contents: List[Dict[str, Any]],
    communities: List[List[str]],
) -> Dict[int, List[Dict[str, Any]]]:
    """把内容分配到社区：内容的实体命中哪个社区最多，就归哪个。

    没命中任何社区的内容 → 归 -1（杂项），不假装有归属。
    """
    ent_to_comm: Dict[str, int] = {}
    for i, comm in enumerate(communities):
        for ent in comm:
            ent_to_comm[ent] = i

    result: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for c in contents:
        ents = c.get("entities") or []
        if isinstance(ents, str):
            try:
                ents = json.loads(ents)
            except (ValueError, TypeError):
                ents = []
        useful = [e for e in ents
                  if len(e) > 4 and not e.startswith("platform_") and not _is_generic_entity(e)]
        scores: Dict[int, int] = defaultdict(int)
        for e in useful:
            if e in ent_to_comm:
                scores[ent_to_comm[e]] += 1
        if scores:
            best = max(scores, key=scores.get)
            result[best].append(c)
        else:
            result[-1].append(c)   # 杂项
    return dict(result)


def community_report(
    comm_id: int,
    entities: List[str],
    contents: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """为一个社区生成结构化报告（规则模板版，LLM 后续升级）。"""
    n = len(contents)
    total_content = sum(c.get("content_count", 0) or 0 for c in contents) if contents else 0
    platforms: Counter = Counter()
    heats: List[float] = []
    titles: List[str] = []
    for c in contents:
        for p in (c.get("platforms") or []):
            platforms[p] += 1
        heats.append(c.get("hot_score") or 0)
        t = c.get("canonical_title") or c.get("title") or ""
        if t and len(t) > 3:
            titles.append(t)

    max_heat = max(heats) if heats else 0
    avg_heat = sum(heats) / len(heats) if heats else 0

    return {
        "community_id": f"comm_{comm_id}",
        "entities": entities,
        "entity_labels": [e.split("_", 1)[1] for e in entities[:8]],
        "n_events": n,
        "total_content": total_content,
        "avg_heat": round(avg_heat, 4),
        "max_heat": round(max_heat, 4),
        "platforms": dict(platforms.most_common()),
        "top_titles": titles[:5],
    }


def full_pipeline(contents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """完整流程：图构建 → 社区检测 → 内容分配 → 报告生成。"""
    G = build_entity_graph(contents)
    communities = detect_communities(G)
    assigned = assign_contents_to_communities(contents, communities)

    reports = []
    for i, ents in enumerate(communities):
        items = assigned.get(i, [])
        if items:
            reports.append(community_report(i, ents, items))

    misc = assigned.get(-1, [])
    return {
        "communities": reports,
        "misc_count": len(misc),
        "total_assigned": sum(len(v) for v in assigned.values() if v is not assigned.get(-1)),
        "graph_stats": {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "n_communities": len(communities),
        },
    }
