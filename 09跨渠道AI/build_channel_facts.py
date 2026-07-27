#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从四渠标注表聚合 facts_cross_channel.json（数字只来自代码）。"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

MOD_DIR = Path(__file__).resolve().parent
ROOT = MOD_DIR.parent

TZ_CN = timezone(timedelta(hours=8))
TOPIC_CN = {
    "gacha": "抽卡/商业化",
    "balance": "数值/平衡",
    "gameplay": "关卡/玩法",
    "story": "剧情",
    "event": "活动/版本",
    "client": "客户端/性能",
    "ops": "运营/客服",
    "other": "综合/其他",
}
CHANNEL_ORDER = ("taptap", "bilibili", "douyin", "weibo")


def pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def clean_anns(rows: list[dict]) -> list[dict]:
    out = []
    seen: set[str] = set()
    for r in rows:
        rid = str(r.get("review_id") or "").strip()
        if not rid or rid.startswith("probe_"):
            continue
        if (r.get("error") or "").strip():
            continue
        if not (r.get("sentiment") or "").strip():
            continue
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


def detect_mode(rows: list[dict]) -> str:
    if not rows:
        return "none"
    models = Counter((r.get("model") or "") for r in rows)
    top = models.most_common(1)[0][0]
    if "weak" in top:
        return "weak_rules"
    return "llm"


def sentiment_block(rows: list[dict]) -> dict:
    c = Counter((r.get("sentiment") or "").strip() for r in rows)
    n = sum(c.values())
    block = {"n": n, "counts": {k: int(c.get(k, 0)) for k in ("正", "中", "负")}}
    block["pct"] = {k: pct(block["counts"][k], n) for k in ("正", "中", "负")}
    block["neg_rate"] = round(block["counts"]["负"] / n, 4) if n else 0.0
    return block


def topic_block(rows: list[dict], top_k: int = 8) -> list[dict]:
    c = Counter((r.get("topic_primary") or "other").strip() or "other" for r in rows)
    n = sum(c.values()) or 1
    out = []
    for k, v in c.most_common(top_k):
        out.append(
            {
                "topic": k,
                "label": TOPIC_CN.get(k, k),
                "n": int(v),
                "pct": pct(v, n),
            }
        )
    return out


def rhetoric_block(rows: list[dict]) -> dict:
    types = Counter()
    non = 0
    for r in rows:
        rhe = (r.get("rhetoric") or "none").strip() or "none"
        if rhe != "none":
            non += 1
            types[rhe] += 1
    n = len(rows)
    return {
        "non_none_n": non,
        "non_none_pct": pct(non, n),
        "by_type": [{"rhetoric": k, "n": int(v), "pct": pct(v, n)} for k, v in types.most_common()],
    }


def pick_quotes(
    anns: list[dict],
    text_by_id: dict[str, str],
    *,
    per_sentiment: int = 2,
    max_len: int = 72,
) -> list[dict]:
    buckets: dict[str, list[tuple[float, dict]]] = {"正": [], "中": [], "负": []}
    for a in anns:
        sent = (a.get("sentiment") or "").strip()
        if sent not in buckets:
            continue
        rid = str(a["review_id"])
        text = (text_by_id.get(rid) or "").replace("\n", " ").strip()
        if len(text) < 8:
            continue
        try:
            conf = float(a.get("confidence") or 0)
        except ValueError:
            conf = 0.0
        clipped = text if len(text) <= max_len else text[: max_len - 1] + "…"
        buckets[sent].append(
            (
                conf,
                {
                    "id": rid,
                    "sentiment": sent,
                    "topic": (a.get("topic_primary") or "other").strip() or "other",
                    "topic_label": TOPIC_CN.get(
                        (a.get("topic_primary") or "other").strip() or "other",
                        a.get("topic_primary") or "other",
                    ),
                    "rhetoric": (a.get("rhetoric") or "none").strip() or "none",
                    "text": clipped,
                    "confidence": conf,
                },
            )
        )
    out: list[dict] = []
    for sent in ("负", "正", "中"):
        items = sorted(buckets[sent], key=lambda x: x[0], reverse=True)[:per_sentiment]
        for _, q in items:
            out.append(q)
    return out


