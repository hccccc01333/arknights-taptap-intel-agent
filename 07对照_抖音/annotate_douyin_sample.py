#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对抖音对照样本做限量标注（优先 DeepSeek v1.4 schema，无 Key 则弱规则）。

输出：annotations_douyin_sample.csv
口径与 02数据/annotate_reviews.py 对齐；抖音无星级 → score_raw 置空。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

MOD_DIR = Path(__file__).resolve().parent
ROOT = MOD_DIR.parent
sys.path.insert(0, str(ROOT / "02数据"))

from annotate_reviews import (  # noqa: E402
    ANN_FIELDS,
    PROMPT_VERSION,
    MODEL_DEFAULT,
    BASE_URL_DEFAULT,
    SYSTEM_PROMPT,
    build_messages,
    call_deepseek,
    load_dotenv_files,
    truncate_text,
    validate_annotation,
    now_iso,
)

TZ_CN = timezone(timedelta(hours=8))
COMMENTS_CSV = MOD_DIR / "comments_sample.csv"
OUT_CSV = MOD_DIR / "annotations_douyin_sample.csv"
RAW_DIR = MOD_DIR / "raw_llm"
CHECKPOINT = MOD_DIR / "checkpoint_annotate.json"
REPORT_DIR = MOD_DIR / "reports"

NEG_PAT = re.compile(
    r"(退坑|卸载|恶心|垃圾|诈骗|骗钱|欠债|爆炸|炸了|保底|非酋|坐牢|劝退|差评|翻车|崩溃|卡死|闪退|肝爆|肝炸|太肝|太贵|歪了)"
)
POS_PAT = re.compile(r"(好看|好听|神作|爱了|太强|好玩|感动|期待|感谢|YYDS|yyds|神中神|漂亮|帅|绝了|泪目|真香)")
GACHA_PAT = re.compile(r"(抽卡|十连|六星|出货|保底|歪了|非酋|欧皇|寻访|凭证|合成玉|垫了)")
BAL_PAT = re.compile(r"(强度|削弱|加强|失衡|环境|打不过|打红温|超标)")
STORY_PAT = re.compile(r"(剧情|主线|故事|EP|结局|刀子|感动|泪目|彩蛋|凯尔希)")
EVENT_PAT = re.compile(r"(活动|复刻|DD|集成战略|生息演算|剿灭|周年|联动|危机合约)")
CLIENT_PAT = re.compile(r"(闪退|卡顿|更新|客户端|包体|下载|登录|bug|BUG|Bug)")
OPS_PAT = re.compile(r"(客服|运维|维护|公告|补偿|礼包|价格|氪|定价)")


def length_bucket(n: int) -> str:
    if n < 40:
        return "short"
    if n < 200:
        return "mid"
    return "long"


def weak_annotate(text: str) -> dict[str, Any]:
    t = text or ""
    topic = "other"
    if GACHA_PAT.search(t):
        topic = "gacha"
    elif BAL_PAT.search(t):
        topic = "balance"
    elif STORY_PAT.search(t):
        topic = "story"
    elif EVENT_PAT.search(t):
        topic = "event"
    elif CLIENT_PAT.search(t):
        topic = "client"
    elif OPS_PAT.search(t):
        topic = "ops"
    elif re.search(r"(玩法|关卡|干员|部署|技能|肉鸽|塔防|弓箭)", t):
        topic = "gameplay"

    neg = bool(NEG_PAT.search(t))
    pos = bool(POS_PAT.search(t))
    if neg and not pos:
        sent = "负"
    elif pos and not neg:
        sent = "正"
    elif pos and neg:
        sent = "中"
    else:
        sent = "中"

    rhetoric = "none"
    cues = "none"
    if "😋" in t or "狗头" in t:
        if neg:
            rhetoric = "sarcasm"
            cues = "community_irony"
    if re.search(r"(顶级的|优质的).*(推荐是)", t):
        rhetoric = "template_praise"
        cues = "template_parallel,fake_recommend"
        sent = "中"

    return {
        "topic_primary": topic,
        "literal_sentiment": sent,
        "intended_sentiment": sent,
        "sentiment": sent,
        "actionable": "是" if (neg or topic in {"gacha", "client", "ops", "balance"}) else "否",
        "confidence": 0.42,
        "rhetoric": rhetoric,
        "rhetoric_confidence": 0.4,
        "incongruity_cues": cues,
        "reason": "弱规则标注(无API)"[:30],
    }


def load_comments(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done = set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("review_id") and not row.get("error"):
                done.add(str(row["review_id"]))
    return done


def append_row(path: Path, row: dict[str, Any]) -> None:
    write_header = not path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ANN_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in ANN_FIELDS})


