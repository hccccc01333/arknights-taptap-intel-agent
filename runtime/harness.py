#!/usr/bin/env python3
"""Agent harness —— 任务注册表 + 工具契约 + 预算 + 失败策略 + 轨迹。

设计依据
  · `docs/任务契约.md`（§2 schema 16 项 · 四条纪律）
  · `docs/Agent-v2-架构设计.md` §6 第 10–15 条（控制面议题）

★ harness 只做四件事，**不做业务**：
    1. 校验   参数 schema 不合法 → 拒绝（不进图，但记轨迹）
    2. 预算   max_tool_calls / max_net_calls / timeout 超限 → 中止本轮（记轨迹）
    3. 执行 + 判定   跑工具 → 按**产出契约**判成败（returncode 只是必要条件）
    4. 分派 + 记轨迹  按错误类型 retry / degrade / reject / abort；每步 attempt/耗时/成败/产物 落盘

★ 时钟驱动的是**任务**，不是脚本；任务内部才调工具。
   （此前把时钟画成「时钟 → 采集工具」是按数据流画的，按任务画是「时钟 → 任务 → 工具」。）

★ 落地纪律（本项目已踩过的坑，勿犯）
  · 模块常量**绝不能**做函数默认参数（def 时绑定 → 测试 patch 无效 + 污染真实数据）。
  · 子进程注入 PYTHONIOENCODING=utf-8 + errors="replace"（Windows 控制台编码坑）。
  · 绝不 os.kill(pid, 0) 探活 —— Windows 上那会**真的把进程杀掉**。用文件锁。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

# 同目录模块导入引导：直接 `python harness.py` 时脚本目录本就在 sys.path，
# 但测试用 importlib 从别处加载时不在 —— 这里补上，保证两种加载方式都能用。
_LAB = Path(__file__).resolve().parent
if str(_LAB) not in sys.path:
    sys.path.insert(0, str(_LAB))

import task_contracts as tc

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
STATE = LAB / "state"
TRACE_JSONL = STATE / "trace.jsonl"
HARNESS_DB = STATE / "harness.sqlite3"
LOCK_FILE = STATE / "harness.lock"

TZ = timezone(timedelta(hours=8))
DEFAULT_TIMEOUT = 600
LOCK_STALE_SEC = 3600          # 锁文件超过该时长视为陈旧（兜底，正常靠 OS 释放）

CRAWLER = ROOT / "L1_data_source/collectors/taptap" / "crawl_taptap_discovery.py"
PLATFORM = LAB / "platform_insight.py"
TRACKER = LAB / "topic_tracker.py"
EVENTS = LAB / "events.py"
FEATURES = LAB / "features.py"
MATERIALS = LAB / "materials.py"          # ★ T5 素材获取（纯代码两类）
MATERIALS_JSONL = ROOT / "data/raw/taptap" / "materials" / "materials.jsonl"
THREADS_JSONL = ROOT / "data/raw/taptap" / "materials" / "threads.jsonl"

TRACKER_DB = STATE / "topic_tracker.sqlite3"
EVENTS_DB = STATE / "events.sqlite3"

REQUIRED_CHILD_DEPS = ("requests",)
VENV_HINT = r"C:\Users\Hzz\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

TRACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_runs (
    idem_key    TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    tool        TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    elapsed     REAL,
    artifacts   TEXT
);
"""


# ================================================================ 工具注册表

@dataclass
class ToolSpec:
    """一个真 tool 的声明。参数 schema + 产出契约是 harness 判成败的依据。"""
    name: str
    script: Path
    build_argv: Callable[[dict[str, Any]], list[str]] = lambda p: []
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    net: bool = False                       # 是否消耗网络配额（反爬风险）
    timeout: int | None = None
    result: dict[str, Any] = field(default_factory=dict)
    implemented: bool = True
    note: str = ""
    # ★ 未实现/不可用时归入哪类错误 —— 决定走 on_fail 的哪条策略。
    #   例：llm_classify 不可用属 `llm` 类 → 契约里 `llm: degrade` → **降级而不是整任务失败**。
    unavailable_class: str = "schema"


def _rows_of(csv_path: Path) -> int:
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def artifact_rows(path: Path | str) -> int:
    """产物的「行数」——**按类型分派**（CSV 数数据行 / JSONL 数非空行 / 其它数非空行）。

    ⚠️ 不按类型分派会数错：用 CSV 的方式读 JSONL，会把首行当表头，少算一行还看不出问题。
    """
    p = Path(path)
    if p.suffix.lower() in (".csv", ".tsv"):
        return _rows_of(p)
    with open(p, encoding="utf-8-sig", errors="replace") as f:
        return sum(1 for line in f if line.strip())


