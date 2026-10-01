#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""runtime/scheduler.py — 采样调度器（探测层 + 深采层）。

定位：把「全天热点追踪」从手动跑变成自动跑，并且**自己收集校准自己的数据**。

设计依据（docs/热点追踪设计.md §5.2，2026-09-30 实测推导）：
  · 探测层：固定 15 分钟（实测：39% 评论在帖子发布后 1 小时内出现，
    特征时间 p25=24min → 间隔 ≤ 12min 才能看见过程；15min 留余量）
  · 深采层：**按状态 + 可发酵度自适应** —— 自适应用可发酵度而不是当前热度，
    因为按当前热度会漏掉冷启动（观察者悖论）
  · 成本不是约束：探测仅 1 请求/次，15min 才 96 次/天。真约束是反爬（上界，待实测）

两条链：
  探测 = 拉热榜（1 请求）→ 生成 facts → 记录采样（判状态迁移）
  深采 = 对选中的少数话题拉帖 + 评论（十余请求/话题）

用法：
  python runtime/scheduler.py --once            # 跑一轮就退出（推荐，配 cron/计划任务）
  python runtime/scheduler.py --once --dry-run  # 只打印将要执行什么，不真跑
  python runtime/scheduler.py --status          # 采样健康度（频率校准的依据）
  python runtime/scheduler.py --loop            # 常驻循环（仅本地调试用）
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
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

CRAWLER = ROOT / "L1_data_source/collectors/taptap" / "crawl_taptap_discovery.py"
PLATFORM = LAB / "platform_insight.py"
TRACKER = LAB / "topic_tracker.py"
EVENTS = LAB / "events.py"          # ★ 触发链：迁移 → 事件 → 分发下游
FEATURES = LAB / "features.py"      # ★ 特征落盘：F1–F7 先算不判，为 ML 攒数据
GRAPH = LAB / "agent_graph.py"      # ★ 巡检图（LangGraph）：时钟驱动的**任务**（探测链只此一步）

SCHED_DB = LAB / "state" / "scheduler.sqlite3"
REPORT_DIR = LAB / "reports"

# ---- 配置（可用环境变量覆盖；阈值全部标注是否已校准）----
# 探测频率：**已推导**（实测特征时间 24min ÷ 2 = 12min 下界；15min 留余量）
# ⚠️ 上界（反爬限流）尚未实测，跑几天后回看 --status 再校准
PROBE_INTERVAL_MIN = 15
# 深采：每话题拉多少评论、单轮最多采几个话题、同话题冷却多久
DRILL_COMMENT_LIMIT = 20
DRILL_MAX_TOPICS = 5
DRILL_COOLDOWN_MIN = 60
# 单轮外部脚本超时（爬虫含随机 sleep，给宽一点）
SCRIPT_TIMEOUT_SEC = 600

SCHEMA_VERSION = "1.0"

# 子进程用**同一个解释器**（sys.executable）跑，所以调度器所在环境必须有爬虫依赖。
# 踩过的坑：用只装了标准库的解释器跑 → 爬虫 ImportError → 只看到「退出码 1」，很难查。
# 所以下面做预检，快速失败并给出可执行的建议。
REQUIRED_CHILD_DEPS = ("requests",)
VENV_HINT = r"C:\Users\Hzz\.workbuddy\binaries\python\envs\default\Scripts\python.exe"


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
# 状态库：探测日志 + 深采日志
#
# 为什么日志本身是「记忆」而不是「缓存」：
# 它是**校准频率的唯一数据来源** —— 删了就得重新跑几天才能再校准（见能力形态归类）
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
            n_topics    INTEGER,               -- 本轮探测到多少话题
            n_changed   INTEGER,               -- ★ 有多少话题的热度值变了（校准频率的关键）
            max_delta   INTEGER,               -- 最大变化幅度
            elapsed_sec REAL,
            error       TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS drill_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_key  TEXT NOT NULL,
            title      TEXT,
            reason     TEXT NOT NULL,          -- 为什么选它（状态 / 可发酵度）
            drilled_at TEXT NOT NULL
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


def last_drill_at(con: sqlite3.Connection, topic_key: str) -> datetime | None:
    row = con.execute(
        "SELECT drilled_at FROM drill_log WHERE topic_key=? ORDER BY id DESC LIMIT 1",
        (topic_key,),
    ).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["drilled_at"])
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


