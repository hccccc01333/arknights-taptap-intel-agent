#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Event Bus —— 第一层与第二层之间唯一的接口。

生产版是 Kafka / Redpanda，topic 设计（与规格一致）：
    raw.content.created / raw.content.updated / raw.metric.snapshot / raw.rank.snapshot
    source.health / source.failure / source.schema.error
    acquisition.priority.command   ← 第三层反向控制采集频率（事件，不是 API 调用）
    deadletter.raw

本机无 Kafka → 用 **append-only JSONL（按 topic 分目录按天分文件）** 等价实现：
语义相同（按 topic 发布/订阅、可回溯、可重放），只是吞吐与分布式能力不同。
换 Kafka 时替换 `publish` / `consume` 两个方法即可，topic 名不变。

★ 关键设计：**Trend Engine 给 Crawl Planner 发的是事件，不是 API 调用** ——
这样第一层和第三层不形成强耦合，任何一方挂掉另一方照常跑。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator, List, Optional

from schema.content_event import now_cn  # noqa: E402

TOPICS = {
    "raw.content.created": "新内容事件",
    "raw.content.updated": "已知内容的新版本",
    "raw.metric.snapshot": "指标快照（时间 × 指标）",
    "raw.rank.snapshot":   "榜单/排名快照",
    "source.health":       "数据源健康",
    "source.failure":      "采集失败",
    "source.schema.error": "Schema drift / 校验失败",
    "acquisition.priority.command": "★ 第三层→第一层：调频指令（含 ttl）",
    "deadletter.raw":      "死信（可 replay）",

    # —— L2 加工层产出（第二层 → 第三层的接口）——
    "processed.content.created": "标准化内容（CanonicalContent）",
    "processed.content.updated": "已知内容的新版本",
    "processed.metric.feature": "指标特征（百分位 / 速度）",
    "processed.entity.detected": "实体抽取与链接结果",
    "processed.content.deleted": "内容下架/删除",
    "processing.data_quality": "加工层数据质量指标",
    "processing.dlq.schema":   "Schema 校验失败（带 error_field）",

    # —— L3 趋势层产出（第三层 → 第四层的接口）——
    "trend.event.created":       "新事件",
    "trend.event.updated":       "事件指标更新",
    "trend.event.emerging":      "进入 Emerging（最有行动价值的窗口）",
    "trend.event.growing":       "进入 Growing",
    "trend.event.peaking":       "进入 Peaking",
    "trend.event.declining":     "进入 Declining",
    "trend.event.merged":        "事件合并（保留历史）",
    "trend.event.split":         "事件拆分",
    "trend.event.high_priority": "★ 第四层的触发门（hot/momentum/confidence 全达标）",
    "trend.event.early_alert":   "★ 早期预警（加速度极高但热度还不高）",
}


class EventBus:
    def __init__(self, root: Optional[str] = None) -> None:
        if root is None:
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            root = os.path.join(os.path.dirname(here), "data", "event_bus")
        self.root = root
        os.makedirs(self.root, exist_ok=True)

    def _file(self, topic: str) -> str:
        safe = topic.replace(".", "_")
        d = os.path.join(self.root, safe)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{now_cn().date().isoformat()}.jsonl")

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        if topic not in TOPICS:
            raise KeyError(f"未登记的 topic: {topic}")
        rec = {"topic": topic, "published_at": now_cn().isoformat(), "payload": payload}
        with open(self._file(topic), "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def publish_many(self, topic: str, payloads: List[Dict[str, Any]]) -> int:
        with open(self._file(topic), "a", encoding="utf-8", newline="") as fh:
            for p in payloads:
                fh.write(json.dumps({"topic": topic, "published_at": now_cn().isoformat(),
                                     "payload": p}, ensure_ascii=False) + "\n")
        return len(payloads)

    def consume(self, topic: str, since: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        """按时间顺序读（第二层只订阅 raw.content.* 与 raw.metric.*）。"""
        safe = topic.replace(".", "_")
        d = os.path.join(self.root, safe)
        if not os.path.isdir(d):
            return
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".jsonl"):
                continue
            with open(os.path.join(d, fn), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if since and rec.get("published_at", "") < since:
                        continue
                    yield rec

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for topic in TOPICS:
            safe = topic.replace(".", "_")
            d = os.path.join(self.root, safe)
            n = 0
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.endswith(".jsonl"):
                        with open(os.path.join(d, fn), "r", encoding="utf-8") as fh:
                            n += sum(1 for _ in fh)
            out[topic] = n
        return out
