#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1 采集系统入口（取代早期的一次性跑批 normalize.py）。

用法：
    python L1_data_source/pipeline.py --seed          # 初始化数据源注册表
    python L1_data_source/pipeline.py --plan          # 看采集频率计划（谁该采了）
    python L1_data_source/pipeline.py --once          # 跑一轮（只跑到期的 source）
    python L1_data_source/pipeline.py --once --all    # 跑一轮（全部 enabled，忽略到期）
    python L1_data_source/pipeline.py --only tap_hot_hashtags
    python L1_data_source/pipeline.py --health        # 数据源健康
    python L1_data_source/pipeline.py --stats         # 事件总线与快照统计
    python L1_data_source/pipeline.py --priority tap_topic_feed --level high --ttl 7200
    python L1_data_source/pipeline.py --dlq           # 看死信
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

_L1 = os.path.dirname(os.path.abspath(__file__))
if _L1 not in sys.path:
    sys.path.insert(0, _L1)

from storage.metadata_db import MetadataStore          # noqa: E402
from storage.raw_lake import RawLake                    # noqa: E402
from bus.event_bus import EventBus, TOPICS              # noqa: E402
from registry.source_registry import SourceRegistry     # noqa: E402
from planner.crawl_planner import CrawlPlanner          # noqa: E402
from connectors.runtime import ConnectorRuntime          # noqa: E402
from schema.content_event import now_cn                  # noqa: E402


def _build():
    store = MetadataStore()
    reg = SourceRegistry(store)
    lake = RawLake()
    bus = EventBus()
    planner = CrawlPlanner(store, bus)
    runtime = ConnectorRuntime(store, lake, bus)
    return store, reg, lake, bus, planner, runtime


