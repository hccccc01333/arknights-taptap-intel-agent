#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Schema Validation（§5）—— 第一层数据进来先挡一道。

第一层可能因平台变化出现：字段消失、类型变化、时间格式变化、空值暴增、异常 JSON。
**不要因为一条坏记录把整个 batch 干掉** —— 单条进 DLQ，其余继续。

DLQ 记录必须带：source_id / schema_version / error_field / error_type / raw_ref，
否则事后无法回答"这批数据为什么少了"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

REQUIRED_FIELDS = ("event_id", "platform", "external_id", "observed_at")
REQUIRED_NON_EMPTY = ("platform", "external_id")


class SchemaError(Exception):
    def __init__(self, error_field: str, error_type: str, detail: str = "") -> None:
        super().__init__(f"{error_field}: {error_type}")
        self.error_field = error_field
        self.error_type = error_type
        self.detail = detail


def validate_raw_event(ev: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]]]:
    """校验一条第一层事件。返回 (是否合法, 错误列表)。"""
    errors: List[Dict[str, Any]] = []

    for f in REQUIRED_FIELDS:
        if f not in ev or ev.get(f) in (None, ""):
            errors.append({"error_field": f, "error_type": "missing_or_empty",
                           "detail": f"必填字段缺失或为空: {f}"})
    for f in REQUIRED_NON_EMPTY:
        v = ev.get(f)
        if isinstance(v, str) and not v.strip():
            errors.append({"error_field": f, "error_type": "empty_string", "detail": f"{f} 是空串"})

    # 类型检查：指标必须是数字或 None（字符串数字允许，转不了就报错）
    for f in ("views", "likes", "comments", "shares", "favorites", "rank"):
        v = ev.get(f)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append({"error_field": f, "error_type": "type_mismatch",
                           "detail": f"{f}={v!r} 不是数字"})

    # 时间格式
    for f in ("published_at", "observed_at", "crawled_at"):
        v = ev.get(f)
        if v is not None and not isinstance(v, str):
            errors.append({"error_field": f, "error_type": "type_mismatch", "detail": f"{f} 不是字符串"})

    # 内容不能全空（没有内容就没有加工的必要）
    if not (ev.get("title") or ev.get("content")):
        errors.append({"error_field": "content", "error_type": "empty_content",
                       "detail": "title 与 content 全空"})

    return (len(errors) == 0), errors


def dlq_record(ev: Dict[str, Any], err: Dict[str, Any]) -> Dict[str, Any]:
    """按 §5 生成 DLQ 记录（带齐溯源字段）。"""
    return {
        "source_id": ev.get("source_id", ""),
        "platform": ev.get("platform", ""),
        "schema_version": ev.get("schema_version", ""),
        "error_field": err["error_field"],
        "error_type": err["error_type"],
        "detail": err.get("detail", ""),
        "raw_ref": ev.get("raw_ref", ""),
        "event_id": ev.get("event_id", ""),
        "external_id": str(ev.get("external_id", ""))[:64],
    }
