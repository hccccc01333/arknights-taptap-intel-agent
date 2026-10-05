#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""runtime/scheduler.py — 调度器：时钟驱动的六层主链。

定位：把「全天情报运营」从手动跑变成自动跑。
  · 探测层（trend_intelligence）：L1 采集到期源 → L2 加工 → L3 聚类评分，15 分钟一轮
    （间隔承自实测推导：特征时间 24min÷2=12min 下界，留余量；**反爬上界未实测**）
  · 下游链（intelligence_run → memory_ingest → ops_alerts）：L4 推理 → L5 记忆回填
    → L6 告警/工作台。各任务自带幂等（窗口桶 / input_hash / 确定性主键），重跑安全。

★ 2026-10-03 重接线：旧的「探测→深采自适应」循环（topic_state/ferment_judge）随
  旧舆情平台存量移除 —— 深采决策的输入信号已不存在，与其留一套跑不动的机器，
  不如如实收窄为本链，等新链的状态信号积累后再设计下一版自适应（roadmap）。

用法：
  python runtime/scheduler.py --once            # 跑一轮就退出（推荐，配 cron/计划任务）
  python runtime/scheduler.py --once --dry-run  # 只打印将要执行什么，不真跑
  python runtime/scheduler.py --status          # 采样健康度（频率校准的依据）
  python runtime/scheduler.py --loop            # 常驻循环（仅本地调试用）
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 同目录模块导入引导（直接跑时脚本目录已在 sys.path；测试用 importlib 加载时不在）
LAB = Path(__file__).resolve().parent
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

import harness  # noqa: E402  （执行层：子进程出口 harness.exec_command，不自己起进程）

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]

GRAPH = LAB / "agent_graph.py"      # ★ 巡检图（LangGraph）：时钟驱动的**任务**（探测链只此一步）
DEFAULT_TASK = "trend_intelligence"
DOWNSTREAM_TASKS = ("intelligence_run", "memory_ingest", "ops_alerts")

SCHED_DB = LAB / "state" / "scheduler.sqlite3"
REPORT_DIR = LAB / "reports"

# ---- 配置（可用环境变量覆盖；阈值全部标注是否已校准）----
# 探测频率：**已推导**（实测特征时间 24min ÷ 2 = 12min 下界；15min 留余量）
# ⚠️ 上界（反爬限流）尚未实测，跑几天后回看 --status 再校准
PROBE_INTERVAL_MIN = 15
# 单轮外部脚本超时（采集含随机 sleep，给宽一点）
SCRIPT_TIMEOUT_SEC = 900

SCHEMA_VERSION = "1.1"

# 子进程用**同一个解释器**（sys.executable）跑，所以调度器所在环境必须有采集依赖。
# 踩过的坑：用只装了标准库的解释器跑 → 爬虫 ImportError → 只看到「退出码 1」，很难查。
# 所以下面做预检，快速失败并给出可执行的建议。
REQUIRED_CHILD_DEPS = ("requests",)
VENV_HINT = "当前解释器：" + sys.executable + " —— 请用装了依赖的解释器运行此模块"


def _has_module(name: str) -> bool:
    """安全地判断模块是否可用。

    ⚠️ 不能直接用 `importlib.util.find_spec`：当某个模块被塞进 `sys.modules`
    但 `__spec__` 为 None（测试桩常见做法）时，find_spec 会抛
    `ValueError: xxx.__spec__ is None`。预检是"守门"代码，不该被这种事绊倒。
    """
    import importlib.util as _u
    try:
        return _u.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return name in sys.modules


def preflight() -> dict[str, Any]:
    """预检运行环境：子进程所需的依赖在不在。"""
    missing = [m for m in REQUIRED_CHILD_DEPS if not _has_module(m)]
    return {
        "ok": not missing,
        "missing": missing,
        "interpreter": sys.executable,
        "hint": (f"当前解释器缺依赖：{', '.join(missing)}。"
                 f"请用装了依赖的解释器运行本调度器，例如：\n    \"{VENV_HINT}\" "
                 f"runtime/scheduler.py --once") if missing else None,
    }


