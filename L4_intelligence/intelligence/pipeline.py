#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第四层主入口。

    Trend Event (L3)
        → Event Gate(§45 分级) → Evidence → Trend Analyst → Relevance(§14 gate)
        → [Research] → Audience → Opportunity → Strategist → Creative
        → Evaluator(§31 有界修订环) → Risk(§32) → Human Review(§34)
        → Growth Intelligence Package(§52)

用法：
    python L4_intelligence/intelligence/pipeline.py --run --limit 5
    python L4_intelligence/intelligence/pipeline.py --event evt_xxx --package
    python L4_intelligence/intelligence/pipeline.py --stats
    python L4_intelligence/intelligence/pipeline.py --feedback idea_1 adopt "理由"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_L4 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L4)
for p in (_ROOT, _L4, _HERE, os.path.join(_ROOT, "L2_signal"), os.path.join(_ROOT, "L1_data_source")):
    if p not in sys.path:
        sys.path.insert(0, p)

from intelligence.graph.graph import Ctx, HAS_LANGGRAPH, run  # noqa: E402
from intelligence.state import empty_state  # noqa: E402
from intelligence.store import IntelligenceStore, tier_of, gate_basis  # noqa: E402
from intelligence.upstream import Upstream  # noqa: E402
from intelligence import INTELLIGENCE_VERSION  # noqa: E402

# T0 不进 Agent（§45）：hot < .40 直接跳过，省掉绝大部分成本
MIN_TIER_FOR_AGENT = ("T1", "T2", "T3")


def temporal_ok(up: Upstream, sample: int = 3000) -> bool:
    """数据有没有时间分辨率（与 L3 `evaluation.temporal_resolution` 同一判定）。

    有 → Event Gate 用规格口径（hot/momentum）；无 → 用 confidence + 体量降级口径。
    """
    from datetime import datetime
    from intelligence.nodes.evidence import build as _build   # 复用同一时间解析
    rows = (up.l2.list_content(limit=sample) if up.l2 else [])[:sample]
    stamps = []
    for r in rows:
        t = r.get("observed_at") or r.get("published_at")
        if not t:
            continue
        try:
            stamps.append(datetime.fromisoformat(str(t).replace("Z", "").replace("+08:00", "")))
        except ValueError:
            continue
    if len(stamps) < 2:
        return False
    return (max(stamps) - min(stamps)).total_seconds() / 3600.0 >= 1.0


def run_event(up: Upstream, store: IntelligenceStore, event: Dict[str, Any],
              engine: str = "auto", use_cache: bool = True,
              human_review: bool = True, temporal_ok_flag: bool = False) -> Dict[str, Any]:
    tier = tier_of(event, temporal_ok=temporal_ok_flag)
    if use_cache:
        cached = store.cached_analysis(event)
        if cached:
            payload = store.get_analysis(cached)
            if payload:
                payload["cache_hit"] = True
                payload["analysis_id"] = cached
                return payload
    state = empty_state(event)
    ctx = Ctx(up, human_review=human_review)
    result = run(state, ctx, engine=engine)
    result["tier"] = tier
    result["trace"] = ctx.trace          # ★ 必须在保存前写，否则 payload 里没有轨迹
    result["analysis_id"] = store.save_analysis(event, result)
    result["cache_hit"] = False
    return result


