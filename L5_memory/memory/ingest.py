# -*- coding: utf-8 -*-
"""回填（Ingest）：把项目现有真实数据灌进第五层。

数据源与去向（全部走治理管道 store.py，PII / 写入策略自动生效）：
  games/<key>.json                    → Entity Knowledge（§6）
  L4_intelligence/intelligence/knowledge.py（TapTap 资产/目标/动机/创意类型）
                                      → Business Knowledge（§5，P2/candidate：
                                        公开产品形态通用描述，未经内部文档验证 —— 如实定级）
  L3 trend_event（已闭合生命周期）     → Trend Memory（长期，§19）
  L3 trend_event（进行中）             → Short-term Memory active_trend（§18）
  L4 intelligence_creative             → Creative Memory（§11，含被拒的，agent_generated/P4）
  L4 human_feedback                    → Decision Memory（§16，映射 §17 taxonomy）

★ 可重入：全部确定性主键（重跑 = 幂等），版本由 store 层递增。
★ 诚实纪律：当前 L3 数据 211 个事件全部 ended_at=NULL / 生命周期未闭合 →
  长期 Trend Memory 入 0 条是**正确结果**，不放宽判据凑数。
"""

from __future__ import annotations

import glob
import json
import os
import sys
from typing import Any, Dict, List, Optional

from memory.store import MemoryStore, _det_id

# L3 生命周期里视为"闭合"的状态（进行中的 DECLINING/EMERGING 都不算结束）
_CLOSED_LIFECYCLES = {"ENDED", "SUBSIDED", "SUNK"}
_L4_DECISION_MAP = {"adopt": "approve", "approve": "approve",
                    "reject": "reject", "edit": "edit"}


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ================================================================ Entity Knowledge
def ingest_games(store: MemoryStore, games_dir: Optional[str] = None) -> Dict[str, Any]:
    """games/*.json → Entity Knowledge。authority=P1（项目维护的档案），tier=verified。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dir_ = games_dir or os.path.join(root, "games")
    out: Dict[str, Any] = {"ingested": 0, "skipped": []}
    for path in sorted(glob.glob(os.path.join(dir_, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                profile = json.load(f)
        except (ValueError, OSError) as e:
            out["skipped"].append({"file": os.path.basename(path), "reason": str(e)})
            continue
        key = profile.get("key") or os.path.splitext(os.path.basename(path))[0]
        # §55 PII 纪律：记忆层不存平台账号 id（weibo_uids / official_account.user_id /
        # group_id 都是账号或社群标识，会被检索进 Agent Context）。
        # 档案级事实才入记忆；要账号 id 时回查 games/<key>.json（源头永远可溯）。
        payload = {k: profile.get(k) for k in
                   ("key", "name", "app_id", "aliases", "high_hours", "notes") if profile.get(k)}
        payload["cross_channel_keywords"] = (profile.get("cross_channel") or {}).get("bili_keywords")
        payload["omitted_fields"] = ("cross_channel 账号 id / official_account / group_id"
                                     "（PII 闸门，§55）；回查 games/%s.json" % key)
        store.upsert_knowledge(
            memory_type="entity", subject=key, title=profile.get("name") or key,
            payload=payload, tags=["game", "registry"],
            kind="business_knowledge_update", tier="verified", authority="P1",
            source="games.registry", source_type="curated_registry",
            confidence=0.95, human_verified=True, verified_by="games.registry")
        out["ingested"] += 1
    return out


# ================================================================ Business Knowledge
def ingest_taptap_assets(store: MemoryStore) -> Dict[str, Any]:
    """L4 knowledge.py 的资产/目标/动机/创意类型 → Business Knowledge。

    ★ 定级如实：这是「基于公开产品形态的通用描述」（L4 knowledge.py 自述），
    不是 TapTap 内部能力清单 → P2 / candidate / human_verified=False。
    接内部真实能力后用 `--promote` 升 verified（§53 升级路径）。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    l4_dir = os.path.join(root, "L4_intelligence", "intelligence")
    if l4_dir not in sys.path:
        sys.path.insert(0, l4_dir)
    try:
        import knowledge as K               # noqa: E402  (L4_intelligence/intelligence/knowledge.py)
    except ImportError as e:
        return {"ingested": 0, "skipped": [{"reason": f"L4 knowledge.py 不可导入: {e}"}]}

    n = 0
    for a in K.TAPTAP_ASSETS:
        store.upsert_knowledge(
            memory_type="business", subject=str(a["asset_id"]),
            title=str(a.get("name") or a["asset_id"]), payload=dict(a),
            tags=["taptap_asset", str(a.get("cost"))],
            kind="business_knowledge_update", tier="candidate", authority="P2",
            source="l4_intelligence.knowledge", source_type="public_form_description",
            confidence=0.8)
        n += 1
    for ct_id, ct in K.CREATIVE_TYPES.items():
        store.upsert_knowledge(
            memory_type="business", subject=f"creative_type:{ct_id}",
            title=str(ct.get("name") or ct_id),
            payload={"creative_type_id": ct_id, **ct},
            tags=["creative_type", str(ct.get("cost"))],
            kind="business_knowledge_update", tier="candidate", authority="P2",
            source="l4_intelligence.knowledge", source_type="public_form_description",
            confidence=0.8)
        n += 1
    for m in K.USER_MOTIVATIONS:
        store.upsert_knowledge(
            memory_type="business", subject=f"motivation:{m['motivation_id']}",
            title=str(m.get("name")), payload=dict(m), tags=["user_motivation"],
            kind="business_knowledge_update", tier="candidate", authority="P2",
            source="l4_intelligence.knowledge", source_type="public_form_description",
            confidence=0.8)
        n += 1
    store.upsert_knowledge(
        memory_type="business", subject="growth_goals", title="增长目标集合",
        payload=dict(K.GROWTH_GOALS), tags=["growth_goal"],
        kind="business_knowledge_update", tier="candidate", authority="P2",
        source="l4_intelligence.knowledge", source_type="public_form_description",
        confidence=0.8)
    n += 1
    return {"ingested": n}


