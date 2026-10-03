# -*- coding: utf-8 -*-
"""Memory Evaluation（§50-§52）。

★ §50：第五层自己的评估最重要的**不是** Vector Search Recall，而是：
    Retrieval Relevance / Knowledge Freshness / Grounding Rate /
    Memory Utilization / Case Reuse Rate / Improvement Lift（A/B）。

★ 诚实纪律（全项目同款）：数据不够的指标返回 **None + 原因**，绝不返回 0 冒充。
  例如 Improvement Lift（§51 A/B）在"有 Memory / 无 Memory"两组对照数据收齐之前就是 None。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


def utilization(store_store: Any, days: int = 30) -> Optional[Dict[str, Any]]:
    """§52 Memory Utilization：检索返回的条目里，Agent 实际用了多少。

    没有任何检索日志 → None（不是 0%）。mark_used 没回填的日志按"未使用"计 ——
    这本身就是要暴露的问题，不粉饰。
    """
    since = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
    logs = store_store.db.query(
        "SELECT returned_ids, used_ids FROM retrieval_log WHERE ts >= ?", (since,))
    if not logs:
        return None
    n_returned = 0
    n_used = 0
    n_unmarked = 0
    used_counter: Dict[str, int] = {}
    for log in logs:
        returned = store_store.db.loads(log.get("returned_ids"), []) or []
        used = store_store.db.loads(log.get("used_ids"), None)
        n_returned += len(returned)
        if used is None:
            n_unmarked += len(returned)      # Agent 没回填 —— 观测缺口，如实计
            continue
        n_used += len(used or [])
        for u in used or []:
            used_counter[u] = used_counter.get(u, 0) + 1
    return {"window_days": days, "n_retrievals": len(logs),
            "n_returned": n_returned, "n_used": n_used, "n_unmarked_logs": n_unmarked,
            "utilization": round(n_used / n_returned, 4) if n_returned else None,
            "top_used": sorted(used_counter.items(), key=lambda kv: -kv[1])[:5]}


def freshness(store_store: Any) -> Dict[str, Any]:
    """§50 Knowledge Freshness：已失效（valid_to 过去）记忆占比 —— 高了就该归档。"""
    from memory import governance as G
    out: Dict[str, Any] = {}
    for table in ("knowledge_item", "growth_case", "playbook"):
        rows = store_store.db.query(f"SELECT valid_from, valid_to FROM {table}")
        expired = sum(1 for r in rows if not G.is_currently_valid(r))
        out[table] = {"total": len(rows), "expired": expired,
                      "fresh_ratio": round(1 - expired / len(rows), 4) if rows else None}
    return out


def case_reuse_rate(store_store: Any, days: int = 30) -> Optional[float]:
    """§50 Case Reuse Rate：历史经验有多少真正被重新使用。无日志 → None。"""
    since = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
    logs = store_store.db.query(
        "SELECT used_ids FROM retrieval_log WHERE ts >= ? AND memory_type IN ('case','playbook')",
        (since,))
    used_case_ids = set()
    for log in logs:
        for u in store_store.db.loads(log.get("used_ids"), []) or []:
            used_case_ids.add(u)
    total = store_store.db.table_count("growth_case")
    if not total:
        return None
    return round(len([c for c in used_case_ids if c]) / total, 4)


def grounding_rate(store_store: Any, l4_db: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """§50 Grounding Rate：第四层的创意结论有多少**引用了**有效 Memory。

    判据：L4 intelligence_creative.source_refs 里出现 L5 记忆 id（kb_/case_/exp_/trd_/ap_/pb_）。
    L4 库不存在 / 没有创意 → None（不是 0）。
    """
    path = l4_db or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "state", "l4_intelligence.sqlite3")
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT source_refs FROM intelligence_creative").fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    if not rows:
        return None
    prefixes = ("kb_", "case_", "exp_", "trd_", "ap_", "pb_")
    grounded = 0
    total = 0
    for (refs_json,) in rows:
        total += 1
        refs = str(refs_json or "")
        if any(p in refs for p in prefixes):
            grounded += 1
    return {"n_creatives": total, "grounded": grounded,
            "grounding_rate": round(grounded / total, 4)}


def improvement_lift_ab() -> Optional[Dict[str, Any]]:
    """§51 A/B Evaluation：有 Memory 组 vs 无 Memory 组比业务采用率 / 评审分 / Token 成本。

    MVP 阶段没有分组的对照运行数据 → **如实返回 None**。
    等接入方式：同一批事件分别跑 L4（memory on / off），把两组的
    adoption / mean_evaluator_score / llm_usage 存进来后本函数才有东西可比。
    """
    return None


def summary(store_store: Any, l4_db: Optional[str] = None) -> Dict[str, Any]:
    """§50 全量评估汇总。每个指标带 status: ok|insufficient_data。"""
    util = utilization(store_store)
    gr = grounding_rate(store_store, l4_db)
    crr = case_reuse_rate(store_store)
    return {
        "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "freshness": freshness(store_store),
        "utilization": util or {"status": "insufficient_data",
                                "reason": "还没有检索日志（§50：没数据不冒充 0%）"},
        "grounding_rate": gr or {"status": "insufficient_data",
                                 "reason": "L4 创意库为空或不存在"},
        "case_reuse_rate": crr if crr is not None else
        {"status": "insufficient_data", "reason": "还没有被复用的 Case"},
        "improvement_lift_ab": improvement_lift_ab() or
        {"status": "insufficient_data",
         "reason": "A/B 两组对照运行尚未进行（§51）"},
    }
