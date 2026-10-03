#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""命令层测试（webapp/commands.py）。

钉死的纪律：
  ① 解析确定性：位置参数 + --flag（含引号值），不认识就报 usage **不猜**
  ② 命令 → services → L6：权限仍在 L6 层（reviewer 不能 launch、viewer 不能 decide）
  ③ 动作面完整：§37 的 too_late / not_relevant / need_more_research 都能从命令走到
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_ROOT, os.path.join(_ROOT, "webapp"), os.path.join(_ROOT, "L6_execution"),
           os.path.join(_ROOT, "L5_memory"), os.path.join(_ROOT, "L4_intelligence")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from webapp.commands import parse_args, USAGE      # noqa: E402


class TestParse(unittest.TestCase):
    def test_positional_and_flags(self):
        args, flags = parse_args("idea_2 --note 机制清晰")
        self.assertEqual(args, ["idea_2"])
        self.assertEqual(flags["note"], "机制清晰")

    def test_quoted_flag_value(self):
        args, flags = parse_args('idea_1 --note "机制清晰, 可落地"')
        self.assertEqual(args, ["idea_1"])
        self.assertEqual(flags["note"], "机制清晰, 可落地")

    def test_boolean_flag(self):
        args, flags = parse_args("idea_1 --experiment")
        self.assertEqual(args, ["idea_1"])
        self.assertEqual(flags["experiment"], "true")

    def test_unbalanced_quotes_fallback(self):
        args, flags = parse_args('idea_1 --note "未闭合')
        self.assertEqual(args[0], "idea_1")        # 不抛异常，退回朴素切分


class TestUsageTable(unittest.TestCase):
    def test_every_command_documented(self):
        for name in ("follow", "approve", "reject", "too-late", "not-relevant", "research",
                     "assign", "assets", "plan", "launch", "stop", "observe", "finish",
                     "ack", "set", "help"):
            self.assertIn(name, USAGE, f"/{name} 缺 usage")

    def test_decision_family_complete(self):
        """§37 决策动作面：不能只有 approve/reject。"""
        for name in ("approve", "reject", "too-late", "not-relevant", "research"):
            self.assertIn(name, USAGE)


class TestCommandRun(unittest.TestCase):
    """命令执行（走真实 L6 模块，临时库隔离 —— 复用 webapp 测试的 fixture 思路）。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._old = {k: os.environ.get(k) for k in ("L6_EXECUTION_DB", "L5_MEMORY_DB")}
        os.environ["L6_EXECUTION_DB"] = str(tmp / "l6.sqlite3")
        os.environ["L5_MEMORY_DB"] = str(tmp / "l5.sqlite3")
        from memory.store import MemoryStore
        m = MemoryStore(os.environ["L5_MEMORY_DB"])
        m.close()
        import webapp.services as S
        S.reset_state()
        self.S = S
        self.st = S.get_state()

    def tearDown(self):
        self.S.reset_state()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_unknown_command_reports_usage(self):
        from webapp import commands as C
        out = C.run("/nonsense x", {"actor": "A", "role": "reviewer"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "usage")
        self.assertIn("/help", out["message"])

    def test_non_command_rejected(self):
        from webapp import commands as C
        out = C.run("approve idea_1", {"actor": "A", "role": "reviewer"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "not_command")

    def test_help_returns_table(self):
        from webapp import commands as C
        out = C.run("/help", {"actor": "A", "role": "reviewer"})
        self.assertTrue(out["ok"])
        self.assertIn("/approve", out["message"])

    def test_decide_requires_existing_object(self):
        """不存在的对象 → 报错（404 语义），不是静默成功。"""
        from webapp import commands as C
        out = C.run("/approve ghost_idea --note x", {"actor": "B", "role": "reviewer"})
        self.assertFalse(out["ok"])

    def test_viewer_cannot_decide(self):
        """权限由 L6 层强制（§43）—— 命令层不自己判，也不绕过。"""
        from webapp import commands as C
        from execution.pipeline import _ensure_creative_item

        def seed():
            creative = self.S._creative(app=self.st.app, idea_id="idea_2")
            return _ensure_creative_item(self.st.app, creative)

        item = self.st.run(seed)
        out = C.run(f"/approve {item['object_id']}", {"actor": "E", "role": "viewer"})
        self.assertFalse(out["ok"])
        self.assertIn("无权", out["message"])

    def test_set_requires_admin(self):
        from webapp import commands as C
        out = C.run("/set --dev 2", {"actor": "B", "role": "reviewer"})
        self.assertFalse(out["ok"])
        self.assertIn("configure", out["message"])

    def test_decide_full_chain_to_l5(self):
        """命令 → 状态机 → L5 Decision Memory（闭环① 的命令面证据）。"""
        from webapp import commands as C
        from execution.pipeline import _ensure_creative_item

        def seed():
            creative = self.S._creative(app=self.st.app, idea_id="idea_2")
            return _ensure_creative_item(self.st.app, creative)

        oid = self.st.run(seed)["object_id"]
        # ① operator 提交评审（submit 非决策动作）
        r1 = C.run(f"/submit {oid}", {"actor": "op", "role": "operator"})
        self.assertTrue(r1["ok"], r1["message"])
        # ② reviewer 批准
        r2 = C.run(f"/approve {oid} --note ok", {"actor": "rev", "role": "reviewer"})
        self.assertTrue(r2["ok"], r2["message"])
        decisions = self.st.run(lambda: self.st.app.memory.candidates("decision"))
        self.assertEqual(decisions[0]["decision"], "approve")

    def test_submit_is_open_to_operator_decide_is_not(self):
        """§43 边界在命令层同样成立：提交人人可，决策要 reviewer。"""
        from webapp import commands as C
        from execution.pipeline import _ensure_creative_item

        def seed():
            return _ensure_creative_item(self.st.app,
                                         self.S._creative(app=self.st.app, idea_id="idea_3"))

        oid = self.st.run(seed)["object_id"]
        ok_sub = C.run(f"/submit {oid}", {"actor": "op", "role": "operator"})
        self.assertTrue(ok_sub["ok"], ok_sub["message"])
        no_dec = C.run(f"/approve {oid}", {"actor": "op", "role": "operator"})
        self.assertFalse(no_dec["ok"])
        self.assertIn("无权", no_dec["message"])


if __name__ == "__main__":
    unittest.main()