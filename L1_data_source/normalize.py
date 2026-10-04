#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1 跑批入口：data/raw/<platform>/ → data/events/*.jsonl

这是采集层唯一的出口。跑完必须能回答三个数字：
    每个数据集读了多少条、坏了多少条、坏在哪（给出样例）
这份统计写进 _manifest.json，下游（L2 及之后）只信 manifest 里的数字。

用法：
    python L1_data_source/normalize.py                  # 全量
    python L1_data_source/normalize.py --only taptap    # 单平台
    python L1_data_source/normalize.py --dry-run        # 只统计不落盘
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)          # 中文目录不做包导入，显式挂 path

from schema.content_event import (      # noqa: E402
    ContentEvent, events_to_jsonl, PLATFORM_REGISTRY, TZ_CN,
)
from adapters import ADAPTERS, coverage  # noqa: E402
from quality_gate import gate as quality_gate  # noqa: E402  采集层质量闸门

RAW_DIR = os.path.join(_ROOT, "data", "raw")
EVENT_DIR = os.path.join(_ROOT, "data", "events")
MANIFEST = os.path.join(EVENT_DIR, "_manifest.json")


def normalize_platform(platform: str, dry_run: bool = False, verbose: bool = False) -> Dict[str, Any]:
    adapter = ADAPTERS[platform]
    raw_platform_dir = os.path.join(RAW_DIR, platform)
    out: Dict[str, Any] = {"platform": platform, "datasets": [], "total": 0, "ok": 0,
                            "invalid": 0, "gated": 0, "gated_reasons": {}}

    for ds in adapter.datasets:
        path = os.path.join(raw_platform_dir, ds.filename)
        res = adapter.normalize_file(ds, path)
        raw_events: List[ContentEvent] = getattr(res, "events", [])

        # ★ 质量闸门：低信息量内容在这里被拦下，**不落 events.jsonl**。
        #   实测旧库 3811 条里「好玩」38 条、≤2 字标题 72 条 —— 它们稀释聚类、
        #   污染热度分。拦截统计写进 manifest，让"为什么少了内容"可追溯。
        events: List[ContentEvent] = []
        for ev in raw_events:
            v = quality_gate(title=getattr(ev, "title", None),
                             content=getattr(ev, "content", None),
                             platform=platform, source_type=getattr(ev, "source_type", "post"))
            if v["reject"]:
                out["gated"] += 1
                out["gated_reasons"][v["code"]] = out["gated_reasons"].get(v["code"], 0) + 1
                continue
            events.append(ev)
        res.events = events
        res.ok = len(events)
        res.gated = len(raw_events) - len(events)
        rec = res.as_dict()
        out["datasets"].append(rec)
        out["total"] += res.total
        out["ok"] += res.ok
        out["invalid"] += res.invalid

        if not dry_run and events:
            os.makedirs(EVENT_DIR, exist_ok=True)
            target = os.path.join(EVENT_DIR, f"{platform}__{ds.name}.jsonl")
            events_to_jsonl(events, target)
            rec["output"] = os.path.relpath(target, _ROOT).replace("\\", "/")
        if verbose:
            print(f"  [{platform}:{ds.name}] total={res.total} ok={res.ok} "
                  f"invalid={res.invalid} gated={getattr(res, 'gated', 0)}")
            for p in res.problems[:3]:
                print(f"      ! {p}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="L1 信号采集层：原始数据 → 统一 Content Event")
    ap.add_argument("--only", help="只跑指定平台（taptap/bilibili/douyin/weibo）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    targets = [args.only] if args.only else list(ADAPTERS.keys())
    unknown = [t for t in targets if t not in ADAPTERS]
    if unknown:
        print(f"[error] 没有适配器的平台: {unknown}；已接入: {list(ADAPTERS)}")
        return 2

    started = datetime.now(TZ_CN)
    report: Dict[str, Any] = {
        "generated_at": started.isoformat(),
        "layer": "L1_data_source",
        "platforms": [],
        "coverage": coverage(),
        "totals": {},
    }
    gt = go = gi = gg = 0
    grep_: Dict[str, int] = {}
    for p in targets:
        r = normalize_platform(p, dry_run=args.dry_run, verbose=args.verbose)
        report["platforms"].append(r)
        gt += r["total"]; go += r["ok"]; gi += r["invalid"]; gg += r.get("gated", 0)
        for k, v in (r.get("gated_reasons") or {}).items():
            grep_[k] = grep_.get(k, 0) + v
        print(f"[{p}] total={r['total']} ok={r['ok']} invalid={r['invalid']} "
              f"gated={r.get('gated', 0)}")

    report["totals"] = {"total": gt, "ok": go, "invalid": gi, "gated": gg}
    report["gated_reasons"] = grep_
    if not args.dry_run:
        os.makedirs(EVENT_DIR, exist_ok=True)
        with open(MANIFEST, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\nmanifest -> {os.path.relpath(MANIFEST, _ROOT)}")
    print(f"合计 total={gt} 通过闸门={go} 结构损坏={gi} 质量拦截={gg}")
    if grep_:
        print("  拦截明细：" + "、".join(f"{k}={v}" for k, v in sorted(grep_.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