def source_quality(comments: list[dict]) -> dict:
    src = Counter((c.get("source") or "").strip() or "unknown" for c in comments)
    total = sum(src.values()) or 1
    degraded_keys = ("fallback_seed", "degraded_sample")
    degraded_n = sum(src.get(k, 0) for k in degraded_keys)
    live_n = total - degraded_n
    return {
        "n_raw": int(sum(src.values())),
        "by_source": {k: int(v) for k, v in src.most_common()},
        "live_n": int(live_n),
        "degraded_n": int(degraded_n),
        "degraded_pct": pct(degraded_n, total),
        "is_degraded": degraded_n > 0 and degraded_n / total >= 0.5,
    }


def channel_facts(
    *,
    key: str,
    label: str,
    role: str,
    anns: list[dict],
    text_by_id: dict[str, str],
    comments: list[dict] | None = None,
    note: str = "",
) -> dict:
    anns = clean_anns(anns)
    quality = source_quality(comments) if comments is not None else {
        "n_raw": len(anns),
        "by_source": {"taptap_reviews": len(anns)},
        "live_n": len(anns),
        "degraded_n": 0,
        "degraded_pct": 0.0,
        "is_degraded": False,
    }
    return {
        "key": key,
        "label": label,
        "role": role,
        "n_annotated": len(anns),
        "annotate_mode": detect_mode(anns),
        "prompt_version": Counter((r.get("prompt_version") or "") for r in anns).most_common(1)[0][0]
        if anns
        else "",
        "model_top": Counter((r.get("model") or "") for r in anns).most_common(1)[0][0] if anns else "",
        "data_quality": quality,
        "note": note,
        "sentiment": sentiment_block(anns),
        "topics": topic_block(anns),
        "rhetoric": rhetoric_block(anns),
        "sample_quotes": pick_quotes(anns, text_by_id),
    }


def cross_block(channels: dict[str, dict]) -> dict:
    topic_map: dict[str, list[dict]] = {}
    for ck in CHANNEL_ORDER:
        ch = channels[ck]
        for t in ch["topics"]:
            topic_map.setdefault(t["topic"], []).append(
                {
                    "channel": ck,
                    "label": ch["label"],
                    "n": t["n"],
                    "pct": t["pct"],
                }
            )
    alignment = []
    for topic, presence in sorted(
        topic_map.items(),
        key=lambda kv: (-len(kv[1]), -sum(x["n"] for x in kv[1])),
    ):
        alignment.append(
            {
                "topic": topic,
                "topic_label": TOPIC_CN.get(topic, topic),
                "n_channels": len(presence),
                "presence": presence,
            }
        )
    return {
        "neg_pct_by_channel": {
            ck: channels[ck]["sentiment"]["pct"]["负"] for ck in CHANNEL_ORDER
        },
        "n_annotated_by_channel": {
            ck: channels[ck]["n_annotated"] for ck in CHANNEL_ORDER
        },
        "topic_alignment_seed": alignment,
        "degraded_channels": [
            ck
            for ck in CHANNEL_ORDER
            if channels[ck]["data_quality"].get("is_degraded")
        ],
    }


def allowed_numbers(payload: dict) -> dict:
    """供合成脚本校验：允许出现的整数与百分比集合。"""
    ints: set[int] = set()
    pcts: set[float] = set()

    def add_int(x: int) -> None:
        ints.add(int(x))

    def add_pct(x: float) -> None:
        pcts.add(round(float(x), 1))

    for ck, ch in payload["channels"].items():
        add_int(ch["n_annotated"])
        q = ch["data_quality"]
        add_int(q.get("n_raw", 0))
        add_int(q.get("live_n", 0))
        add_int(q.get("degraded_n", 0))
        add_pct(q.get("degraded_pct", 0))
        s = ch["sentiment"]
        add_int(s["n"])
        for k in ("正", "中", "负"):
            add_int(s["counts"][k])
            add_pct(s["pct"][k])
        for t in ch["topics"]:
            add_int(t["n"])
            add_pct(t["pct"])
        rhe = ch["rhetoric"]
        add_int(rhe["non_none_n"])
        add_pct(rhe["non_none_pct"])
        for bt in rhe["by_type"]:
            add_int(bt["n"])
            add_pct(bt["pct"])
    cross = payload["cross"]
    for v in cross["neg_pct_by_channel"].values():
        add_pct(v)
    for v in cross["n_annotated_by_channel"].values():
        add_int(v)
    for row in cross["topic_alignment_seed"]:
        add_int(row["n_channels"])
        for p in row["presence"]:
            add_int(p["n"])
            add_pct(p["pct"])
    # small structural constants often appear in prose
    for i in range(0, 5):
        add_int(i)
    return {
        "ints": sorted(ints),
        "pcts": sorted(pcts),
    }