# ================================================================ L3 Trend
def ingest_l3_trends(store: MemoryStore, l3_db: Optional[str] = None) -> Dict[str, Any]:
    """L3 trend_event → 长期 Trend Memory（仅生命周期闭合）+ 短期 active_trend。

    ★ 诚实结果预期：当前快照数据无 ended_at → 长期 0 条、短期全量。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db_path = l3_db or os.path.join(root, "data", "state", "l3_trend.sqlite3")
    if not os.path.exists(db_path):
        return {"ingested": 0, "short_term": 0, "skipped": [{"reason": f"L3 库不存在 {db_path}"}]}
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM trend_event").fetchall()]
    finally:
        conn.close()
    long_n = short_n = merged_n = 0
    for r in rows:
        if r.get("status") == "merged":
            merged_n += 1
            continue                        # 合并掉的事件不入记忆（防同一热点双份经验）
        entities = store.db.loads(r.get("entity_ids"), []) or []
        rec = {"event_id": r["event_id"], "title": r.get("canonical_title"),
               "event_type": r.get("event_type"), "entities": entities,
               "started_at": r.get("started_at"), "peak_at": r.get("peak_at"),
               "ended_at": r.get("ended_at"),
               "lifecycle_duration_hours": _duration_hours(r),
               "peak_hot_score": r.get("hot_score"),
               "final_hot_score": r.get("hot_score"),
               "platform_diffusion": store.db.loads(r.get("platforms"), []) or [],
               "diffusion_path": [],        # L3 无平台时序 → 如实留空，不编传播路径（§9）
               "audiences": [], "narratives": [],
               "content_count": r.get("content_count"),
               "platform_count": r.get("platform_count"),
               "confidence": r.get("confidence_score")}
        closed = r.get("ended_at") or \
            (r.get("lifecycle") in _CLOSED_LIFECYCLES)
        if closed:
            store.save_trend(rec)
            long_n += 1
        else:
            store.put_short_term(
                kind="active_trend", payload=rec,
                memory_id=_det_id("stm", r["event_id"]))
            short_n += 1
    return {"ingested": long_n, "short_term": short_n, "merged_skipped": merged_n,
            "note": "长期 Trend 仅收生命周期闭合事件（§19）；进行中的进短期记忆（§18）"}


def _duration_hours(row: Dict[str, Any]) -> Optional[float]:
    import datetime as _dt
    s, e = row.get("started_at"), row.get("ended_at")
    if not (s and e):
        return None
    try:
        fmt = lambda t: _dt.datetime.fromisoformat(str(t).replace("Z", "").replace("+08:00", ""))
        return round((fmt(e) - fmt(s)).total_seconds() / 3600.0, 2)
    except (ValueError, TypeError):
        return None


# ================================================================ L4 创意与反馈
def ingest_l4(store: MemoryStore, l4_db: Optional[str] = None) -> Dict[str, Any]:
    """L4 intelligence_creative + human_feedback → Creative / Decision Memory。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db_path = l4_db or os.path.join(root, "data", "state", "l4_intelligence.sqlite3")
    if not os.path.exists(db_path):
        return {"creatives": 0, "decisions": 0,
                "skipped": [{"reason": f"L4 库不存在 {db_path}"}]}
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        creatives = [dict(r) for r in conn.execute(
            "SELECT * FROM intelligence_creative").fetchall()]
        feedback = [dict(r) for r in conn.execute("SELECT * FROM human_feedback").fetchall()]
    finally:
        conn.close()

    # idea_id → feedback（L4 反馈按 idea_id 挂）；idea_id → 已落库 creative_id（反馈回写要能对上）
    fb_by_idea: Dict[str, Dict[str, Any]] = {}
    for fb in feedback:
        fb_by_idea.setdefault(fb.get("idea_id") or "", fb)
    creative_id_by_idea: Dict[str, str] = {}
    for c in creatives:
        if c.get("idea_id"):
            creative_id_by_idea[c["idea_id"]] = _det_id(
                "cre", c.get("analysis_id") or "unknown", c["idea_id"])

    n_cre = 0
    for c in creatives:
        payload = store.db.loads(c.get("payload"), {}) or {}
        evaluation = payload.get("evaluation") or {}
        creative_id = _det_id("cre", c.get("analysis_id") or "unknown", c.get("idea_id") or "")
        fb = fb_by_idea.get(c.get("idea_id") or "")
        decision = _L4_DECISION_MAP.get((fb or {}).get("decision") or "", "pending") \
            if fb else "pending"
        rec = {
            "creative_id": creative_id,
            "event_id": c.get("event_id"),
            "opportunity_id": payload.get("opportunity_id"),
            "analysis_id": c.get("analysis_id"),
            "creative_type": c.get("creative_type") or payload.get("creative_type"),
            "title": c.get("idea_name") or payload.get("idea_name"),
            "target_audience": payload.get("target_audience"),
            "user_motivation": payload.get("user_motivation"),
            "growth_mechanism": payload.get("growth_mechanism"),
            "concept": payload.get("concept"),
            "primary_metric": payload.get("primary_metric"),
            "launch_window": payload.get("launch_window"),
            "estimated_cost": payload.get("estimated_cost"),
            "evaluator_score": c.get("score") if c.get("score") is not None
            else evaluation.get("score"),
            "passed": 1 if c.get("passed") else 0,
            "human_decision": decision,
            "rejection_reason": (fb or {}).get("reason") if decision == "reject" else None,
        }
        store.save_creative(rec)
        n_cre += 1

    n_dec = 0
    for fb in feedback:
        mapped = _L4_DECISION_MAP.get(fb.get("decision") or "", fb.get("decision"))
        idea_id = fb.get("idea_id") or ""
        object_id = creative_id_by_idea.get(idea_id) or f"l4_idea:{idea_id}"
        store.record_decision(
            object_type="creative", object_id=object_id,
            decision=mapped or "reject",
            reason_text=fb.get("reason") or "",
            reviewer_role="operator", source_ref=f"l4.human_feedback:{fb.get('feedback_id')}")
        n_dec += 1
    return {"creatives": n_cre, "decisions": n_dec,
            "note": "Agent 产出默认 agent_generated/P4；人工采纳经 record_decision 升 verified（§19）"}


def ingest_all(store: MemoryStore, with_l3: bool = True, with_l4: bool = True) -> Dict[str, Any]:
    out: Dict[str, Any] = {"games": ingest_games(store),
                           "taptap_assets": ingest_taptap_assets(store)}
    if with_l3:
        out["l3_trends"] = ingest_l3_trends(store)
    if with_l4:
        out["l4"] = ingest_l4(store)
    return out