def TOOLS() -> dict[str, ToolSpec]:
    """工具注册表（函数形式：**调用时**取路径常量，避免 def 时绑定）。

    `result` 是**产出契约**——它才是"什么算成功"的判据：
      · artifact / min_rows   ：产物文件必须存在且行数达标（防"返回 0 却写了空文件"）
      · must_exist            ：JSON 产物必须存在
      · db_delta              ：某表行数增量 ≥ N
    """
    return {
        "crawl_hot_hashtags": ToolSpec(
            name="crawl_hot_hashtags",
            script=CRAWLER,
            build_argv=lambda p: ["--source", "hot-hashtags", "--limit", str(p["limit"])],
            params={"limit": {"type": "int", "required": False, "default": 10,
                              "min": 1, "max": 30}},
            net=True,
            result={"artifact": ROOT / "data/raw/taptap" / "hot_hashtags.csv",
                    "min_rows": 1},
        ),
        "platform_facts": ToolSpec(
            name="platform_facts",
            script=PLATFORM,
            result={"must_exist": LAB / "outputs" / "platform_insight.json"},
        ),
        "topic_sample": ToolSpec(
            name="topic_sample",
            script=TRACKER,
            build_argv=lambda p: ["--sample"],
            result={"db_delta": {"path": TRACKER_DB, "table": "topic_series", "min_rows": 1}},
        ),
        "events_run": ToolSpec(
            name="events_run",
            script=EVENTS,
            build_argv=lambda p: ["--run"],
        ),
        "features_run": ToolSpec(
            name="features_run",
            script=FEATURES,
            build_argv=lambda p: ["--run"],
        ),
        # ---- T5 素材获取：纯代码那两类（梗/二创角度需 LLM，见 implemented=False）----
        "material_extract_code": ToolSpec(
            name="material_extract_code",
            script=MATERIALS,
            build_argv=lambda p: ["--run", "--per-topic", str(p["per_topic"]),
                                  "--per-thread", str(p["per_thread"])],
            params={"per_topic": {"type": "int", "required": False, "default": 5,
                                  "min": 1, "max": 20},
                    "per_thread": {"type": "int", "required": False, "default": 3,
                                   "min": 1, "max": 10}},
            net=False,
            # 产出契约：**两个产物都要有行**（素材库 + Thread 库）
            result={"artifacts": [{"artifact": MATERIALS_JSONL, "min_rows": 1},
                                  {"artifact": THREADS_JSONL, "min_rows": 1}]},
        ),
        "llm_classify": ToolSpec(
            name="llm_classify", script=Path("（未实现）"),
            net=False, implemented=False, unavailable_class="llm",
            note="梗（两轴语义分类）/ 二创角度（生成）需 LLM；当前无 key（402）→ 分层降级",
        ),
    }


# ================================================================ 参数校验

def validate_params(spec: ToolSpec, params: dict[str, Any] | None) -> tuple[dict, list[str]]:
    """按 schema 校验参数。返回 (规整后的参数, 问题列表)。问题非空即**拒绝**。"""
    params = dict(params or {})
    problems: list[str] = []
    clean: dict[str, Any] = {}

    for name, rule in spec.params.items():
        supplied = name in params and params[name] is not None
        if not supplied:
            if rule.get("required") and "default" not in rule:
                problems.append(f"缺必填参数 {name}")
                continue
            clean[name] = rule.get("default")
            continue
        v = params[name]
        t = rule.get("type", "str")
        if t == "int":
            if isinstance(v, bool) or not isinstance(v, int):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    problems.append(f"{name} 应为 int，得到 {v!r}")
                    continue
            if "min" in rule and v < rule["min"]:
                problems.append(f"{name}={v} < 下限 {rule['min']}")
                continue
            if "max" in rule and v > rule["max"]:
                problems.append(f"{name}={v} > 上限 {rule['max']}")
                continue
        elif t == "str":
            if not isinstance(v, str):
                problems.append(f"{name} 应为 str，得到 {type(v).__name__}")
                continue
        elif t == "bool":
            if not isinstance(v, bool):
                problems.append(f"{name} 应为 bool，得到 {type(v).__name__}")
                continue
        if "choices" in rule and v not in rule["choices"]:
            problems.append(f"{name}={v!r} 不在允许集合 {rule['choices']}")
            continue
        clean[name] = v

    unknown = [k for k in params if k not in spec.params]
    if unknown:
        problems.append(f"未知参数：{unknown}（白名单：{sorted(spec.params)}）")
    return clean, problems


# ================================================================ 失败分类

WAF_MARKS = ("WAF", "waf", "挑战页", "captcha", "验证页", "Just a moment")
NET_MARKS = ("URLError", "Temporary failure", "502", "503", "504",
             "Tunnel connection failed", "ConnectionError", "timed out",
             "Max retries", "NewConnectionError", "RemoteDisconnected")
LLM_MARKS = ("402", "DEEPSEEK_API_KEY", "余额", "insufficient", "quota")
SCHEMA_MARKS = ("argparse", "unrecognized arguments", "ImportError",
                "ModuleNotFoundError", "KeyError", "JSONDecodeError",
                "usage:", "No such file", "SyntaxError")


def classify_failure(text: str) -> str:
    """把失败文本归到契约里的错误类型之一。

    ★ 未识别的错误归入 `schema`（打回，需人看），**不归入 network** —— 避免无意义重试。
    """
    blob = text or ""
    if any(m in blob for m in WAF_MARKS):
        return "waf"
    if any(m in blob for m in LLM_MARKS):
        return "llm"
    if any(m in blob for m in NET_MARKS):
        return "network"
    if any(m in blob for m in SCHEMA_MARKS):
        return "schema"
    return "schema"          # 未识别 → 打回（保守：不盲目重试、不静默降级）


# ================================================================ 结果判定