def to_package(result: Dict[str, Any]) -> Dict[str, Any]:
    """§52：给第六层的最终对象（不是一篇报告，是结构化 Growth Intelligence Package）。"""
    pack = result.get("evidence_pack") or {}
    ev = pack.get("event") or {}
    creatives = result.get("creatives") or []
    return {
        "analysis_id": result.get("analysis_id"),
        "engine": result.get("engine"),
        "tier": result.get("tier"),
        "event": {
            "event_id": result.get("event_id"),
            "title": ev.get("title"),
            "lifecycle": ev.get("lifecycle"),
            "hot_score": ev.get("hot_score"),
            "momentum": ev.get("momentum_score"),
            "confidence": ev.get("confidence"),
        },
        "analysis": {
            "trigger": (result.get("trend_analysis") or {}).get("trigger"),
            "why_now": (result.get("trend_analysis") or {}).get("why_now"),
            "narratives": (result.get("trend_analysis") or {}).get("narratives", [])[:3],
            "unknowns": (result.get("trend_analysis") or {}).get("key_uncertainties", []),
        },
        "taptap": {
            "relevance": (result.get("relevance") or {}).get("score"),
            "dimensions": (result.get("relevance") or {}).get("dimensions"),
            "route": (result.get("relevance") or {}).get("route"),
            "audiences": [a.get("segment") for a in (result.get("audiences") or [])],
        },
        "opportunities": [
            {"name": o.get("name"), "score": o.get("opportunity_score"),
             "growth_goal": o.get("growth_goal"),
             "growth_mechanism": o.get("growth_mechanism"),
             "window": o.get("opportunity_window")}
            for o in (result.get("opportunities") or [])
        ],
        "creatives": [
            {"idea_id": c.get("idea_id"), "name": c.get("idea_name"),
             "type": c.get("creative_type_name"), "score": c.get("score"),
             "launch_window": c.get("launch_window"),
             "primary_metric": c.get("primary_metric"),
             "source_refs": c.get("source_refs")}
            for c in creatives
        ],
        "risks": [r.get("description") for r in ((result.get("risk") or {}).get("risks") or [])],
        "risk_level": (result.get("risk") or {}).get("risk_level"),
        "recommended_metrics": sorted({c.get("primary_metric") for c in creatives
                                       if c.get("primary_metric")}),
        "status": result.get("status"),
        "llm_used": result.get("llm_used", False),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="L4 AI Intelligence & Growth Reasoning")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--event", help="只跑指定事件")
    ap.add_argument("--engine", default="auto", choices=("auto", "stdlib", "langgraph"))
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--human-review", dest="human_review", action="store_true", default=True)
    ap.add_argument("--no-human-review", dest="human_review", action="store_false")
    ap.add_argument("--package", action="store_true", help="输出 §52 Growth Intelligence Package")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--feedback", nargs=3, metavar=("IDEA_ID", "DECISION", "REASON"))
    ap.add_argument("--history", help="查看某事件的历次分析（§44 不覆盖）")
    args = ap.parse_args(argv)

    store = IntelligenceStore()

    if args.stats:
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
        print(f"langgraph 已装: {HAS_LANGGRAPH}")
        return 0
    if args.feedback:
        idea, decision, reason = args.feedback
        fid = store.save_feedback(idea, "", decision, reason)
        print(f"已记录反馈 {fid}（decision={decision}）")
        print(json.dumps(store.feedback_stats(), ensure_ascii=False))
        return 0
    if args.history:
        print(json.dumps(store.analyses_for(args.history), ensure_ascii=False, indent=2))
        return 0
    if not (args.run or args.event):
        ap.print_help()
        return 0

    up = Upstream()
    tok = temporal_ok(up)
    try:
        if args.event:
            ev = up.event(args.event)
            if not ev:
                print(f"[warn] 没有事件 {args.event}")
                return 0
            events = [ev]
        else:
            events = up.events(limit=5000)

        results: List[Dict[str, Any]] = []
        skipped_t0 = 0
        n = 0
        for ev in events:
            if tier_of(ev, temporal_ok=tok) not in MIN_TIER_FOR_AGENT:
                skipped_t0 += 1
                continue
            r = run_event(up, store, ev, engine=args.engine,
                          use_cache=not args.no_cache, human_review=args.human_review,
                          temporal_ok_flag=tok)
            results.append(r)
            n += 1
            if args.limit and n >= args.limit:
                break

        if args.package or args.event:
            for r in results:
                print(json.dumps(to_package(r), ensure_ascii=False, indent=2))
        else:
            summary = {
                "intelligence_version": INTELLIGENCE_VERSION,
                "engine": results[0].get("engine") if results else None,
                "langgraph_installed": HAS_LANGGRAPH,
                "ran": len(results),
                "skipped_tier0": skipped_t0,
                "gate_basis": gate_basis(tok),
                "temporal_resolution_ok": tok,
                "status_counts": _count(results, "status"),
                "route_counts": _count(results, "route"),
                "risk_counts": _count(results, lambda r: (r.get("risk") or {}).get("risk_level")),
                "creatives": sum(len(r.get("creatives") or []) for r in results),
                "llm_used_any": any(r.get("llm_used") for r in results),
                "items": [{"event_id": r.get("event_id"),
                           "title": (r.get("evidence_pack", {}).get("event") or {}).get("title"),
                           "relevance": (r.get("relevance") or {}).get("score"),
                           "route": r.get("route"), "status": r.get("status"),
                           "n_opp": len(r.get("opportunities") or []),
                           "n_creative": len(r.get("creatives") or []),
                           "mean_score": (r.get("evaluation") or {}).get("mean_score"),
                           "risk": (r.get("risk") or {}).get("risk_level"),
                           "cache_hit": r.get("cache_hit")} for r in results],
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        up.close()
        store.close()


def _count(results: List[Dict[str, Any]], key: Any) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in results:
        v = key(r) if callable(key) else r.get(key)
        out[str(v)] = out.get(str(v), 0) + 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
