# -*- coding: utf-8 -*-
"""Asset Production（§18 / §19 / §20）+ Execution Center（§21 / §22 / §29）。

★ §19 策略与素材分离：Opportunity → Strategy → Creative Concept → Asset Generation。
  本模块只做最后一环（Creative Concept → Asset），战略变了素材重来是正常代价。

★ §20 素材版本化：(creative_id, kind) 每次生成 version+1；记录 approved_by / published_at
  —— 实验结果将来必须能关联到"真正上线的那个版本"。

★ §18 MVP 是**结构化模板 + 占位符**：从创意字段填已知事实，填不出的进 placeholders
  （人工/LLM 补）。绝不生成假数字（facts 锁数在本层同样生效 —— 通知里的
  "+320%" 必须来自 L3 实测，模板不编）。

★ §21 执行成熟度 Level 2：AI 准备 → 人工 approve → 系统执行。
  launch/publish 由 publisher 角色人工触发；agent 调用直接 PermissionDenied（§43）。
★ §29 Kill Switch：pause/stop/rollback 独立于 Agent，动作全部审计。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from execution.audit import AuditLog, PermissionDenied, require_role
from execution.db import ExecutionDB
from execution import ops_context

EXECUTION_CENTER_VERSION = "execution-1.0"

# §18 素材种类（MVP 六种；图片类生成超出 stdlib 范围 → brief 里给设计要求）
ASSET_KINDS = ("push_copy", "feed_copy", "campaign_brief", "design_brief",
               "ops_sop", "creator_brief")

# 各 kind 的模板骨架：字段来自 creative（真实数据），*_placeholder 是留给人的
_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "push_copy": {"fields": ["title", "body"], "placeholders": ["正文文案（含行动点）"]},
    "feed_copy": {"fields": ["title", "body", "hashtags"], "placeholders": ["正文文案"]},
    "campaign_brief": {"fields": ["objective", "mechanism", "timeline", "metrics"],
                       "placeholders": ["奖励规则", "法务确认项"]},
    "design_brief": {"fields": ["visual_requirements", "sizes"],
                     "placeholders": ["视觉方向", "参考素材"]},
    "ops_sop": {"fields": ["steps", "owner", "rollback"], "placeholders": ["值班安排"]},
    "creator_brief": {"fields": ["angle", "dos", "donts"], "placeholders": ["达人名单"]},
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class AssetStudio:
    def __init__(self, db: ExecutionDB, audit: AuditLog):
        self.db = db
        self.audit = audit

    def generate(self, creative: Dict[str, Any], kinds: Optional[List[str]] = None,
                 actor: str = "agent", role: str = "agent") -> List[Dict[str, Any]]:
        """从已批准创意批量产素材（策略与素材分离：creative 必须已 APPROVED）。"""
        require_role(role, "create_creative")
        item = self.db.query_one(
            "SELECT * FROM workflow_item WHERE object_type='creative' AND object_id=?",
            (creative.get("idea_id") or creative.get("creative_id"),))
        if not item or item["state"] not in ("APPROVED", "EXECUTING", "LIVE"):
            raise PermissionDenied(
                f"创意 {creative.get('idea_id')} 状态 {item['state'] if item else '不存在'}"
                f" —— 只有 APPROVED 后的创意才进素材生产（§19 策略与素材分离）")
        out = []
        for kind in (kinds or ASSET_KINDS):
            if kind not in _TEMPLATES:
                raise ValueError(f"未知素材类型 {kind}（可选：{ASSET_KINDS}）")
            out.append(self._generate_one(item, creative, kind, actor, role))
        return out

    def _generate_one(self, item: Dict[str, Any], creative: Dict[str, Any],
                      kind: str, actor: str, role: str) -> Dict[str, Any]:
        spec = _TEMPLATES[kind]
        content = {
            "creative_id": item["object_id"],
            "event_id": item["event_id"],
            "idea_name": creative.get("idea_name"),
            "growth_mechanism": creative.get("growth_mechanism"),
            "primary_metric": creative.get("primary_metric"),
            "launch_window": creative.get("launch_window"),
            "growth_hypothesis": creative.get("growth_hypothesis"),
        }
        placeholders = list(spec["placeholders"])
        # facts 纪律：模板里没有任何数字字面量 —— 数字（讨论增速/热分）只从 L3/L4 产物引用
        if kind == "push_copy":
            content["title"] = f"【{creative.get('idea_name') or '社区动态'}】话题进行中"
            content["body_placeholder"] = True
        elif kind == "campaign_brief":
            content["objective"] = creative.get("primary_metric")
            content["mechanism"] = creative.get("growth_mechanism")
            content["timeline"] = creative.get("launch_window")
            content["metrics"] = [creative.get("primary_metric")] + \
                list(creative.get("secondary_metrics") or [])
        elif kind == "ops_sop":
            content["steps"] = ["确认素材与规则", "配置话题页/资源位", "上线与巡检",
                                "结束归因（回填第五层）"]
            content["owner"] = item["owner"]
            content["rollback"] = "发现品牌/事实风险 → execution_center.kill_switch"
        # §20 版本化
        prev = self.db.query_one(
            "SELECT MAX(version) AS v FROM production_asset WHERE creative_id=? AND kind=?",
            (item["object_id"], kind))
        version = int((prev or {}).get("v") or 0) + 1
        oid = item["object_id"]
        asset_id = f"ast_{hashlib.sha1(f'{oid}|{kind}|{version}'.encode()).hexdigest()[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO production_asset(asset_id, creative_id, event_id, kind, version,"
            " status, content, placeholders, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (asset_id, item["object_id"], item["event_id"], kind, version, "draft",
             self.db.dumps(content), self.db.dumps(placeholders), now, now))
        self.db.commit()
        self.audit.record(action="asset_generated", actor=actor, role=role,
                          object_type="asset", object_id=asset_id,
                          detail={"kind": kind, "version": version})
        return {"asset_id": asset_id, "kind": kind, "version": version, "status": "draft",
                "placeholders": placeholders}

    def approve(self, asset_id: str, actor: str, role: str) -> Dict[str, Any]:
        require_role(role, "approve_asset")
        row = self.db.query_one("SELECT * FROM production_asset WHERE asset_id=?", (asset_id,))
        if not row:
            raise ValueError(f"素材不存在：{asset_id}")
        self.db.execute("UPDATE production_asset SET status='approved', approved_by=?,"
                        " updated_at=? WHERE asset_id=?", (actor, _now(), asset_id))
        self.db.commit()
        self.audit.record(action="asset_approved", actor=actor, role=role,
                          object_type="asset", object_id=asset_id,
                          detail={"version": row["version"]})
        return {"asset_id": asset_id, "status": "approved"}

    def mark_published(self, asset_id: str, actor: str, role: str) -> Dict[str, Any]:
        require_role(role, "publish")
        row = self.db.query_one("SELECT * FROM production_asset WHERE asset_id=?", (asset_id,))
        if not row:
            raise ValueError(f"素材不存在：{asset_id}")
        if row["status"] != "approved":
            raise PermissionDenied("素材未批准不得发布（§21 Level 2）")
        self.db.execute("UPDATE production_asset SET status='published', published_at=?,"
                        " updated_at=? WHERE asset_id=?", (_now(), _now(), asset_id))
        self.db.commit()
        self.audit.record(action="asset_published", actor=actor, role=role,
                          object_type="asset", object_id=asset_id)
        return {"asset_id": asset_id, "status": "published"}

    def list_for(self, creative_id: str) -> List[Dict[str, Any]]:
        return self.db.query(
            "SELECT asset_id, kind, version, status, approved_by, published_at"
            " FROM production_asset WHERE creative_id=? ORDER BY kind, version",
            (creative_id,))


class ExecutionCenter:
    def __init__(self, db: ExecutionDB, audit: AuditLog, workflow: Any):
        self.db = db
        self.audit = audit
        self.wf = workflow

    def create_plan(self, creative_id: str, channels: List[str], audience: str,
                    start_at: str, end_at: str, asset_ids: List[str],
                    experiment_enabled: bool, control: str, treatment: str,
                    actor: str, role: str) -> Dict[str, Any]:
        """§22：审批通过的创意 → Execution Plan。**创建不等于上线**（launch 另行人工触发）。"""
        require_role(role, "create_creative")
        item = self.db.query_one(
            "SELECT * FROM workflow_item WHERE object_type='creative' AND object_id=?",
            (creative_id,))
        if not item or item["state"] != "APPROVED":
            raise PermissionDenied(f"创意 {creative_id} 未 APPROVED，不能生成执行计划（§21）")
        if experiment_enabled and not control:
            raise ValueError("启用实验必须声明 control（§23：没有对照不叫实验）")
        # §39 资源约束：无研发时禁止产品开发型渠道进入计划
        ctx = ops_context.load()
        cap = ops_context.max_lead_time_hours(ctx)
        if any(c in ("product_feature", "h5_build") for c in channels) and cap <= 6.0:
            raise PermissionDenied(f"当前运营约束（dev={ctx['resources'].get('dev')}）"
                                   f"不允许产品开发型渠道（§39 Operational Context）")
        plan_id = f"pln_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO execution_plan(plan_id, creative_id, event_id, channels, audience,"
            " start_at, end_at, asset_ids, experiment_enabled, control, treatment,"
            " status, created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (plan_id, creative_id, item["event_id"], self.db.dumps(channels), audience,
             start_at, end_at, self.db.dumps(asset_ids), 1 if experiment_enabled else 0,
             control, treatment, "planned", actor, now, now))
        self.db.commit()
        self.audit.record(action="plan_created", actor=actor, role=role,
                          object_type="plan", object_id=plan_id,
                          detail={"creative_id": creative_id, "channels": channels})
        return {"plan_id": plan_id, "status": "planned"}

    def launch(self, plan_id: str, actor: str, role: str) -> Dict[str, Any]:
        """§21 Level 2 的关键闸门：**上线是人的决定**（publisher/admin）。"""
        require_role(role, "launch")
        plan = self._get(plan_id)
        if plan["status"] not in ("planned", "paused"):
            raise ValueError(f"计划 {plan_id} 状态 {plan['status']}，不能 launch")
        # 工作流同步：APPROVED 的创意先走 start_execution（EXECUTING），再 launch（LIVE）
        item = self.db.query_one(
            "SELECT state FROM workflow_item WHERE object_type='creative' AND object_id=?",
            (plan["creative_id"],))
        if item and item["state"] == "APPROVED":
            self.wf.apply("creative", plan["creative_id"], "start_execution", actor, role)
        self._set_status(plan_id, "live")
        self.wf.apply("creative", plan["creative_id"], "launch", actor, role)
        self.audit.record(action="plan_launched", actor=actor, role=role,
                          object_type="plan", object_id=plan_id)
        return {"plan_id": plan_id, "status": "live"}

    # -------------------------------------------------------------- §29 Kill Switch
    def kill_switch(self, plan_id: str, action: str, reason: str,
                    actor: str, role: str) -> Dict[str, Any]:
        """PAUSE / STOP / ROLLBACK。这个控制**永不交给 Agent 自己**（§29 原文）。"""
        if action not in ("pause", "stop", "rollback"):
            raise ValueError(f"kill_switch 动作必须是 pause|stop|rollback，收到 {action!r}")
        require_role(role, "kill_switch")          # agent 角色在这里被硬拦
        plan = self._get(plan_id)
        if plan["status"] in ("completed", "stopped", "rolled_back"):
            raise ValueError(f"计划 {plan_id} 已终态（{plan['status']}）")
        status = {"pause": "paused", "stop": "stopped", "rollback": "rolled_back"}[action]
        self._set_status(plan_id, status)
        self.audit.record(action=f"kill_switch_{action}", actor=actor, role=role,
                          object_type="plan", object_id=plan_id,
                          detail={"reason": reason, "from": plan["status"]})
        return {"plan_id": plan_id, "status": status, "reason": reason}

    def complete(self, plan_id: str, actor: str, role: str) -> Dict[str, Any]:
        require_role(role, "create_creative")
        plan = self._get(plan_id)
        if plan["status"] != "live":
            raise ValueError(f"计划 {plan_id} 状态 {plan['status']}，不能 complete")
        self._set_status(plan_id, "completed")
        self.wf.apply("creative", plan["creative_id"], "complete", actor, role)
        self.audit.record(action="plan_completed", actor=actor, role=role,
                          object_type="plan", object_id=plan_id)
        return {"plan_id": plan_id, "status": "completed"}

    def _get(self, plan_id: str) -> Dict[str, Any]:
        plan = self.db.query_one("SELECT * FROM execution_plan WHERE plan_id=?", (plan_id,))
        if not plan:
            raise ValueError(f"执行计划不存在：{plan_id}")
        return plan

    def _set_status(self, plan_id: str, status: str) -> None:
        self.db.execute("UPDATE execution_plan SET status=?, updated_at=? WHERE plan_id=?",
                        (status, _now(), plan_id))
        self.db.commit()

    def active_plans(self) -> List[Dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM execution_plan WHERE status IN ('planned','live','paused')"
            " ORDER BY updated_at DESC")
