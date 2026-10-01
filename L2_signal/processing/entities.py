#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Entity Engine（§12 / §13 / §14）—— 抽取、链接、消歧。

★ Entity Linking 比 NER 更重要（规格原话）：
NER 只能说"黑猴是个游戏"，业务需要的是把「黑猴 / 黑神话 / 悟空 / Black Myth」全部
链接到 game_id = 黑神话：悟空。所以必须有 **Game Knowledge Registry**。

Registry 数据来源：
1. `games/<key>.json`（项目既有游戏档案，含 aliases + official_account）—— 权威来源
2. `EXTRA_GAMES`：内置常见游戏别名表（跨游戏信号识别用）

★ 消歧（§14）："LOL" 可能是 League of Legends，也可能只是 laugh out loud。
不能只查 alias，要结合上下文：P(entity|context) 用规则近似
（附近有无游戏词、平台是不是游戏源、有无游戏类 hashtag）。

★ 默认不用 LLM（§33）：只有 confidence < 0.5 **且**内容重要时才走 LLM 兜底
（接口 `llm_fallback` 已留，本机未接；未接时如实返回 unresolved，不瞎猜）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from .canonical import Entity, content_id_of  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GAMES_DIR = os.path.join(_ROOT, "games")

ENTITY_TYPES = ("GAME", "GAME_IP", "PUBLISHER", "DEVELOPER", "PLATFORM",
                "CHARACTER", "CREATOR", "ESPORT_TEAM", "EVENT", "GENRE", "TECHNOLOGY")

# 内置补充词典（Registry 之外的常见游戏/实体，用于跨游戏信号识别）
EXTRA_GAMES: Dict[str, Dict[str, Any]] = {
    "game_black_myth_wukong": {"canonical_name": "黑神话：悟空",
                               "aliases": ["黑神话悟空", "黑神话", "黑猴", "悟空", "Black Myth Wukong", "Black Myth"]},
    "game_genshin": {"canonical_name": "原神", "aliases": ["原神", "Genshin", "Genshin Impact"]},
    "game_star_rail": {"canonical_name": "崩坏：星穹铁道", "aliases": ["星穹铁道", "崩铁", "Honkai Star Rail"]},
    "game_delta_force": {"canonical_name": "三角洲行动", "aliases": ["三角洲行动", "三角洲", "Delta Force"]},
    "game_zenless": {"canonical_name": "绝区零", "aliases": ["绝区零", "Zenless Zone Zero", "ZZZ"]},
    "game_honor_of_kings": {"canonical_name": "王者荣耀", "aliases": ["王者荣耀", "农药", "Honor of Kings"]},
    "game_pubg_mobile": {"canonical_name": "和平精英", "aliases": ["和平精英", "吃鸡", "PUBG Mobile"]},
    "game_lol": {"canonical_name": "英雄联盟", "aliases": ["英雄联盟", "LOL", "撸啊撸", "League of Legends"]},
    "game_endfield": {"canonical_name": "明日方舟：终末地", "aliases": ["终末地", "Endfield", "明日方舟终末地"]},
}

ENTITY_DICT: Dict[str, List[Tuple[str, str]]] = {
    "PLATFORM": [("TapTap", "TapTap"), ("B站", "B站"), ("哔哩哔哩", "B站"), ("抖音", "抖音"),
                 ("微博", "微博"), ("Steam", "Steam"), ("PS5", "PS5"), ("Switch", "Switch"),
                 ("安卓", "Android"), ("iOS", "iOS"), ("小红书", "小红书"), ("知乎", "知乎")],
    "DEVELOPER": [("鹰角", "鹰角网络"), ("鹰角网络", "鹰角网络"), ("库洛", "库洛游戏"),
                  ("米哈游", "米哈游"), ("游戏科学", "游戏科学"), ("腾讯", "腾讯"), ("网易", "网易")],
    "PUBLISHER": [("鹰角", "鹰角网络"), ("腾讯", "腾讯"), ("网易", "网易"), ("米哈游", "米哈游")],
    "GENRE": [("二次元", "二次元"), ("开放世界", "开放世界"), ("塔防", "塔防"), ("肉鸽", "Roguelike"),
              ("抽卡", "抽卡"), ("FPS", "FPS"), ("MOBA", "MOBA"), ("RPG", "RPG"), ("单机", "单机"),
              ("联机", "联机"), (" strategy", "策略")],
    "EVENT": [("联动", "联动"), ("周年庆", "周年庆"), ("版本更新", "版本更新"), ("新版本", "版本更新"),
              ("复刻", "复刻"), ("卡池", "卡池"), ("限定", "限定"), ("内测", "内测"), ("公测", "公测"),
              ("DLC", "DLC"), ("联动活动", "联动"), ("直播", "直播")],
}

# 歧义词：mention → (候选 entity_id, 需要上下文条件)
AMBIGUOUS = {
    "lol": ("game_lol", "game"),       # LOL：英雄联盟 vs laugh out loud
    "吃鸡": ("game_pubg_mobile", "game"),
    "农药": ("game_honor_of_kings", "game"),
}

GAME_CONTEXT_WORDS = ("游戏", "版本", "联动", "角色", "卡池", "抽卡", "上线", "公测",
                      "更新", "皮肤", "活动", "boss", "关卡", "攻略", "强度")


