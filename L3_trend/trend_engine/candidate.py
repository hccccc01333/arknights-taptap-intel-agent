#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Candidate Filter（§5）—— 第三层的输入**不是**所有内容。

第二层每天可能产出百万条，全量进聚类是浪费。先按静态门槛 + 动态条件筛：

    静态：gaming_probability > 0.5 且 quality_score > 0.4 且 spam_score < 0.8
    动态（满足任一）：指标百分位 > P70 / 有速度 / 官方源 / 命中高权重实体

★ 这一步的意义是**成本控制**，不是判断热点 —— 被筛掉的内容只是"暂时不值得算"，
不是"不是热点"。所以筛选结果要可解释（记录 reason），便于事后复核。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

GAMING_MIN = 0.5
QUALITY_MIN = 0.4
SPAM_MAX = 0.8
PERCENTILE_MIN = 0.70

# 高权重实体：命中就进候选（例如官方游戏本体、重要赛事）
IMPORTANT_ENTITY_PREFIXES = ("game_",)


def is_candidate(c: Dict[str, Any], explain: bool = False) -> Any:
    """c 是 L2 的 content 行（含 features/entities/metrics 的 JSON 串或 dict）。"""
    import json

    def _load(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except ValueError:
                return {}
        return v or {}

    feats = _load(c.get("features"))
    metrics = _load(c.get("metrics"))
    ents = _load(c.get("entities")) or []
    src = _load(c.get("source_features"))
    reasons: List[str] = []
    rejects: List[str] = []

    gp = float(c.get("gaming_probability") or 0.0)
    q = float(c.get("quality_score") or 0.0)
    spam = float(c.get("spam_score") or 0.0)

    # ---- 静态门槛 ----
    if gp < GAMING_MIN:
        rejects.append(f"gaming_probability={gp:.2f}<{GAMING_MIN}")
    else:
        reasons.append(f"gaming={gp:.2f}")
    if q < QUALITY_MIN:
        rejects.append(f"quality={q:.2f}<{QUALITY_MIN}")
    if spam > SPAM_MAX:
        rejects.append(f"spam={spam:.2f}>{SPAM_MAX}")
    if bool(c.get("is_duplicate")):
        rejects.append("duplicate")

    if rejects:
        return (False, rejects) if explain else False

    # ---- 动态条件（任一满足）----
    hits: List[str] = []
    for k in ("view_percentile", "like_percentile", "comment_percentile"):
        v = feats.get(k)
        if isinstance(v, (int, float)) and v >= PERCENTILE_MIN:
            hits.append(f"{k}={v:.2f}")
    vv = feats.get("view_velocity")
    cv = feats.get("comment_velocity")
    if isinstance(vv, (int, float)) and vv > 0:
        hits.append(f"view_velocity={vv:.1f}/min")
    if isinstance(cv, (int, float)) and cv > 0:
        hits.append(f"comment_velocity={cv:.1f}/min")
    if src.get("is_official"):
        hits.append("official_source")
    important = [e for e in ents if str(e).startswith(IMPORTANT_ENTITY_PREFIXES)]
    if important:
        hits.append(f"entities={','.join(important[:2])}")

    ok = bool(hits)
    if not ok:
        return (False, ["未命中任何动态条件"]) if explain else False
    if explain:
        return True, reasons + hits
    return True


def filter_candidates(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """批量筛选，返回 {kept, dropped, drop_reasons} —— 便于回答"为什么这批没进"。"""
    kept: List[Dict[str, Any]] = []
    reasons: Dict[str, int] = {}
    for r in rows:
        ok, info = is_candidate(r, explain=True)
        if ok:
            kept.append(r)
        else:
            for x in info:
                key = x.split("=")[0].split("<")[0].split(">")[0]
                reasons[key] = reasons.get(key, 0) + 1
    return {"kept": kept, "n_in": len(rows), "n_kept": len(kept),
            "drop_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:8])}
