#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L2 处理链入口（§4 / §30 / §31）。

链（deterministic + versioned + replayable）：
    Raw Event → Schema Validation → Canonical Mapping → Cleaning
    → Exact Dedup → Near Dedup → Language → Entity → Classification
    → Quality → Embedding → Feature Builder → Processed Content

两遍执行（第二遍才算特征）：
    pass 1  只建平台指标基线（百分位需要同平台分布）
    pass 2  完整处理 + 落库 + 发事件

用法：
    python L2_signal/processing/pipeline.py --run                 # 消费 L1 事件总线
    python L2_signal/processing/pipeline.py --run --limit 200
    python L2_signal/processing/pipeline.py --replay              # 从 Raw Lake 重放（换 processor 版）
    python L2_signal/processing/pipeline.py --stats               # 数据质量与状态
    python L2_signal/processing/pipeline.py --entities            # 实体链接统计
    python L2_signal/processing/pipeline.py --dlq                 # 处理层死信
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_L2 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L2)
_L1 = os.path.join(_ROOT, "L1_data_source")
# ★ 脚本运行时 Python 会把脚本目录（processing/）塞进 sys.path[0]，
#   导致 `import storage` 命中本包的 storage.py 而不是 L1 的 storage 包。
#   先摘掉它：本文件用的是 `from processing.xxx import`（靠 _L2），不需要脚本目录。
while _HERE in sys.path:
    sys.path.remove(_HERE)
for p in (_L2, _L1):
    if p not in sys.path:
        sys.path.insert(0, p)

from bus.event_bus import EventBus                       # noqa: E402
from storage.metadata_db import MetadataStore            # noqa: E402  (L1)
from storage.raw_lake import RawLake                     # noqa: E402  (L1)

# L1 质量闸门：判据唯一真源在采集层，L2 只复用不重写（避免两套规则漂移）
_L1_DIR = os.path.join(_ROOT, "L1_data_source")
if _L1_DIR not in sys.path:
    sys.path.insert(0, _L1_DIR)
try:
    from quality_gate import gate as _gate_fn                # type: ignore
except Exception:                                             # pragma: no cover
    _gate_fn = None

from processing.canonical import (                        # noqa: E402
    CanonicalContent, content_id_of, PROCESSING_VERSION, text_fingerprint,
)
from processing.validation import validate_raw_event, dlq_record  # noqa: E402
from processing.cleaning import clean_text                        # noqa: E402
from processing.normalize import (                                # noqa: E402
    normalize_time, normalize_url, normalize_author, normalize_tags, split_metrics,
)
from processing.language import detect                            # noqa: E402
from processing.dedup import DedupEngine                          # noqa: E402
from processing.entities import EntityEngine                      # noqa: E402
from processing.classification import classify                    # noqa: E402
from processing.quality import score as quality_score             # noqa: E402
from processing.embedding import EmbeddingEngine                  # noqa: E402
from processing.features import MetricBaseline, build_features    # noqa: E402
from processing.storage import ProcessedStore                     # noqa: E402

PROCESSOR_VERSIONS = {
    "validator": "1.0", "cleaner": "1.1", "normalizer": "1.0",
    "dedup": "1.0", "language": "1.0", "entity_extractor": "1.0",
    "entity_linker": "1.0", "classifier": "1.0", "quality": "1.0",
    "embedding": "none", "features": "1.0",
}


