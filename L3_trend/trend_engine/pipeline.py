#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3 Trend Intelligence 主入口。

    Processed Content (L2)
        → Candidate Filter → Event Clustering → Time-Series
        → Signals(V/A/B/N/D/E/S/C) → Scoring(Hot/Momentum/Confidence)
        → Lifecycle → Event Store → 第四层 + 第一层反馈

用法：
    python L3_trend/trend_engine/pipeline.py --run
    python L3_trend/trend_engine/pipeline.py --top 15          # 按 Hot Score
    python L3_trend/trend_engine/pipeline.py --top 15 --by opportunity_score
    python L3_trend/trend_engine/pipeline.py --stats
    python L3_trend/trend_engine/pipeline.py --show evt_xxx    # 含分数历史
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_L3 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L3)
for p in (_L3, _HERE, os.path.join(_ROOT, "L2_signal"), os.path.join(_ROOT, "L1_data_source")):
    if p not in sys.path:
        sys.path.insert(0, p)

from processing.storage import ProcessedStore                 # noqa: E402  (L2)
from bus.event_bus import EventBus                            # noqa: E402  (L1)
from trend_engine.candidate import filter_candidates          # noqa: E402
from trend_engine.similarity import SimilarityEngine          # noqa: E402
from trend_engine.clustering import EventClusterer, resolve_parent_child  # noqa: E402
from trend_engine.evaluation import evaluate                  # noqa: E402
from trend_engine.timeseries import EventTimeSeries, EntityBaseline  # noqa: E402
from trend_engine import signals as S                         # noqa: E402
from trend_engine.scoring import hot_score, momentum_score, confidence_score, rank_score  # noqa: E402
from trend_engine.lifecycle import classify, opportunity_window  # noqa: E402
from trend_engine.store import EventStore                     # noqa: E402
from trend_engine.feedback import FeedbackEngine              # noqa: E402
from trend_engine import TREND_ENGINE_VERSION                 # noqa: E402

_JSON_FIELDS = ("metrics", "platform_metrics", "entities", "topics", "extracted",
                "features", "source_features", "processor_versions", "subcategory")


def hydrate(row: Dict[str, Any]) -> Dict[str, Any]:
    """把 L2 库里的 JSON 串解析回 dict/list，供特征计算使用。"""
    out = dict(row)
    for k in _JSON_FIELDS:
        v = out.get(k)
        if isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except ValueError:
                out[k] = {} if k.endswith(("metrics", "features")) else []
    return out


