# -*- coding: utf-8 -*-
"""Audit Log + 角色权限（§43 / §44）。

★ §43 硬规则：**agent 服务账号永远拿不到 approve / publish / kill-switch 权限**。
  这不是 UI 约束，是 `require_role()` 在写路径上强制 —— 违者抛 `PermissionDenied` 并落审计。

★ §44：谁、在什么时候、看了/改了/批了/发了什么，连同当时的模型版本 / prompt 版本一起落库。
  生产 Agent 系统的基本要求，没有商量余地。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from execution.db import ExecutionDB

AUDIT_VERSION = "audit-1.0"

# §43 角色
ROLES = ("viewer", "analyst", "operator", "reviewer", "admin", "publisher", "agent")

# action → 允许的角色。没列出的 action 视为"所有已登记角色可用"（如 read/note）。
PERMISSIONS: Dict[str, tuple] = {
    "edit_analysis":   ("analyst", "admin"),
    "create_creative": ("operator", "analyst", "admin", "agent"),   # agent 可以出草稿
    "assign":          ("reviewer", "admin"),
    "decide":          ("reviewer", "admin"),                        # approve/reject
    "approve_asset":   ("reviewer", "admin"),
    "publish":         ("publisher", "admin"),                       # §43：单独权限，不给 agent
    "launch":          ("publisher", "admin"),
    "kill_switch":     ("reviewer", "admin", "publisher"),           # §29：不交给 Agent
    "configure":       ("admin",),
    "acknowledge_alert": ("viewer", "analyst", "operator", "reviewer", "admin",
                          "publisher"),                             # 明确排除 agent
}


class PermissionDenied(PermissionError):
    pass


def role_can(role: str, action: str) -> bool:
    if role not in ROLES:
        return False                     # 未登记角色一律拒绝
    allowed = PERMISSIONS.get(action)
    return True if allowed is None else role in allowed


def require_role(role: str, action: str) -> None:
    if not role_can(role, action):
        raise PermissionDenied(f"角色 {role!r} 无权执行 {action!r}（§43 权限矩阵）")


class AuditLog:
    def __init__(self, db: ExecutionDB):
        self.db = db

    def record(self, action: str, actor: str, role: str,
               object_type: str = "", object_id: str = "",
               detail: Optional[Dict[str, Any]] = None,
               model_version: str = "", prompt_version: str = "",
               analysis_version: str = "") -> None:
        # 任何已登记角色都可写审计；未登记角色连审计都进不来（§43 默认拒绝）
        if role not in ROLES:
            raise PermissionDenied(f"未登记角色 {role!r}，拒绝写入审计（§43）")
        self.db.execute(
            "INSERT INTO audit_log(ts, actor, role, action, object_type, object_id,"
            " detail, model_version, prompt_version, analysis_version)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().astimezone().isoformat(timespec="seconds"),
             actor, role, action, object_type, object_id,
             self.db.dumps(detail or {}), model_version, prompt_version, analysis_version))
        self.db.commit()

    def for_object(self, object_type: str, object_id: str) -> list:
        return self.db.query(
            "SELECT ts, actor, role, action, detail, model_version, prompt_version"
            " FROM audit_log WHERE object_type=? AND object_id=? ORDER BY seq",
            (object_type, object_id))

    def tail(self, limit: int = 50) -> list:
        return self.db.query(
            "SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,))

    @staticmethod
    def versions_from(result: Dict[str, Any]) -> Dict[str, str]:
        """从 L4 产物里取版本三元组（§44：审计要带当时的模型/prompt 版本）。"""
        return {
            "model_version": json.dumps((result or {}).get("model_versions") or "",
                                        ensure_ascii=False) if (result or {}).get("model_versions") else "",
            "prompt_version": str((result or {}).get("prompt_versions") or ""),
            "analysis_version": str((result or {}).get("analysis_version") or ""),
        }
