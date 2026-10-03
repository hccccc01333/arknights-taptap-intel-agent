# -*- coding: utf-8 -*-
"""Operational Context（§39 / §40）：把组织约束显式化，交给第四层。

★ §39：AI 不应该在真空里做增长策略。今天设计=1、研发=0、Push slot=2 ——
  这些不进 prompt，进**数据**（与 L4 knowledge.py 同一纪律）：
  第四层的 Opportunity/Creative 节点读取本模块，就不会推荐"做一个大型互动产品"
  而会推荐 内容 + UGC模板 + Push（§39 原例）。

存储：data/state/ops_context.json（配置而非业务数据 → 文件而非库）。
带 valid_until —— 过期约束自动失效返回 defaults，绝不拿上周的资源位配置误导本周决策。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

OPS_CONTEXT_VERSION = "ops-context-1.0"

_DEFAULTS: Dict[str, Any] = {
    "resources": {"design": 1, "dev": 0, "ops": 3},
    "slots_24h": {"push": 2, "homepage_banner": 1, "activity_page": 0},
    "budget_level": "low",
    "channels_allowed": ["taptap_inhouse"],
    "notes": "",
}


def _path() -> str:
    env = os.environ.get("L6_OPS_CONTEXT")
    if env:
        return env
    from paths import STATE
    return str(STATE / "ops_context.json")


def load() -> Dict[str, Any]:
    """读取运营约束。过期/损坏 → 返回 defaults 并带 `degraded` 标注（不静默）。"""
    path = _path()
    if not os.path.exists(path):
        return {**_DEFAULTS, "degraded": "未初始化（用 defaults，从未配置过）",
                "version": OPS_CONTEXT_VERSION}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError) as e:
        return {**_DEFAULTS, "degraded": f"配置文件损坏，回退 defaults：{e}",
                "version": OPS_CONTEXT_VERSION}
    valid_until = data.get("valid_until")
    if valid_until:
        try:
            if str(valid_until) <= datetime.now().astimezone().isoformat():
                return {**_DEFAULTS,
                        "degraded": f"配置已于 {valid_until} 过期，回退 defaults（§39 约束必须保鲜）",
                        "version": OPS_CONTEXT_VERSION}
        except (ValueError, TypeError):
            pass
    return {**_DEFAULTS, **data, "version": OPS_CONTEXT_VERSION}


def save(resources: Dict[str, int], slots_24h: Dict[str, int],
         budget_level: str = "low", channels_allowed: Optional[list] = None,
         ttl_hours: float = 24.0, notes: str = "") -> str:
    """写入约束（admin 的 configure 动作，CLI 已做权限检查）。TTL 默认 24h。"""
    now = datetime.now().astimezone()
    data = {
        "version": OPS_CONTEXT_VERSION,
        "resources": resources,
        "slots_24h": slots_24h,
        "budget_level": budget_level,
        "channels_allowed": channels_allowed or _DEFAULTS["channels_allowed"],
        "notes": notes,
        "updated_at": now.isoformat(timespec="seconds"),
        "valid_until": (now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds"),
    }
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def max_lead_time_hours(ctx: Optional[Dict[str, Any]] = None) -> float:
    """把资源约束折算成"能承受的最大创意 lead_time"（§6 时效约束变成产品约束的资源侧）。

    无研发 → 排除 product/ai_interactive 类（L4 creative.py 的 lead_time 表管不到资源，
    资源侧的硬约束在这里）。
    """
    ctx = ctx or load()
    resources = ctx.get("resources") or {}
    dev = int(resources.get("dev") or 0)
    if dev <= 0:
        return 6.0                       # 无研发：只允许 ≤6h 上线的类型（内容/话题/Push）
    design = int(resources.get("design") or 0)
    if design <= 0:
        return 24.0
    return 72.0                          # 正常资源
