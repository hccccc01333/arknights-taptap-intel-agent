#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全项目唯一路径出口（分层重排后新增）。

为什么要有它：分层重排后，同一份代码散在 L1..L6 / runtime / data 里，
每个文件自己写 `Path(__file__).parents[N]` 上溯找根 —— 层数一变就全部错位
（2026-10-01 重排时实测踩到：parents[1] 从"项目根"变成了"L1_data_source"）。

用法（任何层）：
    import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # 到根
    from paths import ROOT, DATA, RAW, EVENTS, load_module

`load_module(name, layer=...)` 解决跨层 import：中文目录名不适合做包导入，
所以模块一律用 importlib 从文件加载，层由调用方显式指定。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent

# —— 分层目录 ——
L1 = ROOT / "L1_data_source"
L2 = ROOT / "L2_signal"
L3 = ROOT / "L3_semantic"
L3_TREND = ROOT / "L3_trend"
L4 = ROOT / "L4_intelligence"
L5 = ROOT / "L5_generation"
L5_MEMORY = ROOT / "L5_memory"          # 设计口径的第五层：知识与增长记忆
L6 = ROOT / "L6_delivery"
RUNTIME = ROOT / "runtime"
COMMON = ROOT / "common"
GAMES = ROOT / "games"

LAYERS: Dict[str, Path] = {
    "L1": L1, "L1_data_source": L1,
    "L2": L2, "L2_signal": L2,
    "L3": L3, "L3_semantic": L3,
    "L3": L3_TREND, "L3_trend": L3_TREND,
    "L4": L4, "L4_intelligence": L4,
    "L5": L5, "L5_generation": L5,
    "L5_memory": L5_MEMORY,
    "L6": L6, "L6_delivery": L6,
    "runtime": RUNTIME,
    "common": COMMON,
    "games": GAMES,
}

# —— 数据湖 ——
DATA = ROOT / "data"
RAW = DATA / "raw"
EVENTS = DATA / "events"
ANNOTATIONS = DATA / "annotations"
FACTS = DATA / "facts"
PROCESSED = DATA / "processed"
STATE = DATA / "state"
OUTPUTS = DATA / "outputs"

# 各平台原始数据目录
RAW_PLATFORM = {p: RAW / p for p in ("taptap", "bilibili", "douyin", "weibo")}


def ensure_on_path() -> None:
    """把项目根与 common 加入 sys.path（供 import paths / import pii_hash 使用）。"""
    for p in (str(ROOT), str(COMMON)):
        if p not in sys.path:
            sys.path.insert(0, p)


def load_module(name: str, layer: Optional[str] = None, search: Optional[list] = None) -> ModuleType:
    """按名字从指定层加载模块（跨层 import 的唯一正确姿势）。

    load_module("risk_insight", layer="L5_generation")
    load_module("harness", layer="runtime")
    """
    ensure_on_path()
    cands = []
    if layer:
        base = LAYERS.get(layer)
        if base is None:
            raise KeyError(f"未知层: {layer}；可选: {sorted(LAYERS)}")
        cands.append(base / f"{name}.py")
    cands.append(COMMON / f"{name}.py")
    if search:
        cands.extend(Path(s) / f"{name}.py" for s in search)
    for c in cands:
        if c.exists():
            spec = importlib.util.spec_from_file_location(name, str(c))
            mod = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
            sys.modules[name] = mod
            spec.loader.exec_module(mod)                          # type: ignore[union-attr]
            return mod
    raise FileNotFoundError(f"找不到模块 {name}.py（已找: {[str(c) for c in cands]}）")
