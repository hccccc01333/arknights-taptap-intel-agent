#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Connector 抽象层：所有 Connector 实现同一个接口。

★ 业务代码永远不要关心「这条数据是 API 来的还是网页抓的」。
无论 ApiConnector / RSSConnector / WebConnector / WebhookConnector / InternalDataConnector，
输出都统一是 FetchResult。

规格里给的是 async；本机采集器是同步的（stdlib + 本地文件），
所以这里定义**同步接口**，语义与 async 版一一对应（cursor / since / has_more），
将来接真实 API 时包一层 asyncio 即可，不改下游。
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_L1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _L1 not in sys.path:
    sys.path.insert(0, _L1)

from schema.content_event import RawContentEvent, now_cn  # noqa: E402


class FailureType:
    """失败分类 —— 不同失败必须区别对待，不能一律 retry。"""

    TIMEOUT = "timeout"                 # → 重试
    SERVER_ERROR = "server_error"       # 5xx → 指数退避
    RATE_LIMIT = "rate_limit"           # → 延长 interval（不是重试更凶）
    AUTH = "auth"                       # → 告警，不盲目 retry
    PARSE_ERROR = "parse_error"         # → DLQ
    SCHEMA_DRIFT = "schema_drift"       # → Schema 告警
    INVALID_RECORD = "invalid_record"   # → DLQ
    SOURCE_DOWN = "source_down"         # → 熔断
    NOT_IMPLEMENTED = "not_implemented"

    RETRYABLE = {TIMEOUT, SERVER_ERROR}
    BACKOFF = {TIMEOUT, SERVER_ERROR, RATE_LIMIT}
    TO_DLQ = {PARSE_ERROR, INVALID_RECORD}


class ConnectorError(Exception):
    """带失败分类的异常，Runtime 按分类决定重试 / 退避 / DLQ / 熔断。"""

    def __init__(self, failure_type: str, message: str, detail: Optional[Dict] = None) -> None:
        super().__init__(message)
        self.failure_type = failure_type
        self.detail = detail or {}


@dataclass
class FetchResult:
    """统一输出：一批记录 + 游标。"""

    records: List[Dict[str, Any]] = field(default_factory=list)
    next_cursor: Optional[str] = None
    has_more: bool = False
    request_id: str = ""
    raw_ref: str = ""              # 本次请求落 Raw Lake 的指针
    observed_at: str = ""          # 这批记录被观察到的时刻
    metadata: Dict[str, Any] = field(default_factory=dict)


class SourceConnector:
    """所有 Connector 的基类。子类实现 fetch / health_check / parse。"""

    #: 采集方式：api / rss / web / webhook / internal
    acquisition_mode = "internal"

    def __init__(self, source_cfg: Dict[str, Any]) -> None:
        self.cfg = source_cfg
        self.source_id = source_cfg.get("source_id", "")
        self.platform = source_cfg.get("platform", "")
        self.parser_version = source_cfg.get("parser_version", "")

    # —— 采集 ——
    def fetch(self, cursor: Optional[str] = None, since: Optional[str] = None) -> FetchResult:
        raise ConnectorError(FailureType.NOT_IMPLEMENTED, f"{type(self).__name__}.fetch 未实现")

    def health_check(self) -> Dict[str, Any]:
        """默认健康：子类可覆盖（真实 API 应探活）。"""
        return {"ok": True, "checked_at": now_cn().isoformat()}

    # —— 协议标准化（Protocol Adapter）——
    def parse(self, record: Dict[str, Any], ctx: Dict[str, Any]) -> RawContentEvent:
        """一条原始记录 → RawContentEvent。lineage 字段由 Runtime 通过 ctx 注入。"""
        raise ConnectorError(FailureType.NOT_IMPLEMENTED, f"{type(self).__name__}.parse 未实现")

    def new_request_id(self) -> str:
        return f"{self.source_id}-{uuid.uuid4().hex[:12]}"
