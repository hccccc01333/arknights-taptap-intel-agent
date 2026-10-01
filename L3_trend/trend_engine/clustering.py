#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Event Clustering（§6 / §9 / §10 / §11 / §12 / §13）。

两阶段（§9）：
    Stage 1 在线匹配：新内容 → 与近 72h 的 Event 算相似度 → attach / create
    Stage 2 离线纠错：Merge（本应一个却分成俩）/ Split（本应分开却聚成一个）

★ Event 不是 immutable，它是 **evolving semantic object**（§11）：
    合并要留 `event_merge_history`，拆分要留 lineage，否则历史无法追踪。

§13 Canonical Title：规则化生成（主要实体 + 核心关键词 + 事件动词）。
规格说这里可以用轻量 LLM —— 本机无 key，走规则兜底，结果标 `title_source="rule"`。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .similarity import SimilarityEngine  # noqa: E402

ATTACH_THRESHOLD = 0.62      # 内容→事件：高于此值归入该事件
MERGE_THRESHOLD = 0.86       # 事件→事件：高于此值应合并
SPLIT_INNER_THRESHOLD = 0.35 # 事件内部成员两两相似度过低 → 疑似该拆
WINDOW_HOURS = 72

# 事件动词（用于生成 canonical title）
EVENT_VERBS = {
    "leak": ["泄露", "曝光", "偷跑", "leak"],
    "release": ["上线", "发售", "发布", "开服", "公测", "release"],
    "update": ["更新", "版本", "补丁", "维护", "update"],
    "controversy": ["争议", "骂", "炎上", "抵制", "bug", "翻车"],
    "collab": ["联动", "合作", "联名"],
    "anniversary": ["周年", "庆典"],
    "esports": ["比赛", "赛事", "夺冠", "战队"],
    "meme": ["梗", "二创", "整活", "沙雕"],
}


def _detect_verb(texts: List[str]) -> Optional[str]:
    blob = " ".join(texts).lower()
    best, hits = None, 0
    for verb, kws in EVENT_VERBS.items():
        n = sum(1 for k in kws if k in blob)
        if n > hits:
            best, hits = verb, n
    return best


def make_title(members: List[Dict[str, Any]], entities: List[str]) -> Tuple[str, str]:
    """§13：主实体 + 关键词 + 事件动词 → canonical_title。

    注意：**不取点赞最高的标题**（规格明确反对），那是"最吵的那条"，不是"这件事"。

    优先级（实测调过）：
      1) 成员共有的话题标签（topics）—— 事件往往就是围绕一个话题标签形成的
      2) 主实体 + 事件动词
      3) 兜底：最长那条的前 30 字（信息量通常比最短的高）
    """
    texts = [m.get("normalized_text") or m.get("raw_text") or "" for m in members]

    # 1) 高频话题标签
    freq: Dict[str, int] = {}
    for m in members:
        for t in (m.get("topics") or []):
            if t and len(t) >= 3:
                freq[t] = freq.get(t, 0) + 1
    top_topic = max(freq, key=lambda k: freq[k]) if freq else ""

    verb = _detect_verb(texts)
    kw = ""
    if verb:
        for v in EVENT_VERBS[verb]:
            if any(v in t.lower() for t in texts):
                kw = v
                break

    main = top_topic
    if not main and entities:
        main = entities[0].replace("game_", "").replace("_", " ")
    title = " ".join(x for x in (main, kw) if x).strip()
    if not title:
        cand = max(texts, key=lambda t: len(t or "")) if texts else ""
        title = (cand or "")[:30].strip()
    return title, "rule"


