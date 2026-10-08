#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""平台适配器注册表：这里回答「全网有哪些源，哪些真的接了」。

★ 诚实原则：登记在册 ≠ 已接入。status 只有 active / planned 两种，
   没写适配器的一律 planned，绝不把"打算接"说成"接了"。
"""

from __future__ import annotations

from typing import Dict, List

from .base import BaseAdapter
from .taptap import TapTapAdapter
from .bilibili import BilibiliAdapter
from .douyin import DouyinAdapter
from .weibo import WeiboAdapter

ADAPTERS: Dict[str, BaseAdapter] = {
    "taptap": TapTapAdapter(),
    "bilibili": BilibiliAdapter(),
    "douyin": DouyinAdapter(),
    "weibo": WeiboAdapter(),
}

__all__ = ["ADAPTERS", "BaseAdapter", "coverage"]


def coverage() -> Dict:
    """平台覆盖情况：给下游看"我们现在到底能看到哪些平台"。"""
    from schema.content_event import PLATFORM_REGISTRY

    active, planned = [], []
    for key, meta in PLATFORM_REGISTRY.items():
        item = {
            "platform": key,
            "name": meta["name"],
            "kinds": meta["kinds"],
            "datasets": [d.name for d in ADAPTERS[key].datasets] if key in ADAPTERS else [],
        }
        (active if meta["status"] == "active" else planned).append(item)
    return {"active": active, "planned": planned, "n_active": len(active), "n_planned": len(planned)}
