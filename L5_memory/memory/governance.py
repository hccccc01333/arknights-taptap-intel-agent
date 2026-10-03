# -*- coding: utf-8 -*-
"""记忆治理（§18-§23 / §38-§39 / §53-§55）。

这一层回答四个问题：
  ① 什么允许进长期记忆？（§19-§20 Write Policy —— 不是所有 Agent 中间推理都值得长期保存）
  ② 这条知识是谁说的、多权威、还有效吗？（§21-§23 Provenance / 时效 / 版本）
  ③ 两条知识打架听谁的？（§38 冲突消解：最新 + 权威 + 仍有效）
  ④ 谁能读什么？（§54 RBAC）—— 以及 PII 绝不入记忆（§55）

★ 设计 §53 的知识污染防线就在这里：Agent 输出永远只能落 agent_generated/candidate，
  升级到 verified 必须经过 人工确认 / 真实实验 —— 否则就是
  「Agent 幻觉 → 写入 Knowledge → 下次检索 → 幻觉变事实」的 feedback loop。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- §39 权威级别
AUTHORITY_LEVELS: Dict[str, Dict[str, Any]] = {
    "P0": {"desc": "官方系统数据", "score": 1.0},
    "P1": {"desc": "内部产品文档", "score": 0.9},
    "P2": {"desc": "已批准业务文档", "score": 0.75},
    "P3": {"desc": "人工笔记", "score": 0.55},
    "P4": {"desc": "Agent 推断", "score": 0.35},
}

# §53 三级知识：agent 输出 → candidate；升级 verified 必须过人/过实验
KNOWLEDGE_TIERS = ("verified", "candidate", "agent_generated")
TIER_SCORE = {"verified": 1.0, "candidate": 0.6, "agent_generated": 0.3}

# ---------------------------------------------------------------- §17 拒绝理由 taxonomy
DECISION_TAXONOMY = (
    "LOW_RELEVANCE", "TOO_LATE", "TOO_EXPENSIVE", "BRAND_RISK",
    "RESOURCE_UNAVAILABLE", "LOW_NOVELTY", "WEAK_USER_INSIGHT",
    "NO_GROWTH_MECHANISM", "DUPLICATE_IDEA", "LEGAL_RISK", "OTHER",
)


def normalize_reason_code(text: str) -> str:
    """自由文本拒绝理由 → taxonomy（命中词面才算，否则如实 OTHER，不硬猜）。"""
    t = (text or "").upper()
    mapping = {
        "LOW_RELEVANCE": ("相关", " relevance", "不搭", "无关"),
        "TOO_LATE": ("晚", "过时", "窗口", "late", "过期"),
        "TOO_EXPENSIVE": ("贵", "成本", "预算", "expensive", "cost"),
        "BRAND_RISK": ("品牌", "风险", "brand", "敏感"),
        "RESOURCE_UNAVAILABLE": ("资源", "人手", "排期", "resource"),
        "LOW_NOVELTY": ("老套", "重复", "novelty", "没新意"),
        "WEAK_USER_INSIGHT": ("洞察", "insight", "牵强"),
        "NO_GROWTH_MECHANISM": ("机制", "mechanism", "怎么增长"),
        "DUPLICATE_IDEA": ("重复创意", "撞车", "duplicate", "同款"),
        "LEGAL_RISK": ("法务", "合规", "legal", "版权"),
    }
    for code, kws in mapping.items():
        if any(k in t for k in kws):
            return code
    return "OTHER"


# ---------------------------------------------------------------- §20 Write Policy
# 什么进长期、什么只进短期。规则即策略，**策略本身可测试**。
# 返回 {"horizon": long_term|short_term, "tier": ..., "reason": ...}
WRITE_POLICY = [
    # (kind, horizon, tier, 理由)
    ("experiment_result",         "long_term",  "verified",       "真实实验发生（§19）"),
    ("human_approved_creative",   "long_term",  "verified",       "人工确认（§19）"),
    ("human_decision",            "long_term",  "verified",       "人工决策本身就是训练数据（§16）"),
    ("event_lifecycle_ended",     "long_term",  "candidate",      "事件生命周期结束（§19）"),
    ("business_knowledge_update", "long_term",  "verified",       "业务知识正式更新（§19）"),
    ("repeated_pattern",          "long_term",  "candidate",      "重复出现的行为模式（§19）"),
    ("active_trend",              "short_term", "candidate",      "进行中热点：短期记忆，天/周生命周期（§18）"),
    ("llm_inference",             "short_term", "agent_generated", "LLM 推断绝不自动进长期（§19/§53）"),
    ("agent_insight",             "short_term", "agent_generated", "Agent 洞察先落 candidate/短期（§53）"),
]


def write_route(kind: str) -> Dict[str, str]:
    for k, horizon, tier, reason in WRITE_POLICY:
        if k == kind:
            return {"kind": kind, "horizon": horizon, "tier": tier, "reason": reason}
    # 未登记的 kind 一律按最保守处理：短期 + agent_generated（宁可少存，不可污染）
    return {"kind": kind, "horizon": "short_term", "tier": "agent_generated",
            "reason": "未登记的 kind，按最保守策略（§53 知识污染防护）"}


# ---------------------------------------------------------------- §55 PII 防线
# 记忆里存 segment-level / aggregate（§55），不存个体轨迹。
# 字段名出现这些标记 → 值必须是哈希形态（本项目 common/pii_hash.py 的 HMAC 输出）才放行。
PII_FIELD_MARKS = ("user_id", "uid", "author_id", "nickname", "user_name",
                   "avatar", "phone", "email", "open_id", "device_id")
_HEXISH = re.compile(r"^[0-9a-f]{16,64}$")           # sha/hmac 截断形态
# 明显是原始 id 的形态：纯数字、或过短的 hex
_RAW_ID = re.compile(r"^\d{4,}$")


def _value_is_hashed(v: Any) -> bool:
    if not isinstance(v, str):
        return False
    return bool(_HEXISH.match(v)) and not _RAW_ID.match(v)


def scan_pii(record: Dict[str, Any]) -> List[str]:
    """递归扫描记忆条目；返回违规字段路径列表（空 = 通过）。

    规则（与项目 PII 分层约定一致）：
      - 原始数据层允许明文 id（用于回查）；**记忆层不允许** —— 记忆会被检索进 Agent Context。
      - 哈希形态放行（跨通道关联仍可做）；样本量/聚合值放行。
    """
    bad: List[str] = []

    def walk(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = str(k).lower()
                if any(m in kl for m in PII_FIELD_MARKS):
                    if isinstance(v, (list, dict)):
                        bad.append(f"{path}.{k}（集合型个体数据，应改聚合）")
                    elif v is not None and not _value_is_hashed(v):
                        bad.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(record, "")
    return bad


# ---------------------------------------------------------------- §22/§23 时效与版本
def is_currently_valid(row: Dict[str, Any], at: Optional[str] = None) -> bool:
    """valid_from / valid_to 区间判定（§22：不能永远有效，且要能历史复现）。"""
    now = at or datetime.now().astimezone().isoformat()
    vf = row.get("valid_from")
    vt = row.get("valid_to")
    if vf and str(vf) > now:
        return False
    if vt and str(vt) <= now:
        return False
    return True


def next_version(existing: Optional[Dict[str, Any]]) -> int:
    """§23 知识版本化：内容变了 version+1，历史靠 valid_from/valid_to 复现。"""
    return int((existing or {}).get("version") or 0) + 1


# ---------------------------------------------------------------- §38 冲突消解
def resolve_conflicts(rows: List[Dict[str, Any]], at: Optional[str] = None) -> List[Dict[str, Any]]:
    """同一 subject 多版本知识 → 只保留应生效的：仍有效者优先，再按 (authority, updated_at) 降序。

    §38 不能把「功能支持图片评论」和「新版已下线」同时塞给 Agent。
    """
    now = at or datetime.now().astimezone().isoformat()
    def key(r: Dict[str, Any]):
        current = 1 if is_currently_valid(r, now) else 0
        auth = AUTHORITY_LEVELS.get(r.get("authority") or "P3", {}).get("score", 0.5)
        return (current, auth, r.get("updated_at") or r.get("created_at") or "")
    return sorted(rows, key=key, reverse=True)


# ---------------------------------------------------------------- §54 访问控制（RBAC）
# 每类调用方能读的 memory_type。设计 §44：不要所有 Agent 共用同一个超级 RAG；
# §54：creative_agent 只看 approved growth cases，不看敏感合同 / 个人数据。
ACCESS_MATRIX: Dict[str, tuple] = {
    "relevance_agent":  ("business", "entity", "trend", "case", "playbook"),
    "opportunity_agent": ("trend", "case", "experiment", "anti_pattern", "playbook"),
    "creative_agent":   ("case", "playbook", "business", "creative"),
    "evaluator":        ("case", "anti_pattern", "decision", "business"),
    "research_agent":   ("trend", "entity", "case", "experiment"),
    "human":            ("business", "entity", "trend", "creative", "experiment",
                         "decision", "case", "anti_pattern", "playbook"),
    "system":           ("business", "entity", "trend", "creative", "experiment",
                         "decision", "case", "anti_pattern", "playbook"),
}


def access_allowed(caller: str, memory_type: str) -> bool:
    return memory_type in ACCESS_MATRIX.get(caller, ())   # 未登记 caller = 无权限（默认拒绝）


def trust_score(row: Dict[str, Any]) -> float:
    """检索 Trust 维度（§25 w6 / §39）：权威 + 层级 + 人工验证加权。"""
    auth = AUTHORITY_LEVELS.get(row.get("authority") or "P3", {}).get("score", 0.5)
    tier = TIER_SCORE.get(row.get("tier") or "candidate", 0.5)
    hv = 1.0 if row.get("human_verified") else 0.0
    return round(0.5 * auth + 0.3 * tier + 0.2 * hv, 4)


# ---------------------------------------------------------------- §37 Memory Decay
# 按 memory type 分 λ：实体/业务知识不衰减，实验慢衰减，热点快衰减。
DECAY_LAMBDA_PER_DAY: Dict[str, float] = {
    "business": 0.0, "entity": 0.0,       # 长期实体关系不应快速衰减（§37）
    "experiment": 0.005, "case": 0.002,
    "creative": 0.01, "decision": 0.01,
    "trend": 0.05,                        # 热点规律三个月就过时
}


def recency_weight(ref_ts: Optional[str], kind: str = "trend",
                   now: Optional[datetime] = None) -> float:
    """指数衰减 R = e^{-λt}（§37）；无时间戳 → 0.5（不奖励也不清零）。"""
    if not ref_ts:
        return 0.5
    lam = DECAY_LAMBDA_PER_DAY.get(kind, 0.01)
    if lam <= 0:
        return 1.0
    try:
        t = datetime.fromisoformat(str(ref_ts).replace("Z", "+00:00"))
    except ValueError:
        return 0.5
    if t.tzinfo is None:
        t = t.astimezone()          # naive 视为本地时间（★ 不能剥 offset：aware/naive 相减会炸）
    base = now or datetime.now().astimezone()
    days = max(0.0, (base - t).total_seconds() / 86400.0)
    return round(pow(2.718281828, -lam * days), 4)


def short_term_ttl_days(kind: str) -> int:
    """§18 短期记忆生命周期：进行中热点 14 天，LLM 推断 7 天。"""
    return 14 if kind == "active_trend" else 7


def default_expiry(kind: str, now: Optional[datetime] = None) -> str:
    base = now or datetime.now().astimezone()
    return (base + timedelta(days=short_term_ttl_days(kind))).isoformat(timespec="seconds")