def _print_table(rows: List[Dict[str, Any]], cols: List[str], title: str = "") -> None:
    if title:
        print(f"\n== {title} ==")
    if not rows:
        print("  (空)")
        return
    widths = {c: max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    print("  " + "  ".join(str(c).ljust(widths[c]) for c in cols))
    print("  " + "  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  " + "  ".join(str(r.get(c, "")).ljust(widths[c])[: widths[c]] for c in cols))


def cmd_seed(reg: SourceRegistry) -> None:
    n = reg.seed()
    print(f"已写入/更新 {n} 个 Source（已存在的不覆盖）")
    s = reg.summary()
    print(json.dumps(s, ensure_ascii=False, indent=2))


def cmd_plan(reg: SourceRegistry, planner: CrawlPlanner) -> None:
    srcs = reg.list_sources(enabled_only=True)
    plan = planner.plan(srcs)
    rows = [{
        "source_id": p["source_id"], "platform": p["platform"],
        "base(s)": p["base_interval_seconds"], "next(s)": p["interval_seconds"],
        "priority": p["priority"], "due": "YES" if p["due"] else "-",
    } for p in plan]
    _print_table(rows, ["source_id", "platform", "base(s)", "next(s)", "priority", "due"],
                 "采集频率计划（next = f(优先级, 变化率, 健康, 反馈)）")
    due = [p for p in plan if p["due"]]
    print(f"\n  到期待采: {len(due)}/{len(plan)}")


def cmd_run(reg: SourceRegistry, planner: CrawlPlanner, runtime: ConnectorRuntime,
            only: Optional[str] = None, run_all: bool = False, dry: bool = False) -> int:
    srcs = reg.list_sources(enabled_only=True)
    if only:
        srcs = [s for s in srcs if s["source_id"] == only]
        if not srcs:
            print(f"[error] 没有启用的 source: {only}")
            return 2
    if not run_all:
        plan = {p["source_id"]: p for p in planner.plan(srcs)}
        srcs = [s for s in srcs if plan.get(s["source_id"], {}).get("due")]
        if not srcs and not only:
            print("没有到期的 source（加 --all 强制跑全部）")
            return 0

    results = runtime.run_all(srcs, dry_run=dry)
    rows = [{
        "source_id": r["source_id"], "status": r["status"], "records": r["records"],
        "events": r["events_ok"], "invalid": r["events_invalid"],
        "snap": r["snapshots"], "dup": r["duplicates"], "dlq": r["dlq"],
        "ms": r["elapsed_ms"], "error": r.get("error_type") or "",
    } for r in results]
    _print_table(rows, ["source_id", "status", "records", "events", "invalid", "snap", "dup",
                        "dlq", "ms", "error"], "本轮采集结果")
    tot = {k: sum(int(r.get(k, 0)) for r in results) for k in
           ("records", "events_ok", "events_invalid", "snapshots", "duplicates", "dlq")}
    print(f"\n  合计: {json.dumps(tot, ensure_ascii=False)}")
    for r in results:
        for p in r.get("problems_sample", []):
            print(f"  ! [{r['source_id']}] {p}")
    return 0


def cmd_health(store: MetadataStore, reg: SourceRegistry) -> None:
    health = store.all_health()
    rows = []
    for s in reg.list_sources():
        h = health.get(s["source_id"], {})
        rows.append({
            "source_id": s["source_id"], "enabled": s["enabled"],
            "succ%": h.get("success_rate", ""), "lat(ms)": h.get("request_latency_ms", ""),
            "rec/run": h.get("records_per_run", ""), "fails": h.get("consecutive_failures", 0),
            "circuit": h.get("circuit_state", "closed"),
        })
    _print_table(rows, ["source_id", "enabled", "succ%", "lat(ms)", "rec/run", "fails", "circuit"],
                 "Source Health（一等公民）")


def cmd_stats(store: MetadataStore, bus: EventBus) -> None:
    print("\n== 事件总线（topic 计数）==")
    for t, n in bus.counts().items():
        print(f"  {t:<32} {n:>6}   {TOPICS[t]}")
    print(f"\n  指标快照总数: {store.count_snapshots()}")


def cmd_dlq(store: MetadataStore) -> None:
    items = store.dlq_items()
    _print_table([{"source_id": i["source_id"], "error": i["error_type"],
                   "failed_at": (i["failed_at"] or "")[:19], "replayed": i["replayed"]}
                  for i in items], ["source_id", "error", "failed_at", "replayed"],
                 f"死信队列（{len(items)} 条，可 replay）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="L1 信号采集层：采集系统入口")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--all", action="store_true", help="忽略到期判断，跑全部 enabled")
    ap.add_argument("--only", help="只跑指定 source_id")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--dlq", action="store_true")
    ap.add_argument("--reset-cursor", help="清空指定 source 的游标（重采：数据修复/换 parser 后重放时用）")
    ap.add_argument("--velocity", help="对指定 source 算指标速度/加速度（演示 Snapshot 的价值）")
    ap.add_argument("--priority", help="给指定 source 发调频指令（第三层→第一层的事件）")
    ap.add_argument("--level", default="high", choices=["urgent", "high", "normal", "low"])
    ap.add_argument("--ttl", type=int, default=7200)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    store, reg, lake, bus, planner, runtime = _build()
    reg.seed()

    if args.priority:
        planner.request_priority(args.priority, args.level, args.ttl, reason="manual")
        print(f"已发调频指令: {args.priority} → {args.level}（ttl={args.ttl}s）")
        return 0
    if args.seed:
        cmd_seed(reg); return 0
    if args.plan:
        cmd_plan(reg, planner); return 0
    if args.health:
        cmd_health(store, reg); return 0
    if args.stats:
        cmd_stats(store, bus); return 0
    if args.dlq:
        cmd_dlq(store); return 0
    if args.reset_cursor:
        store.conn.execute("DELETE FROM source_checkpoint WHERE source_id=?", (args.reset_cursor,))
        store.conn.commit()
        print(f"已清空游标: {args.reset_cursor}（下次采集从头开始）")
        return 0
    if args.velocity:
        from analytics import series, velocity, freshness
        rows = series(store, source_id=args.velocity, limit=500)
        if not rows:
            print(f"[warn] {args.velocity} 没有快照")
            return 0
        by_key: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            by_key.setdefault(r["content_key"], []).append(r)
        print(f"\n== {args.velocity} 的指标速度（{len(by_key)} 条内容 / {len(rows)} 个快照）==")
        shown = 0
        for k, rs in by_key.items():
            v = velocity(rs)
            if not v:
                continue
            print(f"  {k}: " + " → ".join(
                f"[{p['observed_at'][11:19]}] v={p['velocity_per_min']}/min a={p['acceleration']}"
                for p in v[:4]))
            shown += 1
            if shown >= 5:
                break
        if shown == 0:
            print("  快照时点不足 2 个 —— 需要至少两轮采集才能算速度。")
        print(f"\n  freshness: {freshness(store, args.velocity)}")
        return 0
    if args.once or args.only:
        return cmd_run(reg, planner, runtime, only=args.only, run_all=args.all, dry=args.dry_run)

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