def log_probe(con: sqlite3.Connection, *, ok: bool, n_topics: int | None,
              n_changed: int | None, max_delta: int | None,
              elapsed: float, error: str = "") -> None:
    con.execute(
        "INSERT INTO probe_log (probed_at,ok,n_topics,n_changed,max_delta,elapsed_sec,error)"
        " VALUES (?,?,?,?,?,?,?)",
        (datetime.now(TZ).isoformat(timespec="seconds"), 1 if ok else 0,
         n_topics, n_changed, max_delta, round(elapsed, 2), error or None),
    )
    con.commit()


def log_drill(con: sqlite3.Connection, topic_key: str, title: str, reason: str) -> None:
    con.execute(
        "INSERT INTO drill_log (topic_key,title,reason,drilled_at) VALUES (?,?,?,?)",
        (topic_key, title, reason, datetime.now(TZ).isoformat(timespec="seconds")),
    )
    con.commit()


# ---------------------------------------------------------------------------
# 自适应决策：这轮深采哪些话题
# ---------------------------------------------------------------------------

def decide_drill(con: sqlite3.Connection, topic_state: dict, ferment: dict,
                 now: datetime, *, max_topics: int = DRILL_MAX_TOPICS,
                 cooldown_min: int = DRILL_COOLDOWN_MIN) -> dict[str, Any]:
    """决定深采名单。

    三个通道（按优先级）：
      A 升温/爆发 —— 状态机判出的，最该抓
      B 冒头 + 可发酵度 act —— **避开观察者悖论的关键**：
        冷话题只要「有讨论动机」照样深采，不按当前热度筛
      C 其余冒头 —— 低优先，只在名额剩余时补位
    过滤：冷却期内（刚采过）的一律跳过。
    """
    by_state = (topic_state or {}).get("by_state") or {}
    ferment_by_hid = {}
    for t in ((ferment or {}).get("topics") or []):
        hid = str(t.get("hashtag_id") or "")
        if hid:
            ferment_by_hid[hid] = t

    picked: list[dict[str, Any]] = []
    seen_keys: set[str] = set()       # 按 topic_key 去重
    seen_topics: set[str] = set()     # ★ 按「话题身份」去重（见下）
    n_cooldown = 0

    def consider(item: dict, channel: str, reason: str) -> None:
        nonlocal n_cooldown
        key = item.get("topic_key")
        if not key or key in seen_keys:
            return
        hid = str(item.get("hashtag_id") or "")
        # ★ 同一话题的浏览量/互动量是两条 topic_key（metric_kind 不同），
        #   但**深采是按 hashtag_id 发请求的** —— 不去重就会对同一个话题重复采一遍。
        #   所以这里按 hashtag_id（无则退回 title）做第二层去重。
        ident = hid or (item.get("title") or key)
        if ident in seen_topics:
            return
        last = last_drill_at(con, key)
        if last is not None and (now - last).total_seconds() / 60 < cooldown_min:
            n_cooldown += 1
            return
        f = ferment_by_hid.get(hid) or {}
        seen_keys.add(key)
        seen_topics.add(ident)
        picked.append({
            "topic_key": key, "title": item.get("title"),
            "hashtag_id": hid, "state": item.get("state"),
            "ferment_score": f.get("ferment_score"), "verdict": f.get("verdict"),
            "channel": channel, "reason": reason,
        })

    # A 通道：升温 / 爆发
    for st in ("爆发", "升温"):
        for it in (by_state.get(st) or []):
            consider(it, "A", f"状态={st}")

    # B 通道：冒头 + 可发酵度 act
    for it in (by_state.get("冒头") or []):
        hid = str(it.get("hashtag_id") or "")
        f = ferment_by_hid.get(hid) or {}
        if f.get("verdict") == "act":
            consider(it, "B", f"冒头但可发酵度 act（{f.get('ferment_score')}）")

    # C 通道：其余冒头（补位）
    for it in (by_state.get("冒头") or []):
        if len(picked) >= max_topics:
            break
        consider(it, "C", "冒头补位")

    return {
        "n_picked": len(picked),
        "picked": picked[:max_topics],
        "n_skipped_cooldown": n_cooldown,
        "n_unique_topics": len(seen_topics),
    }


