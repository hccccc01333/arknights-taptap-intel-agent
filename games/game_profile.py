#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""games/game_profile.py — 游戏档案加载器（管线参数化的唯一入口）。

一个「游戏档案」= 一款游戏跑通全链路所需的全部可变参数：
  key / name / app_id / high_hours / aliases / cross_channel(关键词、官号)

用法（各脚本）：
    from game_profile import load
    prof = load("arknights")          # 或 load(game_key) 由 --game 传入
    prof["app_id"], prof["name"], prof["high_hours"]

缺字段时给安全默认值；档案不存在时抛 FileNotFoundError（不静默兜底）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GAMES_DIR = Path(__file__).resolve().parent

DEFAULTS: dict[str, Any] = {
    "name": "",
    "app_id": None,
    "high_hours": 100.0,
    "aliases": [],
    "cross_channel": {"bili_keywords": "", "weibo_uids": ""},
}


def profile_path(key: str) -> Path:
    """游戏档案路径；key 也接受直接的 .json 路径。"""
    p = Path(key)
    if p.suffix == ".json":
        return p
    return GAMES_DIR / f"{key}.json"


def load(key: str = "arknights") -> dict[str, Any]:
    """加载游戏档案并与默认值合并（浅合并 + cross_channel 深合并一层）。"""
    path = profile_path(key)
    if not path.exists():
        raise FileNotFoundError(
            f"游戏档案不存在: {path}（在 games/ 下新增 <key>.json 即可接入新游戏）"
        )
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    prof: dict[str, Any] = {**DEFAULTS, **raw}
    prof["cross_channel"] = {**DEFAULTS["cross_channel"], **raw.get("cross_channel", {})}
    if not prof.get("key"):
        prof["key"] = path.stem
    return prof


def available() -> list[str]:
    """列出全部已接入的游戏 key。"""
    return sorted(p.stem for p in GAMES_DIR.glob("*.json"))
