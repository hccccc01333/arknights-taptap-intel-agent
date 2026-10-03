# -*- coding: utf-8 -*-
"""Experiment Engine（§23 / §24 / §25 / §26-§28 / §30 / §31）。

★ §23：没有 Control/Treatment 的"上线"不叫实验，"数据看起来不错"不说明方案有效。
★ §24：实验由 L4 的 Growth Hypothesis 驱动（AI Reasoning → Testable Hypothesis → Experiment），
  hypothesis 字段直接取 creative.growth_hypothesis —— 不是复盘时编的。
★ §25：实验结果自动回第五层（Experiment Memory → Case Distillation → 下次检索）——
  Learning Loop 的最后一米在这里接上。
★ §28：Primary / Secondary / **Guardrail** 分开记账；护栏击穿 → 不许判 WIN。
★ §30：结果五态 WIN / LOSS / INCONCLUSIVE / STOPPED / INVALID —— **失败必须允许存在**，
  "活动取得良好效果"这种总结在本层是语法错误。

统计：两比例 pooled z 检验直接复用 L3_trend/intel_stats.py（stdlib，已在生产使用）。
比例指标给 (x_t, n_t, x_c, n_c) → 有 p 值；连续指标只算 lift，p 值如实 None。
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_ROOT, os.path.join(_ROOT, "L3_trend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from execution.audit import AuditLog, PermissionDenied, require_role  # noqa: E402
from execution.db import ExecutionDB  # noqa: E402

EXPERIMENT_ENGINE_VERSION = "experiment-1.0"

# §30 五种结果态
RESULT_STATES = ("WIN", "LOSS", "INCONCLUSIVE", "STOPPED", "INVALID")

ALPHA = 0.05
DEFAULT_GUARDRAILS = ("report_rate", "hide_rate", "d1_retention")
DEFAULT_GUARDRAIL_MAX_REL_INCREASE = 0.50    # §28：举报率 +200% 不算成功；+50% 即击穿


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ztest(x_t: int, n_t: int, x_c: int, n_c: int):
    """复用 L3 的两比例 z 检验（stdlib）。L3 缺失时返回 None（诚实降级）。"""
    try:
        from intel_stats import two_prop_ztest
        return two_prop_ztest(x_t, n_t, x_c, n_c)      # (z, p, significant)
    except Exception:
        return None, None, None


class ExperimentEngine:
    def __init__(self, db: ExecutionDB, audit: AuditLog, memory_store: Any = None):
        self.db = db
        self.audit = audit
        self.memory = memory_store        # L5 MemoryStore（§25 回写；None 则跳过并如实标注）

    # -------------------------------------------------------------- §24 建实验
    def create(self, creative_id: str, plan_id: Optional[str], event_id: str,
               name: str, hypothesis: str, population: str, treatment: str,
               control: str, primary_metric: str,
               secondary_metrics: Optional[List[str]] = None,
               guardrail_metrics: Optional[List[str]] = None,
               guardrail_max_relative_increase: float = DEFAULT_GUARDRAIL_MAX_REL_INCREASE,
               duration_hours: float = 24.0,
               actor: str = "operator", role: str = "operator") -> Dict[str, Any]:
        require_role(role, "create_creative")
        if not control:
            raise ValueError("§23：没有 control 就不是实验 —— 拒绝创建")
        if not hypothesis:
            raise ValueError("§24：实验必须由 Growth Hypothesis 驱动 —— 拒绝无假设实验")
        experiment_id = f"exp_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO experiment(experiment_id, creative_id, plan_id, event_id, name,"
            " hypothesis, population, treatment, control, primary_metric,"
            " secondary_metrics, guardrail_metrics, guardrail_max_relative_increase,"
            " duration_hours, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (experiment_id, creative_id, plan_id, event_id, name, hypothesis, population,
             treatment, control, primary_metric,
             self.db.dumps(secondary_metrics or []),
             self.db.dumps(guardrail_metrics or list(DEFAULT_GUARDRAILS)),
             guardrail_max_relative_increase, duration_hours, "planned", now, now))
        self.db.commit()
        self.audit.record(action="experiment_created", actor=actor, role=role,
                          object_type="experiment", object_id=experiment_id,
                          detail={"hypothesis": hypothesis, "primary": primary_metric})
        return {"experiment_id": experiment_id, "status": "planned"}

    def start(self, experiment_id: str, actor: str = "operator", role: str = "operator"):
        self._set(experiment_id, status="running", started_at=_now())
        self.audit.record(action="experiment_started", actor=actor, role=role,
                          object_type="experiment", object_id=experiment_id)
        return {"experiment_id": experiment_id, "status": "running"}

    # -------------------------------------------------------------- §26/§28 记录观测
    def observe(self, experiment_id: str, metric_name: str, metric_class: str,
                baseline_value: Optional[float] = None,
                treatment_value: Optional[float] = None,
                x_t: Optional[int] = None, n_t: Optional[int] = None,
                x_c: Optional[int] = None, n_c: Optional[int] = None,
                actor: str = "operator", role: str = "operator") -> Dict[str, Any]:
        """记录一次指标观测。比例指标给 (x,n) 对 → 自动 z 检验；连续指标给值 → 只算 lift。"""
        require_role(role, "create_creative")
        exp = self._get(experiment_id)
        if exp["status"] not in ("running", "planned"):
            raise ValueError(f"实验 {experiment_id} 状态 {exp['status']}，不能记录观测")
        rel_class = ("primary", "secondary", "guardrail")
        if metric_class not in rel_class:
            raise ValueError(f"metric_class 必须是 {rel_class}（§28 三类分开记账）")
        if metric_class != "primary" and metric_name == exp["primary_metric"]:
            raise ValueError(f"{metric_name} 是主指标，必须以 primary 记录")
        if metric_class == "primary" and metric_name != exp["primary_metric"]:
            raise ValueError(f"{metric_name} 不是主指标（主指标是 {exp['primary_metric']}）")
        p_value: Optional[float] = None
        sig: Optional[bool] = None
        if None not in (x_t, n_t, x_c, n_c):
            _, p_value, sig = _ztest(x_t, n_t, x_c, n_c)   # 比例指标
        base = baseline_value
        treat = treatment_value
        if base is None and n_c:
            base = x_c / n_c
        if treat is None and n_t:
            treat = x_t / n_t
        abs_lift = rel_lift = None
        if base is not None and treat is not None:
            abs_lift = round(treat - base, 6)
            rel_lift = round((treat - base) / base, 4) if base else None
        self.db.execute(
            "INSERT OR REPLACE INTO experiment_metric VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (experiment_id, metric_name, metric_class, base, treat,
             x_t, n_t, x_c, n_c, abs_lift, rel_lift, p_value,
             (n_t or 0) + (n_c or 0) or None))
        self.db.commit()
        return {"experiment_id": experiment_id, "metric": metric_name,
                "relative_lift": rel_lift, "p_value": p_value, "significant": sig}

    # -------------------------------------------------------------- §30 结果判定
    def finish(self, experiment_id: str, actor: str = "reviewer", role: str = "reviewer",
               reason: str = "") -> Dict[str, Any]:
        require_role(role, "decide")
        exp = self._get(experiment_id)
        if exp["status"] in ("completed", "stopped", "invalid"):
            raise ValueError(f"实验 {experiment_id} 已终态（{exp['status']}）")
        metrics = self.db.query(
            "SELECT * FROM experiment_metric WHERE experiment_id=?", (experiment_id,))
        primary = next((m for m in metrics if m["metric_class"] == "primary"), None)
        guardrails = [m for m in metrics if m["metric_class"] == "guardrail"]
        breached = [m["metric_name"] for m in guardrails
                    if (m["relative_lift"] or 0)
                    > float(exp["guardrail_max_relative_increase"] or 0.5)]
        result_state, why = self._judge(exp, primary, breached, metrics)
        self._set(experiment_id, status="completed" if result_state != "STOPPED"
                  else "stopped",
                  result_state=result_state, result_reason=why,
                  guardrail_breached=1 if breached else 0, ended_at=_now())
        self.audit.record(action="experiment_finished", actor=actor, role=role,
                          object_type="experiment", object_id=experiment_id,
                          detail={"result_state": result_state, "why": why,
                                  "guardrail_breached": breached})
        writeback = self._writeback_memory(exp, metrics, result_state)
        return {"experiment_id": experiment_id, "result_state": result_state,
                "reason": why, "guardrail_breached": breached,
                "memory_writeback": writeback}

    @staticmethod
    def _judge(exp: Dict[str, Any], primary: Optional[Dict[str, Any]],
               breached: List[str], metrics: List[Dict[str, Any]]) -> tuple:
        if exp.get("status") == "stopped":
            return "STOPPED", "人工终止（kill switch / 主动停止）"
        if exp.get("status") == "invalid" or not primary:
            return "INVALID", "无主指标观测或设计无效 —— 不产生任何效果结论"
        lift = primary["relative_lift"]
        p = primary["p_value"]
        if lift is None:
            return "INVALID", "主指标没有可比数值"
        if breached:
            return ("INCONCLUSIVE",
                    f"主指标正向但护栏击穿（{','.join(breached)}）—— 按纪律不算成功（§28）")
        if lift > 0 and p is not None and p < ALPHA:
            return "WIN", f"主指标相对 lift {lift:+.2%} 且 p={p:.3f}<{ALPHA}"
        if lift > 0:
            return ("INCONCLUSIVE",
                    f"主指标正向（{lift:+.2%}）但未达显著（p={'无' if p is None else f'{p:.3f}' }）")
        if lift < 0 and p is not None and p < ALPHA:
            return "LOSS", f"主指标显著负向（{lift:+.2%}）—— 失败也是有效结论（§30）"
        return "INCONCLUSIVE", f"主指标 {lift:+.2%}，方向/显著性不足以判定"

    # -------------------------------------------------------------- §25 回写第五层
    def _writeback_memory(self, exp: Dict[str, Any], metrics: List[Dict[str, Any]],
                          result_state: str) -> Dict[str, Any]:
        if self.memory is None:
            return {"status": "skipped", "reason": "未接第五层（§25 Learning Loop 缺口，如实标注）"}
        try:
            store = self.memory
            record = {
                "creative_id": exp.get("creative_id"), "event_id": exp.get("event_id"),
                "experiment_name": exp.get("name"),
                "experiment_type": "growth_experiment",
                "audience_segment": exp.get("population"),
                "treatment": exp.get("treatment"), "control_definition": exp.get("control"),
                "started_at": exp.get("started_at"), "ended_at": exp.get("ended_at"),
                "primary_metric": exp.get("primary_metric"),
                "status": "completed" if exp.get("status") == "completed" else exp.get("status"),
                "result_summary": f"{result_state}: {exp.get('result_reason') or ''}",
                "statistical_significance": next(
                    (m["p_value"] for m in metrics
                     if m["metric_class"] == "primary" and m["p_value"] is not None), None),
                # §15 Experiment Context：渠道/资源/季节 —— 归因离不开这些（§31）
                "context": {"channels": exp.get("plan_id"),
                            "guardrails": self.db.loads(exp.get("guardrail_metrics")),
                            "result_state": result_state},
            }
            metric_rows = [{
                "metric_name": m["metric_name"], "metric_class": m["metric_class"],
                "baseline_value": m["baseline_value"], "experiment_value": m["treatment_value"],
                "absolute_lift": m["absolute_lift"], "relative_lift": m["relative_lift"],
                "p_value": m["p_value"], "sample_size": m["sample_size"],
            } for m in metrics]
            out = store.save_experiment(record, metrics=metric_rows,
                                        source="l6.experiment_engine")
            return {"status": "written", "experiment_id_l5": out["experiment_id"],
                    "reliability": out["reliability_score"]}
        except Exception as e:
            return {"status": "failed", "reason": str(e)}   # 回写失败如实报告，不静默

    # -------------------------------------------------------------- §26 监控
    def monitor(self, experiment_id: str) -> Dict[str, Any]:
        """Expected vs Observed + 护栏状态（§26/§27/§28 的 Execution Mode 数据）。"""
        exp = self._get(experiment_id)
        metrics = self.db.query(
            "SELECT * FROM experiment_metric WHERE experiment_id=?", (experiment_id,))
        guardrails = [m for m in metrics if m["metric_class"] == "guardrail"]
        breached = [m["metric_name"] for m in guardrails
                    if (m["relative_lift"] or 0)
                    > float(exp["guardrail_max_relative_increase"] or 0.5)]
        return {"experiment_id": experiment_id, "name": exp["name"],
                "status": exp["status"], "result_state": exp["result_state"],
                "expected": {"hypothesis": exp["hypothesis"],
                             "primary_metric": exp["primary_metric"]},
                "observed": [{"metric": m["metric_name"], "class": m["metric_class"],
                              "baseline": m["baseline_value"],
                              "treatment": m["treatment_value"],
                              "relative_lift": m["relative_lift"], "p_value": m["p_value"]}
                             for m in metrics],
                "guardrail_breached": breached,
                "negative_feedback_watched": True,       # §27：不只看增长指标
                }

    def _get(self, experiment_id: str) -> Dict[str, Any]:
        exp = self.db.query_one("SELECT * FROM experiment WHERE experiment_id=?",
                                (experiment_id,))
        if not exp:
            raise ValueError(f"实验不存在：{experiment_id}")
        return exp

    def _set(self, experiment_id: str, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE experiment SET {sets}, updated_at=? WHERE experiment_id=?",
                        tuple(fields.values()) + (_now(), experiment_id))
        self.db.commit()

    def stop(self, experiment_id: str, reason: str, actor: str, role: str) -> Dict[str, Any]:
        """§29：实验级急停（与 plan 级 kill switch 配套）。"""
        require_role(role, "kill_switch")
        self._set(experiment_id, status="stopped", ended_at=_now())
        self.audit.record(action="experiment_stopped", actor=actor, role=role,
                          object_type="experiment", object_id=experiment_id,
                          detail={"reason": reason})
        return {"experiment_id": experiment_id, "status": "stopped"}
