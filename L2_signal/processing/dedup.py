#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Deduplication（§8 / §9）—— 热点系统必须解决的两层去重。

**精确去重**：同一条被转载 API 返回两次 → content_id 或 hash(normalized_text) 秒判。
**近似去重**：互联网最大的麻烦是"黑神话DLC要来了 / 黑神话：悟空 DLC 疑似曝光 / 震惊！黑神话可能有DLC"——
有的是**同一篇搬运**，有的是**同一事件的不同内容**。

★ 边界（规格明确）：第二层只判断"内容是不是近似副本"，
**不在这一层判断是不是同一个 Event** —— 那是第三层的事。
    sim > 0.97        → near_duplicate（搬运）
    0.7 < sim ≤ 0.97  → 保留，交给第三层判断是否同事件

实现：字符 3-gram Jaccard（确定性、无需模型）。
语义相似度那一路需要 embedding，本机没有模型 → 接口留好，未接入（不伪造分数）。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set, Tuple

NEAR_DUPLICATE_THRESHOLD = 0.97
EVENT_CANDIDATE_THRESHOLD = 0.70


def exact_key(content_id: str, normalized_text: str) -> str:
    """精确去重键：优先用 content_id（稳定），没有就用归一化文本哈希。"""
    if content_id:
        return content_id
    return "txt:" + str(abs(hash(normalized_text or "")))


def shingles(text: str, n: int = 3) -> Set[str]:
    """字符 n-gram 集合。短文本用 2-gram，避免空集。"""
    t = (text or "").strip()
    if not t:
        return set()
    if len(t) < n:
        n = max(1, len(t))
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def similarity(text_a: str, text_b: str) -> float:
    """词汇相似度（字符 n-gram Jaccard）。"""
    return jaccard(shingles(text_a), shingles(text_b))


class DedupEngine:
    """两阶段去重。进程内维护已见集合（生产版换成 Redis / 向量库）。"""

    def __init__(self, near_threshold: float = NEAR_DUPLICATE_THRESHOLD,
                 event_threshold: float = EVENT_CANDIDATE_THRESHOLD,
                 max_track: int = 500) -> None:
        """max_track：只与最近 N 条比较（滑动窗口）。

        两两比较是 O(n²)，3825 条就要 1400 万次 Jaccard —— 单机跑不动。
        近似重复（搬运/转载）在时间上几乎总是**邻近到达**的，所以滑动窗口
        在召回上损失很小。生产版换成向量库做 ANN 就没有这个限制。
        """
        self.near_threshold = near_threshold
        self.event_threshold = event_threshold
        self.max_track = max_track
        self._exact: Set[str] = set()
        self._seen: List[Tuple[str, Set[str], str]] = []   # (content_id, shingles, group)
        self._group_seq = 0

    def _new_group(self) -> str:
        self._group_seq += 1
        return f"dupg_{self._group_seq:05d}"

    def check(self, content_id: str, normalized_text: str) -> Dict[str, Any]:
        """返回 {is_duplicate, dup_type, duplicate_group, max_similarity}。"""
        key = exact_key(content_id, normalized_text)
        if key in self._exact:
            return {"is_duplicate": True, "dup_type": "exact", "duplicate_group": None,
                    "max_similarity": 1.0}
        self._exact.add(key)

        sh = shingles(normalized_text)
        best_sim, best_group = 0.0, None
        for cid, other, group in self._seen:
            s = jaccard(sh, other)
            if s > best_sim:
                best_sim, best_group = s, group
        self._seen.append((content_id, sh, ""))
        if len(self._seen) > self.max_track:
            self._seen = self._seen[-self.max_track:]

        if best_sim >= self.near_threshold and best_group:
            return {"is_duplicate": True, "dup_type": "near", "duplicate_group": best_group,
                    "max_similarity": round(best_sim, 4)}
        if best_sim >= self.event_threshold and best_group:
            # ★ 不判为重复：交给第三层判断是不是同一事件
            return {"is_duplicate": False, "dup_type": "event_candidate",
                    "duplicate_group": best_group, "max_similarity": round(best_sim, 4)}

        g = self._new_group()
        if self._seen:
            self._seen[-1] = (content_id, sh, g)
        return {"is_duplicate": False, "dup_type": "unique", "duplicate_group": g,
                "max_similarity": round(best_sim, 4)}

    def stats(self) -> Dict[str, Any]:
        return {"exact_keys": len(self._exact), "tracked": len(self._seen),
                "groups": self._group_seq}


def cross_platform_fingerprint(text: str, urls: Optional[List[str]] = None) -> str:
    """§9 跨平台转载指纹（转发链：官方微博 → B站搬运 → Reddit翻译 → TapTap转帖）。"""
    from .canonical import text_fingerprint
    return text_fingerprint(text, urls)
