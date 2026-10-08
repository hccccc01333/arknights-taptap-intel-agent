#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/watchdog.py —— 采集链路看门狗（数据超时即告警 + 自动重启）。

★ 为什么需要（2026-10-06 事故）：
  B站 watch 无声死过一次 —— robust_watch 只保证"进程自己不死"（异常隔离），
  进程被外部杀掉/机器重启/依赖崩穿时没人知道，数据停更只能靠人翻 CSV 发现。

★ 机制（外部视角，不信任进程自己）：
  每 5 分钟检查各渠道**数据的最新观测时间**（CSV 里 observed_at 的 max，
  这是业务真相——进程活着但数据停更同样算故障）：
    超时 → 告警行（stderr，可见）+ watchdog.json 状态
         → 自动重启对应采集器（分离进程，日志续写同文件）
  重启带冷却：同渠道 10 分钟内只重启一次、每小时 ≤5 次（防重启风暴）。

★ 哨兵 vs 数据双检查：
  数据通道看 CSV 新鲜度；agent 这类"无信号就不产出"的看日志 mtime
  （robust_watch 每轮都打印，日志停更 = 进程死了）。

用法：
    python watchdog.py --check        # 单次检查（调试）
    python watchdog.py --watch        # 常驻（默认 300s 一轮），自身也走 robust_watch
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TZ_CN = timezone(timedelta(hours=8))
STATE = ROOT / "data" / "state" / "watchdog.json"

PY = sys.executable or "python"

# 渠道注册表：freshness = 检查对象（csv 最新 observed_at / 文件 mtime），
# cmd = 超时后的重启命令（工作目录恒为项目根）
CHANNELS = [
    {"name": "百度", "kind": "csv", "path": "data/raw/baidu_index/hot_search.csv",
     "max_min": 25, "log": "data/logs/baidu_watch.log",
     "cmd": [PY, "-u", "L1_data_source/collectors/baidu/crawl_baidu_hot.py",
             "--watch", "--interval", "600"]},
    {"name": "微博", "kind": "csv", "path": "data/raw/weibo/hot_search.csv",
     "max_min": 30, "log": "data/logs/weibo_watch.log",
     "cmd": [PY, "-u", "L1_data_source/collectors/weibo/crawl_weibo_hot.py",
             "--watch", "--interval", "600"]},
    {"name": "B站", "kind": "csv", "path": "data/raw/bilibili/hot_videos.csv",
     "max_min": 35, "log": "data/logs/bili_watch.log",
     "cmd": [PY, "-u", "L1_data_source/collectors/bilibili/crawl_bili_game_hot.py",
             "--watch", "--interval", "900", "--sources", "ranking,search"]},
    {"name": "贴吧", "kind": "dir", "path": "data/raw/tieba/forums",
     "max_min": 40, "log": "data/logs/tieba_watch.log",
     "cmd": [PY, "-u", "L1_data_source/collectors/tieba/crawl_tieba_forum.py",
             "--watch", "--interval", "900"]},
    {"name": "媒体", "kind": "csv", "path": "data/raw/gamemedia/news.csv",
     "max_min": 70, "log": "data/logs/gamemedia_watch.log",
     "cmd": [PY, "-u", "L1_data_source/collectors/gamemedia/crawl_gamemedia.py",
             "--watch", "--interval", "1800"]},
    # agent / 词表是"有变化才产出"型：看日志 mtime（robust_watch 每轮必打印）
    {"name": "搜索agent", "kind": "file", "path": "data/logs/agent_watch.log",
     "max_min": 240, "log": "data/logs/agent_watch.log",
     "cmd": [PY, "-u", "L1_data_source/collectors/agent/hotspot_search_agent.py",
             "--watch", "--interval", "1800"]},
    {"name": "词表watch", "kind": "file", "path": "data/state/game_term_table.json",
     "max_min": 720, "log": "data/logs/terms_watch.log",
     "cmd": [PY, "-u", "L1_data_source/collectors/taptap/build_game_terms.py",
             "--watch", "--interval", "600"]},
]

RESTART_COOLDOWN_S = 600      # 同渠道两次重启最小间隔
RESTART_HOURLY_CAP = 5        # 每渠道每小时重启上限


def _now() -> datetime:
    return datetime.now(TZ_CN)


def _csv_age_min(path: Path) -> float:
    """CSV 里最新 observed_at 距今的分钟数（没有就退文件 mtime，再没有 = inf）。"""
    try:
        newest = None
        with path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                ts = r.get("observed_at") or ""
                if ts and (newest is None or ts > newest):
                    newest = ts
        if newest:
            return (_now() - datetime.fromisoformat(newest)).total_seconds() / 60
    except OSError:
        pass
    return _file_age_min(path)


def _file_age_min(path: Path) -> float:
    try:
        return (_now().timestamp() - path.stat().st_mtime) / 60
    except OSError:
        return float("inf")