# ---------------------------------------------------------------------------
# 执行外部脚本（编排者角色：调用现有脚本，不重写逻辑）
# ---------------------------------------------------------------------------

def run_step(name: str, argv: list[str], *, dry_run: bool = False,
             timeout: int = SCRIPT_TIMEOUT_SEC) -> dict[str, Any]:
    """跑一个外部步骤。失败不抛异常——记录并降级（沿用项目降级纪律）。

    ★ 2026-10-01：子进程出口**统一走 `harness.exec_command`** ——
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


def probe_steps(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """探测链：**一步 —— 跑巡检图（LangGraph）**。

    ★ 2026-10-01 改：时钟驱动的是**任务**，不是脚本。
      之前这里是 5 步、由调度器自己拼脚本：
          拉热榜 → 生成 facts → 记录采样 → 触发链 → 特征落盘
      现在收成 1 步 —— 交给 `agent_graph.py`（LangGraph 巡检图）：
          图里的节点体 = harness 的一步（校验/预算/幂等/产出契约判定/失败分派/轨迹）
          图自己管流转：质检不过重采一次（有界环）、条件边、checkpointer 可恢复
      **调度器只管"该不该跑"与采样健康度**（`log_probe`）与深采决策，不再管任务内部怎么跑。

    thread_id 用 15min 桶：同一窗口重复唤起会命中图/harness 的幂等，不会重复采样。
    """
    thread = "probe-" + datetime.now(TZ).strftime("%Y%m%dT%H%M")
    return [
        run_step(f"巡检图（LangGraph，thread={thread}）",
                 [str(GRAPH), "--run", "--thread", thread],
                 dry_run=dry_run),
    ]


def drill_steps(topic_ids: list[str], *, comment_limit: int = DRILL_COMMENT_LIMIT,
                dry_run: bool = False) -> list[dict[str, Any]]:
    """深采链：对指定话题拉帖 + 评论 → 刷新 facts。"""
    if not topic_ids:
        return []
    return [
        run_step(f"深采 {len(topic_ids)} 个话题（帖+评论）",
                 [str(CRAWLER), "--source", "hashtag-feed",
                  "--hashtag-ids", ",".join(topic_ids),
                  "--comment-limit", str(comment_limit)],
                 dry_run=dry_run),
        run_step("刷新平台 facts", [str(PLATFORM)], dry_run=dry_run),
    ]


# ---------------------------------------------------------------------------
# 采样对比：给「频率校准」产出数字
# ---------------------------------------------------------------------------

def diff_probe(prev: dict[str, int], curr: dict[str, int]) -> dict[str, int]:
    """比较两轮探测的热度值，回答「15 分钟里到底变了多少」。

    这是**校准频率的核心数字**：
      · n_changed 长期为 0 → 说明采得太密，可以放宽
      · n_changed 每次都很高 → 说明变化快，15min 是对的甚至要更快
    """
    changed = 0
    max_delta = 0
    for k, v in curr.items():
        if k in prev and prev[k] != v:
            changed += 1
            max_delta = max(max_delta, abs(v - prev[k]))
    return {"n_common": len(set(prev) & set(curr)), "n_changed": changed,
            "max_delta": max_delta}


def read_heat_map() -> dict[str, int]:
    """从 topic_state 快照读「话题 → 当前热度」。"""
    p = LAB / "outputs" / "topic_state.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, int] = {}
    for items in (d.get("by_state") or {}).values():
        for it in (items or []):
            k = it.get("topic_key")
            if k and isinstance(it.get("last_metric"), int):
                out[k] = it["last_metric"]
    return out


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_once(*, dry_run: bool = False, skip_probe_if_not_due: bool = True,
             interval_min: int = PROBE_INTERVAL_MIN,
             max_topics: int = DRILL_MAX_TOPICS) -> dict[str, Any]:
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
        return _run_once_inner(con, out, now, dry_run=dry_run,
                               skip_probe_if_not_due=skip_probe_if_not_due,
                               interval_min=interval_min, max_topics=max_topics)
    finally:
        con.close()


def _run_once_inner(con: sqlite3.Connection, out: dict[str, Any], now: datetime, *,
                    dry_run: bool, skip_probe_if_not_due: bool,
                    interval_min: int, max_topics: int) -> dict[str, Any]:
    # ---- 1. 探测（带节流）----
    due = due_for_probe(con, now, interval_min)
    out["probe_due"] = due
    if skip_probe_if_not_due and not due["due"]:
        print(f"[skip] 探测未到时间：{due['reason']}")
    else:
        print(f"[probe] {due['reason']}")
        t0 = time.time()
        before = read_heat_map()
        results = probe_steps(dry_run=dry_run)
        ok = all(r.get("ok") for r in results)
        if not dry_run:
            after = read_heat_map()
            d = diff_probe(before, after)
            log_probe(con, ok=ok, n_topics=len(after),
                      n_changed=d["n_changed"], max_delta=d["max_delta"],
                      elapsed=time.time() - t0,
                      error="" if ok else str([r.get("error") for r in results if not r.get("ok")]))
            out["probe"] = {"ok": ok, "n_topics": len(after), **d,
                            "elapsed_sec": round(time.time() - t0, 1)}
        else:
            out["probe"] = {"ok": True, "dry_run": True}

    # ---- 2. 决定深采名单（自适应）----
    topic_state = load_json(LAB / "outputs" / "topic_state.json")
    ferment = load_json(LAB / "outputs" / "ferment_judge.json")
    plan = decide_drill(con, topic_state, ferment, now, max_topics=max_topics)
    out["drill_plan"] = plan
    print(f"[drill] 选中 {plan['n_picked']} 个话题"
          + (f"：{', '.join(p['title'] or p['topic_key'] for p in plan['picked'])}"
             if plan["picked"] else "（无可采话题）"))
    for p in plan["picked"]:
        print(f"        [{p['channel']}] {p['title']} — {p['reason']}")

    # ---- 3. 执行深采 ----
    ids = [p["hashtag_id"] for p in plan["picked"] if p.get("hashtag_id")]
    if ids:
        res = drill_steps(ids, dry_run=dry_run)
        ok = all(r.get("ok") for r in res)
        out["drill"] = {"ok": ok, "n_topics": len(ids)}
        if not dry_run and ok:
            for p in plan["picked"]:
                log_drill(con, p["topic_key"], p.get("title") or "", p["reason"])
    else:
        out["drill"] = {"ok": True, "n_topics": 0, "skipped": "无选中话题"}

    con.close()
    out["ok"] = bool(out.get("probe", {}).get("ok", True)) and bool(out.get("drill", {}).get("ok", True))
    return out


def status_report(limit: int = 20) -> str:
    """采样健康度：**这就是校准频率要看的报告。**"""
    con = connect()
    rows = con.execute(
        "SELECT probed_at,ok,n_topics,n_changed,max_delta,elapsed_sec,error"
        " FROM probe_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    drills = con.execute(
        "SELECT topic_key,title,reason,drilled_at FROM drill_log"
        " ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    n_total = con.execute("SELECT COUNT(*) c FROM probe_log").fetchone()["c"]
    con.close()

    L = ["# 采样健康度（频率校准依据）", ""]
    L += [f"- 累计探测 {n_total} 次；下面展示最近 {len(rows)} 次", ""]
    if not rows:
        L += ["- **尚无探测记录** —— 跑 `scheduler.py --once` 开始累积。", ""]
    else:
        L += ["| 时间 | 结果 | 话题数 | **有变化** | 最大变化 | 耗时 |",
              "|------|------|--------|-----------|----------|------|"]
        for r in rows:
            L.append(f"| {r['probed_at'][5:16]} | {'成功' if r['ok'] else '失败'} "
                     f"| {r['n_topics']} | {r['n_changed']} | {r['max_delta']} "
                     f"| {r['elapsed_sec']}s |")
        L.append("")
        ok_rows = [r for r in rows if r["ok"] and r["n_changed"] is not None]
        # ★ 样本不足时**不给结论** —— 相邻两次探测只隔几分钟当然不会有变化，
        #   凭 1 个样本说「采得太密」是会误导人的（实测踩过：间隔 1 分钟 → 0 变化 →
        #   报告建议放宽到 30min）。要等到样本覆盖了足够长的时间窗才能判断。
        MIN_SAMPLES = 5
        L += [f"**怎么读这张表（校准 {PROBE_INTERVAL_MIN} 分钟够不够）**", ""]
        if len(ok_rows) < MIN_SAMPLES:
            L += [f"- ⏳ **样本不足**（{len(ok_rows)}/{MIN_SAMPLES} 次成功探测）—— "
                  f"先让它跑够，再回来读结论。", "",
                  "> 为什么要有最小样本：相邻两次探测若只隔几分钟，热度当然不变，",
                  "> 此时「0 变化」不能推出「采得太密」。**样本够之前不给建议，免得误导。**", ""]
        else:
            zero = sum(1 for r in ok_rows if r["n_changed"] == 0)
            avg = sum(r["n_changed"] for r in ok_rows) / len(ok_rows)
            L += [f"- 平均每轮 **{avg:.1f}** 个话题发生变化；{zero}/{len(ok_rows)} 轮**完全没有变化**", ""]
            if zero == len(ok_rows):
                L += ["> 「有变化」长期为 0 → **可能采得太密**，可考虑放宽间隔（如 30min）。", ""]
            elif avg >= 3:
                L += ["> 每轮变化话题多 → **当前间隔是必要的**，甚至要考虑更密。", ""]
            else:
                L += ["> 变化不密集也不为空 → **当前间隔大体合适**，继续观察。", ""]
        L += ["> ⚠️ 反爬上界仍需独立观察：若出现失败记录（退出码/超时），说明可能被限流。", ""]

    L += ["## 最近的深采记录", ""]
    if drills:
        L += ["| 时间 | 话题 | 为什么选它 |", "|------|------|-----------|"]
        for d in drills:
            L.append(f"| {d['drilled_at'][5:16]} | {d['title'] or d['topic_key']} | {d['reason']} |")
    else:
        L += ["- 尚无深采记录。", ""]
    L += ["", "## 边界", "",
          f"- 探测间隔 {PROBE_INTERVAL_MIN} 分钟**已推导**（实测特征时间 24min ÷ 2 = 12min 下界，留余量）；",
          "  **反爬上界尚未实测**，需跑几天观察失败记录。",
          f"- 深采冷却 {DRILL_COOLDOWN_MIN} 分钟、单轮上限 {DRILL_MAX_TOPICS} 个话题 —— 这两个是经验值，未校准。",
          "- 深采采用「可发酵度」而非「当前热度」作判据，以避免漏掉冷启动（观察者悖论）。",
          "", "*报告结束*", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    # ★ 计划任务用 python.exe 启动会弹控制台 → 若该控制台属于本进程就自己隐藏
    #   （不需要改系统设置；彻底不闪仍需让任务改跑 pythonw.exe）
    harness.suppress_console()
    # ★ pythonw 无控制台时 sys.stdout 为 None，任何 print 都会抛 AttributeError。兜底在 harness 里。
    harness.ensure_std_streams()
    ap = argparse.ArgumentParser(description="采样调度器（探测 + 深采自适应）")
    ap.add_argument("--once", action="store_true", help="跑一轮就退出（推荐，配 cron）")
    ap.add_argument("--loop", action="store_true", help="常驻循环（仅本地调试）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行什么，不真跑")
    ap.add_argument("--status", action="store_true", help="输出采样健康度报告")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--interval", type=int, default=PROBE_INTERVAL_MIN,
                    help=f"探测间隔分钟（默认 {PROBE_INTERVAL_MIN}）")
    ap.add_argument("--force-probe", action="store_true", help="忽略节流，强制探测")
    ap.add_argument("--loop-seconds", type=int, default=60, help="--loop 的检查间隔")
    args = ap.parse_args(argv)

    if args.status:
        rep = status_report()
        if args.json:
            con = connect()
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM probe_log ORDER BY id DESC LIMIT 50").fetchall()]
            cols = [dict(r) for r in con.execute(
                "SELECT * FROM drill_log ORDER BY id DESC LIMIT 50").fetchall()]
            con.close()
            print(json.dumps({"schema_version": SCHEMA_VERSION, "probe_log": rows,
                              "drill_log": cols}, ensure_ascii=False, indent=2))
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
                             skip_probe_if_not_due=not args.force_probe)
                if args.json:
                    print(json.dumps(r, ensure_ascii=False))
                time.sleep(args.loop_seconds)
        except KeyboardInterrupt:
            print("\n[loop] 已停止")
        return 0

    # 默认 / --once
    r = run_once(dry_run=args.dry_run, interval_min=args.interval,
                 skip_probe_if_not_due=not args.force_probe)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    print(f"\n[done] ok={r['ok']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
