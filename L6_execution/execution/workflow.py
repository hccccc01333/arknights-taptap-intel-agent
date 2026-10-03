# -*- coding: utf-8 -*-
"""Human Decision Workflow（§12 / §14 / §37 / §38）—— 第六层的心脏。

★ 状态机（§12）：DRAFT → AI_READY → REVIEWING → APPROVED → EXECUTING → LIVE → COMPLETED，
  分叉 REJECTED；**所有状态变化都落 workflow_event**（可回放）→ §13 Time-to-Action 的数据源。

★ §37 决策动作不是只有 Approve/Reject：
  follow / assign / approve / reject / not_relevant / too_late / need_more_research ...
  其中 **too_late 是热点系统最重要的反馈** —— 它告诉上游"发现速度或窗口判断需要优化"。

★ §38 拒因回第五层：每个决策同时写 L5 Decision Memory（taxonomy 归一）+ L4 human_feedback
  （adoption 统计）—— Human Feedback 是整个系统的监督信号，不能只存在 UI 里。

★ §21 Level 2 纪律落在转移表上：launch 只允许 publisher/admin（agent 永远不可）。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from execution.audit import AuditLog, require_role, PermissionDenied
from execution.db import ExecutionDB

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

WORKFLOW_VERSION = "workflow-1.0"

STATES = ("DRAFT", "AI_READY", "REVIEWING", "APPROVED", "EXECUTING", "LIVE",
          "COMPLETED", "REJECTED")

# (from_state, action) → to_state。不在表里 = 非法转移（宁可拒绝，不留糊涂账）。
TRANSITIONS: Dict[tuple, str] = {
    ("DRAFT", "evaluate_ready"): "AI_READY",
    ("DRAFT", "follow"): "REVIEWING",          # §36：feed 上"关注"= 直接进处理列
    ("AI_READY", "submit"): "REVIEWING",
    ("AI_READY", "follow"): "REVIEWING",
    ("REVIEWING", "approve"): "APPROVED",
    ("REVIEWING", "reject"): "REJECTED",
    ("REVIEWING", "not_relevant"): "REJECTED",  # §37
    ("REVIEWING", "too_late"): "REJECTED",      # §37：重要的上游反馈信号
    ("REVIEWING", "need_more_research"): "AI_READY",
    ("APPROVED", "start_execution"): "EXECUTING",
    ("EXECUTING", "launch"): "LIVE",
    ("LIVE", "complete"): "COMPLETED",
}

# §37 需要决策权的动作；launch 的发布权在 §21/§43 单独收紧
DECISION_ACTIONS = ("approve", "reject", "not_relevant", "too_late", "need_more_research")

# §38 L5 taxonomy 映射
_REASON_CODE = {"not_relevant": "LOW_RELEVANCE", "too_late": "TOO_LATE"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Workflow:
    def __init__(self, db: ExecutionDB, audit: AuditLog,
                 l5_store: Any = None, l4_store: Any = None):
        self.db = db
        self.audit = audit
        self.l5 = l5_store            # memory.store.MemoryStore（可选；None = 跳过回写）
        self.l4 = l4_store            # intelligence.store.IntelligenceStore（可选）

    # -------------------------------------------------------------- 建档
    def ensure_item(self, object_type: str, object_id: str, title: str = "",
                    event_id: str = "", analysis_id: str = "", opportunity_id: str = "",
                    detected_at: str = "", payload: Optional[Dict[str, Any]] = None
                    ) -> Dict[str, Any]:
        """对象首次进入工作流（DRAFT）。detected_at 来自 L3（TTA 的起点，§13）。"""
        row = self.db.query_one(
            "SELECT * FROM workflow_item WHERE object_type=? AND object_id=?",
            (object_type, object_id))
        if row:
            return row
        now = _now()
        self.db.execute(
            "INSERT INTO workflow_item(object_type, object_id, event_id, analysis_id,"
            " opportunity_id, state, title, payload, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (object_type, object_id, event_id, analysis_id, opportunity_id,
             "DRAFT", title, self.db.dumps({"detected_at": detected_at, **(payload or {})}),
             now, now))
        self.db.commit()
        return self.db.query_one(
            "SELECT * FROM workflow_item WHERE object_type=? AND object_id=?",
            (object_type, object_id))

    # -------------------------------------------------------------- 状态转移
    def apply(self, object_type: str, object_id: str, action: str, actor: str, role: str,
              note: str = "", deadline: Optional[str] = None) -> Dict[str, Any]:
        item = self.db.query_one(
            "SELECT * FROM workflow_item WHERE object_type=? AND object_id=?",
            (object_type, object_id))
        if not item:
            raise ValueError(f"工作流对象不存在：{object_type}/{object_id}（先 ensure_item）")
        # 权限先于转移校验 —— agent 越权要报"无权"，而不是"状态不对"（§43）
        if action in DECISION_ACTIONS:
            require_role(role, "decide")
        frm = item["state"]
        key = (frm, action)
        if key not in TRANSITIONS:
            raise ValueError(f"非法转移：{frm} + {action}（§12 状态机不允许）")
        to = TRANSITIONS[key]
        now = _now()
        self.db.execute(
            "INSERT INTO workflow_event(object_type, object_id, from_state, to_state,"
            " action, actor, role, note, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (object_type, object_id, frm, to, action, actor, role, note, now))
        if deadline and action in ("follow", "assign"):
            self.db.execute(
                "UPDATE workflow_item SET deadline=? WHERE object_type=? AND object_id=?",
                (deadline, object_type, object_id))
        self.db.execute(
            "UPDATE workflow_item SET state=?, updated_at=? WHERE object_type=? AND object_id=?",
            (to, now, object_type, object_id))
        self.db.commit()
        self._writeback(item, action, note, actor)
        self.audit.record(action=f"workflow_{action}", actor=actor, role=role,
                          object_type=object_type, object_id=object_id,
                          detail={"from": frm, "to": to, "note": note})
        return {"object_id": object_id, "from": frm, "to": to, "action": action,
                "actor": actor, "ts": now}

    # -------------------------------------------------------------- §14 指派
    def assign(self, object_type: str, object_id: str, owner: Optional[str] = None,
               reviewer: Optional[str] = None, deadline: Optional[str] = None,
               actor: str = "", role: str = "reviewer") -> Dict[str, Any]:
        require_role(role, "assign")
        item = self.db.query_one(
            "SELECT * FROM workflow_item WHERE object_type=? AND object_id=?",
            (object_type, object_id))
        if not item:
            raise ValueError(f"工作流对象不存在：{object_type}/{object_id}")
        self.db.execute(
            "UPDATE workflow_item SET owner=COALESCE(?,owner), reviewer=COALESCE(?,reviewer),"
            " deadline=COALESCE(?,deadline), updated_at=? WHERE object_type=? AND object_id=?",
            (owner, reviewer, deadline, _now(), object_type, object_id))
        self.db.execute(
            "INSERT INTO workflow_event(object_type, object_id, from_state, to_state,"
            " action, actor, role, note, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (object_type, object_id, item["state"], item["state"], "assign", actor, role,
             json.dumps({"owner": owner, "reviewer": reviewer, "deadline": deadline},
                        ensure_ascii=False), _now()))
        self.db.commit()
        self.audit.record(action="assign", actor=actor, role=role,
                          object_type=object_type, object_id=object_id,
                          detail={"owner": owner, "reviewer": reviewer,
                                  "deadline": deadline})
        return {"assigned": True, "owner": owner or item["owner"],
                "reviewer": reviewer or item["reviewer"]}

    # -------------------------------------------------------------- §38 回写
    def _writeback(self, item: Dict[str, Any], action: str, note: str, actor: str) -> None:
        """决策 → L5 Decision Memory（taxonomy）+ L4 human_feedback（adoption）。"""
        if action not in DECISION_ACTIONS:
            return
        l5_decision = {"approve": "approve", "reject": "reject",
                       "not_relevant": "reject", "too_late": "reject",
                       "need_more_research": None}.get(action)
        if self.l5 is not None and l5_decision and item["object_type"] == "creative":
            try:
                from memory import governance as G5      # taxonomy 归一（§17）
                reason = note or _REASON_CODE.get(action, "")
                self.l5.record_decision(
                    object_type="creative", object_id=item["object_id"],
                    decision=l5_decision, reason_text=reason,
                    reason_code=(None if action == "approve" or not note
                                 else G5.normalize_reason_code(note)),
                    reviewer_role=actor, source_ref=f"l6.workflow:{action}")
            except Exception:
                pass                                     # 记忆层不可用不阻塞决策，但审计里如实留痕
        if self.l4 is not None and item["object_type"] == "creative":
            l4_decision = {"approve": "adopt", "reject": "reject",
                           "not_relevant": "reject", "too_late": "reject"}.get(action)
            if l4_decision:
                try:
                    self.l4.save_feedback(idea_id=item["object_id"],
                                          event_id=item["event_id"] or "",
                                          decision=l4_decision, reason=note)
                except Exception:
                    pass

    # -------------------------------------------------------------- §13 Time-to-Action
    def time_to_action(self) -> Dict[str, Any]:
        """Time-to-Insight / Time-to-Decision / Time-to-Action（§13 定义，小时）。

        起点 detected_at（L3 first_detected_at，ensure_item 时传入）；
        终点分别是 建档时间（分析完成）/ approve 事件 / launch 事件。
        没有相应事件的对象不计入 —— 样本不足时如实返回 n=0，不冒充中位数。
        """
        items = self.db.query("SELECT * FROM workflow_item")
        insights: List[float] = []
        decisions: List[float] = []
        actions: List[float] = []
        for it in items:
            payload = self.db.loads(it.get("payload"), {}) or {}
            det = payload.get("detected_at")
            if not det:
                continue
            t0 = _parse(det)
            if not t0:
                continue
            t_item = _parse(it["created_at"])
            if t_item:
                insights.append((t_item - t0).total_seconds() / 3600.0)
            evs = self.db.query(
                "SELECT action, created_at FROM workflow_event"
                " WHERE object_type=? AND object_id=? ORDER BY seq",
                (it["object_type"], it["object_id"]))
            for ev in evs:
                t = _parse(ev["created_at"])
                if not t:
                    continue
                if ev["action"] == "approve":
                    decisions.append((t - t0).total_seconds() / 3600.0)
                if ev["action"] == "launch":
                    actions.append((t - t0).total_seconds() / 3600.0)
        return {
            "time_to_insight_h": _median(insights), "n_insight": len(insights),
            "time_to_decision_h": _median(decisions), "n_decision": len(decisions),
            "time_to_action_h": _median(actions), "n_action": len(actions),
            "definition": "起点=检测时间；中位数只在有样本时给出（§13）",
        }

    def items_by_state(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        if state:
            return self.db.query("SELECT * FROM workflow_item WHERE state=? ORDER BY updated_at",
                                 (state,))
        return self.db.query("SELECT * FROM workflow_item ORDER BY updated_at")

    def history(self, object_type: str, object_id: str) -> List[Dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM workflow_event WHERE object_type=? AND object_id=? ORDER BY seq",
            (object_type, object_id))


def _parse(ts: Optional[str]):
    if not ts:
        return None
    try:
        from datetime import datetime as dt
        t = dt.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.astimezone()
        return t
    except (ValueError, TypeError):
        return None


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 3)
