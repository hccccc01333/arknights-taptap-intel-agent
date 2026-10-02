#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§42 父子事件测试。

钉死两条：① 三条规则必须同时成立才挂（不猜）② 取"最小满足者"而不是最大的那个。
"""

from __future__ import annotations

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_L3 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L3)
for p in (_L3, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from trend_engine.clustering import resolve_parent_child  # noqa: E402


def _ev(eid, n, ents, start="2026-01-01T10:00:00", end="2026-01-05T10:00:00",
        status="active"):
    return {"event_id": eid, "content_count": n, "entity_ids": json.dumps(ents),
            "started_at": start, "last_updated_at": end, "status": status,
            "canonical_title": eid}


class TestParentChild(unittest.TestCase):
    def test_child_attached_to_bigger_parent(self):
        parent = _ev("p", 100, ["g1", "g2"])
        child = _ev("c", 10, ["g1"])
        r = resolve_parent_child([parent, child])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["child"], "c")
        self.assertEqual(r[0]["parent"], "p")

    def test_not_attached_when_size_close(self):
        """体量不够悬殊 → 不挂（否则会把任意两个相近事件硬凑父子）。"""
        a = _ev("a", 10, ["g1"])
        b = _ev("b", 12, ["g1"])
        self.assertEqual(resolve_parent_child([a, b]), [])

    def test_not_attached_when_entities_disjoint(self):
        a = _ev("a", 100, ["g1"])
        b = _ev("b", 10, ["g9"])
        self.assertEqual(resolve_parent_child([a, b]), [])

    def test_not_attached_when_time_outside(self):
        parent = _ev("p", 100, ["g1"], start="2026-02-01T00:00:00", end="2026-02-05T00:00:00")
        child = _ev("c", 10, ["g1"], start="2026-01-01T00:00:00", end="2026-01-02T00:00:00")
        self.assertEqual(resolve_parent_child([parent, child]), [])

    def test_picks_nearest_parent_not_biggest(self):
        """三个候选：50 / 100 / 500 —— 应该挂到 50（最近一层），不是 500。"""
        small = _ev("p50", 50, ["g1"])
        mid = _ev("p100", 100, ["g1"])
        big = _ev("p500", 500, ["g1"])
        child = _ev("c", 10, ["g1"])
        r = resolve_parent_child([small, mid, big, child])
        # 结果是**多层**的：c→p50→p100→p500。每个事件都挂到自己最近的那一层父。
        link = next(x for x in r if x["child"] == "c")
        self.assertEqual(link["parent"], "p50")
        self.assertEqual(next(x for x in r if x["child"] == "p50")["parent"], "p100")

    def test_merged_events_ignored(self):
        parent = _ev("p", 100, ["g1"], status="active")
        child = _ev("c", 10, ["g1"], status="merged")
        self.assertEqual(resolve_parent_child([parent, child]), [])

    def test_no_entities_no_parent(self):
        """没有实体 → 不猜父子（实体是唯一的语义锚点）。"""
        parent = _ev("p", 100, [])
        child = _ev("c", 10, [])
        self.assertEqual(resolve_parent_child([parent, child]), [])


if __name__ == "__main__":
    unittest.main()
