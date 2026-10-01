#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Embedding Engine（§17 / §18）—— 接口 + 版本化注册表。

★ 本机没有 embedding 模型（也没网），所以 **不生产向量**。
这里明确不做的一件事：**绝不用"假向量"（随机/哈希向量）冒充语义向量** ——
那会让第三层的聚类看起来有结果、实际全是噪音，属于最坏的一种"数字污染"。
模型未接入时 `embedding_ref = None`，第三层据此降级到词汇相似度。

★ 版本化（§18）是这部分的重点，且与假向量无关：
    content_embedding 主键 = (content_id, model_name, model_version)
换模型时**并存**两个版本，绝不 UPDATE 覆盖 —— 否则历史聚类无法复现。

接入方式（将来）：实现 `EmbeddingProvider.encode()`，填 model_name/version/dim，
注册表自动记录，历史向量不受影响。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

_DB_DEFAULT = os.path.join("data", "state", "l2_embeddings.sqlite3")


class EmbeddingProvider:
    """向量模型接口。子类实现 encode / dim / model 元信息。"""

    name = "none"
    version = "none"
    dim = 0

    def available(self) -> bool:
        return False

    def encode(self, texts: List[str]) -> List[Optional[List[float]]]:
        raise NotImplementedError("需要 embedding 模型；本机未接入，不生产假向量")


class NoneProvider(EmbeddingProvider):
    """占位：明确"未接入"，不返回任何向量。"""
    name = "none"
    version = "2026-10"
    dim = 0

    def available(self) -> bool:
        return False


class EmbeddingEngine:
    """向量生产 + 版本化存储。"""

    def __init__(self, provider: Optional[EmbeddingProvider] = None,
                 db_path: Optional[str] = None) -> None:
        self.provider = provider or NoneProvider()
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.db_path = db_path or os.path.join(root, _DB_DEFAULT)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS content_embedding (
                content_id     TEXT,
                model_name     TEXT,
                model_version  TEXT,
                dim            INTEGER,
                vector         TEXT,
                created_at     TEXT,
                PRIMARY KEY (content_id, model_name, model_version)
            );
            CREATE TABLE IF NOT EXISTS embedding_model_registry (
                model_name     TEXT,
                model_version  TEXT,
                dim            INTEGER,
                status         TEXT,
                note           TEXT,
                registered_at  TEXT,
                PRIMARY KEY (model_name, model_version)
            );
        """)
        self.conn.commit()
        self._register_model()

    def _register_model(self) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO embedding_model_registry VALUES(?,?,?,?,?,?)",
            (self.provider.name, self.provider.version, self.provider.dim,
             "active" if self.provider.available() else "not_available",
             "本机无 embedding 模型；接入后历史向量并存，不覆盖",
             datetime.now().isoformat()))
        self.conn.commit()

    def available(self) -> bool:
        return self.provider.available()

    def embed(self, content_id: str, text: str) -> Optional[str]:
        """返回 embedding_ref（形如 `bge-vX@2026-10:<content_id>`），未接入返回 None。"""
        if not self.available():
            return None
        vecs = self.provider.encode([text])
        v = vecs[0] if vecs else None
        if not v:
            return None
        self.conn.execute("INSERT OR REPLACE INTO content_embedding VALUES(?,?,?,?,?,?)",
                          (content_id, self.provider.name, self.provider.version,
                           len(v), json.dumps(v), datetime.now().isoformat()))
        self.conn.commit()
        return f"{self.provider.name}@{self.provider.version}:{content_id}"

    def get(self, content_id: str, model: Optional[str] = None,
            version: Optional[str] = None) -> Optional[List[float]]:
        sql = "SELECT vector FROM content_embedding WHERE content_id=?"
        args: List[Any] = [content_id]
        if model:
            sql += " AND model_name=?"
            args.append(model)
        if version:
            sql += " AND model_version=?"
            args.append(version)
        r = self.conn.execute(sql + " LIMIT 1", args).fetchone()
        return json.loads(r["vector"]) if r else None

    def registry_rows(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM embedding_model_registry").fetchall()]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM content_embedding").fetchone()[0]
