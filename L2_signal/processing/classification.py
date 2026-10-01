#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Classification Engine（§15 / §16）—— 基础内容分类 + 游戏相关性打分。

★ 这是**内容分类，不是热点分类**（规格原话）。
"这条内容属于游戏领域"、"是动作/二次元/手游" —— 第二层做；
"这个是不是热点" —— 第三层做。

§16 成本控制的关键：先算 `gaming_probability`，
明显不是游戏的（房地产新闻）就不进高成本的 Embedding/第三层。
这一条对本项目的价值很大：TapTap 数据里混着大量非游戏话题帖。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

DOMAINS: Dict[str, List[str]] = {
    "gaming": ["游戏", "手游", "端游", "副本", "关卡", "角色", "抽卡", "卡池", "版本", "联动",
               "皮肤", "强度", "攻略", "boss", "玩家", "公测", "内测", "开服", "服务器",
               "game", "gacha", "patch", "update", "nerf", "buff"],
    "ai": ["大模型", "ai", "gpt", "llm", "agent", "智能体", "chatgpt", "deepseek", "prompt"],
    "tech": ["芯片", "手机", "数码", "评测", "系统", "处理器", "苹果", "安卓", "ios"],
    "entertainment": ["综艺", "明星", "电视剧", "电影", "番剧", "动漫", "追剧"],
    "sports": ["足球", "篮球", "nba", "比赛", "赛季", "夺冠"],
    "lifestyle": ["美食", "穿搭", "旅行", "健身", "减脂", "日常"],
}

GAMING_SUB: Dict[str, List[str]] = {
    "mobile": ["手游", "手机游戏", "移动端"],
    "pc": ["端游", "pc", "steam", "电脑"],
    "console": ["主机", "ps5", "switch", "xbox"],
    "anime_game": ["二次元", "日系", "立绘", "老婆"],
    "fps": ["fps", "射击", "枪"],
    "moba": ["moba", "推塔", "王者荣耀", "英雄联盟"],
    "rpg": ["rpg", "角色扮演", "剧情"],
    "roguelike": ["肉鸽", "roguelike", "随机"],
    "gacha": ["抽卡", "卡池", "保底", "限定", "gacha"],
    "strategy": ["策略", "塔防", "战棋"],
    "open_world": ["开放世界", "大地图", "探索"],
}

# 明确的非游戏信号（降权用）
ANTI_GAMING = ["房产", "楼市", "股票", "基金", "考研", "公务员", "减肥餐", "带货"]


def classify(text: str, entities: List[Any] = None, hashtags: List[str] = None) -> Dict[str, Any]:
    """返回 {domain, subcategory, gaming_probability, evidence}。"""
    t = (text or "").lower()
    tags = " ".join(hashtags or []).lower()
    blob = t + " " + tags

    scores: Dict[str, int] = {}
    evidence: Dict[str, List[str]] = {}
    for dom, kws in DOMAINS.items():
        hit = [k for k in kws if k in blob]
        scores[dom] = len(hit)
        if hit:
            evidence[dom] = hit

    has_game_entity = any(getattr(e, "entity_type", "") in ("GAME", "GAME_IP") for e in (entities or []))
    if has_game_entity:
        scores["gaming"] = scores.get("gaming", 0) + 3     # 实体命中是强证据

    anti = [k for k in ANTI_GAMING if k in blob]
    if anti:
        scores["gaming"] = max(0, scores.get("gaming", 0) - 2 * len(anti))

    if not scores or sum(scores.values()) == 0:
        return {"domain": "others", "subcategory": [], "gaming_probability": 0.2,
                "evidence": {}, "note": "无关键词命中"}

    domain = max(scores, key=lambda k: scores[k])
    total = sum(scores.values()) or 1

    # gaming_probability：游戏得分占比 + 实体加成，压缩到 [0,1]
    prob = scores.get("gaming", 0) / total
    if has_game_entity:
        prob = min(1.0, prob + 0.25)
    if anti:
        prob = max(0.0, prob - 0.2)

    sub = []
    if domain == "gaming":
        for s, kws in GAMING_SUB.items():
            if any(k in blob for k in kws):
                sub.append(s)

    return {
        "domain": domain,
        "subcategory": sub,
        "gaming_probability": round(min(1.0, max(0.0, prob)), 3),
        "evidence": evidence.get(domain, [])[:5],
    }
