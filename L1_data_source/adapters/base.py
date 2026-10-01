#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""适配器基类：定义「一个平台的数据集 → ContentEvent 流」的契约。

每个平台的数据往往不止一张表（B站有视频表和评论表，TapTap 有动态/评论/话题/评分），
所以一个 Adapter 可以声明多个 dataset（数据集），每个 dataset 负责一类文件。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from schema.content_event import (
    ContentEvent,
    make_event_id,
    parse_int,
    to_iso,
    hash_author,
    validate_event,
)


@dataclass
class Dataset:
    """一个具体的数据集（一张 CSV / 一个目录下的同类文件）。"""

    name: str                       # 数据集名，如 taptap_moments
    filename: str                   # 相对 data/raw/<platform>/ 的文件名
    source_type: str                # 产出的 source_type
    row_to_event: Callable          # (row, raw_ref, ctx) -> ContentEvent
    required: bool = True           # 文件不存在时是否算错误


@dataclass
class NormalizeResult:
    """一次归一化的结果，带统计——跑批后要能回答"哪张表坏了几条"。"""

    dataset: str
    total: int = 0
    ok: int = 0
    invalid: int = 0
    problems: List[str] = None      # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.problems is None:
            self.problems = []

    def as_dict(self) -> Dict:
        return {
            "dataset": self.dataset,
            "total": self.total,
            "ok": self.ok,
            "invalid": self.invalid,
            "problems_sample": self.problems[:5],
        }


class BaseAdapter:
    """平台适配器。子类只需声明 platform 与 datasets。"""

    platform: str = ""
    datasets: List[Dataset] = []

    def normalize_file(self, dataset: Dataset, path: str) -> NormalizeResult:
        """读一个文件 → 事件列表 + 统计。文件不存在按 required 处理。"""
        from schema.content_event import read_csv_rows  # 延迟导入避免循环

        res = NormalizeResult(dataset=dataset.name)
        if not os.path.exists(path):
            if dataset.required:
                res.problems.append(f"缺少文件: {path}")
            return res

        rel = os.path.basename(path)
        events: List[ContentEvent] = []
        for lineno, row in read_csv_rows(path):
            raw_ref = f"{rel}:{lineno}"
            try:
                ev = dataset.row_to_event(row, raw_ref, {"path": path})
            except Exception as exc:                      # 单行坏了不让整批崩
                res.total += 1
                res.invalid += 1
                if len(res.problems) < 5:
                    res.problems.append(f"{raw_ref} 解析异常: {type(exc).__name__}: {exc}")
                continue
            if ev is None:                                # 适配器主动丢弃（如空行）
                continue
            if not ev.event_id:
                ev.event_id = make_event_id(ev.platform, ev.source_type, ev.native_id, raw_ref)
            if not ev.collected_at:
                ev.collected_at = to_iso(row.get("crawled_at"))
            res.total += 1
            problems = validate_event(ev)
            if problems:
                res.invalid += 1
                if len(res.problems) < 5:
                    res.problems.append(f"{raw_ref}: {'; '.join(problems)}")
                continue
            res.ok += 1
            events.append(ev)
        res.events = events                               # type: ignore[attr-defined]
        return res


def pick(row: Dict, *keys: str, default: str = "") -> str:
    """按优先级取第一个非空字段。"""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default


def first_line(text: str, limit: int = 40) -> str:
    """用正文首行补标题——很多平台（微博/评论）根本没有标题字段。"""
    t = (text or "").strip().replace("\r", " ").replace("\n", " ")
    if not t:
        return ""
    return t[:limit] + ("…" if len(t) > limit else "")


__all__ = ["Dataset", "NormalizeResult", "BaseAdapter", "pick", "first_line",
           "parse_int", "to_iso", "hash_author", "make_event_id", "ContentEvent"]