# ---------------------------------------------------------------------------
# 状态库：探测日志
#
# 为什么日志本身是「记忆」而不是「缓存」：
# 它是**校准频率的唯一数据来源** —— 删了就得重新跑几天才能再校准
# ---------------------------------------------------------------------------

def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """打开状态库。

    ⚠️ `db_path` 默认为 None 而非直接写 `=SCHED_DB`：**默认参数在函数定义时就绑定**，
    直接写会让 `mock.patch.object(sch, "SCHED_DB", ...)` 失效（测试改不动路径，
    还会污染真实状态库）。在调用时解析才能被替换。
    """
    db_path = db_path or SCHED_DB
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS probe_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            probed_at   TEXT NOT NULL,
            ok          INTEGER NOT NULL,      -- 1 成功 / 0 失败
            task        TEXT,                  -- 本轮跑的探测任务
            elapsed_sec REAL,
            error       TEXT
        )
    """)
    con.commit()
    return con


def last_probe_at(con: sqlite3.Connection, *, ok_only: bool = True) -> datetime | None:
    sql = "SELECT probed_at FROM probe_log"
    if ok_only:
        sql += " WHERE ok=1"
    sql += " ORDER BY id DESC LIMIT 1"
    row = con.execute(sql).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["probed_at"])
    except ValueError:
        return None


def due_for_probe(con: sqlite3.Connection, now: datetime,
                  interval_min: int = PROBE_INTERVAL_MIN) -> dict[str, Any]:
    """该不该探测。**这让 cron 可以每分钟调而调度器自己控 15min 节流。**"""
    last = last_probe_at(con)
    if last is None:
        return {"due": True, "reason": "首次探测", "last": None}
    elapsed = (now - last).total_seconds() / 60
    if elapsed >= interval_min:
        return {"due": True, "reason": f"距上次 {elapsed:.1f} 分钟（≥{interval_min}）",
                "last": last.isoformat(timespec="seconds")}
    return {"due": False, "reason": f"距上次仅 {elapsed:.1f} 分钟（<{interval_min}）",
            "last": last.isoformat(timespec="seconds")}


def log_probe(con: sqlite3.Connection, *, ok: bool, task: str = DEFAULT_TASK,
              elapsed: float = 0.0, error: str = "") -> None:
    con.execute(
        "INSERT INTO probe_log (probed_at,ok,task,elapsed_sec,error)"
        " VALUES (?,?,?,?,?)",
        (datetime.now(TZ).isoformat(timespec="seconds"), 1 if ok else 0,
         task, round(elapsed, 2), error or None),
    )
    con.commit()


# ---------------------------------------------------------------------------
# 执行外部步骤（编排者角色：调用现有入口，不重写逻辑）
# ---------------------------------------------------------------------------

def run_step(name: str, argv: list[str], *, dry_run: bool = False,
             timeout: int = SCRIPT_TIMEOUT_SEC) -> dict[str, Any]:
    """跑一个外部步骤。失败不抛异常——记录并降级（沿用项目降级纪律）。

    ★ 子进程出口**统一走 `harness.exec_command`** ——
      编码注入、超时、平台细节（CREATE_NO_WINDOW / 无窗口）都在 harness 里实现一次，
      这里只负责「报告降级」。**编排者不该自己起进程。**
    """
    printable = " ".join(str(x) for x in argv)
    if dry_run:
        print(f"  [dry-run] {name}: {printable}")
        return {"ok": True, "dry_run": True, "cmd": printable, "stdout": "", "stderr": ""}
    print(f"  → {name}: {printable}")

    r = harness.exec_command([sys.executable, *argv], timeout=timeout)
    if not r["ok"]:
        return {"ok": False, "cmd": printable,
                "error": r["error"] or f"退出码 {r['exit_code']}",
                "stderr": (r["stderr"] or "")[-400:], "elapsed": r["elapsed"]}
    return {"ok": True, "cmd": printable,
            "stdout": (r["stdout"] or "")[-400:],
            "stderr": (r["stderr"] or "")[-200:], "elapsed": r["elapsed"]}


def probe_steps(*, dry_run: bool = False,
                task: str = DEFAULT_TASK) -> list[dict[str, Any]]:
    """探测链：**一步 —— 跑巡检图（LangGraph/stdlib 等价执行器）**。

    ★ 时钟驱动的是**任务**，不是脚本。
      任务内部的流转由 `agent_graph.py` 负责
      （节点=契约工具链、质检有界环、checkpointer 可恢复）。
    thread_id 用 15min 桶：同一窗口重复唤起会命中图/harness 的幂等，不会重复跑。
    """
    thread = "probe-" + datetime.now(TZ).strftime("%Y%m%dT%H%M")
    return [
        run_step(f"巡检图（task={task}，thread={thread}）",
                 [str(GRAPH), "--task", task, "--run", "--thread", thread],
                 dry_run=dry_run),
    ]


def downstream_steps(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """下游链：L4 推理 → L5 记忆回填 → L6 告警/工作台。

    每个任务走 harness.run_task（契约校验/预算/幂等/失败分派齐全）；
    单个任务失败**不杀整轮**（degrade 纪律），失败信息进返回值由 run_once 汇总。
    """
    out: list[dict[str, Any]] = []
    for tid in DOWNSTREAM_TASKS:
        print(f"  → 下游任务 {tid}")
        r = harness.run_task(tid, dry_run=dry_run, use_lock=not dry_run)
        out.append({"task_id": tid, "ok": r.get("ok", False),
                    "refused": r.get("refused", False),
                    "skipped": r.get("skipped", False),
                    "reason": r.get("reason", ""),
                    "n_steps": r.get("n_steps", 0),
                    "degraded": r.get("degraded", [])})
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_once(*, dry_run: bool = False, skip_probe_if_not_due: bool = True,
             interval_min: int = PROBE_INTERVAL_MIN,
             run_downstream: bool = True) -> dict[str, Any]:
    now = datetime.now(TZ)
    out: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "dry_run": dry_run}

    # ---- 0. 环境预检（在开库之前做：失败就不碰 DB，避免连接泄漏）----
    # 干跑不检查——它本来就不调子进程。
    pf = preflight()
    out["preflight"] = pf
    if not dry_run and not pf["ok"]:
        print(f"[error] {pf['hint']}", file=sys.stderr)
        out["ok"] = False
        return out

    con = connect()
    try:
        # ---- 1. 探测（带节流）----
        due = due_for_probe(con, now, interval_min)
        out["probe_due"] = due
        probe_ok = True
        if skip_probe_if_not_due and not due["due"]:
            print(f"[skip] 探测未到时间：{due['reason']}")
            out["probe"] = {"ok": True, "skipped": True, "reason": due["reason"]}
        else:
            print(f"[probe] {due['reason']}")
            t0 = time.time()
            results = probe_steps(dry_run=dry_run)
            probe_ok = all(r.get("ok") for r in results)
            if not dry_run:
                error = "" if probe_ok else str(
                    [r.get("error") for r in results if not r.get("ok")])
                log_probe(con, ok=probe_ok, task=DEFAULT_TASK,
                          elapsed=time.time() - t0, error=error)
            out["probe"] = {"ok": probe_ok, "elapsed_sec": round(time.time() - t0, 1)}

        # ---- 2. 下游链（L4 → L5 → L6；各自幂等，失败不杀整轮）----
        if run_downstream:
            chain = downstream_steps(dry_run=dry_run)
            out["chain"] = chain
            for c in chain:
                flag = ("refused" if c.get("refused") else
                        "skipped" if c.get("skipped") else
                        ("ok" if c.get("ok") else "FAIL"))
                print(f"  [{flag}] {c['task_id']}"
                      + (f"：{c['reason'][:80]}" if c.get("reason") else ""))

        con.close()
        out["ok"] = probe_ok and all(c.get("ok") for c in out.get("chain", [])
                                     if not c.get("refused") and not c.get("skipped"))
        return out
    finally:
        try:
            con.close()
        except Exception:
            pass


def status_report(limit: int = 20) -> str:
    """采样健康度：**这就是校准频率要看的报告。**"""
    con = connect()
    rows = con.execute(
        "SELECT probed_at,ok,task,elapsed_sec,error"
        " FROM probe_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    n_total = con.execute("SELECT COUNT(*) c FROM probe_log").fetchone()["c"]
    con.close()

    L = ["# 采样健康度（频率校准依据）", ""]
    L += [f"- 累计探测 {n_total} 次；下面展示最近 {len(rows)} 次", ""]
    if not rows:
        L += ["- **尚无探测记录** —— 跑 `scheduler.py --once` 开始累积。", ""]
    else:
        L += ["| 时间 | 结果 | 任务 | 耗时 | 错误 |",
              "|------|------|------|------|------|"]
        for r in rows:
            L.append(f"| {r['probed_at'][5:16]} | {'成功' if r['ok'] else '失败'} "
                     f"| {r['task'] or '-'} | {r['elapsed_sec']}s "
                     f"| {(r['error'] or '-')[:40]} |")
        L.append("")
    L += ["## 频率校准", "",
          "- 探测间隔 15 分钟**已推导**（实测特征时间 24min ÷ 2 = 12min 下界，留余量）。",
          "- ⚠️ **每轮变化量的量化校准待重建**：旧机制依赖已移除的话题状态机，",
          "  在新链状态信号积累起来之前，如实标注**待重建** —— 不用旧数据假装连续。",
          "- 反爬上界**未校准**：若失败记录（退出码/超时）增多，说明可能被限流。",
          "- 下游任务（L4/L5/L6）频率**未校准**（各契约 schedule 注有说明）。",
          "", "## 边界", "",
          "- 下游任务每轮跟随执行，靠各自幂等窗口去重；失败不杀整轮（降级纪律）。",
          "", "*报告结束*", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    # ★ 计划任务用 python.exe 启动会弹控制台 → 若该控制台属于本进程就自己隐藏
    #   （不需要改系统设置；彻底不闪仍需让任务改跑 pythonw.exe）
    harness.suppress_console()
    # ★ pythonw 无控制台时 sys.stdout 为 None，任何 print 都会抛 AttributeError。兜底在 harness 里。
    harness.ensure_std_streams()
    ap = argparse.ArgumentParser(description="调度器：六层主链（L1→L3 探测 + L4→L6 下游）")
    ap.add_argument("--once", action="store_true", help="跑一轮就退出（推荐，配 cron）")
    ap.add_argument("--loop", action="store_true", help="常驻循环（仅本地调试）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行什么，不真跑")
    ap.add_argument("--status", action="store_true", help="输出采样健康度报告")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--interval", type=int, default=PROBE_INTERVAL_MIN,
                    help=f"探测间隔分钟（默认 {PROBE_INTERVAL_MIN}）")
    ap.add_argument("--force-probe", action="store_true", help="忽略节流，强制探测")
    ap.add_argument("--no-chain", action="store_true", help="只跑探测，不跑下游任务")
    ap.add_argument("--loop-seconds", type=int, default=60, help="--loop 的检查间隔")
    args = ap.parse_args(argv)

    if args.status:
        rep = status_report()
        if args.json:
            con = connect()
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM probe_log ORDER BY id DESC LIMIT 50").fetchall()]
            con.close()
            print(json.dumps({"schema_version": SCHEMA_VERSION, "probe_log": rows},
                             ensure_ascii=False, indent=2))
        else:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            (REPORT_DIR / "scheduler_status_latest.md").write_text(rep, encoding="utf-8")
            print(rep)
            print(f"\n（已写入 {REPORT_DIR / 'scheduler_status_latest.md'}）")
        return 0

    if args.loop:
        print(f"[loop] 每 {args.loop_seconds}s 检查一次，探测间隔 {args.interval} 分钟。Ctrl+C 退出。")
        try:
            while True:
                r = run_once(dry_run=args.dry_run, interval_min=args.interval,
                             skip_probe_if_not_due=not args.force_probe,
                             run_downstream=not args.no_chain)
                if args.json:
                    print(json.dumps(r, ensure_ascii=False))
                time.sleep(args.loop_seconds)
        except KeyboardInterrupt:
            print("\n[loop] 已停止")
        return 0

    # 默认 / --once
    r = run_once(dry_run=args.dry_run, interval_min=args.interval,
                 skip_probe_if_not_due=not args.force_probe,
                 run_downstream=not args.no_chain)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    print(f"\n[done] ok={r['ok']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
