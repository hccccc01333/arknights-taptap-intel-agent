#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Raw Lake —— 不可变原始数据。

★ 核心原则：**原始数据永远不覆盖**。
生产版是 S3/OSS（`s3://trend-data/raw/year=…/platform=…/source=…`），
本机用**本地分区目录**等价实现，路径语义一致，将来换对象存储只改这里。

每次请求一个文件，内容 = {request, response, observed_at, parser_version}。
为什么值得：将来 parser 出 bug，可以拿 Raw Data → 换新 parser → Replay → 重新生成事件。
只存清洗后数据的系统，历史错误永远修不了。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from schema.content_event import now_cn  # noqa: E402  (sys.path 由调用方保证)


class RawLake:
    def __init__(self, root: Optional[str] = None) -> None:
        if root is None:
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # L1_data_source
            root = os.path.join(os.path.dirname(here), "data", "raw_lake")
        self.root = root
        os.makedirs(self.root, exist_ok=True)

    def _dir(self, platform: str, source_id: str, observed_at: Optional[datetime] = None) -> str:
        d = observed_at or now_cn()
        return os.path.join(
            self.root,
            f"year={d.year}", f"month={d.month:02d}", f"day={d.day:02d}",
            f"platform={platform}", f"source={source_id}",
        )

    def write_request(self, source_id: str, platform: str, request_id: str,
                      request_meta: Dict[str, Any], response: List[Dict[str, Any]],
                      observed_at: str, parser_version: str) -> str:
        """落一次请求的原始快照，返回 raw_ref（相对项目根的路径）。"""
        d = self._dir(platform, source_id)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{request_id}.json")
        payload = {
            "request": request_meta,
            "response": response,
            "observed_at": observed_at,
            "parser_version": parser_version,
            "source_id": source_id,
            "platform": platform,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        return os.path.relpath(path, os.path.dirname(os.path.dirname(self.root))).replace("\\", "/")

    def read(self, raw_ref: str) -> Dict[str, Any]:
        abs_path = raw_ref if os.path.isabs(raw_ref) else os.path.join(
            os.path.dirname(os.path.dirname(self.root)), raw_ref)
        with open(abs_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def size_bytes(self, raw_ref: str) -> int:
        abs_path = raw_ref if os.path.isabs(raw_ref) else os.path.join(
            os.path.dirname(os.path.dirname(self.root)), raw_ref)
        try:
            return os.path.getsize(abs_path)
        except OSError:
            return 0