class EventClusterer:
    """在线匹配 + 离线纠错。"""

    def __init__(self, sim: Optional[SimilarityEngine] = None,
                 attach_threshold: float = ATTACH_THRESHOLD,
                 merge_threshold: float = MERGE_THRESHOLD,
                 window_hours: int = WINDOW_HOURS) -> None:
        self.sim = sim or SimilarityEngine()
        self.attach_threshold = attach_threshold
        self.merge_threshold = merge_threshold
        self.window_hours = window_hours

    # ---------- Stage 1：在线匹配 ----------
    def match(self, content: Dict[str, Any], events: List[Dict[str, Any]]) -> Tuple[Optional[str], Dict[str, Any]]:
        """返回 (最佳 event_id 或 None, 明细)。超过时间窗的事件不参与匹配。"""
        now = self._now(content)
        best_id, best = None, {"score": 0.0, "parts": {}, "semantic_source": self.sim.semantic_source}
        for ev in events:
            if self._out_of_window(ev, now):
                continue
            s = self.sim.score(content, ev)
            if s["score"] > best["score"]:
                best_id, best = ev["event_id"], s
        if best_id and best["score"] >= self.attach_threshold:
            return best_id, best
        return None, best

    def _now(self, content: Dict[str, Any]) -> datetime:
        t = content.get("observed_at")
        try:
            return datetime.fromisoformat(t) if t else datetime.now()
        except ValueError:
            return datetime.now()

    def _out_of_window(self, ev: Dict[str, Any], now: datetime) -> bool:
        t = ev.get("last_updated_at") or ev.get("started_at")
        if not t:
            return False
        try:
            return (now - datetime.fromisoformat(t)) > timedelta(hours=self.window_hours)
        except ValueError:
            return False

    # ---------- 新建事件 ----------
    def create_event(self, content: Dict[str, Any], entity_ids: List[str]) -> Dict[str, Any]:
        eid = f"evt_{uuid.uuid4().hex[:12]}"
        title, src = make_title([content], entity_ids)
        now = content.get("observed_at") or datetime.now().isoformat()
        return {
            "event_id": eid,
            "canonical_title": title,
            "title_source": src,
            "event_type": _detect_verb([content.get("normalized_text") or ""]) or "unknown",
            "primary_topic": entity_ids[0] if entity_ids else None,
            "status": "active",
            "started_at": now,          # ★ 事件发生时间（从内容推断）
            "first_detected_at": now,   # ★ 系统首次发现时间（与上面必须分开，算 Detection Latency）
            "last_updated_at": now,
            "entity_ids": list(entity_ids),
            "primary_platform": content.get("platform"),
            "content_count": 0,
            "platform_count": 0,
        }

    # ---------- Stage 2：离线纠错 ----------
    def find_merges(self, events: List[Dict[str, Any]]) -> List[Tuple[str, str, float]]:
        """哪些事件应该合并（§10）。"""
        out = []
        for i in range(len(events)):
            for j in range(i + 1, len(events)):
                s = self.sim.event_event(events[i], events[j])
                if s >= self.merge_threshold:
                    out.append((events[i]["event_id"], events[j]["event_id"], s))
        return out

    def needs_split(self, event: Dict[str, Any], members: List[Dict[str, Any]]) -> Dict[str, Any]:
        """§11：事件内部两两相似度太低 → 疑似混进了别的事（只报警，不自动拆）。

        MVP 不自动拆：拆分需要语义判断，规则乱拆比不拆更糟。这里只产出证据。
        """
        if len(members) < 4:
            return {"needs_split": False, "reason": "成员太少，不判断"}
        texts = [m.get("normalized_text") or m.get("raw_text") or "" for m in members]
        sims = []
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                sims.append(self.sim._semantic(texts[i], texts[j]))
        low = sum(1 for s in sims if s < SPLIT_INNER_THRESHOLD)
        ratio = low / len(sims) if sims else 0.0
        return {"needs_split": ratio > 0.5, "low_pair_ratio": round(ratio, 3),
                "pairs": len(sims), "threshold": SPLIT_INNER_THRESHOLD}

    # ---------- Centroid（§12）----------
    @staticmethod
    def centroid_members(members: List[Dict[str, Any]]) -> Dict[str, Any]:
        """§12：事件中心不是简单平均，权重 = 质量 × 可信度 × 参与度 × 是否原创。

        规格原话："原创官方来源权重大于 50 个搬运号"。
        """
        if not members:
            return {"representative": None, "weights": []}
        scored = []
        for m in members:
            q = float(m.get("quality_score") or 0.5)
            spam = float(m.get("spam_score") or 0.0)
            feats = m.get("features") or {}
            pct = max([v for v in (feats.get("view_percentile"), feats.get("comment_percentile"))
                       if isinstance(v, (int, float))] or [0.0])
            is_dup = 1 if m.get("is_duplicate") else 0
            official = 1.0 if (m.get("source_features") or {}).get("is_official") else 0.0
            w = (0.3 * q + 0.2 * (1 - spam) + 0.2 * pct + 0.2 * official + 0.1 * (1 - is_dup))
            scored.append((w, m))
        scored.sort(key=lambda x: -x[0])
        return {"representative": scored[0][1].get("content_id"),
                "weights": [{"content_id": m.get("content_id"), "weight": round(w, 3)}
                            for w, m in scored[:5]]}