def judge_result(spec: ToolSpec, before: dict[str, Any] | None = None
                 ) -> tuple[bool, str, list[str]]:
    """按**产出契约**判成败。返回 (是否达标, 说明, 产物列表)。

    ★ 这一关最容易被漏掉：returncode == 0 只说明"跑完了"，
      一次采集**返回 0 却写下空文件**在旧逻辑里也算成功。
    """
    r = spec.result or {}
    artifacts: list[str] = []

    if "must_exist" in r:
        p = Path(r["must_exist"])
        if not p.exists():
            return False, f"产出契约不满足：{p.name} 不存在", artifacts
        if p.stat().st_size == 0:
            return False, f"产出契约不满足：{p.name} 为空文件", artifacts
        artifacts.append(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p))

    if "artifact" in r:
        p = Path(r["artifact"])
        if not p.exists():
            return False, f"产出契约不满足：{p.name} 不存在", artifacts
        try:
            n = artifact_rows(p)                            # 按 .csv/.jsonl 分派
        except Exception as e:                              # 产物损坏也算不达标
            return False, f"产出契约不满足：{p.name} 解析失败（{type(e).__name__}）", artifacts
        want = int(r.get("min_rows", 1))
        if n < want:
            return False, f"产出契约不满足：{p.name} 仅 {n} 行（需 ≥{want}）", artifacts
        artifacts.append(f"{p.name}({n} 行)")

    # 多产物：一个工具可能同时要保证几个文件都有货（如素材库 + Thread 库）
    for spec in r.get("artifacts") or []:
        ok, why, arts = judge_result(
            ToolSpec(spec.get("name", "?"), Path("."), result=spec), before)
        if not ok:
            return False, why, artifacts
        artifacts.extend(arts)

    if "db_delta" in r:
        d = r["db_delta"]
        path, table, want = Path(d["path"]), d["table"], int(d.get("min_rows", 1))
        now_n = _table_count(path, table)
        was_n = ((before or {}).get(f"{path}::{table}") or 0)
        delta = now_n - was_n
        if delta < want:
            return False, f"产出契约不满足：{table} 本轮新增 {delta} 行（需 ≥{want}）", artifacts
        artifacts.append(f"{table}(+{delta})")

    return True, "ok", artifacts


def _table_count(db: Path, table: str) -> int:
    if not Path(db).exists():
        return 0
    try:
        con = sqlite3.connect(str(db))
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.close()
        return int(n)
    except sqlite3.Error:
        return 0


def snapshot_counts(contract: dict[str, Any]) -> dict[str, Any]:
    """跑任务前拍一张产出计数快照（供 db_delta 判定）。"""
    snap: dict[str, Any] = {}
    for tool_name in contract.get("tools") or []:
        spec = TOOLS().get(tool_name)
        if not spec:
            continue
        d = (spec.result or {}).get("db_delta")
        if d:
            snap[f"{Path(d['path'])}::{d['table']}"] = _table_count(Path(d["path"]), d["table"])
    return snap


# ================================================================ 幂等 + 轨迹

def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    # ⚠️ 默认参数不能写 =HARNESS_DB（def 时绑定，测试 patch 无效）——调用时取常量。
    db_path = Path(db_path) if db_path else Path(HARNESS_DB)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.executescript(TRACE_SCHEMA)
    return con


def idem_bucket(contract: dict[str, Any], now: datetime) -> str:
    """幂等窗口：周期任务按 schedule.minutes 分桶；事件/日任务按天分桶。"""
    sch = contract.get("schedule") or {}
    if sch.get("mode") in ("interval", "both") and sch.get("minutes"):
        m = int(sch["minutes"])
        return f"{now.strftime('%Y-%m-%dT%H')}:{(now.minute // m) * m:02d}"
    return now.strftime("%Y-%m-%d")


def idem_key(task_id: str, tool: str, params: dict[str, Any], bucket: str) -> str:
    """幂等键：同任务同工具同参数同窗口 → 同键（重跑安全的前提）。"""
    raw = f"{task_id}|{tool}|{json.dumps(params, sort_keys=True, ensure_ascii=False)}|{bucket}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def lookup_cached(con: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM tool_runs WHERE idem_key=? AND ok=1", (key,)).fetchone()
    if not row:
        return None
    return {"ok": True, "elapsed": row["elapsed"],
            "artifacts": json.loads(row["artifacts"] or "[]"),
            "finished_at": row["finished_at"]}


def remember(con: sqlite3.Connection, key: str, task_id: str, tool: str,
             ok: bool, elapsed: float, artifacts: list[str]) -> None:
    con.execute("INSERT OR REPLACE INTO tool_runs"
                " (idem_key, task_id, tool, finished_at, ok, elapsed, artifacts)"
                " VALUES (?,?,?,?,?,?,?)",
                (key, task_id, tool, datetime.now(TZ).isoformat(timespec="seconds"),
                 1 if ok else 0, elapsed, json.dumps(artifacts, ensure_ascii=False)))
    con.commit()


def trace(rec: dict[str, Any], path: Path | str | None = None) -> None:
    """追加一条轨迹（append-only JSONL，可回放）。"""
    path = Path(path) if path else Path(TRACE_JSONL)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(TZ).isoformat(timespec="seconds"), **rec}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ================================================================ 单例锁