class GameKnowledgeRegistry:
    """§13 Game Knowledge Registry：canonical_name + aliases + 官方账号信息。"""

    def __init__(self) -> None:
        self.games: Dict[str, Dict[str, Any]] = {}
        self._load_from_games_dir()
        self._load_extra()

    def _load_from_games_dir(self) -> None:
        if not os.path.isdir(GAMES_DIR):
            return
        for fn in sorted(os.listdir(GAMES_DIR)):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(GAMES_DIR, fn)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    g = json.load(fh)
            except (ValueError, OSError):
                continue
            key = g.get("key") or fn[:-5]
            aliases = [a for a in (g.get("aliases") or []) if a]
            if g.get("name") and g["name"] not in aliases:
                aliases.append(g["name"])
            self.games[f"game_{key}"] = {
                "entity_id": f"game_{key}",
                "canonical_name": g.get("name", key),
                "aliases": aliases,
                "app_id": g.get("app_id"),
                "official_account": g.get("official_account") or {},
                "source": f"games/{fn}",
            }

    def _load_extra(self) -> None:
        for eid, meta in EXTRA_GAMES.items():
            if eid in self.games:
                # 已由游戏档案定义的，合并别名（档案优先）
                for a in meta["aliases"]:
                    if a not in self.games[eid]["aliases"]:
                        self.games[eid]["aliases"].append(a)
                continue
            self.games[eid] = {
                "entity_id": eid, "canonical_name": meta["canonical_name"],
                "aliases": meta["aliases"], "app_id": None,
                "official_account": {}, "source": "builtin",
            }

    def alias_index(self) -> Dict[str, str]:
        """alias(小写) → entity_id。长别名优先匹配（避免"方舟"抢掉"明日方舟"）。"""
        idx: Dict[str, str] = {}
        for eid, meta in self.games.items():
            for a in meta["aliases"]:
                idx.setdefault(a.lower(), eid)
        return idx

    def summary(self) -> Dict[str, Any]:
        return {"n_games": len(self.games),
                "n_aliases": sum(len(m["aliases"]) for m in self.games.values()),
                "games": [m["canonical_name"] for m in self.games.values()]}


class EntityEngine:
    """抽取 + 链接 + 消歧。确定性规则，可复现。"""

    def __init__(self, registry: Optional[GameKnowledgeRegistry] = None) -> None:
        self.registry = registry or GameKnowledgeRegistry()
        self._alias = self.registry.alias_index()
        # 长别名优先
        self._sorted_aliases = sorted(self._alias.keys(), key=lambda x: -len(x))
        self.llm_fallback = None      # 低置信度兜底（本机未接，见 §33）

    # ---------- 抽取 + 链接 ----------
    def extract(self, text: str, platform: str = "", game_hint: str = "") -> List[Entity]:
        t = (text or "")
        low = t.lower()
        found: Dict[Tuple[str, str], Entity] = {}

        # 1) 游戏：别名最长优先匹配
        for alias in self._sorted_aliases:
            if alias and alias in low:
                eid = self._alias[alias]
                meta = self.registry.games.get(eid, {})
                conf = self._game_confidence(alias, t)
                if conf <= 0.0:
                    continue
                key = (eid, alias)
                if key not in found:
                    found[key] = Entity(entity_id=eid, entity_type="GAME",
                                        canonical_name=meta.get("canonical_name", eid),
                                        mention=alias, confidence=conf,
                                        resolved_by="rule" if conf >= 0.5 else "unresolved")
                # 已匹配过的别名从文本里挖掉，避免"明日方舟"里的"方舟"再命中一次
                low = low.replace(alias, " ")

        # 2) 其它类型实体（词典直配）
        for etype, pairs in ENTITY_DICT.items():
            for surface, canonical in pairs:
                if surface.lower() in low:
                    key = (etype + ":" + canonical, surface)
                    if key not in found:
                        found[key] = Entity(entity_id=f"{etype.lower()}_{canonical}",
                                            entity_type=etype, canonical_name=canonical,
                                            mention=surface, confidence=0.8, resolved_by="rule")

        # 3) 游戏提示（第一层给的 game 字段/话题标签）作为弱证据补链
        if game_hint:
            gh = str(game_hint).strip().lower()
            eid = self._alias.get(gh)
            if eid:
                meta = self.registry.games.get(eid, {})
                found.setdefault((eid, "hint"), Entity(
                    entity_id=eid, entity_type="GAME",
                    canonical_name=meta.get("canonical_name", eid),
                    mention=str(game_hint), confidence=0.9, resolved_by="hint"))

        ents = list(found.values())
        if self.llm_fallback and any(e.confidence < 0.5 and e.entity_type == "GAME" for e in ents):
            ents = self.llm_fallback(ents, t)      # 仅低置信度时调用
        return ents

    def _game_confidence(self, alias: str, context: str) -> float:
        """P(entity|context) 的规则近似（§14 消歧）。"""
        a = alias.lower()
        if a in AMBIGUOUS:
            eid, need = AMBIGUOUS[a]
            has_ctx = any(w in context for w in GAME_CONTEXT_WORDS)
            return 0.9 if has_ctx else 0.35      # 无游戏上下文 → 低置信，不硬链
        # 英文短别名（<=3 字符）容易误命中，需要上下文
        if len(a) <= 3 and re.fullmatch(r"[a-z0-9]+", a):
            return 0.9 if any(w in context for w in GAME_CONTEXT_WORDS) else 0.5
        return 1.0 if len(a) >= 3 else 0.85

    def link(self, mention: str) -> Optional[Entity]:
        """单条 mention → canonical entity（供第三层复用）。"""
        eid = self._alias.get((mention or "").strip().lower())
        if not eid:
            return None
        meta = self.registry.games[eid]
        return Entity(entity_id=eid, entity_type="GAME",
                      canonical_name=meta["canonical_name"], mention=mention,
                      confidence=1.0, resolved_by="rule")

    def stats(self) -> Dict[str, Any]:
        return self.registry.summary()