def sample_rows(rows: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    if n <= 0 or n >= len(rows):
        return list(rows)
    rng = random.Random(seed)
    ranked = sorted(
        rows,
        key=lambda r: (int(r.get("like_count") or 0), len(r.get("text") or "")),
        reverse=True,
    )
    head = ranked[: max(n // 2, 1)]
    rest = [r for r in ranked if r not in head]
    need = n - len(head)
    if need > 0:
        head.extend(rng.sample(rest, min(need, len(rest))))
    return head[:n]


def main(args: argparse.Namespace) -> int:
    load_dotenv_files()
    for path in (MOD_DIR / ".env", ROOT / ".env", MOD_DIR / "config.example.env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

    if not COMMENTS_CSV.exists():
        print(f"缺少评论样本：{COMMENTS_CSV}", file=sys.stderr)
        return 2

    comments = load_comments(COMMENTS_CSV)
    if not comments:
        print("评论样本为空", file=sys.stderr)
        return 2

    picked = sample_rows(comments, args.limit, args.seed)
    done = set() if args.force else load_done(OUT_CSV)
    pending = [c for c in picked if str(c.get("comment_id")) not in done]

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    mode = "llm" if api_key and not args.weak_only else "weak"
    if mode == "weak":
        print("[mode] weak-rules (no API key or --weak-only)")
    else:
        print(f"[mode] llm model={args.model or os.environ.get('DEEPSEEK_MODEL', MODEL_DEFAULT)}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    client = None
    model = args.model or os.environ.get("DEEPSEEK_MODEL", MODEL_DEFAULT)
    if mode == "llm":
        from openai import OpenAI

        base_url = os.environ.get("DEEPSEEK_BASE_URL", BASE_URL_DEFAULT).strip() or BASE_URL_DEFAULT
        client = OpenAI(api_key=api_key, base_url=base_url)

    stats = {"ok": 0, "fail": 0, "mode": mode, "started_at": now_iso(), "total": len(pending)}
    print(f"[start] annotate n={len(pending)} / sample={len(picked)} corpus={len(comments)}")

    for i, c in enumerate(pending, 1):
        rid = str(c["comment_id"])
        text = c.get("text") or ""
        text_cut, truncated = truncate_text(text, args.truncate_at)
        text_len = len(text)
        bucket = length_bucket(text_len)
        score_raw = ""

        try:
            if mode == "llm":
                assert client is not None
                messages = build_messages(score_raw or "NA", text_cut)
                if messages and messages[0]["role"] == "system":
                    messages[0]["content"] = SYSTEM_PROMPT.replace(
                        "TapTap 评价标注员",
                        "抖音短视频评论标注员（无星级；score_raw 可能为 NA；文本偏短梗/弹幕风；schema 不变）",
                    )
                parsed, raw_json = call_deepseek(client, model, messages, args.reasoning_effort)
                (RAW_DIR / f"{rid}.json").write_text(raw_json, encoding="utf-8")
                prompt_version = PROMPT_VERSION
                model_name = model
            else:
                parsed = validate_annotation(weak_annotate(text_cut))
                prompt_version = "weak_rules_v1"
                model_name = "weak_rules"

            append_row(
                OUT_CSV,
                {
                    "review_id": rid,
                    **parsed,
                    "truncated": truncated,
                    "text_len": text_len,
                    "length_bucket": bucket,
                    "score_raw": score_raw,
                    "prompt_version": prompt_version,
                    "model": model_name,
                    "annotated_at": now_iso(),
                    "error": "",
                },
            )
            stats["ok"] += 1
        except Exception as exc:  # noqa: BLE001
            append_row(
                OUT_CSV,
                {
                    "review_id": rid,
                    "topic_primary": "",
                    "literal_sentiment": "",
                    "intended_sentiment": "",
                    "sentiment": "",
                    "actionable": "",
                    "confidence": "",
                    "rhetoric": "",
                    "rhetoric_confidence": "",
                    "incongruity_cues": "",
                    "reason": "",
                    "truncated": truncated,
                    "text_len": text_len,
                    "length_bucket": bucket,
                    "score_raw": score_raw,
                    "prompt_version": PROMPT_VERSION if mode == "llm" else "weak_rules_v1",
                    "model": model if mode == "llm" else "weak_rules",
                    "annotated_at": now_iso(),
                    "error": str(exc)[:300],
                },
            )
            stats["fail"] += 1
            print(f"[fail] {rid}: {exc}")

        if i % 10 == 0 or i == len(pending):
            CHECKPOINT.write_text(
                json.dumps({**stats, "progress": i}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[progress] {i}/{len(pending)} ok={stats['ok']} fail={stats['fail']}")
        if args.sleep > 0 and mode == "llm":
            time.sleep(args.sleep)

    stats["ended_at"] = now_iso()
    report = REPORT_DIR / f"annotate_douyin_{datetime.now(TZ_CN).strftime('%Y%m%d_%H%M%S')}.md"
    report.write_text(
        "\n".join(
            [
                "# 抖音样本标注报告",
                "",
                f"- 模式：{'DeepSeek v1.4 schema' if mode == 'llm' else '弱规则（已标明）'}",
                f"- 开始：{stats['started_at']}",
                f"- 结束：{stats['ended_at']}",
                f"- 成功：{stats['ok']}",
                f"- 失败：{stats['fail']}",
                f"- 输出：`{OUT_CSV.as_posix()}`",
                f"- 源评论：`{COMMENTS_CSV.as_posix()}`",
            ]
        ),
        encoding="utf-8",
    )
    print(f"[done] ok={stats['ok']} fail={stats['fail']} -> {OUT_CSV}")
    print(f"[report] {report}")
    return 0 if stats["fail"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Annotate Douyin contrast sample")
    p.add_argument("--limit", type=int, default=120, help="标注条数，默认 120")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--force", action="store_true")
    p.add_argument("--weak-only", action="store_true", help="强制弱规则，不走 API")
    p.add_argument("--model", default="")
    p.add_argument("--reasoning-effort", default="high", choices=["high", "max"])
    p.add_argument("--truncate-at", type=int, default=1500)
    p.add_argument("--sleep", type=float, default=0.15)
    return p


if __name__ == "__main__":
    raise SystemExit(main(build_parser().parse_args()))
