#!/usr/bin/env python3
"""Agent 巡检图（LangGraph）—— 真实节点 = harness 的一步。

定位：`docs/Agent-v2-架构设计.md` §2「Agent 控制范围：两段式 + 人工域」
      本图 = **第一段（数据运维编排 / 每日巡检）** 的骨架。

================================★ 两个词的关系 ================================
「agent harness + LangGraph 框架」不是两件事，是同一件事的两面：

    LangGraph 管 **怎么流转**：节点 / 条件边 / 重试环 / checkpointer / interrupt
    harness   管 **每一步怎么靠谱地跑**：参数校验 / 预算 / 幂等 / 产出契约判定 / 失败分派 / 轨迹

    所以：**图的节点体 = harness 的一步**。对应关系（一一对应，不是类比）：

    | LangGraph 机制          | harness 里对应的东西                     |
    |------------------------|-----------------------------------------|
    | `State`（TypedDict）    | facts（锁数纪律：原始文本禁入 State）      |
    | `checkpointer`         | 轨迹（每步 attempt/耗时/成败/产物，可回放） |
    | `interrupt`            | 契约的 `human` 字段（高危闸门）            |
    | 条件边 + 重试环          | 契约的 `on_fail`（retry/degrade/reject/abort） |
    | `thread_id`            | 契约的 `idempotency_key`（同窗口不重跑）    |

★ 图的边界（本图只管"跑得对不对"，不管"值不值得做"）：
    质检不过 → 重采一次（**有界环**，上限 2 轮）→ 仍不过 → 标降级继续
    这条对应设计里的「失败自动重爬 1 次，再败标记降级继续」。

★ 降级纪律：langgraph 未安装时，**主测试套件仍须纯标准库可跑**，
  所以本模块的测试整组跳过（不是失败）。导入层不做硬失败。
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import operator
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

_LAB = Path(__file__).resolve().parent
if str(_LAB) not in sys.path:
    sys.path.insert(0, str(_LAB))

import harness  # noqa: E402  （同目录；harness 内已做 sys.path 引导）
import task_contracts as tc  # noqa: E402

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None

if HAS_LANGGRAPH:
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

ROOT = Path(__file__).resolve().parent.parent
STATE = _LAB / "state"
GRAPH_DB = STATE / "graph_checkpoints.sqlite3"
TZ = timezone(timedelta(hours=8))

MAX_QC_ROUNDS = 2             # 质检重来上限（= 首跑 + 重来 1 次，对应设计「失败自动重爬 1 次」）
# ★ 质检判据不再写死行数，而是**复用首工具的产出契约**（harness.judge_result）——
#   这样换任务不用改图：热点追踪看 hot_hashtags.csv，素材获取看 materials.jsonl。
DEFAULT_THREAD = "inspect"

# State 守卫：三条红线（与 tests/test_langgraph_design.py 同源）
MAX_STATE_STR = 300
BANNED_FIELD_MARKS = ("raw_text", "content", "body", "review_text",
                      "comment_text", "full_text", "original")


# ================================================================ State 守卫

def assert_state_clean(state: Any, *, path: str = "state") -> None:
    """State 只准放 facts：**禁原始文本字段名** + 所有字符串有长度上限。

    ★ 这是「facts 是 LLM 与数据之间唯一界面」这条锁数纪律在**框架层**的落地 ——
      不靠提示词约束，靠进 State 前就抛异常。
    """
    if isinstance(state, dict):
        for k, v in state.items():
            kl = str(k).lower()
            bad = [m for m in BANNED_FIELD_MARKS if m in kl]
            if bad:
                raise ValueError(f"{path}.{k} 是原始文本字段（命中 {bad}）——"
                                 "State 只准放 facts，原始文本请落盘")
            assert_state_clean(v, path=f"{path}.{k}")
    elif isinstance(state, (list, tuple)):
        for i, v in enumerate(state):
            assert_state_clean(v, path=f"{path}[{i}]")
    elif isinstance(state, str) and len(state) > MAX_STATE_STR:
        raise ValueError(f"{path} 字符串过长（{len(state)} > {MAX_STATE_STR}）——"
                         "长文本属原始内容，不该进 State")


# ================================================================ State

class InspectState(TypedDict, total=False):
    """任务图的 State —— **只放 facts 与判定量**（原始文本禁入，见 assert_state_clean）。

    ★ 通用化（2026-10-01）：这张图不再只为「热点追踪」而建 —— 节点由**契约的工具链**生成，
      所以同一个 State 要同时装得下任何任务。热点专属的 `series_delta` 之类不再进 State，
      由 `finish` 节点从各步产物里汇总。
    """
    run_id: str
    task_id: str
    qc_rounds: int                  # 质检重来轮数（**有界**，上限见 MAX_QC_ROUNDS）
    n_rows: int                     # 首工具的产物行数（质检判据）
    gate: bool
    approved: bool
    ok: bool
    steps: Annotated[list[dict], operator.add]
    degraded: Annotated[list[str], operator.add]
    log: Annotated[list[str], operator.add]


# ================================================================ 运行上下文

@dataclass
class Ctx:
    """一次图运行的上下文（harness 的连接、预算、轨迹目标等都从这里拿）。"""
    task_id: str
    contract: dict[str, Any]
    con: sqlite3.Connection
    before: dict[str, Any] = field(default_factory=dict)
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    dry_run: bool = False
    force: bool = False
    trace_path: Path | None = None
    call_hook: Callable[[str, dict], dict] | None = None
    use_gate: bool = False
    now: datetime | None = None
    run_id: str = ""

    def __post_init__(self) -> None:
        self.now = self.now or datetime.now(TZ)
        self.run_id = self.run_id or self.now.strftime("%Y-%m-%dT%H:%M")
        if self.trace_path is None:
            self.trace_path = harness.TRACE_JSONL


def _csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def _delta_from_artifacts(step: dict[str, Any], prefix: str = "topic_series(+") -> int:
    """从 harness 的产出契约结果里取行数增量（如 'topic_series(+23)'）。"""
    for a in step.get("artifacts") or []:
        if a.startswith(prefix):
            try:
                return int(a[len(prefix):].rstrip(")"))
            except ValueError:
                return 0
    return 0


# ================================================================ 建图

def first_artifact_rows(tool: str) -> int:
    """首工具的第一个产物有多少行 —— 质检判据的量。"""
    spec = harness.TOOLS().get(tool)
    if spec is None:
        return 0
    r = spec.result or {}
    paths: list[Path] = []
    if "artifact" in r:
        paths.append(Path(r["artifact"]))
    for a in r.get("artifacts") or []:
        if "artifact" in a:
            paths.append(Path(a["artifact"]))
    for p in paths:
        if p.exists():
            try:
                return harness.artifact_rows(p)
            except Exception:
                return 0
    return 0


def build_graph(ctx: Ctx):
    """构造**契约驱动**的任务图：**节点由契约的工具链生成**。

    ★ 这是「一个流程」与「一个框架」的分界：同一张图能跑任何契约（只要工具链是有序的）。
        · 节点体 = `harness.run_step`（一步的校验/预算/幂等/产出契约判定/失败分派/轨迹）
        · 图的形状 = 契约的 `tools` 顺序
        · 质检闸门 = **复用首工具的产出契约**（不写死行数）
        · 重试环 = **有界**（MAX_QC_ROUNDS），超限走 mark_degraded 继续
        · 人工确认点 = 契约 `human`（或 --human-gate 强制）
    """
    if not HAS_LANGGRAPH:
        raise RuntimeError("langgraph 未安装：pip install langgraph langgraph-checkpoint-sqlite")

    tools = list(ctx.contract.get("tools") or [])
    if not tools:
        raise ValueError(f"契约 {ctx.task_id} 没有工具链，无法建图")

    def nid(i: int) -> str:
        return f"t{i}_{tools[i]}"

    first = nid(0)
    after_qc = nid(1) if len(tools) > 1 else "finish"

    def _step(tool: str) -> dict[str, Any]:
        return harness.run_step(
            ctx.task_id, tool, ctx.contract,
            params=ctx.params.get(tool), con=ctx.con, before=ctx.before,
            force=ctx.force, dry_run=ctx.dry_run, now=ctx.now,
            trace_path=ctx.trace_path, call_hook=ctx.call_hook)

    # ---- 节点工厂 ------------------------------------------------------

    def node_tick(state: InspectState) -> dict[str, Any]:
        assert_state_clean(state)
        bucket = harness.idem_bucket(ctx.contract, ctx.now)
        return {"run_id": ctx.run_id, "task_id": ctx.task_id,
                "log": [f"[tick] task={ctx.task_id} run={ctx.run_id} 幂等窗口={bucket}"]}

    def make_tool_node(idx: int):
        tool = tools[idx]

        def _node(state: InspectState) -> dict[str, Any]:
            assert_state_clean(state)
            step = _step(tool)
            upd: dict[str, Any] = {
                "steps": [step],
                "log": [f"[t{idx}] {tool} → {step['outcome']}"
                        + (f"：{step['note'][:60]}" if step.get("note") else "")],
            }
            if idx == 0:                      # 首工具：记轮数 + 产物行数（质检判据）
                upd["qc_rounds"] = int(state.get("qc_rounds") or 0) + 1
                upd["n_rows"] = first_artifact_rows(tool)
                upd["log"] = upd["log"] + [f"[t0] 产物 {upd['n_rows']} 行"
                                           f"（第 {upd['qc_rounds']} 轮）"]
            return upd

        return _node

    def node_qc(state: InspectState) -> dict[str, Any]:
        """质检闸门：判据 = **首工具的产出契约**（不写死行数）。"""
        assert_state_clean(state)
        spec = harness.TOOLS().get(tools[0])
        ok, why, _ = harness.judge_result(spec, ctx.before) if spec else (False, "工具未注册", [])
        return {"log": [f"[qc] {'通过' if ok else '不过'}：{why}"]}

    def route_qc(state: InspectState) -> str:
        spec = harness.TOOLS().get(tools[0])
        ok, _, _ = harness.judge_result(spec, ctx.before) if spec else (False, "", [])
        if ok:
            return "next"
        if int(state.get("qc_rounds") or 0) >= MAX_QC_ROUNDS:
            return "degraded"
        return "retry"                        # ← 有界重试环：回到首工具重跑

    def node_gate(state: InspectState) -> dict[str, Any]:
        """人工确认点（interrupt）：契约 `human=True` 的动作才需人批。

        当前两个契约的 human 都不是 True，所以默认**不经过本节点**；`--human-gate` 可强制开启。
        """
        assert_state_clean(state)
        decision = interrupt({"ask": f"是否批准继续执行「{ctx.task_id}」的后续动作？",
                              "task_id": ctx.task_id,
                              "qc_rounds": state.get("qc_rounds", 1)})
        return {"approved": bool(decision), "log": [f"[gate] 批复={decision}"]}

    def node_mark_degraded(state: InspectState) -> dict[str, Any]:
        assert_state_clean(state)
        return {"degraded": [f"质检 {MAX_QC_ROUNDS} 轮未过（产物 {state.get('n_rows')} 行）"
                             "→ 降级继续"], "log": ["[degraded] 质检超限，标记降级继续"]}

    def node_finish(state: InspectState) -> dict[str, Any]:
        assert_state_clean(state)
        steps = list(state.get("steps") or [])
        failed = [s for s in steps if s.get("outcome") in ("rejected", "aborted")]
        ok = not failed
        arts: list[str] = []
        for s in steps:
            arts.extend(s.get("artifacts") or [])
        note = (f"[finish] {'ok' if ok else 'FAIL'}  步数 {len(steps)}"
                + (f"  产物：{'、'.join(arts[:4])}" if arts else ""))
        return {"ok": ok, "log": [note]}

    # ---- 组装（节点由契约生成）------------------------------------------

    g = StateGraph(InspectState)
    g.add_node("tick", node_tick)
    for i in range(len(tools)):
        g.add_node(nid(i), make_tool_node(i))
    for name, fn in (("qc", node_qc), ("gate", node_gate),
                     ("mark_degraded", node_mark_degraded), ("finish", node_finish)):
        g.add_node(name, fn)

    g.add_edge(START, "tick")
    g.add_edge("tick", first)
    g.add_edge(first, "qc")
    g.add_conditional_edges("qc", route_qc,
                            {"retry": first, "degraded": "mark_degraded",
                             "next": "gate" if ctx.use_gate else after_qc})
    g.add_edge("gate", after_qc)
    g.add_edge("mark_degraded", after_qc)
    # 其余工具按契约顺序串行
    for i in range(1, len(tools)):
        g.add_edge(nid(i), nid(i + 1) if i + 1 < len(tools) else "finish")
    g.add_edge("finish", END)
    return g


def make_checkpointer(db_path: Path | str | None = None):
    """SQLite checkpointer：**中断跨进程存活**（实测验证过），轨迹可回放。"""
    from langgraph.checkpoint.sqlite import SqliteSaver
    path = Path(db_path) if db_path else Path(GRAPH_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(con), con


def compile_graph(ctx: Ctx, *, db_path: Path | str | None = None):
    checkpointer, con = make_checkpointer(db_path)
    app = build_graph(ctx).compile(checkpointer=checkpointer)
    return app, con


# ================================================================ 运行

def run_graph(*, task_id: str = "hotspot_track", dry_run: bool = False,
              force: bool = False, use_gate: bool = False,
              thread: str = DEFAULT_THREAD,
              params: dict[str, dict[str, Any]] | None = None,
              trace_path: Path | str | None = None,
              db_path: Path | str | None = None,
              con: sqlite3.Connection | None = None,
              now: datetime | None = None,
              call_hook: Callable[[str, dict], dict] | None = None,
              resume: bool = False) -> dict[str, Any]:
    """跑一次巡检图（或恢复被中断的那次）。"""
    contract = tc.get(task_id)
    problems = tc.validate(contract)
    if problems:
        return {"ok": False, "refused": True,
                "reason": "契约不合法，拒绝建图：" + "；".join(problems)}
    if contract["status"] == "blank":
        return {"ok": False, "refused": True,
                "reason": f"契约已立但实现空白（{'、'.join(contract['tools'])}）——图不假装能跑"}

    own_con = None
    if con is None:
        own_con = harness.connect()
        con = own_con
    try:
        ctx = Ctx(task_id=task_id, contract=contract, con=con,
                  before=harness.snapshot_counts(contract), params=params or {},
                  dry_run=dry_run, force=force, trace_path=trace_path,
                  call_hook=call_hook, use_gate=use_gate, now=now)
        app, ck_con = compile_graph(ctx, db_path=db_path)
        cfg = {"configurable": {"thread_id": thread}}
        try:
            if resume:
                out = app.invoke(Command(resume=True), cfg)
            else:
                out = app.invoke({}, cfg)
        finally:
            ck_con.close()

        interrupted = "__interrupt__" in out
        result = {
            "ok": bool(out.get("ok")) and not interrupted,
            "interrupted": interrupted,
            "thread": thread,
            "run_id": ctx.run_id,
            "n_steps": len(out.get("steps") or []),
            "n_rows": out.get("n_rows", 0),
            "qc_rounds": out.get("qc_rounds", 0),
            "artifacts": sorted({a for s in (out.get("steps") or [])
                                 for a in (s.get("artifacts") or [])}),
            "degraded": out.get("degraded") or [],
            "log": out.get("log") or [],
            "state": {k: v for k, v in out.items() if k != "__interrupt__"},
        }
        if interrupted:
            result["interrupt"] = out["__interrupt__"][0].value
        harness.trace({"task_id": task_id, "kind": "graph_run",
                       "thread": thread, "ok": result["ok"],
                       "interrupted": interrupted, "n_steps": result["n_steps"],
                       "n_rows": result["n_rows"],
                       "qc_rounds": result["qc_rounds"],
                       "degraded": result["degraded"]}, ctx.trace_path)
        return result
    finally:
        if own_con is not None:
            own_con.close()


def mermaid(*, task_id: str = "hotspot_track", use_gate: bool = False) -> str:
    """导出图结构（mermaid）—— 「有图」这件事的可核对证据。"""
    contract = tc.get(task_id)
    ctx = Ctx(task_id=task_id, contract=contract, con=harness.connect(), use_gate=use_gate)
    app, ck = compile_graph(ctx)
    ck.close()
    try:
        return app.get_graph().draw_mermaid()
    finally:
        ctx.con.close()


def status(*, db_path: Path | str | None = None) -> str:
    """看 checkpoint 库里存了哪些 thread（中断是否还等着恢复）。"""
    path = Path(db_path) if db_path else Path(GRAPH_DB)
    lines = ["巡检图状态（LangGraph + SQLite checkpointer）", ""]
    lines.append(f"langgraph：{'已装 ✅' if HAS_LANGGRAPH else '未装 ❌（pip install langgraph）'}")
    lines.append(f"checkpoint 库：{path}")
    if not path.exists():
        lines.append("  （还没跑过图）")
        return "\n".join(lines)
    try:
        con = sqlite3.connect(str(path))
        rows = con.execute(
            "SELECT thread_id, COUNT(*) AS n, MAX(checkpoint_id) FROM checkpoints"
            " GROUP BY thread_id").fetchall()
        con.close()
    except sqlite3.Error as e:
        lines.append(f"  读取失败：{e}")
        return "\n".join(lines)
    lines.append(f"  已存 thread {len(rows)} 个：")
    for tid, n, last in rows:
        lines.append(f"    {tid}  检查点 {n} 个  最新 {last}")
    return "\n".join(lines)


# ================================================================ CLI

def main(argv: list[str] | None = None) -> int:
    harness.suppress_console()            # ★ 自有控制台就自己隐藏
    harness.ensure_std_streams()          # ★ pythonw 无控制台时把 print 转日志
    ap = argparse.ArgumentParser(description="Agent 巡检图（LangGraph）")
    ap.add_argument("--task", default="hotspot_track", help="跑哪个任务的图")
    ap.add_argument("--run", action="store_true", help="跑一次图")
    ap.add_argument("--resume", action="store_true", help="恢复被 interrupt 停住的那次")
    ap.add_argument("--graph", action="store_true", help="打印图结构（mermaid）")
    ap.add_argument("--status", action="store_true", help="看 checkpoint 库")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--human-gate", action="store_true",
                    help="强制开启人工确认点（契约 human=False 时的演示/测试用）")
    ap.add_argument("--thread", default=DEFAULT_THREAD)
    args = ap.parse_args(argv)

    if not HAS_LANGGRAPH:
        print("❌ langgraph 未安装：pip install langgraph langgraph-checkpoint-sqlite")
        return 2
    if args.graph:
        print(mermaid(task_id=args.task, use_gate=args.human_gate))
        return 0
    if args.status:
        print(status())
        return 0
    if args.run or args.resume:
        r = run_graph(task_id=args.task, dry_run=args.dry_run, force=args.force,
                      use_gate=args.human_gate, thread=args.thread,
                      resume=args.resume)
        if r.get("refused"):
            print(f"⛔ {r['reason']}")
            return 3
        print(f"[graph:{args.task}] thread={r['thread']}"
              f"  {'中断待批' if r['interrupted'] else ('ok' if r['ok'] else 'FAIL')}")
        for line in r["log"]:
            print(f"  {line}")
        if r["interrupted"]:
            print(f"  ⏸ interrupt：{r['interrupt']}")
            print(f"  → 恢复：python agent_graph.py --run --resume --thread {r['thread']}")
        print(f"  步数 {r['n_steps']} ｜ 质检 {r['qc_rounds']} 轮 ｜ 产物 {r['n_rows']} 行")
        for d in r["degraded"]:
            print(f"  ⚠ {d}")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
