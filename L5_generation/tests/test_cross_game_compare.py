#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cross_game_compare 的单元测试：结构层统计、维度聚合、payload 结构。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


cc = _load("cross_game_compare", ROOT / "L5_generation" / "cross_game_compare.py")


def write_clean_csv(path: Path, rows: list[dict]) -> Path:
    import csv

    fields = ["score_norm", "is_recommend", "played_hours_num", "rating_tags"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    return path


class TestCleanStats(unittest.TestCase):
    def test_basic_and_tags(self):
        with tempfile.TemporaryDirectory() as td:
            p = write_clean_csv(
                Path(td) / "clean.csv",
                [
                    {"score_norm": "0.8", "is_recommend": "True", "played_hours_num": "100",
                     "rating_tags": json.dumps([{"label": "可玩性", "value": "up"}, {"label": "运营服务", "value": "down"}])},
                    {"score_norm": "0.2", "is_recommend": "False", "played_hours_num": "300",
                     "rating_tags": json.dumps([{"label": "可玩性", "value": "down"}])},
                ],
            )
            s = cc.load_clean_stats(p)
            self.assertEqual(s["n"], 2)
            self.assertEqual(s["mean_score_norm"], 0.5)
            self.assertEqual(s["not_recommend_rate"], 0.5)
            self.assertEqual(s["played_hours_median"], 300)
            dims = {d["dimension"]: d for d in s["tag_dimensions"]}
            self.assertEqual(dims["可玩性"]["up"], 1)
            self.assertEqual(dims["可玩性"]["down"], 1)
            self.assertEqual(dims["可玩性"]["up_rate"], 0.5)
            self.assertEqual(dims["运营服务"]["down"], 1)

    def test_malformed_tags_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            p = write_clean_csv(
                Path(td) / "clean.csv",
                [{"score_norm": "0.5", "is_recommend": "True", "played_hours_num": "1",
                  "rating_tags": "not-json"},
                 {"score_norm": "", "is_recommend": "", "played_hours_num": "", "rating_tags": "[]"}],
            )
            s = cc.load_clean_stats(p)
            self.assertEqual(s["n"], 2)
            self.assertEqual(s["tag_dimensions"], [])
            self.assertEqual(s["mean_score_norm"], 0.5)  # 仅有效行计入；坏 JSON 行被忽略


class TestPayload(unittest.TestCase):
    def test_structure_only_mode(self):
        entries = [
            {"name": "A", "risk": None, "clean_stats": {"n": 10, "mean_score_norm": 0.6,
             "not_recommend_rate": 0.3, "played_hours_median": 50.0, "n_with_tags": 5,
             "tag_dimensions": [{"dimension": "可玩性", "up": 3, "down": 1, "up_rate": 0.75, "total": 4}]}},
            {"name": "B", "risk": None, "clean_stats": {"n": 5, "mean_score_norm": 0.4,
             "not_recommend_rate": 0.8, "played_hours_median": 20.0, "n_with_tags": 3,
             "tag_dimensions": []}},
        ]
        payload = cc.build_payload(entries)
        self.assertEqual(len(payload["games"]), 2)
        self.assertNotIn("n_total", payload["games"][0])  # 无标注数据时不出现标注层字段
        self.assertEqual(payload["games"][1]["structure"]["n"], 5)
        self.assertIn("非同期", payload["caliber"])


if __name__ == "__main__":
    unittest.main()
