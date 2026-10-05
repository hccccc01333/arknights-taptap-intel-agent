#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_trend/feedback.py — 策略效果回溯。

★ 评估什么：上轮 Agent 调了参数 → 抓回来多少东西？
  如果调了预算翻倍但新帖只多了 1 条，说明预算浪费了 → 下轮降回来。
  如果调了窗口放宽到 168h 但什么都没抓到 → 窗口太宽了 → 收窄。

★ 数据来源：strategy_log.jsonl（上轮决策）+ 本轮 thread_snapshots.csv（实际产出）。
  不需要 LLM，纯统计对比。
"""

from __future__ import annotations

import csv
import json
import os
import sys
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
STRATEGY_LOG = os.path.join(_ROOT, "data", "state", "strategy_log.jsonl")
SNAPSHOTS = os.path.join(_ROOT, "data", "raw", "taptap", "community", "thread_snapshots.csv")
POSTS = os.path.join(_ROOT, "data", "raw", "taptap", "community", "posts.csv")


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _read_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def evaluate() -> Dict[str, Any]:
    """读上轮策略决策和本轮产出 → 评估策略效果。"""
    log = _read_jsonl(STRATEGY_LOG)
    if len(log) < 1:
        return {"ok": False, "reason": "还没有策略日志（strategy_log.jsonl 不存在）"}

    last = log[-1]  # 最近一次决策
    last_ts = last.get("timestamp", "")
    if not last_ts:
        return {"ok": False, "reason": "最近决策无时间戳"}

    # 本轮产出 = 快照时间 > 上轮决策时间的快照
    snaps = _read_csv(SNAPSHOTS)
    this_round = [s for s in snaps if s.get("observed_at", "") > last_ts]
    new_posts = [p for p in _read_csv(POSTS)
                 if p.get("first_seen_at", "") > last_ts]

    # 计算
    total_delta = sum(int(s.get("comment_delta") or 0) for s in this_round)
    threads_touched = len(this_round)
    budget_used = last.get("params", {}).get("max_comment_calls", 0)

    # 策略效果评估
    assessments: List[Dict[str, Any]] = []
    changes = last.get("changes") or {}

    if changes.get("max_comment_calls"):
        change = changes["max_comment_calls"]
        if total_delta > 10:
            assessments.append({
                "param": "max_comment_calls",
                "verdict": "有效",
                "detail": f"预算 {change['from']}→{change['to']}，本轮评论增量 {total_delta}",
            })
        elif total_delta > 0:
            assessments.append({
                "param": "max_comment_calls",
                "verdict": "边际",
                "detail": f"预算翻倍但评论只增 {total_delta}，可能预算过度",
            })
        else:
            assessments.append({
                "param": "max_comment_calls",
                "verdict": "无效",
                "detail": f"预算 {change['to']} 但零增量，可降回",
            })

    return {
        "ok": True,
        "last_decision_at": last_ts,
        "last_params": last.get("params"),
        "this_round": {
            "threads_touched": threads_touched,
            "total_comment_delta": total_delta,
            "new_posts": len(new_posts),
        },
        "assessments": assessments,
        "next_hint": _suggest_next(assessments),
    }


def _suggest_next(assessments: List[Dict[str, Any]]) -> str:
    """基于评估结果给出下轮调整建议（文本，给 strategy.py 参考）。"""
    for a in assessments:
        if a["verdict"] == "无效":
            return f"上轮调整 {a['param']} 无效，建议降回原值"
        if a["verdict"] == "有效":
            return f"上轮调整 {a['param']} 有效，可维持或继续加大"
    return "上轮无显著变化，维持当前参数"


if __name__ == "__main__":
    import json
    result = evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))