# -*- coding: utf-8 -*-
"""TapTap 知识层（§13 / §19 / §23 / §26）。

★ 规格要求：**产品能力不要写死进 Prompt，要作为知识层提供，产品能力变了只改这里。**
所以 TapTap 资产、增长目标、创意类型、用户动机全部是数据，不是 prompt 字符串。

⚠️ 这里的资产清单是**基于公开产品形态的通用描述**，不是 TapTap 内部能力清单。
接真实产品能力时只改这个文件的 `TAPTAP_ASSETS`，不碰任何节点代码。
"""

from __future__ import annotations

from typing import Dict, List

KNOWLEDGE_VERSION = "taptap-kb-1.0"

# ---------------------------------------------------------------- §13 TapTap 资产
# asset_id / name / 承接什么 / 典型增长目标 / 落地成本(low|medium|high) / 上线周期
TAPTAP_ASSETS: List[Dict[str, object]] = [
    {"asset_id": "game_detail", "name": "游戏详情页", "carries": "把对某款游戏的兴趣收口到可浏览的官方信息场",
     "goals": ["game_detail_visit", "game_follow", "registration"], "cost": "low", "lead_time": "0h"},
    {"asset_id": "review", "name": "评价区", "carries": "承接观点表达与口碑沉淀",
     "goals": ["engagement", "ugc", "retention"], "cost": "low", "lead_time": "0h"},
    {"asset_id": "forum", "name": "论坛/社区", "carries": "承接讨论、攻略、二创等深度互动",
     "goals": ["community_activation", "ugc", "engagement"], "cost": "low", "lead_time": "2h"},
    {"asset_id": "moment", "name": "动态内容流", "carries": "轻量化图文/短视频的即时表达与分发",
     "goals": ["ugc", "reach", "engagement"], "cost": "low", "lead_time": "1h"},
    {"asset_id": "follow", "name": "关注关系", "carries": "把一次性兴趣转成长期订阅",
     "goals": ["game_follow", "retention", "reactivation"], "cost": "low", "lead_time": "0h"},
    {"asset_id": "ranking", "name": "榜单", "carries": "用排序制造比较与竞争，放大 UGC 产出",
     "goals": ["ugc", "engagement", "reach"], "cost": "medium", "lead_time": "1d"},
    {"asset_id": "search", "name": "搜索", "carries": "承接主动意图，热点期拦截检索流量",
     "goals": ["game_detail_visit", "reach"], "cost": "low", "lead_time": "0h"},
    {"asset_id": "recommend", "name": "推荐分发", "carries": "把相关内容推给高潜人群",
     "goals": ["reach", "engagement"], "cost": "low", "lead_time": "0h"},
    {"asset_id": "push", "name": "Push", "carries": "主动触达沉默/半沉默用户",
     "goals": ["reactivation", "reach"], "cost": "low", "lead_time": "1h"},
    {"asset_id": "activity_page", "name": "活动页", "carries": "承载有明确规则与奖励的主题活动",
     "goals": ["ugc", "engagement", "community_activation"], "cost": "medium", "lead_time": "1-2d"},
    {"asset_id": "creator", "name": "创作者/达人", "carries": "借达人产能与受众放大内容供给",
     "goals": ["creator_activation", "reach", "ugc"], "cost": "high", "lead_time": "2-3d"},
    {"asset_id": "publisher_coop", "name": "厂商合作", "carries": "拿到官方素材/福利，提高活动正当性",
     "goals": ["reach", "engagement", "install"], "cost": "high", "lead_time": "3d+"},
    {"asset_id": "ugc_tool", "name": "UGC 生产工具", "carries": "降低创作门槛，把表达欲转成可分发物料",
     "goals": ["ugc", "share", "install"], "cost": "medium", "lead_time": "1-2d"},
    {"asset_id": "h5", "name": "H5 / 站外落地", "carries": "站外传播的收口与回流",
     "goals": ["reach", "install", "share"], "cost": "medium", "lead_time": "1-2d"},
]

ASSET_BY_ID: Dict[str, Dict[str, object]] = {a["asset_id"]: a for a in TAPTAP_ASSETS}


def assets_for_goal(goal: str) -> List[Dict[str, object]]:
    return [a for a in TAPTAP_ASSETS if goal in a["goals"]]


# ------------------------------------------------- §23 增长目标（有限集合）
# 规格：不要让模型自己创造奇怪 KPI。
GROWTH_GOALS: Dict[str, str] = {
    "reach": "触达更多人",
    "engagement": "提升互动深度",
    "ugc": "产出用户内容",
    "game_follow": "关注游戏",
    "game_detail_visit": "访问游戏详情页",
    "registration": "预约",
    "install": "下载安装",
    "retention": "留存",
    "community_activation": "激活社区",
    "creator_activation": "激活创作者",
    "reactivation": "召回沉默用户",
    "share": "站外分享",
}

