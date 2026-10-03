# -*- coding: utf-8 -*-
"""Alert System（§16 / §17）。

★ §16：不是每个热点都通知（Alert Fatigue）—— P0 立即通知 / P1 进 Feed / P2 只上 Dashboard。

★ §17：通知本身必须可行动 —— 不是"发现新热点：某游戏更新"，
  而是 Momentum/Relevance/窗口/推荐行动一起给。推荐行动来自 §6 的窗口约束
  （当前资源下还来得及上线的创意类型），不是 LLM 现编的。

★ 口径诚实（与 L4 gate_basis 同款纪律）：规格阈值（hot>.85 ∧ momentum>.9 ∧
  relevance>.85 ∧ window<12h）在无时间分辨率数据上**无法完整评估** →
  降级口径如实标注 `basis=degraded`，绝不假装跑过规格阈值。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from execution.audit import AuditLog, require_role
from execution.db import ExecutionDB
from execution.feed import allowed_creative_types, window_for

ALERT_VERSION = "alerts-1.0"

# §16 规格阈值（P0）
P0_SPEC = {"hot": 0.85, "momentum": 0.90, "relevance": 0.85, "window_hours": 12.0}
# 降级口径（无时间分辨率时）：相关性硬门槛 + L3 可信体量 + 生命周期仍开放
P0_DEGRADED = {"relevance": 0.85, "confidence": 0.60, "content_count": 30,
               "lifecycle": ("EMERGING", "GROWING", "REACTIVATED")}
P1_DEGRADED = {"confidence": 0.50, "lifecycle": ("EMERGING", "GROWING", "REACTIVATED")}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


class AlertSystem:
    def __init__(self, db: ExecutionDB, audit: AuditLog):
        self.db = db
        self.audit = audit

    def evaluate_event(self, ev: Dict[str, Any], relevance: Optional[float],
                       has_analysis: bool) -> Optional[Dict[str, Any]]:
        """单事件分级。返回 None = P2（只上 Dashboard，不发通知）。"""
        hot = float(ev.get("hot_score") or 0)
        mom = float(ev.get("momentum_score") or 0)
        conf = float(ev.get("confidence_score") or 0)
        life = ev.get("lifecycle") or ""
        n = int(ev.get("content_count") or 0)
        rel = float(relevance) if relevance is not None else None

        # 规格口径：window<12h 无法验证（无时间分辨率）→ 整条规格口径判不了 P0
        spec_ok = (hot > P0_SPEC["hot"] and mom > P0_SPEC["momentum"]
                   and rel is not None and rel > P0_SPEC["relevance"])
        if spec_ok:
            # 热度动量相关性都过线，窗口按生命周期近似（如实标注这是近似）
            if life in ("EMERGING", "GROWING", "REACTIVATED"):
                return {"tier": "P0", "basis": "spec_hot_mom_rel + lifecycle近似窗口"
                                              "（window<12h 不可验证）"}
        degraded_ok = (rel is not None and rel >= P0_DEGRADED["relevance"]
                       and conf >= P0_DEGRADED["confidence"]
                       and n >= P0_DEGRADED["content_count"]
                       and life in P0_DEGRADED["lifecycle"])
        if degraded_ok:
            return {"tier": "P0", "basis": "degraded: relevance+confidence+volume+lifecycle"
                                           "（无时间分辨率，规格窗口不可得）"}
        if conf >= P1_DEGRADED["confidence"] and life in P1_DEGRADED["lifecycle"]:
            return {"tier": "P1", "basis": "degraded: emerging with confidence"}
        return None                                            # P2：不上通知

    def sweep(self, feed_cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        """对 Feed 全量卡片分级、去重、落通知。返回本轮新建告警。"""
        created = []
        for card in feed_cards:
            tier_info = self.evaluate_event(card, card.get("relevance"),
                                            card.get("has_analysis"))
            if tier_info is None:
                continue
            tier = tier_info["tier"]
            day_key = f"{card['event_id']}|{tier}|{_today()}"
            dedup_id = f"alr_{hashlib.sha1(day_key.encode()).hexdigest()[:12]}"
            if self.db.query_one("SELECT 1 FROM alert WHERE alert_id=?", (dedup_id,)):
                continue                                        # 同事件同级别同日只通知一次
            body = self._compose(card, tier)
            self.db.execute(
                "INSERT INTO alert(alert_id, event_id, tier, title, body, basis,"
                " created_at) VALUES(?,?,?,?,?,?,?)",
                (dedup_id, card["event_id"], tier, card.get("title") or card["event_id"],
                 self.db.dumps(body), tier_info["basis"], _now()))
            self.db.commit()
            created.append({"alert_id": dedup_id, "tier": tier, "event_id": card["event_id"]})
        return {"created": created, "evaluated": len(feed_cards)}

    def _compose(self, card: Dict[str, Any], tier: str) -> Dict[str, Any]:
        """§17：通知本身可行动 —— 维度 + 窗口 + 推荐行动（来自真实约束）。"""
        allowed = [c["name"] for c in (card.get("allowed_creatives") or [])][:3]
        return {
            "headline": f"{'🚨' if tier == 'P0' else '👀'} {tier} · {card.get('title')}",
            "dimensions": {"momentum": card.get("momentum_score"),
                           "relevance": card.get("relevance"),
                           "confidence": card.get("confidence_score"),
                           "lifecycle": card.get("lifecycle")},
            "window": card.get("window"),
            "recommended_action": (f"优先评估：{' / '.join(allowed)}"
                                   if allowed else "窗口过窄，仅低成本内容响应"),
            "n_opportunities": card.get("n_opportunities"),
            "open": f"L6_execution 工作台 → Feed → {card['event_id']}",
        }

    def acknowledge(self, alert_id: str, actor: str, role: str) -> Dict[str, Any]:
        require_role(role, "acknowledge_alert")     # agent 无权（§16 通知给人看的）
        row = self.db.query_one("SELECT * FROM alert WHERE alert_id=?", (alert_id,))
        if not row:
            raise ValueError(f"告警不存在：{alert_id}")
        self.db.execute(
            "UPDATE alert SET acknowledged_by=?, acknowledged_at=? WHERE alert_id=?",
            (actor, _now(), alert_id))
        self.db.commit()
        self.audit.record(action="alert_acknowledged", actor=actor, role=role,
                          object_type="alert", object_id=alert_id)
        return {"alert_id": alert_id, "acknowledged_by": actor}

    def pending(self, tier: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM alert WHERE acknowledged_at IS NULL"
        params: tuple = ()
        if tier:
            sql += " AND tier=?"
            params = (tier,)
        sql += " ORDER BY created_at DESC"
        return [{**r, "body": self.db.loads(r.get("body"))}
                for r in self.db.query(sql, params)]