def _lock_acquire(fh) -> bool:
    """非阻塞抢锁（锁区固定为**字节 0**，1 字节）。进程死亡时由 OS 释放。"""
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _lock_release(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


class TaskLock:
    """单例锁：防 tick 重叠（时钟内化后会出现的第一个新问题）。

    ★ 不要用 os.kill(pid, 0) 探活 —— Windows 上 os.kill 的非 0/CTRL 信号路径
      会调用 TerminateProcess，**真的把那个进程杀掉**。改为依赖 OS 文件锁：
      进程一死，锁自动释放。

    ★ 两个实测坑（都踩过，勿改回去）：
      1. 用「读文件判空」会踩 Windows 的字节区间锁 —— **读被锁区间直接 PermissionError**。
         所以锁区固定在**字节 0**，持有者信息写在**偏移 1 之后**，双方各读各的区间。
      2. 必须用 `r+`（不是 `a+`）：append 模式下写入永远落在文件末尾，seek 控制不了偏移。
    """

    LOCK_BYTE = 1              # 锁区长度：1 字节（偏移 0）

    def __init__(self, path: Path | str | None = None, stale_sec: int | None = None):
        self.path = Path(path) if path else Path(LOCK_FILE)
        self.stale_sec = LOCK_STALE_SEC if stale_sec is None else stale_sec
        self._fh = None
        self.holder: dict[str, Any] = {}

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)                  # 先确保存在（不截断）
        self._fh = open(self.path, "r+", encoding="utf-8")
        fh = self._fh
        fh.seek(0, os.SEEK_END)
        if fh.tell() < self.LOCK_BYTE:                  # 空文件补够锁区（写入不需读权）
            fh.write(" " * (self.LOCK_BYTE - fh.tell()))
            fh.flush()
        if _lock_acquire(fh):
            fh.seek(self.LOCK_BYTE)
            fh.write(json.dumps({
                "pid": os.getpid(),
                "at": datetime.now(TZ).isoformat(timespec="seconds")}))
            fh.truncate()
            fh.flush()
            return True
        # 抢不到：读持有者（**从锁区之后读**，否则 Windows 会拒绝），并立刻关闭句柄
        try:
            fh.seek(self.LOCK_BYTE)
            self.holder = json.loads(fh.read() or "{}")
        except Exception:
            self.holder = {}
        finally:
            fh.close()              # 没拿到锁 → 留着句柄就是泄漏
            self._fh = None
        return False

    def release(self) -> None:
        if self._fh:
            _lock_release(self._fh)
            self._fh.close()
            self._fh = None


# ================================================================ 运行环境适配（Windows）
#
# ★ 为什么这块在 harness 里，而不是一个独立模块（2026-10-01 用户：「这种应该放进 agent harness 里面」）：
#   「怎么起进程」属于**执行层**——而执行层就是 harness。所以平台适配不该是被三处 import 的平行模块，
#   而是 harness 的内部构成；外部只需要用 `harness.exec_command()` 这一个出口。

IS_WINDOWS = os.name == "nt"
NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)   # 子进程不新建控制台
MAX_CONSOLE_LOG_BYTES = 2 * 1024 * 1024

# ⚠️ 「计划任务不弹窗」必须两半同时做，只做一半会更糟：
#   ① 计划任务从 python.exe（控制台子系统）改跑 pythonw.exe（GUI 子系统）→ 自身不弹窗；
#      但 pythonw **没有控制台可继承**，它 spawn 的每个控制台子进程都会被 Windows 新建控制台
#      → 一轮探测 6 个子进程 = 6 次弹窗（1 个窗变 N 个）！所以子进程必须带 CREATE_NO_WINDOW。
#   ② pythonw 下 CPython 会把 sys.stdout/sys.stderr 置为 None → 任何 print() 抛 AttributeError
#      → 调度器每轮静默崩。所以入口要调 ensure_std_streams() 兜底。


def no_window_kwargs() -> dict[str, Any]:
    """给 subprocess 展开用的 kwargs（非 Windows 返回空 dict，无副作用）。"""
    return {"creationflags": NO_WINDOW_FLAGS} if IS_WINDOWS else {}


def _open_console_log(log_path: Path | str | None = None):
    if log_path is None:
        log_path = Path(STATE) / "console.log"
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_console_log(log_path)
    fh = open(log_path, "a", encoding="utf-8", buffering=1)      # 行缓冲：崩了也留痕
    fh.write(f"\n===== {datetime.now(TZ).isoformat(timespec='seconds')} "
             f"pid={os.getpid()}（无可见控制台，输出转存至此）=====\n")
    return log_path, fh


def _owns_own_console() -> bool:
    """这个控制台是不是**为本进程创建**的？（而不是从父进程继承来的）

    ★ 必须判这个：从终端/IDE 里跑时进程共享父控制台，此时隐藏会把**用户的终端窗口**也隐掉。
      判据：`GetConsoleProcessList` 只列出本进程。
    """
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        pids = (ctypes.c_uint * 64)()
        n = k32.GetConsoleProcessList(pids, 64)
        return n == 1 and pids[0] == os.getpid()
    except Exception:
        return False


def suppress_console(log_path: Path | str | None = None) -> bool:
    """**隐藏本进程自有的控制台窗口**，并把输出转日志。返回是否隐藏。

    ★ 用途（2026-10-01）：计划任务用 `python.exe` 启动 → Windows 为它新建一个控制台 → **弹窗**。
      而在计划任务里跑时，这个控制台**只属于本进程**，所以进程可以自己把它藏起来 ——
      **不需要改任何系统设置**（本项目所在环境禁止 `schtasks.exe`，改任务做不到；这条能立刻见效）。

    ⚠️ 诚实边界：控制台在「进程启动 → 执行到本函数」之间仍会**短暂闪现**（约 0.1–0.3s，取决于导入耗时）。
       要**彻底**不闪，仍须让计划任务改跑 `pythonw.exe`（见 `scripts/register_scheduler_task.ps1`）。
       两条不冲突：pythonw 是根治，本函数是无须改系统时的止血。
    """
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return False                          # 本来就没有控制台（pythonw / 已分离）
        if not _owns_own_console():
            return False                          # 共享控制台 → 绝不能隐（会隐掉用户的终端）
        ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
        # 窗口看不见了 → 输出必须留痕（否则崩了完全无迹可查）
        _, fh = _open_console_log(log_path)
        sys.stdout, sys.stderr = fh, fh
        return True
    except Exception:
        return False


