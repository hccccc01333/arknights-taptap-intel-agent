# -*- coding: utf-8 -*-
"""第五层记忆检索适配（L4 README「待建」项：混合检索：历史案例，设计 §37/§38）。

★ 设计 §24：第四层 Agent **不许直查记忆库** —— 全部经本适配器走
  第五层 `RetrievalEngine.retrieve()` 统一服务（评分/重排/多样性/权限/观测都在那边）。

★ 降级纪律（与 tools.py 同款）：L5 记忆库不存在 → `available()=False`，
  检索函数返回空列表 —— 是「记忆里没有」而不是「检索过没结果」，两种空必须可区分
  时用 `available()` 先判。绝不编造历史效果数据。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_L5_DIR = os.path.join(_ROOT, "L5_memory")

_MEMORY_ADAPTER_VERSION = "l4-memory-adapter-1.0"


def _default_db() -> str:
    env = os.environ.get("L5_MEMORY_DB")
    if env:
        return env
    return os.path.join(_ROOT, "data", "state", "l5_memory.sqlite3")


def _open():
    """导入并打开第五层；任何缺失（未安装/库不存在）都返回 None，不炸调用方。"""
    try:
        if _L5_DIR not in sys.path:
            sys.path.insert(0, _L5_DIR)
        from memory.store import MemoryStore          # noqa: E402
        from memory.retrieval import RetrievalEngine  # noqa: E402
    except Exception:
        return None
    db = _default_db()
    if not os.path.exists(db):
        return None
    try:
        return MemoryStore(db), RetrievalEngine
    except Exception:
        return None


def available() -> bool:
    """第五层记忆库是否可用（不存在 = False：没历史就是没历史，如实说）。"""
    opened = _open()
    if opened is None:
        return False
    opened[0].close()
    return True


def _retrieve(memory_type: str, query: str, top_k: int, caller: str,
              filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    opened = _open()
    if opened is None:
        return []
    store, engine_cls = opened
    try:
        res = engine_cls(store).retrieve(query or memory_type, memory_type=memory_type,
                                         filters=filters, top_k=top_k, caller=caller)
        return res["items"]
    except Exception:
        return []
    finally:
        store.close()


# ---------------------------------------------------------------- §43 五个标准请求
def similar_trends(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """类似热点以前发生过吗？（Trend Memory + 短期进行中热点）"""
    return _retrieve("trend", query, top_k, "relevance_agent")


def similar_experiments(query: str = "", top_k: int = 5) -> List[Dict[str, Any]]:
    """历史成功实验（§27：performance_filter 只找正 lift）。"""
    return _retrieve("experiment", query, top_k, "opportunity_agent",
                     filters={"performance_filter": {"relative_lift": ">0"}})


def product_capabilities(query: str = "产品能力 资产 约束", top_k: int = 8) -> List[Dict[str, Any]]:
    """TapTap 产品能力 / 业务知识（§44 Relevance Agent 读取项）。"""
    return _retrieve("business", query, top_k, "relevance_agent")


def user_segments(query: str = "用户画像 动机 受众", top_k: int = 5) -> List[Dict[str, Any]]:
    """用户动机 / 受众先验。"""
    return _retrieve("business", query, top_k, "relevance_agent")


def failure_cases(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """失败案例 / 反模式（§40 Negative Memory）。"""
    return _retrieve("anti_pattern", query, top_k, "opportunity_agent")


def similar_cases(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """历史 Growth Case（§29：最 valuable 的记忆单元）。"""
    return _retrieve("case", query, top_k, "opportunity_agent")


def entity_profile(entity: str) -> Optional[Dict[str, Any]]:
    """实体档案（别名 / 归属）—— games.registry 已入 Entity Knowledge。"""
    opened = _open()
    if opened is None:
        return None
    store, _ = opened
    try:
        row = store.db.query_one(
            "SELECT * FROM knowledge_item WHERE memory_type='entity' AND subject=?"
            " AND valid_to IS NULL ORDER BY version DESC", (str(entity).lower(),))
        if not row:
            return None
        return {"subject": row["subject"], "title": row["title"],
                "payload": store.db.loads(row.get("payload"), {}) or {},
                "item_id": row["item_id"], "tier": row["tier"]}
    except Exception:
        return None
    finally:
        store.close()


def context_for(agent_type: str, query: str) -> Optional[Dict[str, Any]]:
    """§44 按 Agent 组装完整 Memory Context（带 log_id，用完请回填 mark_used）。"""
    opened = _open()
    if opened is None:
        return None
    store, _ = opened
    try:
        from memory.context import ContextBuilder
        return ContextBuilder(store).build(agent_type, query=query)
    except Exception:
        return None
    finally:
        store.close()
