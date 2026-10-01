#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""InternalDataConnector —— 读已落盘的本地数据（CSV / JSONL），本机唯一真实可用的 Connector。

★ 诚实边界：本机没有外网采集凭据，也没有 API 配额，
所以 api / rss / web / webhook 四类 Connector 目前只有**接口与骨架**（见 api_web.py），
真正跑得起来的是这个 internal —— 它读 `data/raw/<platform>/` 下已采集的文件。
这不是设计妥协，是能力边界：把边界写清楚，比假装"全网已接入"有用。

它同时演示了完整链路该有的东西：游标续采（cursor=行号）、since 过滤、
协议标准化（复用 adapters 的字段映射）、以及每条记录带 raw_ref。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_L1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _L1 not in sys.path:
    sys.path.insert(0, _L1)

from schema.content_event import RawContentEvent, to_iso, hash_author, now_cn  # noqa: E402
from connectors.base import SourceConnector, FetchResult, ConnectorError, FailureType  # noqa: E402
from adapters import taptap as tap_ad, bilibili as bili_ad, douyin as dy_ad, weibo as wb_ad  # noqa: E402

# dataset → (解析函数, content_type)
PARSERS: Dict[Tuple[str, str], Tuple[Any, str]] = {
    ("taptap", "moments"):       (tap_ad._moment,  "moment"),
    ("taptap", "comments"):      (tap_ad._comment, "comment"),
    ("taptap", "hashtags"):      (tap_ad._hashtag, "hashtag"),
    ("taptap", "reviews"):       (tap_ad._review,  "review"),
    ("bilibili", "videos"):      (bili_ad._video,  "video"),
    ("bilibili", "comments"):    (bili_ad._comment, "comment"),
    ("douyin", "videos"):        (dy_ad._video,    "video"),
    ("douyin", "comments"):      (dy_ad._comment,  "comment"),
    ("weibo", "posts"):          (wb_ad._post,     "post"),
    ("weibo", "comments"):       (wb_ad._comment,  "comment"),
}


def _first_line(text: str, limit: int = 40) -> str:
    t = (text or "").strip().replace("\r", " ").replace("\n", " ")
    return t[:limit] + ("…" if len(t) > limit else "") if t else ""


class InternalDataConnector(SourceConnector):
    """读本地已采集文件。cursor = 已消费到的行号（崩了就从这里续，不从头抓）。"""

    acquisition_mode = "internal"

    def __init__(self, source_cfg: Dict[str, Any]) -> None:
        super().__init__(source_cfg)
        root = os.path.dirname(_L1)
        self.dataset = (source_cfg.get("config") or {}).get("dataset", "")
        rel = (source_cfg.get("config") or {}).get("file", "")
        self.path = os.path.join(root, "data", "raw", self.platform, rel)
        self.batch_size = int((source_cfg.get("config") or {}).get("batch_size", 500))

    # ---------- fetch ----------
    def fetch(self, cursor: Optional[str] = None, since: Optional[str] = None) -> FetchResult:
        if not os.path.exists(self.path):
            raise ConnectorError(FailureType.SOURCE_DOWN, f"数据文件不存在: {self.path}")

        start_line = int(cursor) if cursor and cursor.isdigit() else 2   # 1 是表头
        records: List[Dict[str, Any]] = []

        if self.path.endswith(".jsonl"):
            records = self._read_jsonl(start_line, since)
        else:
            records = self._read_csv(start_line)

        # 简单分页：一次最多 batch_size 条，剩下的靠 cursor 续
        has_more = len(records) > self.batch_size
        page = records[: self.batch_size]
        next_cursor = str(start_line + len(page)) if has_more else None
        return FetchResult(
            records=page,
            next_cursor=next_cursor,
            has_more=has_more,
            request_id=self.new_request_id(),
            observed_at=now_cn().isoformat(),
            metadata={"path": os.path.relpath(self.path, os.path.dirname(_L1)), "cursor": start_line},
        )

    def _read_csv(self, start_line: int) -> List[Dict[str, Any]]:
        import csv
        out = []
        with open(self.path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            for idx, row in enumerate(reader, start=2):
                if idx < start_line:
                    continue
                row["__line__"] = idx
                out.append(row)
        return out

    def _read_jsonl(self, start_line: int, since: Optional[str]) -> List[Dict[str, Any]]:
        out = []
        with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
            for idx, line in enumerate(fh, start=1):
                if idx < start_line:
                    continue
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if since and (obj.get("crawled_at") or "") < since:
                    continue
                obj["__line__"] = idx
                out.append(obj)
        return out

    def health_check(self) -> Dict[str, Any]:
        ok = os.path.exists(self.path)
        size = os.path.getsize(self.path) if ok else 0
        return {"ok": ok, "size_bytes": size, "path": self.path, "checked_at": now_cn().isoformat()}

    # ---------- 协议标准化 ----------
    def parse(self, record: Dict[str, Any], ctx: Dict[str, Any]) -> RawContentEvent:
        src_id = ctx.get("source_id", self.source_id)
        raw_ref = ctx.get("raw_ref", "")
        lineno = record.get("__line__", 0)

        if self.dataset == "announcements":
            ev = self._parse_announcement(record, src_id, raw_ref, lineno)
        else:
            key = (self.platform, self.dataset)
            if key not in PARSERS:
                raise ConnectorError(FailureType.PARSE_ERROR,
                                     f"没有对应的解析器: platform={self.platform} dataset={self.dataset}")
            fn, content_type = PARSERS[key]
            ev = fn(record, f"{os.path.basename(self.path)}:{lineno}", ctx)
            ev.content_type = ev.content_type or content_type

        # ★ lineage 与三个时间由 Runtime 注入（parser 不该关心这些）
        ev.source_id = src_id
        ev.platform = self.platform or ev.platform
        ev.signal_type = ctx.get("signal_type", ev.signal_type or "community")
        ev.observed_at = ev.observed_at or ctx.get("observed_at")
        ev.crawled_at = ctx.get("crawled_at") or now_cn().isoformat()
        ev.request_id = ctx.get("request_id", "")
        ev.crawl_run_id = ctx.get("crawl_run_id", "")
        ev.parser_version = self.parser_version or ev.parser_version
        if raw_ref:
            ev.raw_ref = raw_ref
        ev.sync_metrics()

        # 合规：未许可 PII 时，明文作者名不进事件（raw lake 里才有）
        if not (ctx.get("compliance") or {}).get("contains_pii", False):
            ev.author_name = None
        return ev

    def _parse_announcement(self, rec: Dict[str, Any], src_id: str, raw_ref: str, lineno: int) -> RawContentEvent:
        text = rec.get("raw_text") or rec.get("text") or ""
        return RawContentEvent(
            platform=self.platform,
            source_id=src_id,
            content_type="article",
            signal_type="official",
            title=_first_line(text, 40),
            content=text,
            author_id="unknown",
            published_at=to_iso(rec.get("publish_time") or rec.get("crawled_at")),
            external_id=str(rec.get("announcement_id") or rec.get("id") or lineno),
            url=rec.get("url"),
            raw_ref=f"{os.path.basename(self.path)}:{lineno}",
            metadata={"game": rec.get("game")},
        )
