#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Event Similarity（§7 / §8）—— **不要只靠 Embedding 聚类**。

反例（规格给的）："黑神话 DLC 泄露" 和 "黑神话年度销量突破" 都在讲黑神话，
纯语义相似度会把它们错误聚到一起。所以必须多因子：

    S = 0.45·Semantic + 0.25·Entity + 0.15·Temporal + 0.10·Lexical + 0.05·Source

★ 诚实降级：本机没有 embedding 模型，`Semantic` 用**词汇相似度**（字符 n-gram Jaccard）代替，
   并在结果里标 `semantic_source = "lexical_fallback"`。
   这不是"假装语义"，是明确标注的降级 —— 接入 embedding 后把 `semantic_fn` 换掉即可，
   权重与下游代码一行不改。
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

# §8 权重
W = {"semantic": 0.45, "entity": 0.25, "temporal": 0.15, "lexical": 0.10, "source": 0.05}

# 时间衰减尺度 τ（小时）：超过 τ*3 基本不可能同一事件
TAU_HOURS = 24.0

RE_TOKEN = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]")


def _shingles(text: str, n: int = 3) -> Set[str]:
    t = (text or "").strip()
    if not t:
        return set()
    if len(t) < n:
        n = max(1, len(t))
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _jaccard(a: Set, b: Set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def lexical_sim(text_a: str, text_b: str) -> float:
    """词级重合（中文按字切 + 英文数字按词）。"""
    ta = set(RE_TOKEN.findall(text_a or ""))
    tb = set(RE_TOKEN.findall(text_b or ""))
    return _jaccard(ta, tb)


def shingle_sim(text_a: str, text_b: str) -> float:
    return _jaccard(_shingles(text_a), _shingles(text_b))


def entity_sim(ents_a: List[str], ents_b: List[str]) -> float:
    """§8 实体 Jaccard。"""
    return _jaccard(set(ents_a or []), set(ents_b or []))


def temporal_sim(t_a: Optional[str], t_b: Optional[str], tau_hours: float = TAU_HOURS) -> float:
    """§8：S_t = e^(-Δt/τ)。缺时间返回 0.5（中性，不假装接近）。"""
    if not t_a or not t_b:
        return 0.5
    try:
        da = datetime.fromisoformat(t_a)
        db = datetime.fromisoformat(t_b)
    except ValueError:
        return 0.5
    dt_h = abs((da - db).total_seconds()) / 3600.0
    return round(math.exp(-dt_h / tau_hours), 4)


def source_sim(meta_a: Dict[str, Any], meta_b: Dict[str, Any]) -> float:
    """§8 来源相似：同平台加分，同作者强加分。"""
    if not meta_a or not meta_b:
        return 0.0
    s = 0.0
    if meta_a.get("platform") and meta_a.get("platform") == meta_b.get("platform"):
        s += 0.5
    if meta_a.get("author_id") and meta_a.get("author_id") == meta_b.get("author_id"):
        s += 0.5
    return round(s, 4)


class SimilarityEngine:
    """可插拔语义函数：有 embedding 就传 semantic_fn，没有就用词汇降级。"""

    def __init__(self, weights: Optional[Dict[str, float]] = None,
                 semantic_fn: Optional[Callable[[str, str], float]] = None,
                 tau_hours: float = TAU_HOURS) -> None:
        self.w = dict(W)
        if weights:
            self.w.update(weights)
        self.semantic_fn = semantic_fn
        self.tau_hours = tau_hours
        self.semantic_source = "embedding" if semantic_fn else "lexical_fallback"
        # ★ 降级权重重分配：没有 embedding 时，`semantic` 实际只是词汇重合（与 lexical 重复计算），
        #   若还占 0.45 权重，总分会被一个恒低的量拖住 → 实测每条内容都成了独立事件。
        #   所以把权重让给 entity 与 lexical，并明确标注 degraded_weights=True。
        if not semantic_fn:
            self.w = {"semantic": 0.0, "entity": 0.45, "temporal": 0.20,
                      "lexical": 0.30, "source": 0.05}
            self.degraded_weights = True
        else:
            self.degraded_weights = False

    def _semantic(self, a: str, b: str) -> float:
        if self.semantic_fn:
            try:
                return float(self.semantic_fn(a, b))
            except Exception:      # noqa: BLE001 —— 模型挂了不能让整条链崩
                return shingle_sim(a, b)
        return shingle_sim(a, b)

    def score(self, content: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
        """内容 vs 事件的六因子相似度。"""
        ct = content.get("normalized_text") or content.get("raw_text") or ""
        et = event.get("canonical_title") or ""
        s_sem = self._semantic(ct, et)
        s_ent = entity_sim(content.get("entities") or [], event.get("entity_ids") or [])
        s_tem = temporal_sim(content.get("observed_at"), event.get("last_updated_at"), self.tau_hours)
        s_lex = lexical_sim(ct, et)
        s_src = source_sim({"platform": content.get("platform"), "author_id": content.get("author_id")},
                           {"platform": event.get("primary_platform"), "author_id": None})
        total = (self.w["semantic"] * s_sem + self.w["entity"] * s_ent +
                 self.w["temporal"] * s_tem + self.w["lexical"] * s_lex +
                 self.w["source"] * s_src)
        return {
            "score": round(total, 4),
            "parts": {"semantic": round(s_sem, 3), "entity": round(s_ent, 3),
                      "temporal": round(s_tem, 3), "lexical": round(s_lex, 3),
                      "source": round(s_src, 3)},
            "semantic_source": self.semantic_source,
            "degraded_weights": self.degraded_weights,
        }

    def event_event(self, a: Dict[str, Any], b: Dict[str, Any]) -> float:
        """事件 vs 事件（用于 Merge / Split / Novelty）。"""
        s_sem = self._semantic(a.get("canonical_title", ""), b.get("canonical_title", ""))
        s_ent = entity_sim(a.get("entity_ids") or [], b.get("entity_ids") or [])
        s_tem = temporal_sim(a.get("started_at"), b.get("started_at"), self.tau_hours * 3)
        return round(0.6 * s_sem + 0.3 * s_ent + 0.1 * s_tem, 4)
