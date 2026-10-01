#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""games/game_profile.py 的单元测试：档案加载、默认回退、缺失报错。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GAMES_DIR = ROOT / "games"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


gp = _load("game_profile_under_test", GAMES_DIR / "game_profile.py")


class TestLoadDefaultProfile(unittest.TestCase):
    def test_arknights_profile_complete(self):
        prof = gp.load("arknights")
        self.assertEqual(prof["key"], "arknights")
        self.assertEqual(prof["name"], "明日方舟")
        self.assertEqual(prof["app_id"], 70253)
        self.assertEqual(prof["high_hours"], 100.0)
        self.assertIn("明日方舟", prof["aliases"])
        self.assertTrue(prof["cross_channel"]["bili_keywords"])
        self.assertTrue(prof["cross_channel"]["weibo_uids"])

    def test_available_lists_arknights(self):
        self.assertIn("arknights", gp.available())

    def test_missing_profile_raises(self):
        with self.assertRaises(FileNotFoundError):
            gp.load("no-such-game")


class TestDefaultsAndMerge(unittest.TestCase):
    def test_partial_profile_gets_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "minigame.json"
            p.write_text(json.dumps({"name": "某新游", "app_id": 12345}), encoding="utf-8")
            prof = gp.load(str(p))
            self.assertEqual(prof["name"], "某新游")
            self.assertEqual(prof["app_id"], 12345)
            self.assertEqual(prof["high_hours"], 100.0)  # 默认回退
            self.assertEqual(prof["aliases"], [])
            self.assertEqual(prof["cross_channel"]["weibo_uids"], "")  # 深合并默认
            self.assertEqual(prof["key"], "minigame")  # 未给 key 时取文件名

    def test_key_falls_back_to_stem(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "another.json"
            p.write_text("{}", encoding="utf-8")
            self.assertEqual(gp.load(str(p))["key"], "another")


class TestProfilePath(unittest.TestCase):
    def test_json_suffix_treated_as_path(self):
        self.assertEqual(gp.profile_path("x.json").suffix, ".json")

    def test_plain_key_maps_to_games_dir(self):
        self.assertEqual(gp.profile_path("arknights"), GAMES_DIR / "arknights.json")


if __name__ == "__main__":
    unittest.main()