class TrendEngine:
    def __init__(self, store: Optional[EventStore] = None,
                 l2: Optional[ProcessedStore] = None,
                 bus: Optional[EventBus] = None) -> None:
        self.store = store or EventStore()
        self.l2 = l2 or ProcessedStore()
        self.bus = bus or EventBus()
        self.sim = SimilarityEngine()
        self.clusterer = EventClusterer(self.sim)
        self.feedback = FeedbackEngine(self.bus)

    # ---------- 主流程 ----------
    def run(self, limit: int = 0) -> Dict[str, Any]:
        started = datetime.now()
        rows = [hydrate(r) for r in self.l2.list_content(limit=limit or 100000)]
        cand = filter_candidates(rows)
        kept = cand["kept"]
        baseline = EntityBaseline(rows)

        # 已有事件（用于在线匹配）
        events: List[Dict[str, Any]] = [dict(e) for e in self.store.list_events(limit=5000)]
        created = 0
        for c in kept:
            eid, detail = self.clusterer.match(c, events)
            if eid is None:
                ev = self.clusterer.create_event(c, list(c.get("entities") or []))
                events.append(ev)
                self.store.upsert_event(ev)
                self.bus.publish("trend.event.created", {"event_id": ev["event_id"],
                                                         "canonical_title": ev["canonical_title"]})
                created += 1
                eid = ev["event_id"]
            self.store.link_content(eid, c.get("content_id", ""),
                                    detail.get("score", 0.0), TREND_ENGINE_VERSION)
            ev = next(e for e in events if e["event_id"] == eid)
            ev["last_updated_at"] = c.get("observed_at") or ev.get("last_updated_at")

        # 离线纠错：合并
        merges = self.clusterer.find_merges(events)
        merge_done = []
        for a, b, s in merges:
            ea = next((e for e in events if e["event_id"] == a), None)
            eb = next((e for e in events if e["event_id"] == b), None)
            if not ea or not eb:
                continue
            # 保留成员多的那个（被合并的事件标 merged，历史留在 merge_history 里）
            keep, drop = (ea, eb) if (ea.get("content_count") or 0) >= (eb.get("content_count") or 0) else (eb, ea)
            self.store.record_merge(keep["event_id"], drop["event_id"], s, "离线纠错：相似度超阈值")
            self.bus.publish("trend.event.merged", {"kept": keep["event_id"],
                                                    "merged": drop["event_id"], "similarity": s})
            merge_done.append({"kept": keep["event_id"], "merged": drop["event_id"], "similarity": s})
            events = [e for e in events if e["event_id"] != drop["event_id"]]

        # 逐个事件算分
        scored = []
        member_index = self._member_index()
        for ev in events:
            out = self.score_event(ev, member_index, baseline, kept)
            if out:
                scored.append(out)

        scored.sort(key=lambda e: -(e.get("opportunity_score") or 0))

        # —— §42 父子事件（派生关系，每轮重算）——
        self.store.clear_parents()
        pc_links = resolve_parent_child(events)
        for link in pc_links:
            self.store.set_parent(link["child"], link["parent"])
        self.store.conn.commit()
        pc_counts = self.store.parent_child_counts()

        # —— §38/39 评估（缺 ground truth 的项不给数字）——
        sims_by_event: Dict[str, List[float]] = {}
        for ev in events:
            sims_by_event[ev["event_id"]] = [
                r[0] for r in self.store.conn.execute(
                    "SELECT similarity_score FROM event_content WHERE event_id=? AND similarity_score IS NOT NULL",
                    (ev["event_id"],)).fetchall()]
        evaluation = evaluate(events, member_index, sims_by_event)

        elapsed = (datetime.now() - started).total_seconds()
        return {
            "engine_version": TREND_ENGINE_VERSION,
            "input_contents": len(rows), "candidates": len(kept),
            "candidate_rate": round(len(kept) / len(rows), 4) if rows else 0,
            "drop_reasons": cand["drop_reasons"],
            "events_total": len(events), "events_created": created,
            "merges": merge_done,
            "scored": len(scored),
            "lifecycle_counts": self.store.lifecycle_counts(),
            "parent_child": {**pc_counts, "links": pc_links[:5]},
            "evaluation": evaluation,
            "elapsed_seconds": round(elapsed, 1),
            "top": [{"event_id": e["event_id"], "title": e.get("canonical_title"),
                     "lifecycle": e.get("lifecycle"), "hot": e.get("hot_score"),
                     "momentum": e.get("momentum_score"), "confidence": e.get("confidence_score"),
                     "opportunity": e.get("opportunity_score"),
                     "content_count": e.get("content_count")} for e in scored[:10]],
        }

    def _member_index(self) -> Dict[str, List[Dict[str, Any]]]:
        """event_id → 成员内容（hydrate 过的）。"""
        idx: Dict[str, List[Dict[str, Any]]] = {}
        by_id = {r["content_id"]: r for r in self.l2.list_content(limit=100000)}
        for ev in self.store.list_events(limit=5000):
            ids = self.store.members(ev["event_id"])
            idx[ev["event_id"]] = [hydrate(by_id[i]) for i in ids if i in by_id]
        return idx

    # ---------- 单事件打分 ----------
    def score_event(self, ev: Dict[str, Any], member_index: Dict[str, List],
                    baseline: EntityBaseline, all_kept: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        members = member_index.get(ev["event_id"], [])
        if not members:
            return None
        ts = EventTimeSeries(members)
        totals = ts.totals()
        series = ts.series("15m")
        mention_seq = [p["mention_count"] for p in series] or [len(members)]
        view_seq = [p["view_delta"] for p in series] or [0.0]

        # —— 信号 ——
        primary_entity = (ev.get("entity_ids") or [None])[0]
        base = baseline.rate(primary_entity)
        # 当前速率：最近窗口的 mention / 窗口小时数
        hours = max(1 / 60, len(series) * 0.25) if series else 1.0
        rate_now = (sum(mention_seq[-3:]) / max(1, min(3, len(mention_seq)))) / 0.25 if series else len(members)
        vel = S.velocity_score(rate_now, base["rate"], base["sufficient"])
        # 速度序列（相邻窗口的速率）→ 加速度
        vel_seq = [m / 0.25 for m in mention_seq]
        acc = S.acceleration_score(vel_seq)
        burst = S.burst_score(view_seq)
        # Novelty：与历史事件（不含自己）最大相似度
        others = [e for e in self.store.list_events(limit=200) if e["event_id"] != ev["event_id"]]
        max_sim = max((self.sim.event_event(ev, o) for o in others), default=None) if others else None
        nov = S.novelty_score(max_sim)
        platform_counts: Dict[str, int] = {}
        for m in members:
            p = m.get("platform")
            if p:
                platform_counts[p] = platform_counts.get(p, 0) + 1
        diff = S.diffusion_score(platform_counts)
        # Engagement：取成员百分位的中位数（不是最高，避免被单条极端值拉高）
        pcts = _median_percentiles(members)
        eng = S.engagement_score(pcts)
        unique_authors = totals["unique_authors"]
        orig_ratio = 1.0 - (sum(1 for m in members if m.get("is_duplicate")) / max(1, len(members)))
        div = S.diversity_score(unique_authors, len(members), orig_ratio)
        official_ratio = sum(1 for m in members
                             if (m.get("source_features") or {}).get("is_official")) / max(1, len(members))
        avg_q = sum(float(m.get("quality_score") or 0) for m in members) / max(1, len(members))
        cred = S.credibility_score(official_ratio, min(unique_authors, 10), avg_q)

        dims = {"velocity": vel["score"], "acceleration": acc["score"], "burst": burst["score"],
                "diffusion": diff["score"], "engagement": eng["score"], "novelty": nov["score"],
                "source_diversity": div["score"], "credibility": cred["score"]}
        hot = hot_score(dims)

        # Momentum（§34）：velocity_growth 用速度序列首尾比，diffusion_growth 用平台数增长
        v_growth = _growth(vel_seq)
        d_growth = min(1.0, len(platform_counts) / 3.0)
        mom = momentum_score(acc["score"], v_growth, d_growth)

        # 簇内一致性：成员与事件标题的平均相似度
        coherence = _coherence(members, ev, self.sim)
        conf = confidence_score(len(members), len(platform_counts), div["score"], avg_q,
                                coherence, base["sufficient"])

        vol_norm = min(1.0, len(members) / 100.0)
        life = classify(vol_norm, vel["score"], acc["score"], d_growth, ev.get("lifecycle"))
        opp = opportunity_window(hot["hot_score"], acc["score"], life["lifecycle"],
                                 conf["confidence_score"])
        rank = rank_score(hot["hot_score"], mom["momentum_score"], conf["confidence_score"])

        ev.update({
            "canonical_title": ev.get("canonical_title") or (members[0].get("raw_text") or "")[:30],
            "lifecycle": life["lifecycle"],
            "hot_score": hot["hot_score"], "momentum_score": mom["momentum_score"],
            "confidence_score": conf["confidence_score"], "opportunity_score": opp["opportunity_score"],
            "novelty_score": nov["score"], "velocity_score": vel["score"],
            "acceleration_score": acc["score"], "burst_score": burst["score"],
            "diffusion_score": diff["score"], "engagement_score": eng["score"],
            "source_diversity_score": div["score"], "credibility_score": cred["score"],
            "content_count": len(members), "platform_count": len(platform_counts),
            "platforms": sorted(platform_counts.keys()),
            "entity_ids": ev.get("entity_ids") or [],
            "last_updated_at": totals.get("last_at") or ev.get("last_updated_at"),
            "peak_at": ev.get("peak_at") or (totals.get("last_at") if life["lifecycle"] == "PEAKING" else None),
            "representative_content_id": self.clusterer.centroid_members(members)["representative"],
            "metadata": {"signals_detail": {"velocity": vel, "acceleration": acc, "burst": burst,
                                            "novelty": nov, "diffusion": diff, "engagement": eng,
                                            "diversity": div, "credibility": cred},
                         "coherence": round(coherence, 3), "rank": rank,
                         "lifecycle_reason": life["reason"],
                         "semantic_source": self.sim.semantic_source},
            "engine_version": TREND_ENGINE_VERSION,
        })
        self.store.upsert_event(ev)

        # ★ 分数只追加，不覆盖（§37）
        self.store.append_score_history({
            "event_id": ev["event_id"], "scored_at": datetime.now().isoformat(),
            "hot_score": hot["hot_score"], "momentum_score": mom["momentum_score"],
            "confidence_score": conf["confidence_score"], "velocity": vel["score"],
            "acceleration": acc["score"], "diffusion": diff["score"],
            "engagement": eng["score"], "novelty": nov["score"], "lifecycle": life["lifecycle"]})
        if life["lifecycle"] != ev.get("lifecycle"):
            self.store.append_lifecycle(ev["event_id"], ev.get("lifecycle"), life["lifecycle"], life["reason"])
        self.store.save_timeseries(ev["event_id"], "15m", series)

        # 发布 + 闭环反馈
        self.feedback.publish_event(ev, [m.get("content_id") for m in members])
        if life["lifecycle"] in ("EMERGING", "GROWING") and conf["confidence_score"] < 0.6:
            # 置信不足又像早期 → 让第一层多采点数据来补证据（这就是闭环的意义）
            self.feedback.request_priority(ev, [f"tap_topic_feed", "weibo_keyword"], level="P1")
        return ev


def _median_percentiles(members: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for key, src in (("view", "view_percentile"), ("comment", "comment_percentile"),
                     ("favorite", "like_percentile")):
        vals = [float((m.get("features") or {}).get(src))
                for m in members
                if isinstance((m.get("features") or {}).get(src), (int, float))]
        out[key] = sorted(vals)[len(vals) // 2] if vals else None
    out["share"] = None      # 平台转发数只在微博有，缺就留 None（不填充）
    return out


def _growth(seq: List[float]) -> float:
    if len(seq) < 2 or seq[0] == 0:
        return 0.0
    return round(max(0.0, min(1.0, (seq[-1] - seq[0]) / (abs(seq[0]) + 1e-6))), 4)


def _coherence(members: List[Dict[str, Any]], ev: Dict[str, Any], sim: SimilarityEngine) -> float:
    if not members:
        return 0.0
    vals = [sim.score(m, ev)["score"] for m in members[:30]]
    return sum(vals) / len(vals)


def _print_table(rows: List[Dict[str, Any]], cols: List[str], title: str = "") -> None:
    if title:
        print(f"\n== {title} ==")
    if not rows:
        print("  (空)")
        return
    w = {c: max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    print("  " + "  ".join(str(c).ljust(w[c]) for c in cols))
    print("  " + "  ".join("-" * w[c] for c in cols))
    for r in rows:
        print("  " + "  ".join(str(r.get(c, ""))[:w[c]].ljust(w[c]) for c in cols))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="L3 热点识别与趋势智能层")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--top", type=int, default=0, help="列出 Top N 事件")
    ap.add_argument("--by", default="opportunity_score",
                    choices=["hot_score", "momentum_score", "confidence_score", "opportunity_score"])
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--show", help="查看单个事件（含分数历史）")
    ap.add_argument("--eval", action="store_true", help="输出评估结果（检测延迟/聚类一致性/缺什么真值）")
    ap.add_argument("--cluster-truth", help="人工标注 JSON：{content_id: 事件标} → 算 ARI/NMI")
    ap.add_argument("--detection-truth", help="外部真值 JSON：{event_key: 爆发时刻} → 算 Precision/Recall/LeadTime")
    args = ap.parse_args(argv)

    eng = TrendEngine()

    if args.run:
        print(json.dumps(eng.run(limit=args.limit), ensure_ascii=False, indent=2))
        return 0
    if args.stats:
        st = eng.store
        print(f"\n事件总数: {st.count_events()}   成员关联数: {st.count_members()}")
        print("生命周期分布:", json.dumps(st.lifecycle_counts(), ensure_ascii=False))
        print("父子事件:", json.dumps(st.parent_child_counts(), ensure_ascii=False))
        return 0
    if args.eval:
        from trend_engine.evaluation import evaluate as _eval
        st = eng.store
        events = st.list_events(limit=5000)
        # 必须拿到真实成员（带 observed_at），否则时间分辨率体检会误报 no_timestamps
        members = eng._member_index()
        sims: Dict[str, List[float]] = {}
        for e in events:
            sims[e["event_id"]] = [r[0] for r in st.conn.execute(
                "SELECT similarity_score FROM event_content WHERE event_id=? AND similarity_score IS NOT NULL",
                (e["event_id"],)).fetchall()]
        truth = None
        if args.cluster_truth:
            truth = json.load(open(args.cluster_truth, encoding="utf-8"))
        dtruth = None
        if args.detection_truth:
            dtruth = json.load(open(args.detection_truth, encoding="utf-8"))
        print(json.dumps(_eval(events, members, sims, cluster_truth=truth,
                               detection_truth=dtruth), ensure_ascii=False, indent=2))
        return 0
    if args.show:
        ev = eng.store.get_event(args.show)
        if not ev:
            print(f"[warn] 没有事件 {args.show}")
            return 0
        print(json.dumps({k: v for k, v in ev.items() if k != "metadata"},
                         ensure_ascii=False, indent=2))
        _print_table([{k: h[k] for k in ("scored_at", "hot_score", "velocity", "acceleration", "lifecycle")}
                      for h in eng.store.score_history(args.show)],
                     ["scored_at", "hot_score", "velocity", "acceleration", "lifecycle"],
                     "分数历史（只追加不覆盖）")
        return 0
    if args.top:
        rows = eng.store.list_events(limit=args.top, order_by=args.by)
        _print_table([{"event_id": r["event_id"], "title": (r.get("canonical_title") or "")[:26],
                       "lifecycle": r.get("lifecycle"), "hot": r.get("hot_score"),
                       "mom": r.get("momentum_score"), "conf": r.get("confidence_score"),
                       "opp": r.get("opportunity_score"), "n": r.get("content_count"),
                       "plat": r.get("platform_count")} for r in rows],
                     ["event_id", "title", "lifecycle", "hot", "mom", "conf", "opp", "n", "plat"],
                     f"Top {args.top} 事件（按 {args.by}）")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