# ------------------------------------------------- §26 创意类型（有限集合）
CREATIVE_TYPES: Dict[str, Dict[str, object]] = {
    "content": {"name": "热点专题", "cost": "low", "lead_time_hours": 4,
                "default_assets": ["moment", "recommend", "search"]},
    "community": {"name": "讨论活动", "cost": "low", "lead_time_hours": 6,
                  "default_assets": ["forum", "moment"]},
    "ugc": {"name": "投稿挑战", "cost": "medium", "lead_time_hours": 24,
            "default_assets": ["forum", "ranking", "ugc_tool"]},
    "product": {"name": "临时产品机制", "cost": "high", "lead_time_hours": 72,
                "default_assets": ["activity_page", "ugc_tool"]},
    "ai_interactive": {"name": "AI互动玩法", "cost": "high", "lead_time_hours": 72,
                      "default_assets": ["ugc_tool", "activity_page"]},
    "creator": {"name": "达人联动", "cost": "high", "lead_time_hours": 48,
                "default_assets": ["creator", "moment"]},
    "publisher": {"name": "厂商合作", "cost": "high", "lead_time_hours": 72,
                  "default_assets": ["publisher_coop", "activity_page"]},
    "crm": {"name": "Push 触达", "cost": "low", "lead_time_hours": 2,
            "default_assets": ["push", "game_detail"]},
    "h5": {"name": "站外传播", "cost": "medium", "lead_time_hours": 36,
           "default_assets": ["h5", "ugc_tool"]},
    "social": {"name": "社媒传播", "cost": "low", "lead_time_hours": 8,
               "default_assets": ["moment", "h5"]},
}

# ------------------------------------------------- §19 用户动机（比人群画像更重要）
# 规格原话："玩家不是因为'喜欢RPG'参与，而是因为表达身份/炫耀成果/审美展示/模仿/玩梗/社交比较/参与社区"
USER_MOTIVATIONS: List[Dict[str, object]] = [
    {"motivation_id": "identity_expression", "name": "表达身份", "strength_hint": 0.85,
     "best_types": ["ugc", "ai_interactive", "h5"], "signals": ["我的", "自定义", "搭配", "风格"]},
    {"motivation_id": "show_off", "name": "炫耀成果", "strength_hint": 0.88,
     "best_types": ["ugc", "product", "ranking"], "signals": ["晒", "最强", "通关", "满分", "排行"]},
    {"motivation_id": "aesthetic_display", "name": "审美展示", "strength_hint": 0.82,
     "best_types": ["ugc", "community", "content"], "signals": ["好看", "截图", "画面", "壁纸", "美术"]},
    {"motivation_id": "imitation", "name": "模仿跟玩", "strength_hint": 0.78,
     "best_types": ["community", "ugc", "social"], "signals": ["同款", "教程", "怎么弄", "求"]},
    {"motivation_id": "meme", "name": "玩梗", "strength_hint": 0.90,
     "best_types": ["content", "social", "community"], "signals": ["梗", "笑死", "离谱", "整活", "沙雕"]},
    {"motivation_id": "social_comparison", "name": "社交比较", "strength_hint": 0.84,
     "best_types": ["ugc", "product", "ranking"], "signals": ["对比", "谁", "排名", "第一", "强"]},
    {"motivation_id": "community_belonging", "name": "参与社区", "strength_hint": 0.80,
     "best_types": ["community", "content"], "signals": ["大家", "一起", "讨论", "社区"]},
    {"motivation_id": "info_seeking", "name": "获取信息与攻略", "strength_hint": 0.83,
     "best_types": ["content", "community", "crm"], "signals": ["攻略", "怎么", "什么时候", "前瞻", "介绍"]},
    {"motivation_id": "grievance", "name": "表达不满/维权", "strength_hint": 0.86,
     "best_types": ["community", "content"], "signals": ["吐槽", "差评", "退游", "氪金", "骗", "坑"]},
    {"motivation_id": "anticipation", "name": "期待与预约", "strength_hint": 0.87,
     "best_types": ["content", "crm", "publisher"], "signals": ["期待", "什么时候", "预约", "上线", "公布"]},
]

MOTIVATION_BY_ID = {m["motivation_id"]: m for m in USER_MOTIVATIONS}


def detect_motivations(texts: List[str], top_k: int = 3) -> List[Dict[str, object]]:
    """从事件文本里识别动机（规则：关键词命中 + 强度）。

    ★ 这是"读文本"的活，但只读**短文本**（成员标题/摘要），不读全文 —— 与契约 `text_access` 一致。
    无 LLM 时用关键词；有 LLM 时由模型判定，规则结果作为兜底。
    """
    blob = " ".join(t or "" for t in texts).lower()
    scored = []
    for m in USER_MOTIVATIONS:
        hits = sum(1 for s in m["signals"] if s in blob)
        if hits:
            scored.append((hits * float(m["strength_hint"]), m))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, m in scored:
        hits = int(round(score / float(m["strength_hint"])))
        out.append({
            "motivation_id": m["motivation_id"], "name": m["name"],
            "strength": round(min(1.0, float(m["strength_hint"]) * (0.7 + 0.1 * hits)), 3),
            "evidence": f"命中 {hits} 个动机信号词", "source": "rule",
        })
    return out[:top_k]


# ------------------------------------------------- 事件主题 → 受众/资产 的先验
# 只用公开可判的信号（游戏品类 / 事件类型），不做任何个人隐私推断。
AUDIENCE_TEMPLATES: List[Dict[str, object]] = [
    {"segment": "核心玩家", "when": "always", "interest_base": 0.80,
     "motivation_fallback": "info_seeking"},
    {"segment": "UGC 创作者", "when": "creative_friendly", "interest_base": 0.72,
     "motivation_fallback": "show_off"},
    {"segment": "泛兴趣围观用户", "when": "high_reach", "interest_base": 0.55,
     "motivation_fallback": "meme"},
    {"segment": "回流/沉默用户", "when": "nostalgia_or_update", "interest_base": 0.48,
     "motivation_fallback": "anticipation"},
]
