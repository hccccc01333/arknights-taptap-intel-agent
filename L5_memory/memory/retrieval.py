# -*- coding: utf-8 -*-
"""Retrieval Engine（§24-§28 / §45 / §46）—— 第五层最核心组件。

★ 设计 §24：第四层每个 Agent **不许直查库**，统一走本模块：
    retrieve(query, memory_type, filters, top_k, ranking_strategy, caller)
  这样以后升级检索算法（换向量 / 换重排）时，第四层一行代码不用动。

★ 混合评分（§25）：
    RetrievalScore = w1*Semantic + w2*Entity + w3*Metadata + w4*Recency
                   + w5*Performance + w6*Trust
  绝不只做 semantic（§25/§26）—— "意思像"不等于"同游戏/同动机/有效/仍可信"。

★ 语义后端现状（如实标注，§57 不装）：
  `backend="lexical_bigram"`（字符 bigram 余弦）。L2 的 embedding 库有数据后
  换 backend 即可，接口与产物结构不变 —— 这正是 §24 收口检索的意义。

★ 返回 Case Package（§28），不是一堆 chunk：context / strategy / result / lessons / confidence。

★ §46 Diversity Filter：五条"捏脸挑战"塞给 Agent 只会制造局部偏见 ——
  按 creative_type / event_type 类别轮转配额。

★ §52：每次检索落观测日志（query / candidate_count / returned / used / decision），
  事后才能回答"哪些 Memory 真正产生价值"。
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from memory import governance as G
from memory.store import MemoryStore

RETRIEVAL_VERSION = "retrieval-1.0"

# §25 六维权重（ranking_strategy 可覆盖）
DEFAULT_WEIGHTS: Dict[str, float] = {
    "semantic": 0.30, "entity": 0.20, "metadata": 0.20,
    "recency": 0.10, "performance": 0.10, "trust": 0.10,
}

# 增长目标词面（metadata 维度；与 L4 knowledge.GROWTH_GOALS 对齐，此处独立小表避免跨层依赖）
_GOAL_HINTS: Dict[str, Tuple[str, ...]] = {
    "ugc": ("ugc", "投稿", "用户内容", "二创"),
    "share": ("分享", "转发", "裂变", "share"),
    "reach": ("触达", "曝光", "拉新", "扩散", "reach"),
    "engagement": ("互动", "讨论", "engagement"),
    "game_follow": ("关注", "follow"),
    "community_activation": ("社区激活", "社区氛围", "活跃"),
    "reactivation": ("召回", "回流", "沉默"),
    "install": ("下载", "安装", "预约"),
}

_SEMANTIC_TEXT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "business": ("title", "payload_text"),
    "entity": ("title", "payload_text"),
    "trend": ("title", "event_type", "narratives_text"),
    "creative": ("title", "concept", "growth_mechanism"),
    "experiment": ("experiment_name", "treatment", "result_summary"),
    "decision": ("reason_text",),
    "case": ("title", "strategy", "creative_summary", "lessons_text"),
    "anti_pattern": ("pattern", "lesson"),
    "playbook": ("trend_type", "strategy"),
}


# ================================================================ 语义（词面 bigram）
def _bigrams(text: str) -> Counter:
    t = "".join(ch for ch in (text or "").lower() if not ch.isspace())
    return Counter(t[i:i + 2] for i in range(len(t) - 1)) if len(t) > 1 else Counter()


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    na = math_sqrt(sum(v * v for v in a.values()))
    nb = math_sqrt(sum(v * v for v in b.values()))
    return round(dot / (na * nb), 4) if na and nb else 0.0


def math_sqrt(x: float) -> float:
    # 包一层便于测试桩替换；也避免本模块顶部 import math 与局部遮蔽混淆
    import math
    return math.sqrt(x)


# ================================================================ 查询理解（§45 第一步）
def understand_query(query: str, alias_map: Dict[str, str]) -> Dict[str, Any]:
    """Query Understanding + Entity Extraction：别名归一 + 目标词面提示 + bigram。"""
    q = (query or "").lower()
    entities: List[str] = []
    for alias, subject in alias_map.items():
        if alias and alias in q and subject not in entities:
            entities.append(subject)
    goals = [g for g, kws in _GOAL_HINTS.items() if any(k in q for k in kws)]
    return {"query": query, "entities": entities, "goals": goals,
            "bigrams": _bigrams(query)}


# ================================================================ 条目 → 评分输入
def _item_entities(item: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for f in ("entities", "entity_ids", "audience_segments", "user_motivations"):
        v = item.get(f)
        if isinstance(v, list):
            out.update(str(x) for x in v)
    if item.get("subject"):
        out.add(str(item["subject"]))
    return out


def _item_text(item: Dict[str, Any], memory_type: str) -> str:
    parts: List[str] = []
    for f in _SEMANTIC_TEXT_FIELDS.get(memory_type, ()):
        v = item.get(f)
        if isinstance(v, list):
            parts.append(" ".join(str(x) for x in v))
        elif v is not None:
            parts.append(str(v))
    return " ".join(parts)


def _item_goal_tokens(item: Dict[str, Any]) -> Set[str]:
    blob = " ".join(str(item.get(f) or "") for f in
                    ("growth_goal", "primary_metric", "concept", "strategy",
                     "payload_text", "goals_text")).lower()
    return {g for g, kws in _GOAL_HINTS.items() if any(k in blob for k in kws)}


def _ref_time(item: Dict[str, Any]) -> Optional[str]:
    for f in ("updated_at", "created_at", "ended_at", "approved_at"):
        if item.get(f):
            return str(item[f])
    return None


def _performance_of(item: Dict[str, Any], memory_type: str,
                    perf_lookup: Dict[str, Tuple[Optional[float], Optional[float]]]) -> float:
    """performance 维度（§25 w5）：reliability 与正 lift 的组合；无数据 → 0.5 中性。"""
    if memory_type == "experiment":
        rel = item.get("reliability_score")
        lift = perf_lookup.get(item.get("experiment_id"), (None, None))[1]
    elif memory_type == "case":
        rel = item.get("reliability_score")
        outcome = item.get("outcome") or {}
        lifts = [v for v in (outcome or {}).values() if isinstance(v, (int, float))]
        lift = max(lifts) if lifts else None
    else:
        return 0.5
    rel = float(rel) if rel is not None else 0.4
    lift_part = 0.5
    if lift is not None:
        lift_part = min(1.0, max(0.0, float(lift)))      # 相对 lift 1.0 (+100%) 封顶
    return round(0.5 * rel + 0.5 * lift_part, 4)


def to_package(item: Dict[str, Any], memory_type: str,
               perf_lookup: Dict[str, Tuple[Optional[float], Optional[float]]]) -> Dict[str, Any]:
    """§28 Case Package：给 Agent 消费的结构化案例，不是 chunk。"""
    if memory_type == "case":
        outcome = item.get("outcome") or {}
        return {"case_id": item.get("case_id"), "title": item.get("title"),
                "context": {"trend_type": item.get("trend_type"),
                            "entities": item.get("entity_ids"),
                            "audience_segments": item.get("audience_segments"),
                            "user_motivations": item.get("user_motivations")},
                "strategy": item.get("strategy"), "creative_type": item.get("creative_type"),
                "result": outcome if outcome else None,
                "lessons": item.get("lessons") or [],
                "anti_patterns": item.get("anti_patterns") or [],
                "applicability_conditions": item.get("applicability_conditions") or [],
                "reliability": item.get("reliability_score"),
                "human_verified": bool(item.get("human_verified")),
                "memory_id": item.get("case_id")}
    if memory_type == "experiment":
        lift = perf_lookup.get(item.get("experiment_id"), (None, None))[1]
        return {"experiment_id": item.get("experiment_id"), "title": item.get("experiment_name"),
                "context": item.get("context") or {},
                "treatment": item.get("treatment"),
                "result": {"primary_metric": item.get("primary_metric"),
                           "best_relative_lift": lift},
                "reliability": item.get("reliability_score"),
                "status": item.get("status"), "memory_id": item.get("experiment_id")}
    if memory_type == "trend":
        return {"trend_id": item.get("trend_id"), "event_id": item.get("event_id"),
                "title": item.get("title"), "event_type": item.get("event_type"),
                "horizon": item.get("horizon") or "long_term",
                "lifecycle": {"started_at": item.get("started_at"), "peak_at": item.get("peak_at"),
                              "ended_at": item.get("ended_at"),
                              "duration_hours": item.get("lifecycle_duration_hours")},
                "platform_diffusion": item.get("platform_diffusion") or [],
                "memory_id": item.get("trend_id") or
                (f"stm:{item.get('event_id')}" if item.get("horizon") == "short_term"
                 else None)}
    if memory_type == "anti_pattern":
        return {"pattern_id": item.get("pattern_id"), "pattern": item.get("pattern"),
                "lesson": item.get("lesson"), "memory_id": item.get("pattern_id")}
    if memory_type == "playbook":
        return {"playbook_id": item.get("playbook_id"), "trend_type": item.get("trend_type"),
                "strategy": item.get("strategy"), "steps": item.get("steps") or [],
                "status": item.get("status"), "memory_id": item.get("playbook_id")}
    # business / entity / creative / decision：通用包
    return {"memory_id": (item.get("item_id") or item.get("creative_id")
                          or item.get("decision_id")),
            "title": item.get("title") or item.get("subject"),
            "memory_type": memory_type,
            "payload": item.get("payload") if "payload" in item else
            {k: item.get(k) for k in ("creative_type", "concept", "human_decision",
                                      "rejection_reason", "decision", "reason_code")
             if item.get(k) is not None}}


# ================================================================ 多样性（§46）
def diversify(scored: List[Tuple[float, Any]], cat_fn: Any, top_k: int,
              per_category: int = 2) -> List[Tuple[float, Any]]:
    """类别轮转：先每类取 per_category 条，再按分数补齐。避免"五条捏脸挑战"。

    泛型设计：payload 由调用方定义（retrieve 传 (item, parts) 元组），cat_fn 负责给类别。
    """
    buckets: Dict[str, List[Tuple[float, Any]]] = {}
    for s, payload in scored:
        buckets.setdefault(cat_fn(payload), []).append((s, payload))
    for v in buckets.values():
        v.sort(key=lambda x: -x[0])
    picked: List[Tuple[float, Any]] = []
    cats = sorted(buckets, key=lambda c: -(buckets[c][0][0] if buckets[c] else 0))
    for c in cats:                                # 第一轮：每类配额
        picked.extend(buckets[c][:per_category])
        if len(picked) >= top_k:
            break
    rest = [(s, p) for c in cats for (s, p) in buckets[c][per_category:]]
    rest.sort(key=lambda x: -x[0])
    for s, p in rest:                             # 补齐轮：纯分数
        if len(picked) >= top_k:
            break
        picked.append((s, p))
    picked.sort(key=lambda x: -x[0])
    return picked[:top_k]


def _item_category(item: Dict[str, Any]) -> str:
    return str(item.get("creative_type") or item.get("event_type")
               or item.get("trend_type") or item.get("experiment_type") or "_")


# ================================================================ 引擎
class RetrievalEngine:
    def __init__(self, store: MemoryStore, weights: Optional[Dict[str, float]] = None):
        self.store = store
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)

    # -------------------------------------------------------------- 主入口（§24）
    def retrieve(self, query: str, memory_type: Optional[str] = None,
                 filters: Optional[Dict[str, Any]] = None, top_k: int = 10,
                 ranking_strategy: Optional[Dict[str, Any]] = None,
                 caller: str = "system", log: bool = True) -> Dict[str, Any]:
        """统一检索服务。memory_type=None 时跨类型检索（仍受 caller 权限约束）。"""
        weights = dict(self.weights)
        per_cat = 2
        if ranking_strategy:
            weights.update(ranking_strategy.get("weights") or {})
            per_cat = int(ranking_strategy.get("per_category", per_cat))
        filters = dict(filters or {})
        types = [memory_type] if memory_type else \
            [t for t in ("case", "experiment", "trend", "business", "entity",
                         "creative", "anti_pattern", "playbook")
             if G.access_allowed(caller, t)]
        if memory_type and not G.access_allowed(caller, memory_type):
            return self._result(query, caller, memory_type or "multi", [], 0, weights,
                                blocked=f"caller {caller!r} 无权读取 {memory_type!r}（§54）")

        alias_map = self.store.alias_map()
        qu = understand_query(query, alias_map)
        perf_lookup = self._perf_lookup()

        scored_all: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
        backend = "lexical_bigram"
        n_candidates = 0
        for mt in types:
            try:
                rows = self.store.candidates(mt, filters)
            except ValueError:                # 过滤列对该类型非法 → 该类型跳过，不炸整个检索
                continue
            n_candidates += len(rows)
            pf = (filters.get("performance_filter") or {})
            for item in rows:
                # §27 Performance Retrieval：{"relative_lift": ">0"} —— 只找正效果。
                # 实验看相对 lift；Case 看 outcome 里的 lifts；其他类型不受此过滤。
                if pf.get("relative_lift") == ">0" and mt in ("experiment", "case"):
                    if _performance_of(item, mt, perf_lookup) <= 0.5:
                        n_candidates -= 1
                        continue
                s, parts = self._score(item, mt, qu, weights, perf_lookup, filters)
                scored_all.append((s, item, parts))
            # §18：trend 检索合并短期记忆（进行中热点；天/周生命周期，recency 天然占优）
            if mt == "trend":
                stm = self.store.short_term("active_trend", limit=500)
                n_candidates += len(stm)
                for st in stm:
                    item = dict(st.get("payload") or {})
                    item["memory_type"] = "trend"
                    item["horizon"] = "short_term"
                    item["tier"] = st.get("tier") or "candidate"
                    item["authority"] = "P3"
                    item["created_at"] = st.get("created_at")
                    s, parts = self._score(item, "trend", qu, weights, perf_lookup, filters)
                    scored_all.append((s, item, parts))

        scored_all.sort(key=lambda x: -x[0])
        diversified = diversify([(s, (it, parts)) for s, it, parts in scored_all],
                                lambda payload: _item_category(payload[0]),
                                top_k, per_cat)

        items: List[Dict[str, Any]] = []
        for s, (it, parts) in diversified:
            pkg = to_package(it, it.get("memory_type", memory_type or "multi"), perf_lookup)
            pkg["score"] = s
            pkg["scores"] = parts
            pkg["memory_type"] = it.get("memory_type", memory_type)
            pkg["tier"] = it.get("tier")
            pkg["authority"] = it.get("authority")
            items.append(pkg)

        result = self._result(query, caller, memory_type or "multi", items, n_candidates,
                              weights, backend=backend)
        if log:
            self._write_log(result, qu, filters)
        return result

    # -------------------------------------------------------------- 评分（§25）
    def _score(self, item: Dict[str, Any], memory_type: str, qu: Dict[str, Any],
               weights: Dict[str, float], perf_lookup: Dict[str, Any],
               filters: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
        sem = _cosine(qu["bigrams"], _bigrams(_item_text(item, memory_type)))
        q_ent = set(qu["entities"])
        i_ent = _item_entities(item)
        if q_ent and i_ent:
            ent = len(q_ent & i_ent) / len(q_ent | i_ent)
        else:
            ent = 0.0 if q_ent else 0.5            # 查询没提实体 → 中性，不惩罚
        i_goals = _item_goal_tokens(item)
        meta = (len(set(qu["goals"]) & i_goals) / len(qu["goals"])
                if qu["goals"] else 0.5)
        rec = G.recency_weight(_ref_time(item), kind=memory_type)
        perf = _performance_of(item, memory_type, perf_lookup)
        trust = G.trust_score(item)
        parts = {"semantic": sem, "entity": ent, "metadata": meta,
                 "recency": rec, "performance": perf, "trust": trust}
        total = sum(weights.get(k, 0.0) * v for k, v in parts.items())
        return round(total, 4), parts

    # -------------------------------------------------------------- 性能索引
    def _perf_lookup(self) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
        """experiment_id → (reliability, best_relative_lift)。一次查好，候选循环里不再碰库。"""
        out: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
        try:
            for exp in self.store.candidates("experiment", limit=2000):
                out[exp["experiment_id"]] = (exp.get("reliability_score"),
                                             self.store.best_relative_lift(exp["experiment_id"]))
        except Exception:
            pass
        return out

    # -------------------------------------------------------------- 观测（§52）
    def _write_log(self, result: Dict[str, Any], qu: Dict[str, Any],
                   filters: Dict[str, Any]) -> str:
        log_id = f"rlg_{uuid.uuid4().hex[:12]}"
        self.store.db.execute(
            "INSERT INTO retrieval_log(log_id, ts, caller, query, memory_type, filters,"
            " candidate_count, returned_ids, ranking_scores, used_ids, final_decision, backend)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (log_id, datetime.now().astimezone().isoformat(timespec="seconds"),
             result["caller"], qu["query"], result["memory_type"],
             self.store.db.dumps(filters), result["candidate_count"],
             self.store.db.dumps([i["memory_id"] for i in result["items"]]),
             self.store.db.dumps({i["memory_id"]: i["score"] for i in result["items"]}),
             None, None, result["backend"]))
        self.store.db.commit()
        result["log_id"] = log_id
        return log_id

    @staticmethod
    def mark_used(store: MemoryStore, log_id: str, used_ids: List[str],
                  final_decision: Optional[str] = None) -> None:
        """§52 事后回填：Agent 实际用了哪几条 —— 不回填的 Memory 就是白检索。"""
        store.db.execute("UPDATE retrieval_log SET used_ids=?, final_decision=? WHERE log_id=?",
                         (store.db.dumps(used_ids or []), final_decision, log_id))
        store.db.commit()

    # -------------------------------------------------------------- 结果封装
    @staticmethod
    def _result(query: str, caller: str, memory_type: str, items: List[Dict[str, Any]],
                n_candidates: int, weights: Dict[str, float], backend: str = "lexical_bigram",
                blocked: str = "") -> Dict[str, Any]:
        out = {"retrieval_version": RETRIEVAL_VERSION, "query": query, "caller": caller,
               "memory_type": memory_type, "backend": backend,      # §57 如实标注，不假装有向量
               "weights": weights, "candidate_count": n_candidates,
               "items": items, "blocked": blocked}
        if blocked:
            out["items"] = []
        return out