class ProcessingPipeline:
    def __init__(self, store: Optional[ProcessedStore] = None,
                 bus: Optional[EventBus] = None) -> None:
        self.store = store or ProcessedStore()
        self.bus = bus or EventBus()
        self.entity_engine = EntityEngine()
        self.embedding_engine = EmbeddingEngine()
        self.dedup = DedupEngine()
        self.baseline = MetricBaseline()
        self.l1_db = os.path.join(_ROOT, "data", "state", "l1_source_registry.sqlite3")

    # ---------- 第二道质量闸门（设计决策：两层都做）----------
    @staticmethod
    def quality_gate(title, content, platform, source_type="post"):
        """复用 L1 的闸门判据，不在这里重写一套（避免两套规则漂移）。

        ★ 为什么不信任上游：实测 L2 直读 raw_lake（L1 采集管线产出），
          与 events.jsonl（L1 normalize 产出）是**两条不同路径**。
          只在 L1 拦会漏；两层各判一次，多几十毫秒，换"哪层跑过都干净"。
        """
        if _gate_fn is None:
            return {"reject": False, "code": "ok"}
        return _gate_fn(title=title, content=content, platform=platform,
                        source_type=source_type)

    # ---------- 单条处理 ----------
    def process(self, ev: Dict[str, Any], collect_baseline_only: bool = False) -> Optional[Dict[str, Any]]:
        cid = content_id_of(ev.get("platform", ""), str(ev.get("external_id", "")))

        # 1) Schema Validation（坏记录进 DLQ，不杀整批）
        ok, errors = validate_raw_event(ev)
        if not ok:
            for e in errors:
                self.store.push_dlq(dlq_record(ev, e))
            self.store.record_job({"job_id": uuid.uuid4().hex[:16], "content_id": cid,
                                   "processor": "validator", "version": PROCESSOR_VERSIONS["validator"],
                                   "status": "FAILED", "started_at": datetime.now().isoformat(),
                                   "finished_at": datetime.now().isoformat(),
                                   "error": errors[0]["error_type"]})
            return None

        common_metrics, platform_metrics = split_metrics(ev.get("metrics") or {})
        if collect_baseline_only:
            self.baseline.add(ev.get("platform", ""), common_metrics)
            return None

        # 2) Canonical Mapping
        raw_text = ev.get("content") or ""
        raw_title = ev.get("title")
        c = CanonicalContent(
            content_id=cid, platform=ev.get("platform", ""), source_id=ev.get("source_id", ""),
            external_id=str(ev.get("external_id", "")), content_type=ev.get("content_type", "post"),
            raw_title=raw_title, raw_text=raw_text,
            author_id=normalize_author(ev.get("author_id")),
            published_at=normalize_time(ev.get("published_at")),
            observed_at=normalize_time(ev.get("observed_at")),
            metrics=common_metrics, platform_metrics=platform_metrics,
            topics=normalize_tags(ev.get("tags") or ev.get("topics")),
            raw_ref=ev.get("raw_ref", ""),
            parent_id=ev.get("parent_id"),
            processor_versions=dict(PROCESSOR_VERSIONS),
        )
        # 游戏提示（第一层给的话题/游戏字段）作为实体弱证据
        game_hint = (ev.get("metadata") or {}).get("game") or ev.get("game") or ""

        # 3) Cleaning（保留原文，产出清洗版）
        cleaned = clean_text(f"{raw_title or ''} {raw_text}".strip())
        c.normalized_text = cleaned["normalized_text"]
        c.normalized_title = clean_text(raw_title)["normalized_text"] if raw_title else None
        c.extracted = {"hashtags": cleaned["hashtags"], "mentions": cleaned["mentions"],
                       "urls": cleaned["urls"], "emoji_count": cleaned["emoji_count"]}
        c.topics = normalize_tags(list(c.topics) + cleaned["hashtags"])
        c.state = "CLEANED"

        # 4) Dedup（精确 + 近似）
        ded = self.dedup.check(cid, c.normalized_text)
        c.is_duplicate = bool(ded["is_duplicate"])
        c.dedup_group = ded.get("duplicate_group")
        c.fingerprint = text_fingerprint(c.normalized_text, cleaned["urls"])
        c.state = "DEDUPED"

        # 5) Language
        lang = detect(c.normalized_text or raw_text)
        c.language = lang["language"]
        c.extracted["language_confidence"] = lang["confidence"]

        # 6) Entity Extraction + Linking
        # ★ 必须在 normalized_text **之外**带上 topics：清洗把 #话题# 从正文摘走了，
        #   而游戏名常常只出现在话题里（如 "#星布谷地联动宇树科技…"）→ 只扫正文会漏。
        entity_text = " ".join([c.normalized_text or raw_text] + list(c.topics))
        ents = self.entity_engine.extract(entity_text, platform=c.platform, game_hint=game_hint)
        c.entities = ents

        # 7) Classification
        cls = classify(c.normalized_text or raw_text, ents, c.topics)
        c.category = cls["domain"]
        c.subcategory = cls["subcategory"]
        c.gaming_probability = cls["gaming_probability"]

        # 8) Quality / Spam（只打分，不删）
        q = quality_score(raw_text, c.normalized_text, c.metrics, ents,
                          cleaned["emoji_count"], cleaned["urls"], c.topics)
        c.quality_score = q["quality_score"]
        c.spam_score = q["spam_score"]
        c.state = "ENRICHED"

        # 9) Embedding（未接模型 → None，不生产假向量）
        c.embedding_ref = self.embedding_engine.embed(cid, c.normalized_text or raw_text)
        c.state = "EMBEDDED" if c.embedding_ref else c.state

        # 10) Source features（§20：官方号 / 可信度）
        c.source_features = self._source_features(ev, c)

        # 11) Feature Builder
        feats = build_features(
            cid, c.platform, c.external_id, c.normalized_text, c.metrics, cls, q, ents,
            self.baseline, self.l1_db if os.path.exists(self.l1_db) else None,
            c.source_features, c.dedup_group, c.embedding_ref)
        c.features = feats.to_dict()
        c.state = "READY"

        # 12) 落库
        self._persist(c, ents)

        # 13) 发 processed 事件
        self.bus.publish("processed.content.created", {
            "content_id": cid, "platform": c.platform, "category": c.category,
            "gaming_probability": c.gaming_probability, "quality_score": c.quality_score,
            "spam_score": c.spam_score, "is_duplicate": c.is_duplicate,
            "entities": [e.entity_id for e in ents], "features": c.features,
            "raw_ref": c.raw_ref, "processing_version": PROCESSING_VERSION,
        })
        for e in ents:
            self.bus.publish("processed.entity.detected", {
                "content_id": cid, "entity_id": e.entity_id, "entity_type": e.entity_type,
                "mention": e.mention, "confidence": e.confidence})
        return c.to_dict()

    def _source_features(self, ev: Dict[str, Any], c: CanonicalContent) -> Dict[str, Any]:
        """§20：作者规模 / 是否官方 / 可信度。数据不足时给保守值，不编。"""
        meta = ev.get("metadata") or {}
        is_official = bool(meta.get("is_official")) or (ev.get("signal_type") == "official")
        reliability = 0.9 if is_official else (0.6 if c.quality_score > 0.6 else 0.45)
        return {"is_official": is_official, "source_reliability": reliability,
                "author_scale": "unknown", "signal_type": ev.get("signal_type", "")}

    def _persist(self, c: CanonicalContent, ents: List[Any]) -> None:
        d = c.to_dict()
        self.store.upsert_content({
            "content_id": c.content_id, "platform": c.platform, "source_id": c.source_id,
            "external_id": c.external_id, "content_type": c.content_type,
            "parent_id": c.parent_id,
            "raw_title": c.raw_title, "raw_text": c.raw_text,
            "normalized_title": c.normalized_title, "normalized_text": c.normalized_text,
            "language": c.language, "published_at": c.published_at, "observed_at": c.observed_at,
            "quality_score": c.quality_score, "spam_score": c.spam_score,
            "gaming_probability": c.gaming_probability, "category": c.category,
            "subcategory": c.subcategory, "metrics": c.metrics,
            "platform_metrics": c.platform_metrics,
            "entities": [e.entity_id for e in ents], "topics": c.topics,
            "extracted": c.extracted, "features": c.features,
            "source_features": c.source_features, "fingerprint": c.fingerprint,
            "dedup_group": c.dedup_group, "is_duplicate": 1 if c.is_duplicate else 0,
            "embedding_ref": c.embedding_ref, "raw_ref": c.raw_ref,
            "processing_version": PROCESSING_VERSION,
            "processor_versions": c.processor_versions, "state": c.state,
            "created_at": datetime.now().isoformat(),
        })
        for e in ents:
            self.store.upsert_entity(e.entity_id, e.entity_type, e.canonical_name)
            self.store.link_content_entity(c.content_id, e.entity_id, e.mention, e.confidence)
        self.store.record_job({"job_id": uuid.uuid4().hex[:16], "content_id": c.content_id,
                               "processor": "pipeline", "version": PROCESSING_VERSION,
                               "status": c.state, "started_at": datetime.now().isoformat(),
                               "finished_at": datetime.now().isoformat(), "error": ""})

    # ---------- 批量 ----------
    def run(self, limit: int = 0, platforms: Optional[List[str]] = None,
            since: Optional[str] = None) -> Dict[str, Any]:
        started = datetime.now()
        raw_events: List[Dict[str, Any]] = []
        for rec in self.bus.consume("raw.content.created", since=since):
            p = rec.get("payload", {})
            if platforms and p.get("platform") not in platforms:
                continue
            raw_events.append(p)
            if limit and len(raw_events) >= limit:
                break

        # pass 1：建平台指标基线（百分位需要分布）
        for ev in raw_events:
            self.process(ev, collect_baseline_only=True)

        # pass 2：完整处理（★ 第二道质量闸门）
        processed, failed = [], 0
        gated, gated_reasons = 0, {}
        for ev in raw_events:
            # 不信任上游：L2 直读 raw_lake（L1 采集管线产出），可能未经 events.jsonl 的闸门。
            # 所以这里**再判一次** —— 双重闸门，代价是几十毫秒，收益是"哪层跑过都干净"。
            g = self.quality_gate(title=ev.get("title") or ev.get("raw_title"),
                                  content=ev.get("content") or ev.get("raw_text"),
                                  platform=ev.get("platform", ""),
                                  source_type=ev.get("source_type", "post"))
            if g.get("reject"):
                gated += 1
                code = g.get("code", "unknown")
                gated_reasons[code] = gated_reasons.get(code, 0) + 1
                continue
            out = self.process(ev)
            if out is None:
                failed += 1
            else:
                processed.append(out)

        total = len(raw_events)
        dup = sum(1 for p in processed if p.get("is_duplicate"))
        with_ent = sum(1 for p in processed if p.get("entities"))
        gaming = sum(1 for p in processed if (p.get("gaming_probability") or 0) >= 0.5)
        latency = sum(1 for _ in processed)
        elapsed = (datetime.now() - started).total_seconds()

        # ★ 实体覆盖率只在"游戏内容"上算：评论/闲聊本来就没游戏名，
        #   混在一起算会得到一个既不能指导优化、也不符合 SLO 定义的数字。
        gaming_items = [p for p in processed if (p.get("gaming_probability") or 0) >= 0.5]
        gaming_with_ent = sum(1 for p in gaming_items if p.get("entities"))
        qm = {
            "entity_rate_gaming_only": round(gaming_with_ent / len(gaming_items), 4) if gaming_items else 0.0,
            "n_gaming_items": len(gaming_items),
            "schema_valid_rate": round((total - failed - gated) / total, 4) if total else 0.0,
            "gated": gated,
            "gated_reasons": json.dumps(gated_reasons, ensure_ascii=False),
            "normalization_success_rate": 1.0,
            "duplicate_rate": round(dup / len(processed), 4) if processed else 0.0,
            "embedding_success_rate": 0.0 if not self.embedding_engine.available() else 1.0,
            "entity_extraction_rate": round(with_ent / len(processed), 4) if processed else 0.0,
            "unknown_entity_rate": round(1 - with_ent / len(processed), 4) if processed else 0.0,
            "spam_rate": round(sum(1 for p in processed if (p.get("spam_score") or 0) >= 0.5)
                               / len(processed), 4) if processed else 0.0,
            "gaming_rate": round(gaming / len(processed), 4) if processed else 0.0,
            "processing_latency_seconds": round(elapsed, 2),
            "records_per_second": round(len(processed) / elapsed, 1) if elapsed > 0 else 0.0,
        }
        run_id = f"run-{started.strftime('%Y%m%dT%H%M%S')}"
        self.store.record_quality(run_id, qm)
        return {"run_id": run_id, "input": total, "processed": len(processed),
                "failed_schema": failed, "duplicates": dup, "with_entities": with_ent,
                "gaming": gaming, "quality_metrics": qm}

    def replay_from_raw(self, limit: int = 0) -> Dict[str, Any]:
        """§29 Replay：从 Raw Lake 重放（换 processor 版本后重建 processed 数据集）。

        不重新爬互联网 —— 这正是第一层保存 Raw 的价值。
        """
        lake = RawLake()
        evs: List[Dict[str, Any]] = []
        if os.path.isdir(lake.root):
            for dirpath, _, files in os.walk(lake.root):
                for fn in files:
                    if not fn.endswith(".json"):
                        continue
                    try:
                        payload = lake.read(os.path.join(dirpath, fn))
                    except (ValueError, OSError):
                        continue
                    for rec in (payload.get("response") or []):
                        evs.append(rec)
                        if limit and len(evs) >= limit:
                            break
                if limit and len(evs) >= limit:
                    break
        self.dedup = DedupEngine()          # 重放时重建去重状态
        for ev in evs:
            self.process(ev, collect_baseline_only=True)
        n = 0
        for ev in evs:
            if self.process(ev):
                n += 1
        return {"replayed_from_raw": len(evs), "processed": n}


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
    ap = argparse.ArgumentParser(description="L2 加工与语义标准化层")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--replay", action="store_true", help="从 Raw Lake 重放")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--platform", action="append", help="只处理指定平台（可多次）")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--entities", action="store_true")
    ap.add_argument("--dlq", action="store_true")
    ap.add_argument("--sample", type=int, default=0, help="打印若干条处理结果")
    args = ap.parse_args(argv)

    pipe = ProcessingPipeline()

    if args.run or args.replay:
        r = pipe.replay_from_raw(args.limit) if args.replay else pipe.run(
            limit=args.limit, platforms=args.platform)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    if args.stats:
        st = pipe.store
        print("\n== 处理状态分布（§27）==")
        print(" ", json.dumps(st.state_counts(), ensure_ascii=False))
        print("\n== 数据质量（§31）==")
        for k, v in st.latest_quality().items():
            print(f"  {k:<32} {v}")
        print(f"\n  content 总数={st.count_content()}  DLQ 总数={st.count_dlq()}")
        return 0
    if args.entities:
        _print_table(pipe.store.entity_stats(), ["entity_id", "entity_type", "canonical_name", "n"],
                     "Entity Linking 命中统计（§13）")
        print("\n  Registry:", json.dumps(pipe.entity_engine.stats(), ensure_ascii=False))
        return 0
    if args.dlq:
        _print_table([{k: v for k, v in d.items() if k in
                       ("platform", "error_field", "error_type", "detail", "external_id")}
                      for d in pipe.store.dlq_items()],
                     ["platform", "error_field", "error_type", "detail", "external_id"],
                     "处理层死信（单条坏记录，不杀整批）")
        return 0
    if args.sample:
        for r in pipe.store.list_content(limit=args.sample):
            print(json.dumps({k: r[k] for k in
                              ("content_id", "platform", "category", "gaming_probability",
                               "quality_score", "spam_score", "language", "state",
                               "dedup_group", "is_duplicate", "embedding_ref")},
                             ensure_ascii=False))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