def _freshness(ch: dict) -> float:
    p = ROOT / ch["path"]
    if ch["kind"] == "csv":
        return _csv_age_min(p)
    if ch["kind"] == "dir":
        # 目录 mtime 在 Windows 上不随子文件追加更新 —— 扫目录内最新文件
        newest = 0.0
        for sub in p.glob("*"):
            for fp in (sub / "history.csv", sub / "posts.csv"):
                try:
                    newest = max(newest, fp.stat().st_mtime)
                except OSError:
                    pass
        return (_now().timestamp() - newest) / 60 if newest else float("inf")
    return _file_age_min(p)


def _already_running(ch: dict) -> bool:
    """该渠道的采集器进程是否还活着（防双实例：数据 stale 可能只是慢一轮，
    进程还活着就只告警不重启 —— 贴吧双实例就是这么来的）。"""
    import psutil
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            if not (proc.info["name"] or "").lower().startswith("python"):
                continue
            cl = " ".join(proc.info["cmdline"] or [])
            key = ch["cmd"][1]          # 采集器脚本相对路径，如 collectors/baidu/...
            if key in cl and "--watch" in cl:
                return True
    except Exception:
        pass
    return False


def _restart(ch: dict, st: dict) -> bool:
    """带冷却 + 存活检查的自动重启。返回是否真的重启了。"""
    if _already_running(ch):
        return False
    now = time.time()
    hist = st.setdefault("restarts", {}).setdefault(ch["name"], [])
    hist[:] = [t for t in hist if now - t < 3600]
    last = hist[-1] if hist else 0
    if now - last < RESTART_COOLDOWN_S or len(hist) >= RESTART_HOURLY_CAP:
        return False
    log_path = ROOT / ch["log"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lf = log_path.open("a", encoding="utf-8")
    # 分离进程：看门狗死活不影响被拉起的采集器
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(ch["cmd"], cwd=str(ROOT), stdout=lf, stderr=lf, creationflags=flags)
    hist.append(now)
    return True


def check_all() -> dict:
    st = {"last_check": _now().isoformat(timespec="seconds"), "channels": {}}
    if STATE.exists():
        try:
            old = json.loads(STATE.read_text(encoding="utf-8"))
            st["restarts"] = old.get("restarts", {})
        except (ValueError, OSError):
            st["restarts"] = {}

    for ch in CHANNELS:
        age = _freshness(ch)
        status = {"age_min": round(age, 1) if age != float("inf") else None,
                  "max_min": ch["max_min"]}
        if age > ch["max_min"]:
            status["status"] = "STALE"
            print(f"[ALARM] {ch['name']} 数据停更 {age:.0f} 分钟（阈值 {ch['max_min']}）",
                  file=sys.stderr, flush=True)
            if _already_running(ch):
                status["restarted"] = False
                status["note"] = "进程仍在但数据停更（挂起？）——不重启，人工排查"
                print(f"[ALARM] {ch['name']} 进程活着但数据 {age:.0f} 分钟未更新，"
                      "疑似挂起，请人工排查", file=sys.stderr, flush=True)
            elif _restart(ch, st):
                status["restarted"] = True
                print(f"[watchdog] 已自动重启 {ch['name']} 采集器", flush=True)
            else:
                status["restarted"] = False
                n = len(st["restarts"].get(ch["name"], []))
                status["note"] = f"冷却中/达上限（近1小时已重启 {n} 次）"
        else:
            status["status"] = "ok"
        st["channels"][ch["name"]] = status

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [n for n, v in st["channels"].items() if v["status"] != "ok"]
    if bad:
        print(f"[watchdog] 本轮异常渠道: {bad}", flush=True)
    return st


def main() -> int:
    ap = argparse.ArgumentParser(description="采集链路看门狗")
    ap.add_argument("--check", action="store_true", help="单次检查")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    args = ap.parse_args()

    if args.check or not args.watch:
        print(json.dumps(check_all(), ensure_ascii=False, indent=2))
        return 0

    # ★ 单实例锁：两个看门狗 = 双倍重启风暴。发现存活实例就退出。
    import psutil as _ps
    _me = os.getpid()
    for _p in _ps.process_iter(["pid", "name", "cmdline"]):
        try:
            if (not (_p.info["name"] or "").lower().startswith("python")
                    or _p.info["pid"] == _me):
                continue
            if "watchdog.py" in " ".join(_p.info["cmdline"] or [])                     and "--watch" in " ".join(_p.info["cmdline"] or []):
                print(f"[watchdog] 已有常驻实例 pid={_p.info['pid']}，本实例退出")
                return 0
        except Exception:
            pass

    import sys as _sys
    _here = Path(__file__).resolve().parent          # L1_data_source/
    for _p in (_here, _here / "collectors", _here.parent):
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
    from robust_watch import run_forever
    return run_forever(name="watchdog", fn=check_all, interval=args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
