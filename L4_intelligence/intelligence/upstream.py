# -*- coding: utf-8 -*-
"""第四层的上游读取：只读第三层 Event Store 与第二层 Processed Store。

★ 文本纪律（与 `docs/任务契约.md` §2.1 一致）：
  本模块**能**读到原文，但**不把原文放进 State**。
  `read_full_text(content_id)` 只在节点内部作为临时输入使用（如 Research 的深读），
  返回值不写回 State —— State 里永远只有 id + 短引文 + 结论。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "L2_signal"), os.path.join(_ROOT, "L1_data_source")):
    if p not in sys.path:
        sys.path.insert(0, p)

L3_DB = os.path.join(_ROOT, "data", "state", "l3_trend.sqlite3")

_JSON_FIELDS = ("metrics", "platform_metrics", "entities", "topics", "extracted",
                "features", "source_features", "processor_versions", "subcategory")


def hydrate(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for k in _JSON_FIELDS:
        v = out.get(k)
        if isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except (ValueError, TypeError):
                out[k] = {} if k.endswith(("metrics", "features")) else []
    return out


class Upstream:
    """L3 事件 + L2 内容的只读访问。"""

    def __init__(self, l2_store: Optional[Any] = None, l3_db: Optional[str] = None):
        try:
            from processing.storage import ProcessedStore
            self.l2 = l2_store or ProcessedStore()
        except Exception:                       # pragma: no cover - 依赖缺失时显式降级
            self.l2 = None
        self.l3_db = l3_db or L3_DB
        self._conn: Optional[sqlite3.Connection] = None
        self._content_index: Optional[Dict[str, Dict[str, Any]]] = None

    # ---------- L3 ----------
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            if not os.path.exists(self.l3_db):
                raise FileNotFoundError(f"第三层事件库不存在：{self.l3_db}（先跑 L3_trend/trend_engine/pipeline.py --run）")
            self._conn = sqlite3.connect(self.l3_db)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def events(self, limit: int = 5000, status: str = "active") -> List[Dict[str, Any]]:
        rows = self._db().execute(
            "SELECT * FROM trend_event WHERE status=? ORDER BY content_count DESC LIMIT ?",
            (status, limit)).fetchall()
        return [dict(r) for r in rows]

    def event(self, event_id: str) -> Optional[Dict[str, Any]]:
        r = self._db().execute("SELECT * FROM trend_event WHERE event_id=?", (event_id,)).fetchone()
        return dict(r) if r else None

    def member_ids(self, event_id: str) -> List[str]:
        return [r[0] for r in self._db().execute(
            "SELECT content_id FROM event_content WHERE event_id=?", (event_id,)).fetchall()]

    def member_sims(self, event_id: str) -> List[float]:
        return [r[0] for r in self._db().execute(
            "SELECT similarity_score FROM event_content WHERE event_id=? AND similarity_score IS NOT NULL",
            (event_id,)).fetchall()]

    # ---------- L2 ----------
    def _index(self) -> Dict[str, Dict[str, Any]]:
        if self._content_index is None:
            rows = self.l2.list_content(limit=200000) if self.l2 else []
            self._content_index = {r["content_id"]: hydrate(r) for r in rows}
        return self._content_index

    def contents(self, content_ids: List[str]) -> List[Dict[str, Any]]:
        idx = self._index()
        return [idx[i] for i in content_ids if i in idx]

    def members(self, event_id: str) -> List[Dict[str, Any]]:
        return self.contents(self.member_ids(event_id))

    def read_full_text(self, content_id: str) -> Optional[str]:
        """★ 只在节点内部临时使用，返回值**禁止写回 State**。"""
        c = self._index().get(content_id)
        if not c:
            return None
        return c.get("normalized_text") or c.get("raw_text")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
