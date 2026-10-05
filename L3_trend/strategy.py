#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_trend/strategy.py — Agent 采集策略引擎（V2 核心）。

★ V1→V2 的本质变化：
  V1：人配 crawl_config.json → 爬虫固定参数跑（活跃期和安静期一个节奏）
  V2：Agent 看当前数据状态 → 自己决定下一轮怎么采

  Agent 的决策不是"猜"，是基于三条规则的确定性计算：
    ① 活跃度驱动：发帖多→多采，发帖少→少采
    ② 爆火响应：检测到增速异常→自动升频+加大评论预算
    ③ 产出反馈：上轮产出低→自动调整策略（更深/更宽/换渠道）

  规则优先，LLM 可覆盖（--llm 时 Agent 用 LLM 辅助决策，
  没有就纯规则——降级不崩）。

用法：
    from strategy import StrategyEngine
    se = StrategyEngine()
    decision = se.decide()       # 读数据 → 输出下轮参数
    # decision["params"] 可以直接写入 crawl_config.json
"""

from __future__ import annotations

import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

TZ_CN = timezone(timedelta(hours=8))
RAW = os.path.join(_ROOT, "data", "raw", "taptap", "community")
STATE = os.path.join(_ROOT, "data", "state")
SNAPSHOTS = os.path.join(RAW, "thread_snapshots.csv")
POSTS = os.path.join(RAW, "posts.csv")
STRATEGY_LOG = os.path.join(STATE, "strategy_log.jsonl")
CONFIG_PATH = os.path.join(STATE, "crawl_config.json")

# 硬约束（Agent 不能越过的安全线）
HARD_LIMITS = {
    "max_comment_calls": (10, 600),        # 每轮评论接口调用
    "max_pages": (2, 60),                  # 每轮翻页深度
    "fresh_hours": (6, 336),               # 新帖窗口（小时）
    "sleep_min": (0.3, 5.0),              # 请求最小间隔（WAF 保护）
    "sleep_max": (0.5, 10.0),             # 请求最大间隔
}

# ★ 基准参数：每轮策略调整的起点（不是上轮调整后的值）。
#   防止复合膨胀：如果每次在上轮基础上调整，120→270→405→…会失控。
#   Agent 的调整 = BASE ± 信号驱动的偏移量，而不是在上轮基础上乘系数。
BASE_CONFIG: Dict[str, Any] = {
    "fresh_hours": 72,
    "max_pages": 12,
    "max_comment_calls": 150,
    "comment_pages_per_post": 10,
    "max_age_days": 14,
    "sleep_min": 0.8,
    "sleep_max": 1.5,
}


def _n(v: Any) -> int:
    try:
        return int(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0


def _f(v: Any) -> float:
    try:
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _read_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _clamp(key: str, val: Any) -> Any:
    lo, hi = HARD_LIMITS.get(key, (0, 999999))
    v = max(lo, min(hi, int(val))) if isinstance(val, (int, float)) else val
    return v


class StrategyEngine:
    """采集策略引擎：读当前数据状态 → 输出下一轮参数 + 理由。

    ★ 决策逻辑（可解释，不是黑盒）：
      1. 活跃度信号：最近 1h/6h/24h 发帖速率（publish_time 锚定）
      2. 爆火信号：有帖子评论增速 >5/h → 加大评论预算
      3. 覆盖缺口：评论覆盖率 <30% → 加大评论页数上限
      4. 产出反馈：上轮新增 <2 → 放宽窗口/加深翻页
      5. 外部信号：百度游戏榜有 forming → 加大全站采集
    """

    def __init__(self) -> None:
        self.now = _now()

    # ------------------------------------------------------------ 数据采集
    def _load_state(self) -> Dict[str, Any]:
        posts = _read_csv(POSTS)
        snaps = _read_csv(SNAPSHOTS)
        # ★ 每轮从 BASE 出发调整，不是在上轮基础上叠加（防复合膨胀 120→270→405→…）
        cfg = dict(BASE_CONFIG)

        # 发帖速率（publish_time 锚定）
        now_ts = self.now.timestamp()
        rates = {}
        for h in (1, 3, 6, 12, 24, 72):
            cutoff = now_ts - h * 3600
            rates[f"posts_last_{h}h"] = sum(
                1 for r in posts if _n(r.get("publish_time")) >= cutoff)

        # 帖子活跃度分层
        active = [r for r in posts if (r.get("monitor_state") or "active") == "active"]
        closed = [r for r in posts if r.get("monitor_state") == "closed"]

        # 快照增速 TOP（最近一轮有 delta 的）
        latest_delta = {}
        for s in snaps:
            mid = s.get("moment_id", "")
            d = _n(s.get("comment_delta"))
            if mid not in latest_delta or s.get("observed_at","") > latest_delta[mid].get("observed_at",""):
                latest_delta[mid] = s
        growing = sorted(
            ((mid, s) for mid, s in latest_delta.items() if _n(s.get("comment_delta")) > 0),
            key=lambda x: -_n(x[1].get("comment_delta")))

        # 评论覆盖率
        comments = _read_csv(os.path.join(RAW, "comments.csv"))
        cmt_by_post = set(c.get("moment_id") for c in comments)
        covered = sum(1 for p in posts if p["moment_id"] in cmt_by_post)
        coverage = covered / max(len(posts), 1)

        # 外部热点
        forming = _read_json(os.path.join(STATE, "forming_report.json"))
        channels = forming.get("channels") or {}
        external_forming = sum(
            len(v.get("forming", [])) for v in channels.values())
        external_ready = forming.get("ready", False)

        # L6 相关性判定（有几个 related 热点）
        related_n = 0
        rel_path = os.path.join(STATE, "hotspot_relevance.json")
        if os.path.exists(rel_path):
            try:
                rel_data = _read_json(rel_path)
                related_n = sum(1 for v in (rel_data.get("verdicts") or {}).values()
                                if v.get("verdict") == "related")
            except Exception:
                pass

        return {
            "posts": posts, "active": active, "closed": closed,
            "rates": rates, "growing": growing,
            "coverage": coverage, "comment_count": len(comments),
            "external_forming": external_forming,
            "external_ready": external_ready,
            "related_n": related_n,
            "current_config": cfg,
            "n_snapshots": len(snaps),
        }

    # ------------------------------------------------------------ 决策
    def decide(self) -> Dict[str, Any]:
        st = self._load_state()
        rates = st["rates"]
        cfg = st["current_config"]

        cur = {
            "fresh_hours": _n(cfg.get("fresh_hours") or 72),
            "max_pages": _n(cfg.get("max_pages") or 12),
            "max_comment_calls": _n(cfg.get("max_comment_calls") or 150),
            "comment_pages_per_post": _n(cfg.get("comment_pages_per_post") or 10),
            "max_age_days": _n(cfg.get("max_age_days") or 14),
        }

        # ── 信号 ──
        signals: List[Dict[str, str]] = []
        params: Dict[str, Any] = {}

        # 1) 活跃度 → 采集深度
        rate_24h = rates.get("posts_last_24h", 0)
        if rate_24h > 30:
            params["max_pages"] = _clamp("max_pages", 20)
            signals.append("近24h发帖>30，社区活跃，加深翻页")
        elif rate_24h < 5:
            params["max_pages"] = _clamp("max_pages", 6)
            signals.append("近24h发帖<5，社区安静，减少翻页")

        # 2) 爆火信号 → 加大评论预算
        hot_growth = [g for g in st["growing"] if _n(g[1].get("comment_delta")) >= 10]
        if hot_growth:
            params["max_comment_calls"] = _clamp("max_comment_calls",
                                                  cur["max_comment_calls"] * 2)
            signals.append(f"{len(hot_growth)} 个帖评论增长≥10，评论预算翻倍")

        # 3) 覆盖缺口 → 加大评论页数
        if st["coverage"] < 0.3 and len(st["active"]) > 10:
            params["comment_pages_per_post"] = _clamp(
                "comment_pages_per_post", cur["comment_pages_per_post"] + 5)
            signals.append(f"评论覆盖率 {st['coverage']*100:.0f}% < 30%，增加评论页数")

        # 4) 产出反馈：上轮产出太低 → 放宽
        # （用 24h 发帖量近似上轮产出，因为采集是持续的）
        if rate_24h < 3:
            params["fresh_hours"] = _clamp("fresh_hours", 168)
            signals.append("近24h发帖<3，放宽窗口到7天")
        elif rate_24h > 20:
            params["fresh_hours"] = _clamp("fresh_hours", 48)
            signals.append("近24h发帖>20，收紧窗口到48h聚焦当下")

        # 5) 外部信号：有 forming 的游戏热点 → 全力采集
        if st["external_forming"] > 3:
            params["max_comment_calls"] = _clamp("max_comment_calls",
                                                  cur["max_comment_calls"] * 1.5)
            signals.append(f"外部 forming {st['external_forming']} 个，评论预算×1.5")

        # 6) 新颖度：有很多 closed 但突然有增长的帖 → 可能是旧帖翻红
        reactivated = sum(1 for g in st["growing"]
                         if _n(g[1].get("comments")) > 50)
        if reactivated:
            signals.append(f"{reactivated} 个历史帖评论重新增长，可能是旧话题翻红")

        # ── 活跃度评估 ──
        level = "安静"
        if rate_24h > 30:
            level = "高活跃"
        elif rate_24h > 10:
            level = "活跃"
        elif rate_24h > 3:
            level = "中等"

        decision = {
            "timestamp": self.now.isoformat(timespec="seconds"),
            "mode": "rule_v2",
            "activity_level": level,
            "signals": signals,
            "changes": {k: {"from": cur.get(k), "to": v}
                       for k, v in params.items() if cur.get(k) != v},
            "params": {**cur, **params},
            "state_summary": {
                "active_threads": len(st["active"]),
                "closed_threads": len(st["closed"]),
                "comment_coverage": round(st["coverage"] * 100, 1),
                "external_forming": st["external_forming"],
                "related_hotspots": st["related_n"],
            },
        }
        return decision

    # ------------------------------------------------------------ 落盘
    def apply(self, decision: Dict[str, Any]) -> None:
        """把决策写入 crawl_config.json + 追加策略日志。"""
        params = decision.get("params")
        if not params:
            return
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False, indent=2)
        os.makedirs(os.path.dirname(STRATEGY_LOG), exist_ok=True)
        with open(STRATEGY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(decision, ensure_ascii=False) + "\n")


def run() -> Dict[str, Any]:
    se = StrategyEngine()
    d = se.decide()
    se.apply(d)
    return d


if __name__ == "__main__":
    d = run()
    print(json.dumps({
        "activity": d["activity_level"],
        "signals": d["signals"],
        "changed": d["changes"],
        "params": d["params"],
    }, ensure_ascii=False, indent=2))