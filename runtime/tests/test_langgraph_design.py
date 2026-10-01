#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LangGraph 框架设计回归测试。

为什么有这一组测试：本项目把 LangGraph 当作**结构证据**——
「这是个 LangGraph 项目」不能只是一句声明，要有可复跑的验证。
同时它把三条设计红线锁死，防止后来者（或未来的我）在框架层破坏纪律。

覆盖：
  · 条件边 + 重试环（巡检图的质检闸门）
  · interrupt 人工中断点 + 恢复（标注闸门等批准）
  · checkpointer 持久化（中断跨调用存活、轨迹可回放）
  · State 守卫：原始文本禁止进 State（facts 锁数纪律在框架层的落地）

降级纪律：langgraph 未安装时**整组跳过**（不是失败）。
理由：主测试套件保持「纯标准库可跑」（CI 不装 langgraph 也能全绿），
框架测试作为**可选层**存在——这与项目其他地方的可选维度处理一致。
"""

from __future__ import annotations

import importlib.util
import operator
import unittest
from typing import Annotated, TypedDict

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None

if HAS_LANGGRAPH:
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt


# ---------------------------------------------------------------------------
# State 守卫：三条红线的实现（与生产代码同源）
# ---------------------------------------------------------------------------

MAX_STATE_STR = 200


def assert_state_clean(state: dict) -> None:
    """State 守卫：发现长文本或原始文本字段直接抛错。

    ⚠ 这是本项目 facts 锁数纪律在框架层的落地。
    框架不会帮你守这条——LangGraph 的 State 天然诱使人把原始评论塞进去，
    因为那样任一节点取用最方便；但一旦这么做，LLM 节点就能编数字，
    架构上再也拦不住。
    """
    for k, v in state.items():
        if isinstance(v, str) and len(v) > MAX_STATE_STR:
            raise ValueError(
                f"State 禁止存放长文本（{k}，{len(v)} 字符）——违反 facts 锁数纪律"
            )
        if k.startswith("raw_") or k.endswith("_text") or k.endswith("_content"):
            raise ValueError(f"State 禁止出现原始文本字段：{k}")


class TestStateGuard(unittest.TestCase):
    """State 守卫：**不依赖 langgraph**，所以主套件里也要跑。

    守卫是纯 Python 逻辑（扫字段名 + 扫长度），把它锁死在主套件，
    意味着即使哪天 langgraph 没装、框架测试整组跳过，
    「原始文本不得进 State」这条纪律仍然有回归保护。
    """

    def test_rejects_raw_text_field_names(self):
        for bad in ("raw_comments", "review_text", "post_content"):
            with self.subTest(field=bad):
                with self.assertRaises(ValueError):
                    assert_state_clean({bad: ["x"]})

    def test_rejects_long_string(self):
        with self.assertRaises(ValueError):
            assert_state_clean({"junk": "长" * (MAX_STATE_STR + 1)})

    def test_allows_locked_numbers_and_paths(self):
        assert_state_clean({
            "n_records": 3000,
            "neg_rate_pp": 36.93,
            "data_path": "data/processed/reviews/reviews_clean.csv",
            "log": ["短句一", "短句二"],
        })

    def test_allows_moderate_string(self):
        # 理由句、状态名这类短文本是允许的
        assert_state_clean({"rationale": "负向率显著上升 19.22pp，触发深挖。"})


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph 未安装，框架层测试整组跳过")
class TestInspectGraph(unittest.TestCase):
    """巡检图：条件边 + 环 + interrupt + checkpointer 的联合验证。"""

    @staticmethod
    def _build():
        class S(TypedDict):
            n_records: int
            crawl_rounds: int
            degraded: bool
            approved: bool
            data_path: str
            log: Annotated[list[str], operator.add]

        def node_check(state):
            assert_state_clean(state)
            return {"log": ["[check] 需采集"]}

        def node_crawl(state):
            assert_state_clean(state)
            rnd = state.get("crawl_rounds", 0) + 1
            n = 30 if rnd == 1 else 3000  # 第 1 次残缺，重爬后成功
            return {"crawl_rounds": rnd, "n_records": n,
                    "log": [f"[crawl] 第 {rnd} 次 → {n} 条"]}

        def node_qc(state):
            assert_state_clean(state)
            return {"log": [f"[qc] {state.get('n_records')}"]}

        def route_qc(state):
            if state.get("n_records", 0) >= 100:
                return "clean"
            if state.get("crawl_rounds", 0) >= 2:
                return "mark_degraded"
            return "crawl"  # ← 环

        def node_clean(state):
            assert_state_clean(state)
            return {"log": ["[clean] 完成"]}

        def node_degraded(state):
            assert_state_clean(state)
            return {"degraded": True, "log": ["[degraded] 重爬超限"]}

        def node_gate(state):
            """人工确认点：标注要花钱。"""
            assert_state_clean(state)
            decision = interrupt({"ask": "是否批准标注？",
                                  "n_records": state.get("n_records")})
            return {"approved": bool(decision), "log": [f"[gate] 批复={decision}"]}

        def node_annotate(state):
            assert_state_clean(state)
            return {"log": ["[annotate] 已标注" if state.get("approved") else "[annotate] 跳过"]}

        def node_refresh(state):
            assert_state_clean(state)
            return {"log": ["[refresh] 完成"]}

        g = StateGraph(S)
        for name, fn in [("check", node_check), ("crawl", node_crawl), ("qc", node_qc),
                         ("clean", node_clean), ("mark_degraded", node_degraded),
                         ("gate", node_gate), ("annotate", node_annotate),
                         ("refresh", node_refresh)]:
            g.add_node(name, fn)
        g.add_edge(START, "check")
        g.add_edge("check", "crawl")
        g.add_edge("crawl", "qc")
        g.add_conditional_edges("qc", route_qc, ["clean", "crawl", "mark_degraded"])
        g.add_edge("clean", "gate")
        g.add_edge("mark_degraded", "gate")
        g.add_edge("gate", "annotate")
        g.add_edge("annotate", "refresh")
        g.add_edge("refresh", END)
        return g

    def _run_to_interrupt(self):
        from langgraph.checkpoint.memory import InMemorySaver

        app = self._build().compile(checkpointer=InMemorySaver())
        cfg = {"configurable": {"thread_id": "t-inspect"}}
        out = app.invoke(
            {"n_records": 0, "crawl_rounds": 0, "degraded": False,
             "approved": False, "data_path": "data/raw/taptap/raw/x.jsonl", "log": []},
            cfg,
        )
        return app, cfg, out

    def test_retry_loop_fires_once(self):
        """条件边驱动的环：质检不过 → 回头重爬，且只重爬一次。"""
        _, _, out = self._run_to_interrupt()
        self.assertEqual(out["crawl_rounds"], 2, "应在首次质检失败后重爬一次")
        self.assertEqual(out["n_records"], 3000, "重爬后应拿到完整数据")
        self.assertFalse(out["degraded"], "重爬成功则不应标降级")

    def test_interrupt_pauses_graph(self):
        _, _, out = self._run_to_interrupt()
        self.assertIn("__interrupt__", out, "应停在人工确认点")
        payload = out["__interrupt__"][0].value
        self.assertEqual(payload["n_records"], 3000, "中断载荷应带上待批条数")
        self.assertNotIn("[annotate]", "\n".join(out["log"]), "未批准前不应执行标注")

    def test_resume_after_approval(self):
        app, cfg, _ = self._run_to_interrupt()
        out2 = app.invoke(Command(resume=True), cfg)
        self.assertTrue(out2["approved"])
        joined = "\n".join(out2["log"])
        self.assertIn("[annotate] 已标注", joined)
        self.assertIn("[refresh] 完成", joined)

    def test_crawl_not_reexecuted_on_resume(self):
        """关键：恢复时不能把前面的节点重跑一遍（否则采集会重复执行）。"""
        app, cfg, first = self._run_to_interrupt()
        out2 = app.invoke(Command(resume=True), cfg)
        joined = "\n".join(out2["log"])
        self.assertEqual(joined.count("[crawl] 第 1 次"), 1,
                         "恢复不应导致采集重跑")

    def test_checkpoint_keeps_trajectory(self):
        app, cfg, _ = self._run_to_interrupt()
        app.invoke(Command(resume=True), cfg)
        state = app.get_state(cfg)
        self.assertGreaterEqual(len(state.values.get("log", [])), 6,
                                "checkpointer 应保留完整轨迹")
        self.assertEqual(state.next, (), "跑完后不应还有待执行节点")


if __name__ == "__main__":
    unittest.main()
