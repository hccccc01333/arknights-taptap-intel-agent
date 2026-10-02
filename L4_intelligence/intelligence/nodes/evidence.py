# -*- coding: utf-8 -*-
"""Node 1：Evidence Builder（§8 / §9）。

规格原话：**第一步不是 Agent，而是 Evidence Builder**。后面所有 Agent 都基于同一个事实包。

关键设计（§9）：每条证据必须带**事实层级**
    PRIMARY(官方) > SECONDARY(新闻/媒体) > COMMUNITY(玩家) > INFERRED(推断)
否则 LLM 会把「玩家猜测」写成「官方事实」—— 这是情报幻觉的主要来源。

★ 不放原文：excerpt 只是短引文（≤120 字）+ content_id，需要全文时用 `upstream.read_full_text`
  在节点内临时读，不写回 State。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..state import FACT_TIERS, _MAX_EVIDENCE_CHARS

EVIDENCE_VERSION = "evidence-1.0"
EXCERPT_LEN = _MAX_EVIDENCE_CHARS

# 官方/媒体的判定信号（规则；有 LLM 时可交给模型，但层级本身必须是结构字段）
_OFFICIAL_HINTS = ("官方", "公告", "官宣", "正式", "宣布", "运营", "开发组", "制作组")
_NEWS_HINTS = ("报道", "讯", "媒体", "记者", "独家", "消息人士")


def _tier_of(content: Dict[str, Any]) -> str:
    """★ 实测踩坑（2026-10-02）：**"提到官方" ≠ "官方发布"**。

    本机 3811 条内容里 is_official=True 只有 30 条，但含"官方/官宣"关键词的有 108 条，
    且几乎全是玩家/媒体的**转述**（如"三角洲官宣联动…"）。
    最初把关键词命中判成 PRIMARY，结果 Evidence Pack 的 trigger 变成了一条玩家提问 ——
    这正是 §9 要防的事（把玩家猜测写成官方事实）。

    修法：**PRIMARY 只认 `is_official` 这个结构字段**；关键词命中降级为 SECONDARY
    （"疑似官方信源"），转述/讨论一律 COMMUNITY。
    """
    sf = content.get("source_features") or {}
    if sf.get("is_official"):
        return "PRIMARY"
    text = (content.get("normalized_title") or "") + " " + (content.get("normalized_text") or "")
    if any(h in text for h in _OFFICIAL_HINTS):
        return "SECONDARY"      # 疑似官方（转述），不是官方本身
    if any(h in text for h in _NEWS_HINTS):
        return "SECONDARY"
    return "COMMUNITY"


def _metrics_of(content: Dict[str, Any]) -> Dict[str, Any]:
    m = content.get("metrics") or {}
    return {k: m.get(k) for k in ("views", "likes", "comments", "shares") if m.get(k) is not None}


def _excerpt(content: Dict[str, Any]) -> str:
    t = (content.get("normalized_title") or content.get("normalized_text") or "").strip()
    return t[:EXCERPT_LEN]


def build(event: Dict[str, Any], members: List[Dict[str, Any]],
          max_evidence: int = 12) -> Dict[str, Any]:
    """构建 Evidence Pack。members 是 L2 hydrate 过的内容行。"""
    # 层级高的排前面，同层级按互动量排 —— 让后面的 Agent 先看到最硬的证据
    def _rank(c: Dict[str, Any]) -> tuple:
        tier_rank = {"PRIMARY": 3, "SECONDARY": 2, "COMMUNITY": 1, "INFERRED": 0}[_tier_of(c)]
        m = _metrics_of(c)
        return (-tier_rank, -(m.get("views") or 0) - (m.get("comments") or 0) * 5)

    ordered = sorted(members, key=_rank)[:max_evidence]
    evidence: List[Dict[str, Any]] = []
    for i, c in enumerate(ordered):
        evidence.append({
            "evidence_id": f"ev_{i + 1}",
            "content_id": c.get("content_id"),
            "tier": _tier_of(c),
            "platform": c.get("platform"),
            "excerpt": _excerpt(c),
            "metrics": _metrics_of(c),
            "published_at": c.get("published_at") or c.get("observed_at"),
            "quality": c.get("quality_score"),
        })

    # 平台分布（跨平台扩散的静态证据）
    platforms: Dict[str, Dict[str, Any]] = {}
    for c in members:
        p = c.get("platform") or "unknown"
        d = platforms.setdefault(p, {"count": 0, "views": 0, "comments": 0, "tiers": {}})
        d["count"] += 1
        m = _metrics_of(c)
        d["views"] += int(m.get("views") or 0)
        d["comments"] += int(m.get("comments") or 0)
        t = _tier_of(c)
        d["tiers"][t] = d["tiers"].get(t, 0) + 1

    times = sorted(x for x in (c.get("published_at") or c.get("observed_at") for c in members) if x)
    entities: List[str] = []
    for c in members:
        for e in (c.get("entities") or []):
            if e not in entities:
                entities.append(e)

    tier_counts = {t: sum(1 for e in evidence if e["tier"] == t) for t in FACT_TIERS}
    primary_ratio = tier_counts.get("PRIMARY", 0) / max(1, len(evidence))

    # 供下游判定用的聚合信号（放在事实包里，避免每个节点各查一遍上游 → 口径不一致）
    gp = [float(c.get("gaming_probability") or 0) for c in members]
    views = sum(int((_metrics_of(c).get("views")) or 0) for c in members)
    comments = sum(int((_metrics_of(c).get("comments")) or 0) for c in members)
    game_entities = [e for e in entities if str(e).startswith("game_")]
    signals = {
        "gaming_share": round(sum(gp) / max(1, len(gp)), 3) if gp else None,
        "views": views,
        "comments": comments,
        "comment_per_1k_view": round(comments * 1000 / views, 2) if views else None,
        "unique_authors": len({c.get("author_id_hash") for c in members} - {None}),
        "game_entity_count": len(game_entities),
        "game_entities": game_entities[:5],
        "dup_ratio": round(sum(1 for c in members if c.get("is_duplicate")) / max(1, len(members)), 3),
    }

    # ---- §11 三分：事实 / 推断 / 未知 ----
    facts = [
        f"事件共 {len(members)} 条内容，覆盖 {len(platforms)} 个平台",
        f"证据层级分布：PRIMARY {tier_counts.get('PRIMARY', 0)} / SECONDARY {tier_counts.get('SECONDARY', 0)}"
        f" / COMMUNITY {tier_counts.get('COMMUNITY', 0)}",
        f"第三层给出：confidence={event.get('confidence_score')}, lifecycle={event.get('lifecycle')}",
    ]
    inferences = []
    if primary_ratio == 0:
        inferences.append("所有证据均为玩家侧内容，缺少官方信源 → 对外表述必须避免断言式措辞")
    unknowns = []
    if not times:
        unknowns.append("内容缺少发布时间，无法还原传播时间线")
    if primary_ratio == 0:
        unknowns.append("事件真实性未获官方确认")

    return {
        "evidence_version": EVIDENCE_VERSION,
        "event": {
            "event_id": event.get("event_id"),
            "title": event.get("canonical_title"),
            "lifecycle": event.get("lifecycle"),
            "hot_score": event.get("hot_score"),
            "momentum_score": event.get("momentum_score"),
            "confidence": event.get("confidence_score"),
            "event_type": event.get("event_type"),
        },
        "timeline": {"first_seen": times[0] if times else None,
                     "last_seen": times[-1] if times else None,
                     "n_distinct_times": len({t for t in times})},
        "platforms": platforms,
        "entities": entities,
        "evidence": evidence,
        "tier_counts": tier_counts,
        "primary_ratio": round(primary_ratio, 3),
        "signals": signals,
        "facts": facts,
        "inferences": inferences,
        "unknowns": unknowns,
        "n_members": len(members),
        "n_evidence": len(evidence),
    }


def node(state: Dict[str, Any], upstream: Any, router: Any = None,
         prompt_version: str = "") -> Dict[str, Any]:
    """图节点签名：(state, upstream, router) → 只返回要更新的字段。"""
    ev = upstream.event(state["event_id"])
    if ev is None:
        return {"status": "archived", "route": "event_missing"}
    members = upstream.members(state["event_id"])
    pack = build(ev, members)
    return {"evidence_pack": pack, "event_summary": pack["event"]["title"]}