def _rotate_console_log(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_CONSOLE_LOG_BYTES:
            path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def ensure_std_streams(log_path: Path | str | None = None) -> Path | None:
    """标准流缺失时（pythonw 无控制台）接到日志文件；**有控制台时什么都不做**。

    返回所用日志路径；不需要兜底时返回 None。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None
    if log_path is None:
        log_path = Path(STATE) / "console.log"
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_console_log(log_path)
    fh = open(log_path, "a", encoding="utf-8", buffering=1)      # 行缓冲：崩了也留痕
    fh.write(f"\n===== {datetime.now(TZ).isoformat(timespec='seconds')} "
             f"pid={os.getpid()}（无控制台启动，输出转存至此）=====\n")
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh
    return log_path


# ================================================================ 执行层唯一子进程出口


def exec_command(argv: list[str], *, timeout: int | None = None,
                 dry_run: bool = False, cwd: Path | str | None = None) -> dict[str, Any]:
    """**执行层的唯一子进程出口**。所有要起进程的地方都走这里，别再各写一份。

    统一四件最容易各处写歪的事：
      1. **编码**：注入 `PYTHONIOENCODING=utf-8`（源头统一）+ 本侧 `errors="replace"`（兜底）
         —— 子进程按 GBK 输出会让整轮调度失败（Windows 实测踩过）
      2. **不弹窗**：`CREATE_NO_WINDOW`（见上方「两半必须同时做」）
      3. **超时**：返回结构化失败，**不抛**（调用方按降级纪律处理）
      4. **失败分类**：复用 `classify_failure`，错误类型全项目一致

    返回：`{ok, dry_run, cmd, exit_code, stdout, stderr, elapsed, error, error_class}`
    """
    argv = [str(a) for a in argv]
    printable = " ".join(argv[1:]) if len(argv) > 1 else " ".join(argv)
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout

    if dry_run:
        return {"ok": True, "dry_run": True, "cmd": printable, "exit_code": None,
                "stdout": "", "stderr": "", "elapsed": 0.0, "error": None,
                "error_class": None}

    t0 = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                           encoding="utf-8", timeout=timeout,
                           cwd=str(cwd or ROOT),
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                           **no_window_kwargs())
    except subprocess.TimeoutExpired:
        return {"ok": False, "dry_run": False, "cmd": printable, "exit_code": None,
                "stdout": "", "stderr": f"超时（>{timeout}s）",
                "elapsed": round(time.time() - t0, 1),
                "error": f"超时（>{timeout}s）", "error_class": "network"}
    except OSError as e:                     # 例：脚本不存在 / 无法创建进程
        return {"ok": False, "dry_run": False, "cmd": printable, "exit_code": None,
                "stdout": "", "stderr": f"无法启动进程：{e}",
                "elapsed": round(time.time() - t0, 1),
                "error": f"无法启动进程：{e}", "error_class": "schema"}

    elapsed = round(time.time() - t0, 1)
    stdout, stderr = r.stdout or "", r.stderr or ""
    cls = None if r.returncode == 0 else classify_failure(f"{stderr}\n{stdout}")
    return {"ok": r.returncode == 0, "dry_run": False, "cmd": printable,
            "exit_code": r.returncode, "stdout": stdout, "stderr": stderr,
            "elapsed": elapsed,
            "error": None if r.returncode == 0 else f"退出码 {r.returncode}",
            "error_class": cls}


# ================================================================ 执行

def preflight() -> dict[str, Any]:
    """跑前预检：子进程要用的依赖在不在（同一解释器）。"""
    import importlib.util
    missing = [m for m in REQUIRED_CHILD_DEPS if not importlib.util.find_spec(m)]
    ok = not missing
    return {"ok": ok, "missing": missing, "python": sys.executable,
            "hint": ("子进程用同一解释器，缺依赖会只看到「退出码 1」。"
                     f"请用这个解释器跑：{VENV_HINT}") if not ok else ""}


def run_tool_call(spec: ToolSpec, params: dict[str, Any], *, timeout: int,
                  dry_run: bool = False) -> dict[str, Any]:
    """真正跑一次工具 —— 薄适配层，落在 `exec_command` 上（不重复实现编码/超时/平台细节）。"""
    argv = [str(spec.script), *spec.build_argv(params)]
    printable = " ".join(argv)
    if dry_run:
        return {"ok": True, "dry_run": True, "cmd": printable, "elapsed": 0.0,
                "stdout": "", "stderr": "", "exit_code": None}
    if not spec.script.exists():
        return {"ok": False, "cmd": printable, "elapsed": 0.0,
                "stderr": f"工具脚本不存在：{spec.script}", "exit_code": -1}
    return exec_command([sys.executable, *argv], timeout=timeout)


def run_step(task_id: str, tool_name: str, contract: dict[str, Any],
             *, params: dict[str, Any] | None = None, con: sqlite3.Connection | None = None,
             before: dict[str, Any] | None = None, force: bool = False,
             dry_run: bool = False, now: datetime | None = None,
             trace_path: Path | str | None = None,
             call_hook: Callable[[str, dict], dict] | None = None) -> dict[str, Any]:
    """跑一步（=一次工具调用），走完校验 → 预算 → 幂等 → 执行 → 判定 → 分派 → 记轨迹。"""
    now = now or datetime.now(TZ)
    spec = TOOLS().get(tool_name)
    step: dict[str, Any] = {"tool": tool_name, "outcome": "ok", "attempt": 0,
                            "elapsed": 0.0, "error_class": None, "note": "", "artifacts": []}
    fail_policy = contract.get("on_fail") or {}
    max_retries = int((contract.get("budget") or {}).get("max_retries", 0))

    if spec is None:
        step.update(outcome="rejected", error_class="schema",
                    note=f"工具未注册：{tool_name}")
        trace({"task_id": task_id, "kind": "step", **step}, trace_path)
        return step
    if not spec.implemented:
        # ★ 未实现 ≠ 一律中止：按 `unavailable_class` 走契约的 on_fail 策略。
        #   例：llm_classify 不可用 → llm 类 → 契约 `llm: degrade` → 降级继续（分层降级），
        #   而不是让整任务失败。**部分产出 + 显式标注 > 全部失败。**
        cls = spec.unavailable_class
        action = fail_policy.get(cls, "abort")
        outcome = {"retry": "degraded", "degrade": "degraded",
                   "reject": "rejected", "abort": "aborted"}.get(action, "aborted")
        step.update(outcome=outcome, error_class=cls,
                    note=f"工具不可用（{cls} 类 → {action}）：{spec.note or tool_name}")
        trace({"task_id": task_id, "kind": "step", **step}, trace_path)
        return step

    # ① 参数校验
    clean, problems = validate_params(spec, params)
    if problems:
        step.update(outcome="rejected", error_class="schema",
                    note="参数不合法：" + "；".join(problems))
        trace({"task_id": task_id, "kind": "step", **step}, trace_path)
        return step
    step["params"] = clean

    # ③ 幂等（在②预算之前判定：命中就不消耗预算）
    key = idem_key(task_id, tool_name, clean, idem_bucket(contract, now))
    if con is not None and not force and not dry_run:
        cached = lookup_cached(con, key)
        if cached:
            step.update(outcome="ok", elapsed=0.0, artifacts=cached["artifacts"],
                        note=f"幂等命中（{cached['finished_at']} 已有结果，未重复执行）",
                        cached=True)
            trace({"task_id": task_id, "kind": "step", **step}, trace_path)
            return step

    # ④ 执行 + 重试
    result: dict[str, Any] = {}
    for attempt in range(1, max_retries + 2):
        step["attempt"] = attempt
        timeout = spec.timeout or int((contract.get("budget") or {}).get("timeout")
                                      or DEFAULT_TIMEOUT)
        result = (call_hook(tool_name, clean) if call_hook
                  else run_tool_call(spec, clean, timeout=timeout, dry_run=dry_run))
        step["elapsed"] = round(step["elapsed"] + float(result.get("elapsed") or 0), 2)

        if result.get("ok") and not result.get("dry_run"):
            ok, why, arts = judge_result(spec, before)
        elif result.get("dry_run"):
            ok, why, arts = True, "dry-run", ["（dry-run 未执行）"]
        else:
            blob = f"{result.get('stderr','')}\n{result.get('stdout','')}"
            cls = classify_failure(blob)
            action = fail_policy.get(cls, "abort")
            step["error_class"] = cls
            step.update(note=f"{cls}：" + (result.get("stderr") or blob).strip()[:200])
            if action == "retry" and attempt <= max_retries:
                step["outcome"] = "retry"
                trace({"task_id": task_id, "kind": "step_attempt", **step}, trace_path)
                time.sleep(min(2 ** attempt, 8))          # 指数退避（上限 8s）
                continue
            step["outcome"] = {"retry": "degraded", "degrade": "degraded",
                               "reject": "rejected", "abort": "aborted"}.get(action, "aborted")
            step["artifacts"] = arts = []
            break

        if ok:
            step.update(outcome="ok", error_class=None, note=why, artifacts=arts)
            break
        # 产出契约不满足：按 empty 类分派（空产出通常是数据侧问题）
        action = fail_policy.get("empty", "degrade")
        step.update(error_class="empty",
                    outcome={"retry": "degraded", "degrade": "degraded",
                             "reject": "rejected", "abort": "aborted"}.get(action, "degraded"),
                    note=why, artifacts=arts)
        if action == "retry" and attempt <= max_retries:
            step["outcome"] = "retry"
            trace({"task_id": task_id, "kind": "step_attempt", **step}, trace_path)
            time.sleep(min(2 ** attempt, 8))
            continue
        break

    if con is not None and not dry_run and step["outcome"] == "ok":
        remember(con, key, task_id, tool_name, True, step["elapsed"], step["artifacts"])
    trace({"task_id": task_id, "kind": "step", **step}, trace_path)
    return step


def run_task(task_id: str, *, params: dict[str, dict[str, Any]] | None = None,
             dry_run: bool = False, force: bool = False, use_lock: bool = True,
             now: datetime | None = None,
             trace_path: Path | str | None = None,
             con: sqlite3.Connection | None = None,
             call_hook: Callable[[str, dict], dict] | None = None,
             lock_path: Path | str | None = None) -> dict[str, Any]:
    """跑一个任务：按契约的工具链逐步执行，最后按 success 判据总判。"""
    now = now or datetime.now(TZ)
    params = params or {}
    try:
        contract = tc.get(task_id)
    except KeyError as e:
        return {"task_id": task_id, "ok": False, "refused": True, "reason": str(e)}

    problems = tc.validate(contract)
    if problems:
        return {"task_id": task_id, "ok": False, "refused": True,
                "reason": "契约不合法，拒绝执行：" + "；".join(problems)}

    if contract["status"] == "blank":
        pend = contract.get("pending") or []
        return {"task_id": task_id, "ok": False, "refused": True,
                "reason": (f"契约已立但**实现空白**：{'、'.join(contract['tools'])} 尚未实现；"
                           f"待拍板 {len(pend)} 项（{'、'.join(pend)}）。"
                           "harness 不假装能跑。")}

    budget = contract["budget"]
    lock = TaskLock(lock_path) if (use_lock and not dry_run) else None
    if lock is not None and not lock.acquire():
        return {"task_id": task_id, "ok": False, "skipped": True,
                "reason": f"上一轮未结束（锁被 {lock.holder.get('pid', '?')} 持有，"
                          f"起始 {lock.holder.get('at', '?')}）——本轮跳过"}

    own_con = None
    try:
        if con is None:
            own_con = connect()
            con = own_con
        before = snapshot_counts(contract)
        steps: list[dict[str, Any]] = []
        t0 = time.time()

        for tool_name in contract["tools"]:
            spec = TOOLS().get(tool_name)
            if len(steps) >= int(budget["max_tool_calls"]):
                steps.append({"tool": tool_name, "outcome": "aborted", "error_class": "budget",
                              "note": f"超出 max_tool_calls={budget['max_tool_calls']}"})
                break
            # 网络配额：只看**真正要发出去的**调用（缓存命中不算、dry-run 不算）
            n_net_now = sum(1 for s in steps if _is_net_and_live(s))
            if spec is not None and spec.net and not dry_run:
                if n_net_now >= int(budget["max_net_calls"]):
                    steps.append({"tool": tool_name, "outcome": "aborted",
                                  "error_class": "budget",
                                  "note": f"超出 max_net_calls={budget['max_net_calls']}"
                                          "（反爬配额）"})
                    continue
            step = run_step(task_id, tool_name, contract, params=params.get(tool_name),
                            con=con, before=before, force=force, dry_run=dry_run,
                            now=now, trace_path=trace_path, call_hook=call_hook)
            steps.append(step)
            if step["outcome"] in ("rejected", "aborted"):
                break

        elapsed = round(time.time() - t0, 2)
        n_net = sum(1 for s in steps if _is_net_and_live(s))
        ok, notes, degraded = _probe_success(contract, steps)
        summary = {"task_id": task_id, "kind": "run_summary", "ok": ok,
                   "dry_run": dry_run, "n_steps": len(steps),
                   "n_ok": sum(s["outcome"] == "ok" for s in steps),
                   "n_degraded": sum(s["outcome"] == "degraded" for s in steps),
                   "n_failed": sum(s["outcome"] in ("rejected", "aborted") for s in steps),
                   "elapsed": elapsed, "net_calls": n_net, "notes": notes,
                   "degraded": degraded}
        trace(summary, trace_path)
        return {**summary, "steps": steps}
    finally:
        if own_con is not None:
            own_con.close()
        if lock is not None:
            lock.release()


def _is_net_and_live(step: dict[str, Any]) -> bool:
    """该步是否**真的发出了一次网络调用**（缓存命中/dry-run/未执行都不算）。"""
    if step.get("cached") or step.get("dry_run"):
        return False
    if step.get("outcome") in ("aborted", "rejected"):
        return False
    spec = TOOLS().get(step.get("tool", ""))
    return bool(spec and spec.net)


def _probe_success(contract: dict[str, Any],
                   steps: list[dict[str, Any]]) -> tuple[bool, list[str], list[str]]:
    """按契约的 success 判据总判任务成败。返回 (ok, notes, degraded_notes)。"""
    notes: list[str] = []
    degraded: list[str] = []
    crit = contract["success"]
    failed = [s for s in steps if s["outcome"] in ("rejected", "aborted")]
    degen = [s for s in steps if s["outcome"] == "degraded"]

    if failed:
        return False, [f"{len(failed)} 步失败：" +
                       "、".join(f"{s['tool']}({s['error_class']})" for s in failed)], []

    tid = contract["task_id"]
    if tid == "hotspot_track":
        want_min = int(crit.get("min_series_rows", 1))
        delta = 0
        for s in steps:
            for a in s.get("artifacts") or []:
                if a.startswith("topic_series(+"):
                    delta += int(a.split("+")[1].rstrip(")"))
        if delta >= want_min:
            notes.append(f"topic_series 新增 {delta} 行（判据 ≥{want_min}）")
        else:
            if crit.get("empty_is_success"):
                degraded.append("本轮无新增采样/无迁移 —— 按 empty_is_success 判为成功")
            else:
                return False, ["无新增采样且契约不允许空成功"], []
        n_events = _table_count(EVENTS_DB, "topic_event")
        notes.append(f"事件表累计 {n_events} 条")

    if tid == "material_extract":
        # 判据：代码可做的两类必须都出（min_types_code_only）；LLM 两类缺失 → 显式降级
        want = int(crit.get("min_types_code_only", 2))
        types: dict[str, int] = {}
        try:
            for line in Path(MATERIALS_JSONL).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    t = json.loads(line).get("type")
                    types[t] = types.get(t, 0) + 1
        except (OSError, json.JSONDecodeError):
            pass
        if types:
            notes.append("素材类型：" + "、".join(f"{k} {v} 条" for k, v in sorted(types.items())))
        code_types = [t for t in ("original_post", "hot_comment") if types.get(t)]
        if len(code_types) < want:
            return False, [f"代码可做的类型只出了 {len(code_types)} 类"
                           f"（契约要求 ≥{want}）：{code_types}"], degraded
        for t in ("meme", "remix_angle"):
            if not types.get(t):
                degraded.append(f"{t} 未生成（需 LLM，当前无 key）—— **分层降级**，"
                                "已出代码可做的两类")
        # LLM 缺口的**领域化说明**已给出 → 不再重复输出通用降级文案
        degen = [s for s in degen if s.get("error_class") != "llm"]

    if degen:
        degraded.extend(f"{s['tool']}：{s['note'][:80]}" for s in degen)
    return True, notes, degraded


# ================================================================ 状态 / 轨迹查询

def status(*, trace_path: Path | str | None = None,
           db_path: Path | str | None = None, last_n: int = 6) -> str:
    trace_path = Path(trace_path) if trace_path else Path(TRACE_JSONL)
    lines = ["harness 状态", ""]
    pf = preflight()
    lines.append(f"解释器：{pf['python']}")
    lines.append("子进程依赖：" + ("齐全 ✅" if pf["ok"] else f"❌ 缺 {pf['missing']} —— {pf['hint']}"))
    lines.append("")
    bad = tc.validate_all()
    lines.append("契约校验：" + ("全部通过 ✅" if not bad else f"❌ {len(bad)} 个任务有问题"))
    for tid, probs in bad.items():
        lines.extend(f"  · {tid}: {p}" for p in probs)
    lines.append("")
    if not trace_path.exists():
        lines.append(f"轨迹：{trace_path.name} 不存在（还没跑过任务）")
        return "\n".join(lines)
    rows = [json.loads(l) for l in trace_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    summaries = [r for r in rows if r.get("kind") == "run_summary"]
    lines.append(f"轨迹：{trace_path.name} 共 {len(rows)} 条"
                 f"（含 {len(summaries)} 次任务运行）")
    for r in summaries[-last_n:]:
        flag = "ok" if r.get("ok") else "FAIL"
        lines.append(f"  [{r['ts'][:19]}] {r['task_id']}  {flag}"
                     f"  {r.get('n_ok', 0)}ok/{r.get('n_degraded', 0)}降/{r.get('n_failed', 0)}败"
                     f"  {r.get('elapsed', 0)}s")
        for n in r.get("degraded") or []:
            lines.append(f"        ⚠ {n[:100]}")
    return "\n".join(lines)


def show_trace(*, trace_path: Path | str | None = None, n: int = 12) -> str:
    trace_path = Path(trace_path) if trace_path else Path(TRACE_JSONL)
    if not trace_path.exists():
        return f"{trace_path} 不存在"
    rows = [json.loads(l) for l in trace_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = [f"最近 {min(n, len(rows))} 条轨迹（{trace_path.name}）", ""]
    for r in rows[-n:]:
        if r.get("kind") == "run_summary":
            out.append(f"[{r['ts'][11:19]}] ── 任务 {r['task_id']} "
                       f"{'ok' if r.get('ok') else 'FAIL'} 用时 {r.get('elapsed')}s ──")
        else:
            out.append(f"[{r['ts'][11:19]}] {r.get('tool'):<22} {r.get('outcome'):<9}"
                       f"{r.get('elapsed', 0):>6}s  {r.get('note', '')[:64]}")
    return "\n".join(out)


# ================================================================ CLI

def main(argv: list[str] | None = None) -> int:
    suppress_console()            # ★ 自有控制台就自己隐藏（计划任务 python.exe 场景）
    ensure_std_streams()          # ★ pythonw 无控制台时把 print 转日志，别炸
    ap = argparse.ArgumentParser(description="Agent harness —— 任务注册表 + 工具契约 + 轨迹")
    ap.add_argument("--task", help="跑哪个任务（task_id）")
    ap.add_argument("--tasks", action="store_true", help="列出任务注册表 + 契约校验")
    ap.add_argument("--tools", action="store_true", help="列出工具注册表")
    ap.add_argument("--status", action="store_true", help="harness 状态 + 近几次运行")
    ap.add_argument("--trace", nargs="?", type=int, const=12, help="看最近 N 条轨迹")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    ap.add_argument("--force", action="store_true", help="忽略幂等与锁")
    ap.add_argument("--no-lock", action="store_true", help="不加单例锁（测试用）")
    args = ap.parse_args(argv)

    if args.tasks:
        print(tc.summary())
        return 0
    if args.tools:
        print("工具注册表")
        for name, s in TOOLS().items():
            tag = "net" if s.net else "   "
            impl = "" if s.implemented else f"  ⬜ 未实现：{s.note}"
            sch = f"  参数 {sorted(s.params)}" if s.params else ""
            print(f"  [{tag}] {name:<22} {s.script.name if s.script.name else s.script}"
                  f"{sch}{impl}")
        return 0
    if args.status:
        print(status())
        return 0
    if args.trace:
        print(show_trace(n=args.trace))
        return 0
    if args.task:
        pf = preflight()
        if not pf["ok"] and not args.dry_run:
            print(f"❌ 预检失败：缺 {pf['missing']}。{pf['hint']}")
            return 2
        r = run_task(args.task, dry_run=args.dry_run, force=args.force,
                     use_lock=not args.no_lock)
        if r.get("refused"):
            print(f"⛔ 拒绝执行：{r['reason']}")
            return 3
        if r.get("skipped"):
            print(f"⏭ 跳过：{r['reason']}")
            return 0
        print(f"[{r['task_id']}] {'ok' if r['ok'] else 'FAIL'}"
              f"  步数 {r['n_steps']}（{r['n_ok']} ok / {r['n_degraded']} 降级 / {r['n_failed']} 失败）"
              f"  用时 {r['elapsed']}s  网络调用 {r['net_calls']}")
        for s in r["steps"]:
            flag = {"ok": "✓", "degraded": "⚠", "rejected": "✗", "aborted": "⛔"}.get(
                s["outcome"], "?")
            print(f"  {flag} {s['tool']:<22} {s.get('elapsed', 0):>6}s"
                  f"  {s.get('note', '')[:70]}")
        for n in r["notes"]:
            print(f"  · {n}")
        for d in r["degraded"]:
            print(f"  ⚠ {d}")
        print(f"  轨迹 → {TRACE_JSONL.relative_to(ROOT)}(本次已追加)")
        return 0 if r["ok"] else 1
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