def build() -> dict:
    # TapTap
    tt_ann = load_csv(ROOT / "03标注结果" / "annotations_v1_4.csv")
    tt_rev = {
        str(r["review_id"]): (r.get("text") or "")
        for r in load_csv(ROOT / "02数据" / "processed" / "reviews_clean.csv")
    }
    # Bilibili
    bili_ann = load_csv(ROOT / "06对照_B站" / "annotations_bili_sample.csv")
    bili_com = load_csv(ROOT / "06对照_B站" / "comments_sample.csv")
    bili_text = {str(c["comment_id"]): (c.get("text") or "") for c in bili_com}
    # Douyin
    dy_ann = load_csv(ROOT / "07对照_抖音" / "annotations_douyin_sample.csv")
    dy_com = load_csv(ROOT / "07对照_抖音" / "comments_sample.csv")
    dy_text = {str(c["comment_id"]): (c.get("text") or "") for c in dy_com}
    # Weibo
    wb_ann = load_csv(ROOT / "08对照_微博" / "annotations_weibo_sample.csv")
    wb_com = load_csv(ROOT / "08对照_微博" / "comments_sample.csv")
    wb_text = {str(c["comment_id"]): (c.get("text") or "") for c in wb_com}

    channels = {
        "taptap": channel_facts(
            key="taptap",
            label="TapTap",
            role="评价主链（商店口碑）",
            anns=tt_ann,
            text_by_id=tt_rev,
            comments=None,
            note="全量 v1.4 标注；日/周报主 KPI 仅用本渠。",
        ),
        "bilibili": channel_facts(
            key="bilibili",
            label="B站",
            role="传播对照第一渠（视频评论）",
            anns=bili_ann,
            text_by_id=bili_text,
            comments=bili_com,
            note="限量 LLM 标注样本；不作跨渠道 KPI。",
        ),
        "douyin": channel_facts(
            key="douyin",
            label="抖音",
            role="短视频传播对照",
            anns=dy_ann,
            text_by_id=dy_text,
            comments=dy_com,
            note="采集含风控降级；若 source=fallback_seed 占比高，仅作方法演示。",
        ),
        "weibo": channel_facts(
            key="weibo",
            label="微博",
            role="公开热议对照",
            anns=wb_ann,
            text_by_id=wb_text,
            comments=wb_com,
            note="采集含风控降级；若 source=degraded_sample，仅作方法演示。",
        ),
    }
    payload = {
        "meta": {
            "title": "四渠道跨渠 facts",
            "generated_at": datetime.now(TZ_CN).isoformat(timespec="seconds"),
            "schema": "cross_channel_facts_v1",
            "channel_order": list(CHANNEL_ORDER),
            "boundary": (
                "数字仅来自标注/样本聚合；对照渠不作全网 KPI；"
                "抖音/微博若降级样本占比高须在叙事中标明。"
            ),
        },
        "channels": channels,
        "cross": cross_block(channels),
    }
    payload["allowed_numbers"] = allowed_numbers(payload)
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description="Build cross-channel facts JSON")
    p.add_argument(
        "--out",
        default=str(MOD_DIR / "facts_cross_channel.json"),
        help="输出路径",
    )
    args = p.parse_args()
    payload = build()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {out}")
    for ck in CHANNEL_ORDER:
        ch = payload["channels"][ck]
        s = ch["sentiment"]["pct"]
        print(
            f"  {ch['label']}: n={ch['n_annotated']} "
            f"正{s['正']}%/中{s['中']}%/负{s['负']}% "
            f"degraded={ch['data_quality'].get('is_degraded')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
