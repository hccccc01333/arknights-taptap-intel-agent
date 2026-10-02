# -*- coding: utf-8 -*-
"""Node 8：Creative Generator（§26-§28）。

★ §28：每个 Opportunity 最多 3 个，总共 5-8 个，**不追求数量**。
★ §26：创意类型只从有限集合选。
★ §22：没有 growth_mechanism 的不进入（上游 Opportunity 已保证）。

★★ 本项目纪律 ①：**每条创意必须可溯源** —— 带 event_id + opportunity_id + evidence_ids +
   可在 facts JSON 里找回的数字。说不出来源的创意**不算产出**。
   所以这里即使是规则生成，也必须逐条挂 `source_refs`，否则宁可不产。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..knowledge import CREATIVE_TYPES, MOTIVATION_BY_ID
from ..prompts import PROMPT_VERSIONS

MAX_PER_OPPORTUNITY = 3
MAX_TOTAL = 8

# 各类型的 user_flow 骨架与依赖（产品能力变了改这里 / knowledge.py，不改节点逻辑）
_TYPE_FLOW: Dict[str, Dict[str, Any]] = {
    "content": {"flow": ["看到专题", "阅读/浏览", "进入游戏详情", "关注/预约"],
                "deps": ["编辑产能", "分发位"], "channels": ["推荐", "搜索", "动态流"]},
    "community": {"flow": ["看到话题", "进入讨论区", "发表观点", "被回复/点赞", "持续回访"],
                  "deps": ["话题置顶位", "版块运营"], "channels": ["论坛", "动态流", "Push"]},
    "ugc": {"flow": ["看到活动", "投稿/上传", "进入榜单", "被投票", "生成分享卡", "分享站外", "回流"],
            "deps": ["投稿入口", "榜单能力", "分享卡"], "channels": ["论坛", "动态流", "站外"]},
    "product": {"flow": ["进入活动页", "使用新机制", "产出内容", "分享", "回流"],
                "deps": ["产品开发", "活动页"], "channels": ["活动页", "Push", "站外"]},
    "ai_interactive": {"flow": ["进入玩法", "上传素材", "AI 生成", "保存/分享", "回流"],
                       "deps": ["AI 能力", "生成物料模板"], "channels": ["活动页", "站外"]},
    "creator": {"flow": ["邀约达人", "达人产出", "分发放大", "粉丝互动", "进入游戏详情"],
                "deps": ["达人名单", "合作沟通"], "channels": ["动态流", "推荐", "站外"]},
    "publisher": {"flow": ["对接厂商", "拿到素材/福利", "联合活动", "双方分发"],
                  "deps": ["厂商 BD", "法务确认"], "channels": ["活动页", "站内"]},
    "crm": {"flow": ["圈选人群", "Push 触达", "回流落地页", "关注/预约"],
            "deps": ["人群圈选", "Push 通道"], "channels": ["Push"]},
    "h5": {"flow": ["站外看到", "打开 H5", "参与", "生成结果", "分享", "回流"],
           "deps": ["H5 开发", "站外投放"], "channels": ["站外社媒", "H5"]},
    "social": {"flow": ["站内产出", "同步站外", "站外讨论", "回流"],
               "deps": ["社媒账号"], "channels": ["站外社媒"]},
}

# 创意类型 → 适配的增长目标
_TYPE_GOALS: Dict[str, List[str]] = {
    "content": ["reach", "game_detail_visit", "engagement"],
    "community": ["engagement", "community_activation"],
    "ugc": ["ugc", "share"],
    "product": ["ugc", "engagement", "install"],
    "ai_interactive": ["ugc", "share", "reach"],
    "creator": ["creator_activation", "reach", "ugc"],
    "publisher": ["reach", "engagement", "install"],
    "crm": ["reactivation", "reach"],
    "h5": ["share", "reach", "install"],
    "social": ["reach", "share"],
}


def _fits_window(ctype: str, window: str) -> bool:
    """上线周期必须赶得上窗口（§29 Timing 维度的核心约束）。"""
    if "已错过" in window or window == "无窗口":
        return CREATIVE_TYPES.get(ctype, {}).get("lead_time_hours", 999) <= 8
    if "6-12h" in window:
        return CREATIVE_TYPES.get(ctype, {}).get("lead_time_hours", 999) <= 12
    return True


def generate(state: Dict[str, Any], opportunities: Optional[List[Dict[str, Any]]] = None,
             hypotheses: Optional[List[Dict[str, Any]]] = None,
             max_per_opp: int = MAX_PER_OPPORTUNITY) -> List[Dict[str, Any]]:
    opps = opportunities if opportunities is not None else (state.get("opportunities") or [])
    hyps = {h["opportunity_id"]: h for h in
            (hypotheses if hypotheses is not None else (state.get("growth_hypotheses") or []))}
    pack = state.get("evidence_pack") or {}
    ev = pack.get("event") or {}
    evidence_ids = [e["evidence_id"] for e in (pack.get("evidence") or [])[:6]]
    facts_numbers = {
        "content_count": pack.get("n_members"),
        "platform_count": len(pack.get("platforms") or {}),
        "confidence": ev.get("confidence"),
        "lifecycle": ev.get("lifecycle"),
        "primary_ratio": pack.get("primary_ratio"),
    }

    out: List[Dict[str, Any]] = []
    for o in opps:
        goal = o.get("growth_goal")
        mid = o.get("user_motivation")
        mot = MOTIVATION_BY_ID.get(o.get("audience_motivation_id") or "") or {}
        candidates = [t for t, gs in _TYPE_GOALS.items() if goal in gs]
        # 动机适配的类型优先
        preferred = [t for t in (mot.get("best_types") or []) if t in candidates]
        ordered = preferred + [t for t in candidates if t not in preferred]
        picked = [t for t in ordered if _fits_window(t, o.get("opportunity_window", ""))][:max_per_opp]
        if not picked:
            picked = ["content"]      # 兜底：低成本专题总能赶上窗口

        for ctype in picked:
            spec = CREATIVE_TYPES.get(ctype, {})
            flow = _TYPE_FLOW.get(ctype, {})
            hyp = hyps.get(o["opportunity_id"], {})
            name = f"{spec.get('name', ctype)}·{o['audience']}"
            out.append({
                "idea_id": f"idea_{len(out) + 1}",
                "idea_name": name,
                "opportunity_id": o["opportunity_id"],
                "creative_type": ctype,
                "creative_type_name": spec.get("name"),
                "target_audience": o.get("audience"),
                "insight": f"{o['audience']} 的动机是「{o['user_motivation']}」，"
                           f"当前事件处于 {ev.get('lifecycle')} 阶段",
                "concept": (f"围绕「{ev.get('title')}」做{spec.get('name')}，"
                            f"承接「{o['user_motivation']}」，"
                            f"通过 {o['platform_advantage']} 落地"),
                "user_flow": flow.get("flow", []),
                "distribution_channels": flow.get("channels", []),
                "growth_mechanism": o.get("growth_mechanism"),
                "growth_hypothesis": hyp.get("hypothesis"),
                "primary_metric": (o.get("expected_metrics") or [None])[0],
                "secondary_metrics": (o.get("expected_metrics") or [])[1:] or ["reach"],
                "launch_window": o.get("opportunity_window"),
                "implementation_cost": spec.get("cost"),
                "lead_time_hours": spec.get("lead_time_hours"),
                "dependencies": flow.get("deps", []),
                "risks": [],
                # ★★ 溯源（纪律 ①）：说不出来源的创意不算产出
                "source_refs": {
                    "event_id": state.get("event_id"),
                    "opportunity_id": o["opportunity_id"],
                    "evidence_ids": evidence_ids,
                    "facts": facts_numbers,
                },
                "mode": "rule",
                "prompt_version": PROMPT_VERSIONS["creative"],
            })
            if len(out) >= MAX_TOTAL:
                return out
    return out
