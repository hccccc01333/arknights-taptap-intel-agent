#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Connector Runtime —— 采集运行时。

一个 Source 的一轮采集在这里完成：
    health_check → fetch(cursor) → 落 Raw Lake → parse → validate
    → 发事件 → 存 Snapshot → 更新 checkpoint / health → 失败分类处理

★ 必须第一天就做对的四件事：
1. **幂等**：content_key = platform:external_id（内容身份），
   snapshot_id = hash(source_id+external_id+observed_at)（观察身份）。
   同一视频抓 10 次 = 1 个 Content + 10 个 Observation，不是 10 条内容。
2. **Checkpoint**：崩了从 cursor 续，不从头抓。
3. **失败分类**：Timeout 重试 / 5xx 退避 / 限流延长间隔 / 鉴权告警不盲重试 /
   Parser 错与脏记录进 DLQ / 连续失败熔断。**不能一律 retry**。
4. **限流与并发**：按注册表里的 rate_limit_per_minute / max_concurrency 执行，不写在代码里。
"""

from __future__ import annotations

import os
import sys
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional

_L1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _L1 not in sys.path:
    sys.path.insert(0, _L1)

from schema.content_event import (  # noqa: E402
    RawContentEvent, MetricSnapshot, validate_event, now_cn, content_key, make_event_id,
)
from connectors.base import ConnectorError, FailureType  # noqa: E402
from connectors.api_web import build_connector  # noqa: E402
from storage.raw_lake import RawLake  # noqa: E402
from bus.event_bus import EventBus  # noqa: E402

MAX_RETRIES = 3
CIRCUIT_THRESHOLD = 5        # 连续失败多少次后熔断


class ConnectorRuntime:
    def __init__(self, store: Any, lake: Optional[RawLake] = None,
                 bus: Optional[EventBus] = None) -> None:
        self.store = store
        self.lake = lake or RawLake()
        self.bus = bus or EventBus()

    # ---------- 单轮采集 ----------
    def run_source(self, src: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
        source_id = src["source_id"]
        run_id = f"{source_id}-{now_cn().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        started = now_cn()
        stats: Dict[str, Any] = {
            "source_id": source_id, "platform": src.get("platform"), "run_id": run_id,
            "status": "pending", "records": 0, "events_ok": 0, "events_invalid": 0,
            "snapshots": 0, "duplicates": 0, "dlq": 0, "error_type": None, "elapsed_ms": 0,
        }

        connector = build_connector(src)
        ckpt = self.store.get_checkpoint(source_id)
        cursor = ckpt.get("cursor")

        # 健康检查 + 熔断判断
        health = self.store.get_health(source_id)
        if health.get("circuit_state") == "open":
            stats.update(status="skipped", error_type="circuit_open")
            self._finish_run(stats, src, run_id, started)
            return stats

        # 限流：按注册表配置（令牌桶的最小实现：按速率间隔等待）
        self._throttle(src)

        # 采集（带重试与退避）
        result = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                t0 = time.time()
                result = connector.fetch(cursor=cursor, since=ckpt.get("last_published_at"))
                elapsed = (time.time() - t0) * 1000
                timeout_ms = int(src.get("timeout_seconds") or 30) * 1000
                if elapsed > timeout_ms:
                    raise ConnectorError(FailureType.TIMEOUT,
                                         f"采集超时 {elapsed:.0f}ms > {timeout_ms}ms")
                stats["elapsed_ms"] = int(elapsed)
                break
            except ConnectorError as exc:
                if exc.failure_type in (FailureType.NOT_IMPLEMENTED, FailureType.AUTH,
                                        FailureType.PARSE_ERROR, FailureType.SCHEMA_DRIFT):
                    stats.update(status="failed", error_type=exc.failure_type)
                    self._handle_failure(src, exc, {"run_id": run_id, "attempt": attempt})
                    self._finish_run(stats, src, run_id, started)
                    return stats
                if attempt == MAX_RETRIES:
                    stats.update(status="failed", error_type=exc.failure_type)
                    self._handle_failure(src, exc, {"run_id": run_id, "attempt": attempt})
                    self._finish_run(stats, src, run_id, started)
                    return stats
                time.sleep(0.4 * (2 ** (attempt - 1)))      # 指数退避（本机用短退避）
            except Exception as exc:                        # noqa: BLE001
                stats.update(status="failed", error_type="unknown",
                             error_detail=f"{type(exc).__name__}: {exc}")
                self._handle_failure(src, ConnectorError(FailureType.SOURCE_DOWN, str(exc)),
                                     {"run_id": run_id, "trace": traceback.format_exc()[-500:]})
                self._finish_run(stats, src, run_id, started)
                return stats

        if result is None:
            stats.update(status="failed", error_type="empty_result")
            self._finish_run(stats, src, run_id, started)
            return stats

        # ---------- 落不可变原始数据 ----------
        raw_ref = ""
        if not dry_run:
            raw_ref = self.lake.write_request(
                source_id=source_id, platform=src.get("platform", ""),
                request_id=result.request_id or connector.new_request_id(),
                request_meta={"cursor": cursor, "source": source_id, "mode": src.get("acquisition_mode")},
                response=result.records, observed_at=result.observed_at,
                parser_version=src.get("parser_version", ""))
            self.store.index_raw(raw_ref, source_id, result.observed_at,
                                 src.get("parser_version", ""), self.lake.size_bytes(raw_ref))

        # ---------- 协议标准化 + 校验 + 分发 ----------
        ctx_base = {
            "source_id": source_id,
            "request_id": result.request_id,
            "crawl_run_id": run_id,
            "observed_at": result.observed_at,
            "crawled_at": now_cn().isoformat(),
            "signal_type": src.get("signal_type", "community"),
            "compliance": src.get("compliance_config") or {},
            "raw_ref": raw_ref,
        }
        events_payload: List[Dict[str, Any]] = []
        snap_payload: List[Dict[str, Any]] = []

        for rec in result.records:
            stats["records"] += 1
            try:
                ev: RawContentEvent = connector.parse(rec, ctx_base)
            except ConnectorError as exc:
                stats["dlq"] += 1
                if not dry_run:
                    self._to_dlq(source_id, exc.failure_type, rec, run_id)
                continue
            if not ev.event_id:
                ev.event_id = make_event_id(ev.platform, ev.content_type, ev.external_id,
                                            ev.raw_ref or raw_ref)
            problems = validate_event(ev)
            if problems:
                stats["events_invalid"] += 1
                if len(stats.get("problems_sample", [])) < 3:
                    stats.setdefault("problems_sample", []).append(
                        f"{ev.external_id or '?'}: {'; '.join(problems[:3])}")
                if not dry_run and len(problems) and "schema" in " ".join(problems).lower():
                    self.bus.publish("source.schema.error", {"source_id": source_id, "problems": problems})
                if not dry_run:
                    self._to_dlq(source_id, FailureType.INVALID_RECORD, rec, run_id,
                                 problems=problems)
                continue

            ck = content_key(ev.platform, ev.external_id)
            existed = self._content_seen(ck)
            if existed:
                stats["duplicates"] += 1
            stats["events_ok"] += 1

            snap = MetricSnapshot(
                source_id=source_id, external_id=ev.external_id,
                observed_at=ev.observed_at or ctx_base["observed_at"],
                views=ev.views, likes=ev.likes, comments=ev.comments,
                shares=ev.shares, favorites=ev.favorites, rank=ev.rank,
                content_key=ck, metadata={"title": (ev.title or "")[:40]},
            )
            if not dry_run:
                self.store.save_snapshot({
                    "snapshot_id": snap.snapshot_id, "source_id": source_id,
                    "content_key": ck, "external_id": ev.external_id,
                    "observed_at": snap.observed_at, "views": snap.views,
                    "likes": snap.likes, "comments": snap.comments, "shares": snap.shares,
                    "favorites": snap.favorites, "rank": snap.rank, "raw_ref": raw_ref,
                })
                stats["snapshots"] += 1
                events_payload.append(ev.to_dict())
                snap_payload.append(snap.to_dict())

        if not dry_run and events_payload:
            self.bus.publish_many("raw.content.created", events_payload)
        if not dry_run and snap_payload:
            self.bus.publish_many("raw.metric.snapshot", snap_payload)

        # ---------- checkpoint（崩了从这续）----------
        if not dry_run and result.records:
            last_line = max((r.get("__line__", 0) for r in result.records), default=0)
            self.store.save_checkpoint(
                source_id,
                cursor=result.next_cursor or (str(last_line + 1) if last_line else None),
                last_external_id=str(result.records[-1].get("__line__", "")) or None,
                metadata={"run_id": run_id, "records": len(result.records)},
            )

        stats["status"] = "ok"
        self._update_health_ok(src, stats, result.observed_at)
        self._finish_run(stats, src, run_id, started)
        return stats

    def run_all(self, sources: List[Dict[str, Any]], dry_run: bool = False) -> List[Dict[str, Any]]:
        return [self.run_source(s, dry_run=dry_run) for s in sources]

    # ---------- 内部 ----------
    def _throttle(self, src: Dict[str, Any]) -> None:
        """按 rate_limit_per_minute 限制请求频率（本机单线程，最小实现）。"""
        rpm = int(src.get("rate_limit_per_minute") or 60)
        if rpm <= 0:
            return
        key = f"_last_call:{src['source_id']}"
        last = getattr(self, "_last_call", {}).get(src["source_id"])
        if last is not None:
            min_gap = 60.0 / rpm
            gap = time.time() - last
            if gap < min_gap:
                time.sleep(min_gap - gap)
        if not hasattr(self, "_last_call"):
            self._last_call = {}
        self._last_call[src["source_id"]] = time.time()

    def _content_seen(self, ck: str) -> bool:
        r = self.store.conn.execute(
            "SELECT 1 FROM metric_snapshot WHERE content_key=? LIMIT 1", (ck,)).fetchone()
        return r is not None

    def _to_dlq(self, source_id: str, error_type: str, record: Dict, run_id: str,
                problems: Optional[List[str]] = None) -> None:
        payload = {"run_id": run_id, "record_keys": sorted(list(record.keys()))[:20],
                   "problems": problems or []}
        self.store.push_dlq(uuid.uuid4().hex[:16], source_id, error_type, payload)
        self.bus.publish("deadletter.raw", {"source_id": source_id, "error_type": error_type, **payload})

    def _handle_failure(self, src: Dict[str, Any], exc: ConnectorError, extra: Dict) -> None:
        source_id = src["source_id"]
        health = self.store.get_health(source_id)
        fails = int(health.get("consecutive_failures") or 0) + 1
        circuit = "open" if fails >= CIRCUIT_THRESHOLD else "closed"
        self.store.update_health(
            source_id, consecutive_failures=fails, circuit_state=circuit,
            rate_limit_count=int(health.get("rate_limit_count") or 0)
            + (1 if exc.failure_type == FailureType.RATE_LIMIT else 0),
            parse_error_rate=1.0 if exc.failure_type == FailureType.PARSE_ERROR else None,
        )
        self.bus.publish("source.failure", {
            "source_id": source_id, "error_type": exc.failure_type,
            "message": str(exc), "consecutive_failures": fails, "circuit_state": circuit, **extra,
        })
        if exc.failure_type in FailureType.TO_DLQ:
            self.store.push_dlq(uuid.uuid4().hex[:16], source_id, exc.failure_type,
                                {"message": str(exc), **extra})

    def _update_health_ok(self, src: Dict[str, Any], stats: Dict[str, Any], observed_at: str) -> None:
        source_id = src["source_id"]
        health = self.store.get_health(source_id)
        prev_rate = float(health.get("success_rate") or 0.0)
        prev_count = int(health.get("records_per_run") or 0)
        # 简单滑动：新成功率权重 0.3
        success_rate = round(prev_rate * 0.7 + 0.3 * 1.0, 4) if prev_rate else 1.0
        self.store.update_health(
            source_id,
            success_rate=success_rate,
            request_latency_ms=float(stats.get("elapsed_ms") or 0),
            records_per_run=(prev_count * 0.7 + 0.3 * stats["records"]) if prev_count else stats["records"],
            consecutive_failures=0, circuit_state="closed",
        )
        self.bus.publish("source.health", {
            "source_id": source_id, "status": "ok", "records": stats["records"],
            "events": stats["events_ok"], "invalid": stats["events_invalid"],
        })

    def _finish_run(self, stats: Dict[str, Any], src: Dict[str, Any], run_id: str, started) -> None:
        self.store.record_run({
            "run_id": run_id, "source_id": src["source_id"], "request_id": "",
            "started_at": started.isoformat(), "finished_at": now_cn().isoformat(),
            "status": stats["status"], "records": stats["records"],
            "error_type": stats.get("error_type"), "error_detail": stats.get("error_detail", ""),
        })
