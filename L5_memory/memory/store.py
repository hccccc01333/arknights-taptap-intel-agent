# -*- coding: utf-8 -*-
"""第五层写路径：6 类 Memory 的统一写入入口（§3 读写分离的"写"侧）。

★ 所有写入都过两道治理（本模块强制，调用方绕不开）：
  ① `governance.write_route(kind)` —— 决定进长期还是短期（§18-§20）；
     LLM 推断永远只能落短期/agent_generated，不粉饰（§53 知识污染防护）。
  ② `governance.scan_pii(record)` —— PII 绝不入记忆（§55）；
     记忆会被检索进 Agent Context，塞了个体轨迹就再也收不回来。

★ §10：Creative Memory 连被拒绝的都要存 —— 失败方案也是信息。
★ §14：Experiment 存相对 lift 而不仅是绝对数。
★ §34/§35：每条实验记忆带 reliability_score —— 小样本高 lift 不许和大样本同权重。
"""

from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from memory import governance as G
from memory.db import MemoryDB


class WriteRejected(ValueError):
    """PII 或策略拦截。绝不静默丢弃 —— 调用方必须知道为什么没存进去。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _det_id(prefix: str, *parts: str) -> str:
    """确定性 id：同样内容重跑不产生重复行（可重入）。"""
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{h}"


class MemoryStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db = MemoryDB(db_path)

    # ================================================================ knowledge
    def upsert_knowledge(self, memory_type: str, subject: str, title: str,
                         payload: Dict[str, Any], tags: Optional[List[str]] = None,
                         kind: str = "business_knowledge_update",
                         tier: Optional[str] = None, authority: str = "P2",
                         source: str = "", source_type: str = "",
                         confidence: float = 0.8,
                         valid_from: Optional[str] = None,
                         valid_to: Optional[str] = None,
                         verified_by: Optional[str] = None,
                         human_verified: bool = False) -> Dict[str, Any]:
        """§5/§6 事实型知识。同一 subject 出新版本 → 旧版本 valid_to 收口（§22/§23 历史可复现）。"""
        if memory_type not in ("business", "entity"):
            raise ValueError(f"memory_type 必须是 business|entity，收到 {memory_type!r}")
        bad = G.scan_pii({"subject": subject, "payload": payload})
        if bad:
            raise WriteRejected(f"PII 拦截（§55）：{bad}")
        route = G.write_route(kind)
        tier = tier or route["tier"]
        existing = self.db.query_one(
            "SELECT * FROM knowledge_item WHERE memory_type=? AND subject=? AND valid_to IS NULL "
            "ORDER BY version DESC", (memory_type, subject))
        version = G.next_version(existing)
        item_id = f"kb_{memory_type}_{_det_id('', memory_type, subject)[:10]}_v{version}"
        now = _now()
        if existing:
            # 新版本生效即旧版本失效（不是删除 —— §23 要能读"当时的能力"）
            self.db.execute("UPDATE knowledge_item SET valid_to=?, updated_at=? WHERE item_id=?",
                            (now, now, existing["item_id"]))
        self.db.execute(
            "INSERT INTO knowledge_item(item_id, memory_type, subject, title, payload, tags,"
            " tier, authority, source, source_type, confidence, version, valid_from, valid_to,"
            " verified_by, human_verified, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item_id, memory_type, subject, title, self.db.dumps(payload), self.db.dumps(tags or []),
             tier, authority, source, source_type, confidence, version,
             valid_from or now, valid_to, verified_by,
             1 if human_verified else 0, now, now))
        self.db.commit()
        return {"item_id": item_id, "version": version, "tier": tier,
                "superseded": existing["item_id"] if existing else None}

    def promote_knowledge(self, item_id: str, verified_by: str) -> Dict[str, Any]:
        """§53 升级路径：candidate → verified 必须有 verified_by（人/实验），无凭据拒绝。"""
        if not verified_by:
            raise WriteRejected("升级 verified 必须带 verified_by（§53：不许无凭据转正）")
        row = self.db.query_one("SELECT * FROM knowledge_item WHERE item_id=?", (item_id,))
        if not row:
            raise WriteRejected(f"知识不存在：{item_id}")
        self.db.execute(
            "UPDATE knowledge_item SET tier='verified', verified_by=?, human_verified=1,"
            " updated_at=? WHERE item_id=?", (verified_by, _now(), item_id))
        self.db.commit()
        return {"item_id": item_id, "tier": "verified", "verified_by": verified_by}

    # ================================================================ trend
    def save_trend(self, record: Dict[str, Any], source: str = "l3_trend",
                   source_type: str = "system") -> Dict[str, Any]:
        """§8 Trend Memory。只有生命周期闭合的事件进长期（§19）；
        进行中的热点请走 `put_short_term(kind='active_trend')`（§18）。"""
        bad = G.scan_pii(record)
        if bad:
            raise WriteRejected(f"PII 拦截（§55）：{bad}")
        route = G.write_route("event_lifecycle_ended")
        event_id = record.get("event_id")
        if not event_id:
            raise WriteRejected("trend record 缺 event_id")
        trend_id = _det_id("trd", event_id)
        now = _now()
        self.db.execute(
            "INSERT OR REPLACE INTO trend_memory(trend_id, event_id, title, event_type, entities,"
            " started_at, peak_at, ended_at, lifecycle_duration_hours, peak_hot_score,"
            " final_hot_score, platform_diffusion, diffusion_path, audiences, narratives,"
            " content_count, platform_count, outcome,"
            " tier, authority, source, source_type, confidence, version, valid_from, valid_to,"
            " verified_by, human_verified, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trend_id, event_id, record.get("title"), record.get("event_type"),
             self.db.dumps(record.get("entities") or []), record.get("started_at"),
             record.get("peak_at"), record.get("ended_at"),
             record.get("lifecycle_duration_hours"), record.get("peak_hot_score"),
             record.get("final_hot_score"), self.db.dumps(record.get("platform_diffusion") or []),
             self.db.dumps(record.get("diffusion_path") or []),
             self.db.dumps(record.get("audiences") or []),
             self.db.dumps(record.get("narratives") or []),
             record.get("content_count"), record.get("platform_count"),
             self.db.dumps(record.get("outcome")),
             route["tier"], "P1", source, source_type, record.get("confidence", 0.7),
             1, now, None, None, 0, now, now))
        self.db.commit()
        return {"trend_id": trend_id, "tier": route["tier"], "reason": route["reason"]}

    # ================================================================ creative
    def save_creative(self, record: Dict[str, Any],
                      source: str = "l4_intelligence") -> Dict[str, Any]:
        """§11 Creative Memory。Agent 产出默认 agent_generated/P4 ——
        不因为『是 AI 写的』而获得任何信任加成，人工采纳后由 record_decision 升级。"""
        bad = G.scan_pii(record)
        if bad:
            raise WriteRejected(f"PII 拦截（§55）：{bad}")
        analysis_id = record.get("analysis_id") or "unknown"
        idea_id = record.get("idea_id") or record.get("title") or ""
        creative_id = record.get("creative_id") or _det_id("cre", analysis_id, idea_id)
        now = _now()
        self.db.execute(
            "INSERT OR REPLACE INTO creative_memory(creative_id, event_id, opportunity_id,"
            " analysis_id, creative_type, title, target_audience, user_motivation,"
            " growth_mechanism, concept, primary_metric, launch_window, estimated_cost,"
            " evaluator_score, passed, human_decision, rejection_reason,"
            " tier, authority, source, source_type, confidence, version, valid_from, valid_to,"
            " verified_by, human_verified, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (creative_id, record.get("event_id"), record.get("opportunity_id"), analysis_id,
             record.get("creative_type"), record.get("title"), record.get("target_audience"),
             record.get("user_motivation"), record.get("growth_mechanism"),
             record.get("concept"), record.get("primary_metric"), record.get("launch_window"),
             record.get("estimated_cost"), record.get("evaluator_score"),
             1 if record.get("passed") else 0,
             record.get("human_decision") or "pending", record.get("rejection_reason"),
             record.get("tier") or "agent_generated", record.get("authority") or "P4",
             source, "agent_output", 0.5, 1, now, None, None, 0, now, now))
        self.db.commit()
        return {"creative_id": creative_id, "tier": record.get("tier") or "agent_generated"}

    # ================================================================ experiment
    @staticmethod
    def reliability_score(sample_size: Optional[int], p_value: Optional[float],
                          control_defined: bool, context_present: bool) -> float:
        """§35 Reliability = f(SampleSize, StatisticalSignificance, ExperimentDesign, DataQuality)。

        加权和（0~1）：小样本 + 无显著性 → 必然低分。§34 的纪律：
        sample_size=40 的 +80% 不许和 sample_size=5,000,000 的同权重进推荐。
        """
        if sample_size and sample_size > 0:
            s = min(1.0, math.log10(sample_size) / math.log10(1_000_000))
        else:
            s = 0.1                       # 没有样本量 = 最弱证据，不给 0 分也不给分
        if p_value is None:
            sig = 0.1                     # 没报显著性 = 按无显著性处理（不假设显著）
        elif p_value < 0.01:
            sig = 1.0
        elif p_value < 0.05:
            sig = 0.85
        elif p_value < 0.10:
            sig = 0.55
        else:
            sig = 0.15
        design = 0.8 if control_defined else 0.3
        quality = 1.0 if context_present else 0.5
        return round(0.35 * s + 0.30 * sig + 0.20 * design + 0.15 * quality, 3)

    def save_experiment(self, record: Dict[str, Any],
                        metrics: Optional[List[Dict[str, Any]]] = None,
                        source: str = "manual") -> Dict[str, Any]:
        """§12/§13 Experiment Memory + §14 相对 lift + §15 Experiment Context。

        kind=experiment_result → 长期 verified（§19 真实实验发生）；
        但 reliability 单独算 —— verified 是准入，reliability 是权重，两回事。
        """
        bad = G.scan_pii(record)
        if bad:
            raise WriteRejected(f"PII 拦截（§55）：{bad}")
        experiment_id = record.get("experiment_id") or f"exp_{uuid.uuid4().hex[:12]}"
        context = record.get("context")
        reliability = self.reliability_score(
            sample_size=(metrics[0].get("sample_size") if metrics else None)
            or record.get("sample_size"),
            p_value=(metrics[0].get("p_value") if metrics else None)
            or record.get("p_value"),
            control_defined=bool(record.get("control_definition")),
            context_present=bool(context))
        now = _now()
        route = G.write_route("experiment_result")
        self.db.execute(
            "INSERT OR REPLACE INTO experiment_memory(experiment_id, creative_id, event_id,"
            " experiment_name, experiment_type, audience_segment, treatment, control_definition,"
            " started_at, ended_at, primary_metric, status, result_summary,"
            " statistical_significance, context, reliability_score,"
            " tier, authority, source, source_type, confidence, version, valid_from, valid_to,"
            " verified_by, human_verified, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (experiment_id, record.get("creative_id"), record.get("event_id"),
             record.get("experiment_name"), record.get("experiment_type"),
             record.get("audience_segment"), record.get("treatment"),
             record.get("control_definition"), record.get("started_at"), record.get("ended_at"),
             record.get("primary_metric"), record.get("status") or "completed",
             record.get("result_summary"), record.get("statistical_significance"),
             self.db.dumps(context), reliability,
             route["tier"], "P1", source, "experiment_record", 0.9, 1,
             now, None, None, 0, now, now))
        for m in metrics or []:
            self.db.execute(
                "INSERT OR REPLACE INTO experiment_metric VALUES(?,?,?,?,?,?,?,?)",
                (experiment_id, m.get("metric_name"), m.get("baseline_value"),
                 m.get("experiment_value"), m.get("absolute_lift"), m.get("relative_lift"),
                 m.get("p_value"), m.get("sample_size")))
        self.db.commit()
        return {"experiment_id": experiment_id, "reliability_score": reliability,
                "tier": route["tier"]}

    def metrics_for(self, experiment_id: str) -> List[Dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM experiment_metric WHERE experiment_id=?", (experiment_id,))

    def best_relative_lift(self, experiment_id: str,
                           metric_name: Optional[str] = None) -> Optional[float]:
        sql = "SELECT relative_lift FROM experiment_metric WHERE experiment_id=?"
        params: tuple = (experiment_id,)
        if metric_name:
            sql += " AND metric_name=?"
            params = (experiment_id, metric_name)
        rows = self.db.query(sql + " ORDER BY ABS(COALESCE(relative_lift,0)) DESC", params)
        return rows[0]["relative_lift"] if rows else None

    # ================================================================ decision
    def record_decision(self, object_type: str, object_id: str, decision: str,
                        reason_text: str = "", reason_code: Optional[str] = None,
                        editor_diff: Optional[Dict[str, Any]] = None,
                        reviewer_role: str = "operator",
                        source_ref: str = "cli") -> Dict[str, Any]:
        """§16 Decision Memory + §17 结构化 reason + 自动回写被评对象的信任级别。

        approve/edit → 对象升 verified（§19 human_approved_creative）；
        reject → 对象保持 agent_generated，reason 入库供拒因分析（§17）。
        """
        if decision not in ("approve", "reject", "edit"):
            raise WriteRejected(f"decision 必须是 approve|reject|edit，收到 {decision!r}")
        # §17 taxonomy 只对 reject 归一 —— approve 理由里出现"机制"不代表拒因
        code = (reason_code or G.normalize_reason_code(reason_text)) \
            if decision == "reject" else (reason_code or "OTHER")
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        # 幂等：同一来源的同一决策不重复入库（回填重跑不产生重复行）
        if source_ref and source_ref != "cli":
            dup = self.db.query_one(
                "SELECT decision_id FROM decision_memory WHERE object_type=? AND object_id=?"
                " AND decision=? AND source_ref=?",
                (object_type, object_id, decision, source_ref))
            if dup:
                return {"decision_id": dup["decision_id"], "reason_code": code,
                        "dedup": True}
        self.db.execute(
            "INSERT INTO decision_memory VALUES(?,?,?,?,?,?,?,?,?,?)",
            (decision_id, object_type, object_id, decision, code, reason_text,
             self.db.dumps(editor_diff), reviewer_role, source_ref, _now()))
        if object_type == "creative":
            if decision in ("approve", "edit"):
                self.db.execute(
                    "UPDATE creative_memory SET human_decision=?, tier='verified',"
                    " human_verified=1, verified_by=?, updated_at=? WHERE creative_id=?",
                    (decision, reviewer_role, _now(), object_id))
            else:
                self.db.execute(
                    "UPDATE creative_memory SET human_decision=?, rejection_reason=?,"
                    " updated_at=? WHERE creative_id=?",
                    (decision, reason_text or code, _now(), object_id))
        self.db.commit()
        return {"decision_id": decision_id, "reason_code": code}

    # ================================================================ short-term
    def put_short_term(self, kind: str, payload: Dict[str, Any],
                       expires_at: Optional[str] = None,
                       memory_id: Optional[str] = None) -> Dict[str, Any]:
        """§18 短期记忆：进行中热点 / LLM 推断。TTL 按 kind（governance.short_term_ttl_days）。"""
        bad = G.scan_pii(payload)
        if bad:
            raise WriteRejected(f"PII 拦截（§55）：{bad}")
        route = G.write_route(kind if kind != "active_trend" else "active_trend")
        mid = memory_id or f"stm_{uuid.uuid4().hex[:12]}"
        self.db.execute("INSERT OR REPLACE INTO short_term_memory VALUES(?,?,?,?,?)",
                        (mid, kind, self.db.dumps(payload),
                         expires_at or G.default_expiry(kind), _now()))
        self.db.commit()
        return {"memory_id": mid, "horizon": route["horizon"], "tier": route["tier"],
                "expires_at": expires_at or G.default_expiry(kind)}

    def sweep_expired(self, now: Optional[str] = None) -> int:
        """过期清扫。返回删除条数 —— 不是隐藏：短期记忆到期就该消失（§18）。"""
        now = now or _now()
        cur = self.db.execute("DELETE FROM short_term_memory WHERE expires_at <= ?", (now,))
        self.db.commit()
        return cur.rowcount

    def short_term(self, kind: Optional[str] = None, limit: int = 500,
                   include_expired: bool = False) -> List[Dict[str, Any]]:
        """§18 短期记忆读取（检索层的 trend 合并用；过期的不给，除非显式要）。"""
        where = ["1=1"]
        params: List[Any] = []
        if kind:
            where.append("kind=?")
            params.append(kind)
        if not include_expired:
            where.append("expires_at > ?")
            params.append(_now())
        rows = self.db.query(
            f"SELECT * FROM short_term_memory WHERE {' AND '.join(where)}"
            " ORDER BY created_at DESC LIMIT ?", tuple(params) + (limit,))
        for r in rows:
            r["payload"] = self.db.loads(r.get("payload"), {}) or {}
        return rows

    # ================================================================ 读（供 retrieval）
    # 各 memory_type 允许的过滤列（防注入：白名单，不做字符串拼接 SQL）
    _FILTERABLE = {
        "business": ("knowledge_item", "subject", "title"),
        "entity": ("knowledge_item", "subject", "title"),
        "trend": ("trend_memory", "event_type", "event_id"),
        "creative": ("creative_memory", "creative_type", "human_decision", "event_id"),
        "experiment": ("experiment_memory", "experiment_type", "status", "creative_id"),
        "decision": ("decision_memory", "object_type", "decision"),
        "case": ("growth_case", "trend_type", "creative_type", "growth_goal"),
        "anti_pattern": ("anti_pattern",),
        "playbook": ("playbook", "trend_type", "status"),
    }

    _JSON_COLUMNS = {
        "knowledge_item": ("payload", "tags"),
        "trend_memory": ("entities", "platform_diffusion", "diffusion_path",
                         "audiences", "narratives", "outcome"),
        "creative_memory": ("editor_diff",),
        "experiment_memory": ("context",),
        "decision_memory": ("editor_diff",),
        "growth_case": ("entity_ids", "audience_segments", "user_motivations",
                        "outcome", "lessons", "anti_patterns",
                        "applicability_conditions", "source_refs"),
        "anti_pattern": ("evidence_cases",),
        "playbook": ("steps", "source_cases"),
        "short_term_memory": ("payload",),
    }

    def candidates(self, memory_type: str, filters: Optional[Dict[str, Any]] = None,
                   limit: int = 500, include_playbook_candidates: bool = False) -> List[Dict[str, Any]]:
        """检索候选集：白名单过滤 + 时效过滤（§22）+ JSON 解码。

        playbook 默认只回 approved（§42：candidate 不能当公司事实用）。
        """
        spec = self._FILTERABLE.get(memory_type)
        if not spec:
            raise ValueError(f"未知 memory_type: {memory_type}")
        table, *filter_cols = spec
        where = ["1=1"]
        params: List[Any] = []
        # ★ business/entity 同表（knowledge_item）—— 必须按 memory_type 分流，
        #   否则同一个实体条目会以两种 memory_type 重复返回。
        if table == "knowledge_item":
            where.append("memory_type=?")
            params.append(memory_type)
        for k, v in (filters or {}).items():
            if k in ("performance_filter", "memory_type"):
                continue
            if k not in filter_cols:
                raise ValueError(f"{memory_type} 不支持过滤列 {k!r}（白名单：{filter_cols}）")
            where.append(f"{k}=?")
            params.append(v)
        if table == "playbook" and not include_playbook_candidates:
            where.append("status='approved'")
        rows = self.db.query(
            f"SELECT * FROM {table} WHERE {' AND '.join(where)} LIMIT ?", tuple(params) + (limit,))
        out = []
        for r in rows:
            if not G.is_currently_valid(r):
                continue                      # §22 失效知识不进候选
            r = dict(r)
            r["memory_type"] = memory_type
            for col in self._JSON_COLUMNS.get(table, ()):
                if col in r:
                    r[col] = self.db.loads(r.get(col))
            out.append(r)
        return out

    def alias_map(self) -> Dict[str, str]:
        """实体别名 → 规范名（Query Understanding 用；来自 entity knowledge + games 档案）。"""
        mapping: Dict[str, str] = {}
        for row in self.db.query(
                "SELECT subject, title, payload FROM knowledge_item"
                " WHERE memory_type='entity' AND valid_to IS NULL"):
            payload = self.db.loads(row.get("payload"), {}) or {}
            for a in [row.get("subject"), row.get("title")] + list(payload.get("aliases") or []):
                if a:
                    mapping[str(a).lower()] = row["subject"]
        return mapping

    # ================================================================ stats
    def stats(self) -> Dict[str, Any]:
        def n(table: str) -> int:
            return self.db.table_count(table)
        stm = self.db.query_one(
            "SELECT COUNT(*) AS live, SUM(CASE WHEN expires_at<=? THEN 1 ELSE 0 END) AS expired"
            " FROM short_term_memory", (_now(),))
        return {
            "schema_version": self.db.meta("schema_version"),
            "knowledge_business": n("knowledge_item"),
            "trend_memory": n("trend_memory"),
            "creative_memory": n("creative_memory"),
            "experiment_memory": n("experiment_memory"),
            "experiment_metrics": n("experiment_metric"),
            "decision_memory": n("decision_memory"),
            "growth_case": n("growth_case"),
            "anti_pattern": n("anti_pattern"),
            "playbook": self.db.query_one(
                "SELECT status, COUNT(*) c FROM playbook GROUP BY status") and
            {r["status"]: r["c"] for r in self.db.query(
                "SELECT status, COUNT(*) c FROM playbook GROUP BY status")},
            "short_term_live": (stm or {}).get("live") or 0,
            "short_term_expired": (stm or {}).get("expired") or 0,
            "retrieval_logs": n("retrieval_log"),
        }

    def close(self) -> None:
        self.db.close()
