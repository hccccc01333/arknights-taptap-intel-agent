# -*- coding: utf-8 -*-
"""Learning Engine（§29-§32 / §40-§42）：Case / Anti-pattern / Playbook 沉淀。

★ §29：把 Trend × Opportunity × Creative × Experiment × Outcome 压缩成 **Growth Case** ——
  以后最有价值的 Memory 单元。不是创意一出来就压（§30）：
  必须 Event 结束 + Experiment 结束 + 结果确认 之后才做 Case Distillation。

★ §31：Distillation 适合让 LLM 做（成功原因/失败原因/可迁移经验/适用条件），
  但 **lesson 必须保留来源证据** —— 本模块默认规则版（lesson 每条带 evidence id），
  允许注入 `lesson_llm` 回调升级，调用失败自动回退规则版，绝不返回无依据"经验"。

★ §40：Negative Memory —— 不只记什么成功了，还记什么失败了（Anti-pattern Memory）。

★ §42：Agent 可以提出知识（Playbook Candidate），**不能自动把推断变成公司事实** ——
  propose → human review → approved，三段缺一不可。
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional

from memory.store import MemoryStore

# §40 反模式先验：热点窗口 < 预估开发周期 → 产品开发型创意注定失败
_SHORT_WINDOW_HOURS = 48.0
_HIGH_COST_TYPES = ("product", "ai_interactive", "publisher")

LessonLLM = Callable[[Dict[str, Any]], List[Dict[str, Any]]]


def _det_case_id(event_id: str, strategy: str) -> str:
    h = hashlib.sha1(f"{event_id}|{strategy}".encode("utf-8")).hexdigest()[:12]
    return f"case_{h}"


# ================================================================ Case Distillation
def distill_event(store: MemoryStore, event_id: str, force: bool = False,
                  lesson_llm: Optional[LessonLLM] = None) -> Dict[str, Any]:
    """把一个事件的全部历史压缩成 Growth Case（§29/§30 CARL：Context/Action/Result/Lesson）。

    返回 {"status": "created"|"updated"|"deferred", ...}；deferred 必须带原因 ——
    不满足 §30 条件时宁可不压 Case，也不拿半截信息冒充经验。
    """
    trends = store.candidates("trend", filters={"event_id": event_id}, limit=1)
    creatives = store.candidates("creative", filters={"event_id": event_id}, limit=200)
    if not creatives:
        return {"status": "deferred", "reason": f"事件 {event_id} 在 Creative Memory 无记录"}
    trend = trends[0] if trends else None

    # ---- §30 前置条件：Event 结束（除非 force）----
    if trend is None and not force:
        return {"status": "deferred",
                "reason": "事件未入 Trend Memory（生命周期未闭合，§19/§30）；"
                          "确认结束后重跑，或 force=True 显式越界（产物会标 forced）"}

    experiments = store.candidates("experiment", limit=2000)
    exps = [e for e in experiments if e.get("event_id") == event_id]
    # §30：Experiment 结束才算结果确认；没实验也允许压 Case —— 但 lesson 如实说"效果未知"
    unconfirmed = [e for e in exps if e.get("status") not in ("completed", "aborted")]
    if unconfirmed and not force:
        return {"status": "deferred",
                "reason": f"{len(unconfirmed)} 个实验未结束，结果未确认（§30）"}

    # ---- Context（§29）----
    entities = sorted({e for t in [trend] if t for e in (t.get("entities") or [])})
    audience_segments = sorted({c.get("target_audience") for c in creatives
                                if c.get("target_audience")})
    user_motivations = sorted({c.get("user_motivation") for c in creatives
                               if c.get("user_motivation")})
    trend_type = (trend or {}).get("event_type") or "unknown"

    # ---- Action：主导策略 = 通过评审最多 / 分数最高的创意类型 + 机制 ----
    passed = [c for c in creatives if c.get("passed")]
    pool = passed or creatives
    type_counts: Dict[str, int] = {}
    for c in pool:
        type_counts[c.get("creative_type") or "unknown"] = \
            type_counts.get(c.get("creative_type") or "unknown", 0) + 1
    dominant_type = max(type_counts, key=type_counts.get) if type_counts else "unknown"
    mechanisms = sorted({c.get("growth_mechanism") for c in pool if c.get("growth_mechanism")})
    strategy = f"{dominant_type}" + (f" × {'/'.join(mechanisms[:2])}" if mechanisms else "")

    # ---- Result（§14 相对 lift；无实验如实 null）----
    outcome: Dict[str, Any] = {}
    lifts: List[float] = []
    for e in exps:
        lift = store.best_relative_lift(e["experiment_id"])
        if lift is not None:
            metric = e.get("primary_metric") or "primary"
            outcome[metric] = lift
            lifts.append(float(lift))
    if not outcome:
        outcome = None

    # ---- Lesson（§31：每条带证据；LLM 可选，失败回规则）----
    evidence_ids = [e["experiment_id"] for e in exps] + \
                   [c["creative_id"] for c in creatives[:5]]
    lessons: List[Dict[str, Any]] = []
    anti_patterns: List[str] = []
    if lifts:
        avg = sum(lifts) / len(lifts)
        if avg > 0:
            lessons.append({
                "lesson": f"策略[{strategy}]在[{trend_type}]类事件上平均相对 lift {avg:+.2f}，"
                          f"对 {', '.join(outcome.keys())} 有效",
                "evidence_ids": [e["experiment_id"] for e in exps]})
        else:
            lessons.append({
                "lesson": f"策略[{strategy}]在[{trend_type}]类事件上平均相对 lift {avg:+.2f}，"
                          f"无正向效果，慎复用",
                "evidence_ids": [e["experiment_id"] for e in exps]})
            anti_patterns.append("negative_lift_repeat")
    else:
        decisions = store.candidates("decision", limit=2000)
        d_by_obj = {d["object_id"]: d for d in decisions if d.get("object_type") == "creative"}
        approved = [c for c in creatives if d_by_obj.get(c["creative_id"], {})
                    .get("decision") in ("approve", "edit")]
        rejected = [c for c in creatives if d_by_obj.get(c["creative_id"], {})
                    .get("decision") == "reject"]
        if approved:
            lessons.append({
                "lesson": f"创意获人工采纳（{len(approved)}/{len(creatives)}），但**未实验** —— "
                          f"效果未知，不可当有效经验引用（Creative ≠ Experiment，§12）",
                "evidence_ids": [c["creative_id"] for c in approved[:3]]})
        elif rejected:
            codes = [d_by_obj[c["creative_id"]].get("reason_code") or "OTHER"
                     for c in rejected]
            lessons.append({
                "lesson": f"该事件创意全部被拒（主因 {max(set(codes), key=codes.count)}），"
                          f"此类热点 × 此类打法的组合此前不被采纳",
                "evidence_ids": [c["creative_id"] for c in rejected[:3]]})
        else:
            lessons.append({
                "lesson": "创意停留在 Agent 产出、未进入人工决策 —— 无任何结论可沉淀",
                "evidence_ids": [c["creative_id"] for c in creatives[:3]]})

    # §31 LLM 可选：失败/异常一律回退规则版，不返回无依据经验
    if lesson_llm is not None:
        try:
            llm_lessons = lesson_llm({
                "context": {"trend_type": trend_type, "entities": entities,
                            "audience_segments": audience_segments,
                            "user_motivations": user_motivations},
                "action": strategy, "result": outcome,
                "evidence_ids": evidence_ids})
            if isinstance(llm_lessons, list) and llm_lessons:
                lessons = [{"lesson": l.get("lesson"),
                            "evidence_ids": l.get("evidence_ids") or evidence_ids,
                            "source": "llm"}
                           for l in llm_lessons if l.get("lesson")]
        except Exception:                     # 任何 LLM 故障都不阻塞 Case 生成
            pass

    # ---- §40 反模式抽取：短窗口 × 高开发成本 ----
    window_hours = (trend or {}).get("lifecycle_duration_hours")
    if window_hours and window_hours < _SHORT_WINDOW_HOURS \
            and dominant_type in _HIGH_COST_TYPES:
        anti_patterns.append("short_window + high_engineering_cost")

    # ---- 可靠性（§34/§35）：有实验取均值，无实验低分 —— 不许冒充强证据 ----
    if exps:
        reliability = round(sum(float(e.get("reliability_score") or 0.3) for e in exps)
                            / len(exps), 3)
    else:
        reliability = 0.2

    case_id = _det_case_id(event_id, strategy)
    existing = store.db.query_one("SELECT version FROM growth_case WHERE case_id=?", (case_id,))
    version = int((existing or {}).get("version") or 0) + 1
    now = _now(store)
    store.db.execute(
        "INSERT OR REPLACE INTO growth_case(case_id, title, trend_type, event_id, entity_ids,"
        " audience_segments, user_motivations, opportunity_type, strategy, creative_type,"
        " creative_summary, growth_goal, primary_metric, outcome, lessons, anti_patterns,"
        " applicability_conditions, reliability_score, source_refs, embedding_model,"
        " tier, authority, source, source_type, confidence, version, valid_from, valid_to,"
        " verified_by, human_verified, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (case_id,
         f"{(trend or {}).get('title') or event_id}：{strategy}",
         trend_type, event_id,
         store.db.dumps(entities), store.db.dumps(audience_segments),
         store.db.dumps(user_motivations), dominant_type, strategy, dominant_type,
         f"主导打法 {strategy}（{type_counts}）",
         None,
         _first(creatives, "primary_metric"),
         store.db.dumps(outcome), store.db.dumps(lessons), store.db.dumps(anti_patterns),
         store.db.dumps({"trend_type": trend_type,
                         "audience_segments": audience_segments,
                         "forced": bool(force and trend is None)}),
         reliability,
         store.db.dumps({"event_id": event_id, "analysis_ids":
                         sorted({c.get("analysis_id") for c in creatives
                                 if c.get("analysis_id")}),
                         "experiment_ids": [e["experiment_id"] for e in exps]}),
         None,
         "verified" if (exps and not force) else "candidate",
         "P2", "case_distillation", "learning_engine", 0.6 if exps else 0.4,
         version, now, None, None, 0, now, now))
    store.db.commit()

    # ---- §40 Anti-pattern 落库 ----
    ap_rows = []
    for pat in anti_patterns:
        ap = save_anti_pattern(store, pattern=pat,
                               lesson=lessons[0]["lesson"] if lessons else "",
                               evidence_cases=[case_id])
        ap_rows.append(ap["pattern_id"])

    return {"status": "updated" if existing else "created", "case_id": case_id,
            "strategy": strategy, "reliability": reliability,
            "n_experiments": len(exps), "outcome": outcome,
            "anti_patterns": ap_rows, "lessons": lessons}


def _first(rows: List[Dict[str, Any]], key: str) -> Optional[Any]:
    for r in rows:
        if r.get(key):
            return r.get(key)
    return None


# ================================================================ §40 Anti-pattern
def save_anti_pattern(store: MemoryStore, pattern: str, lesson: str,
                      evidence_cases: List[str]) -> Dict[str, Any]:
    pid = f"ap_{hashlib.sha1(pattern.encode('utf-8')).hexdigest()[:10]}"
    existing = store.db.query_one("SELECT * FROM anti_pattern WHERE pattern_id=?", (pid,))
    cases = sorted(set((store.db.loads(existing.get("evidence_cases"), []) if existing else [])
                       + list(evidence_cases)))
    store.db.execute(
        "INSERT OR REPLACE INTO anti_pattern VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, pattern, lesson, store.db.dumps(cases),
         existing.get("tier") if existing else "candidate",
         "P2", "case_distillation", "learning_engine",
         min(0.9, 0.5 + 0.1 * len(cases)),          # 重复出现的失败模式 → confidence 升
         (existing or {}).get("version", 0) + 1,
         (existing or {}).get("valid_from"), None, None, 0,
         (existing or {}).get("created_at"), _now(store)))
    store.db.commit()
    return {"pattern_id": pid, "n_evidence_cases": len(cases)}


# ================================================================ §41/§42 Playbook
def propose_playbook(store: MemoryStore, trend_type: str, min_cases: int = 3) -> Dict[str, Any]:
    """从已验证 Case 归纳推荐打法。§42：只能产出 **candidate**，审批是人的事。"""
    cases = store.candidates("case", filters={"trend_type": trend_type}, limit=200)
    strong = [c for c in cases if (c.get("reliability_score") or 0) >= 0.4
              or c.get("human_verified")]
    if len(strong) < min_cases:
        return {"status": "deferred",
                "reason": f"可靠 Case {len(strong)} < {min_cases}（§41：Case 太少不配归纳 Playbook）"}
    type_counts: Dict[str, int] = {}
    for c in strong:
        type_counts[c.get("creative_type") or "unknown"] = \
            type_counts.get(c.get("creative_type") or "unknown", 0) + 1
    steps = [f"优先 {t}（历史 {n} 例）" for t, n in
             sorted(type_counts.items(), key=lambda kv: -kv[1])]
    pb_id = f"pb_{hashlib.sha1(trend_type.encode()).hexdigest()[:10]}"
    store.db.execute(
        "INSERT OR REPLACE INTO playbook(playbook_id, trend_type, strategy, steps, status,"
        " supersedes, version, approved_by, approved_at, source_cases,"
        " tier, authority, source, source_type, confidence, valid_from, valid_to,"
        " created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pb_id, trend_type,
         f"{trend_type} 类热点的推荐打法（归纳自 {len(strong)} 个 Case）",
         store.db.dumps(steps), "candidate", None, 1, None, None,
         store.db.dumps([c["case_id"] for c in strong]),
         "candidate", "P3", "playbook_induction", "learning_engine", 0.5,
         None, None, _now(store), _now(store)))
    store.db.commit()
    return {"status": "proposed", "playbook_id": pb_id, "n_source_cases": len(strong),
            "steps": steps}


def approve_playbook(store: MemoryStore, playbook_id: str, approved_by: str) -> Dict[str, Any]:
    """§42 Human Review：approved 之前检索层默认不给 Agent 看（retrieval 只回 approved）。"""
    row = store.db.query_one("SELECT * FROM playbook WHERE playbook_id=?", (playbook_id,))
    if not row:
        raise ValueError(f"playbook 不存在：{playbook_id}")
    if row["status"] == "approved":
        return {"status": "already_approved"}
    store.db.execute(
        "UPDATE playbook SET status='approved', approved_by=?, approved_at=?, tier='verified',"
        " updated_at=? WHERE playbook_id=?", (approved_by, _now(store), _now(store), playbook_id))
    store.db.commit()
    return {"status": "approved", "playbook_id": playbook_id, "approved_by": approved_by}


def supersede_playbook(store: MemoryStore, old_id: str, new_steps: List[str],
                       approved_by: str) -> Dict[str, Any]:
    """§48 SUPERSEDES 链：Playbook V3 取代 V2，旧版留档不删除。"""
    old = store.db.query_one("SELECT * FROM playbook WHERE playbook_id=?", (old_id,))
    if not old:
        raise ValueError(f"playbook 不存在：{old_id}")
    new_id = f"{old_id}_v{int(old.get('version') or 1) + 1}"
    store.db.execute(
        "INSERT INTO playbook(playbook_id, trend_type, strategy, steps, status, supersedes,"
        " version, approved_by, approved_at, source_cases, tier, authority, source,"
        " source_type, confidence, valid_from, valid_to, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (new_id, old["trend_type"], old["strategy"], store.db.dumps(new_steps),
         "approved", old_id, int(old.get("version") or 1) + 1, approved_by, _now(store),
         old.get("source_cases"), "verified", "P3", "playbook_induction",
         "human_review", 0.6, _now(store), None, _now(store), _now(store)))
    store.db.execute(
        "UPDATE playbook SET status='superseded', valid_to=?, updated_at=? WHERE playbook_id=?",
        (_now(store), _now(store), old_id))
    store.db.commit()
    return {"status": "superseded", "old": old_id, "new": new_id}


def _now(store: MemoryStore) -> str:
    import datetime as _dt
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")
