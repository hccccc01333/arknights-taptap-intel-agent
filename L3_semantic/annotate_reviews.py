#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DeepSeek 结构化标注 v1.4：两段式裂隙 + 回归样口径微调。"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data/raw/taptap"
OUT_DIR = ROOT / "data/annotations"
PROCESSED = ROOT / "data/processed/reviews"
ANN_DIR = OUT_DIR
RAW_LLM_DIR = ANN_DIR / "raw_llm"
REPORT_DIR = ANN_DIR / "reports"

CLEAN_CSV = PROCESSED / "reviews_clean.csv"
ANN_CSV = ANN_DIR / "annotations_v1_4.csv"
CHECKPOINT = ANN_DIR / "checkpoint_v1_4.json"

PROMPT_VERSION = "v1.4"
MODEL_DEFAULT = "deepseek-v4-pro"
BASE_URL_DEFAULT = "https://api.deepseek.com"

TOPICS = {"gacha", "balance", "gameplay", "story", "event", "client", "ops", "other"}
SENTIMENTS = {"正", "中", "负"}
ACTIONABLES = {"是", "否"}
RHETORICS = {"none", "sarcasm", "gaoji_hei", "fanchuan", "template_praise"}
INCONGRUITY_CUES = {
    "none",
    "praise_shell_neg_fact",
    "template_parallel",
    "paren_leak",
    "community_irony",
    "overstatement",
    "star_text_conflict",
    "fake_recommend",
}

TZ_CN = timezone(timedelta(hours=8))

DEFAULT_GAME = "arknights"  # 游戏档案 key，见 games/<key>.json


def load_game_profile(game_key: str) -> dict[str, Any]:
    """加载游戏档案（games/game_profile.py，唯一参数化入口）。"""
    sys.path.insert(0, str(ROOT / "games"))
    from game_profile import load as _load  # noqa: PLC0415

    return _load(game_key)


GAME_NAME = load_game_profile(DEFAULT_GAME).get("name") or "该游戏"

SYSTEM_PROMPT_TEMPLATE = """你是《__GAME__》TapTap 评价标注员。
你必须输出 JSON 对象（json），不要 Markdown，不要解释。

【强制两段式推理——先修辞，后整体态度】
资料依据：情感不协调（incongruity）是讽刺/高级黑识别核心。禁止跳过第①段直接打正负。

① 修辞通道（单独判断）
先只回答：字面态度与真实意图是否分裂？按下表逐条扫描，命中则记入 incongruity_cues（可多选，英文代码逗号拼接；全无则 "none"）：

| 代码 | 裂隙线索 |
|------|----------|
| praise_shell_neg_fact | 褒义外壳 + 负面事实（体验极佳+退坑/打不过/六炮） |
| template_parallel | 机械排比好评（A是顶级的，B是…推荐是…）信息稀薄 |
| paren_leak | 括号/限定泄真（「可能只是因为议长…」把夸缩成靠单一因素） |
| community_irony | 圈梗+反讽收束（「最需要脑」、差评配😋/开心脸）；注意：狗头炫耀好运≠自动反讽负 |
| overstatement | 夸张到违和（顶级/极佳叠满，后文全槽） |
| star_text_conflict | 星级与文本强冲突（仅作线索，单独命中≠自动打负） |
| fake_recommend | 推荐假动作（「推荐是下载的」像交差） |

再填 literal_sentiment / rhetoric / rhetoric_confidence。
- incongruity_cues≠none 时通常分裂；但「仅 star_text_conflict」或「仅狗头亲昵」证据弱 → 可 rhetoric=none，rhetoric_confidence≤0.55

rhetoric 口径：
- none：无修辞裂隙；真诚轻槽；亲昵吐槽（理智花不完）；建设性提问
- sarcasm：阴阳/反话（开心脸配差抽记录、明确反话）
- gaoji_hei：整段像夸，细节全是骂
- fanchuan：强证据假人设/引战（不足用 sarcasm）
- template_praise：机械排比敷衍好评

② 整体态度通道（在①之后）
intended_sentiment / sentiment = 对游戏的「整体态度」，不是「有没有槽点」。
规则：
1. rhetoric∈{gaoji_hei,fanchuan} 或（sarcasm 且明确踩核）→ sentiment 跟真实意图（常负）
2. rhetoric=template_praise → 多为中
3. rhetoric=none：
   - 正文辱骂/仇恨词/劝退/退坑/卸载 → 负（即使 4–5 星）
   - 高星 + 单点难度/机制吐槽/建设性提问 → 正或中（勿因红温、冷却长直接打负）
   - 亲昵玩笑（理智花不完、太好玩没体力）→ 正
   - 狗头/表情 + 炫耀好运/易上手 → 正（炫耀调侃）；狗头 + 差抽/劝退 → 负
4. 模糊时参考 score_raw：4–5偏正，1–2偏负，3偏中
5. 禁止：仅因 star_text_conflict 或单个槽点把真诚高星打成负
6. 禁止：忽略裂隙清单把高级黑/模板吹捧当真心好评

回归样口径（必须遵守）：
- 「有些关卡打红温」四星 → 正（单点难度槽）
- 「有没有理智花不完教程」五星 → 正，rhetoric=none（亲昵）
- 「冷却为什么这么长」四星 → 中或正（机制建议）
- 「你踏马…写没了」五星辱骂 → 负（星级虚高）
- 「10连出6星很刺激了[狗头]」四星 → 正（炫耀调侃，非差评反讽）

应标正：「好玩就是有点肝」「除了X没缺点」「不错，希望改进XX」
应标负：议长/退坑/六炮高级黑；「推荐是下载的」模板→中；辱骂五星；开心脸配差抽

其他字段：
- topic_primary: gacha|balance|gameplay|story|event|client|ops|other
- actionable: 是/否（有可改点可为是，即使整体=正）
- confidence: 0~1
- incongruity_cues: 见上表
- reason: ≤30汉字；有裂隙须点明

禁止编造原文没有的版本名/活动名。多主题只选主诉求。

EXAMPLE JSON:
{"topic_primary":"gameplay","literal_sentiment":"正","intended_sentiment":"负","sentiment":"负","actionable":"是","confidence":0.9,"rhetoric":"gaoji_hei","rhetoric_confidence":0.9,"incongruity_cues":"praise_shell_neg_fact,paren_leak,community_irony","reason":"褒义外壳+退坑打不过六炮"}
"""

# 游戏名注入：用模板替换而非 f-string（prompt 内含 JSON 花括号，f-string 会被求值污染）
SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.replace("__GAME__", GAME_NAME)

FEW_SHOTS = [
    {
        "user": "score_raw: 5\ntext:\n好玩",
        "assistant": {
            "topic_primary": "other",
            "literal_sentiment": "正",
            "intended_sentiment": "正",
            "sentiment": "正",
            "actionable": "否",
            "confidence": 0.9,
            "rhetoric": "none",
            "rhetoric_confidence": 0.9,
            "incongruity_cues": "none",
            "reason": "短好评无裂隙",
        },
    },
    {
        "user": "score_raw: 5\ntext:\n好玩就是有点肝",
        "assistant": {
            "topic_primary": "gameplay",
            "literal_sentiment": "正",
            "intended_sentiment": "正",
            "sentiment": "正",
            "actionable": "是",
            "confidence": 0.9,
            "rhetoric": "none",
            "rhetoric_confidence": 0.9,
            "incongruity_cues": "none",
            "reason": "真诚轻槽非讽刺",
        },
    },
    {
        "user": "score_raw: 4\ntext:\n有些关卡很容易打红温，已经红温了16-16是人啊",
        "assistant": {
            "topic_primary": "gameplay",
            "literal_sentiment": "负",
            "intended_sentiment": "正",
            "sentiment": "正",
            "actionable": "是",
            "confidence": 0.86,
            "rhetoric": "none",
            "rhetoric_confidence": 0.88,
            "incongruity_cues": "none",
            "reason": "四星单点难度槽仍认可",
        },
    },
    {
        "user": "score_raw: 5\ntext:\n有没有理智花不完教程。。",
        "assistant": {
            "topic_primary": "gameplay",
            "literal_sentiment": "中",
            "intended_sentiment": "正",
            "sentiment": "正",
            "actionable": "否",
            "confidence": 0.88,
            "rhetoric": "none",
            "rhetoric_confidence": 0.85,
            "incongruity_cues": "none",
            "reason": "亲昵玩笑暗示好玩",
        },
    },
    {
        "user": (
            "score_raw: 4\ntext:\n为什么这游戏重新放置角色的冷却时间这么长？"
            "是不推荐作战过程中更换策略吗？如果没有预判好重新来的话会浪费很多时间。"
        ),
        "assistant": {
            "topic_primary": "gameplay",
            "literal_sentiment": "负",
            "intended_sentiment": "中",
            "sentiment": "中",
            "actionable": "是",
            "confidence": 0.86,
            "rhetoric": "none",
            "rhetoric_confidence": 0.88,
            "incongruity_cues": "none",
            "reason": "四星机制建议非劝退",
        },
    },
    {
        "user": "score_raw: 4\ntext:\n易上手，还有10连出6星，很刺激了[表情_狗头]",
        "assistant": {
            "topic_primary": "gacha",
            "literal_sentiment": "正",
            "intended_sentiment": "正",
            "sentiment": "正",
            "actionable": "否",
            "confidence": 0.84,
            "rhetoric": "none",
            "rhetoric_confidence": 0.8,
            "incongruity_cues": "none",
            "reason": "狗头炫耀好运非差评",
        },
    },
    {
        "user": "score_raw: 5\ntext:\n你踏马是出生吧，还真把米格鲁写没了？？？？？？？",
        "assistant": {
            "topic_primary": "story",
            "literal_sentiment": "负",
            "intended_sentiment": "负",
            "sentiment": "负",
            "actionable": "是",
            "confidence": 0.9,
            "rhetoric": "none",
            "rhetoric_confidence": 0.75,
            "incongruity_cues": "star_text_conflict",
            "reason": "五星辱骂剧情态度为负",
        },
    },
    {
        "user": (
            "score_raw: 5\ntext:\n好玩的游戏，游戏体验感极佳（可能只是因为议长大人带给我的体验），"
            "没出之前我是退过几次坑的，抄攻略不会抄，不抄打不过，但有了议长大人就不一样了，"
            "六炮打不死那就再部署再打六炮😋\n我们方舟最需要脑"
        ),
        "assistant": {
            "topic_primary": "gameplay",
            "literal_sentiment": "正",
            "intended_sentiment": "负",
            "sentiment": "负",
            "actionable": "是",
            "confidence": 0.92,
            "rhetoric": "gaoji_hei",
            "rhetoric_confidence": 0.92,
            "incongruity_cues": "praise_shell_neg_fact,paren_leak,community_irony,overstatement",
            "reason": "裂隙:外壳+括号泄真+六炮",
        },
    },
    {
        "user": "score_raw: 5\ntext:\n音乐是顶级的，画面是优质的，玩法是有意思的，推荐是下载的",
        "assistant": {
            "topic_primary": "other",
            "literal_sentiment": "正",
            "intended_sentiment": "中",
            "sentiment": "中",
            "actionable": "否",
            "confidence": 0.85,
            "rhetoric": "template_praise",
            "rhetoric_confidence": 0.9,
            "incongruity_cues": "template_parallel,fake_recommend,overstatement",
            "reason": "裂隙:排比空夸+假推荐",
        },
    },
    {
        "user": "score_raw: 5\ntext:\n抽卡记录可以解释一切[表情_开心][表情_开心][表情_开心]",
        "assistant": {
            "topic_primary": "gacha",
            "literal_sentiment": "正",
            "intended_sentiment": "负",
            "sentiment": "负",
            "actionable": "是",
            "confidence": 0.88,
            "rhetoric": "sarcasm",
            "rhetoric_confidence": 0.86,
            "incongruity_cues": "community_irony,star_text_conflict",
            "reason": "裂隙:开心脸反讽抽卡",
        },
    },
    {
        "user": "score_raw: 1\ntext:\n保底恶心，卸载了",
        "assistant": {
            "topic_primary": "gacha",
            "literal_sentiment": "负",
            "intended_sentiment": "负",
            "sentiment": "负",
            "actionable": "是",
            "confidence": 0.92,
            "rhetoric": "none",
            "rhetoric_confidence": 0.9,
            "incongruity_cues": "none",
            "reason": "直述差评无修辞",
        },
    },
]

ANN_FIELDS = [
    "review_id",
    "topic_primary",
    "literal_sentiment",
    "intended_sentiment",
    "sentiment",
    "actionable",
    "confidence",
    "rhetoric",
    "rhetoric_confidence",
    "incongruity_cues",
    "reason",
    "truncated",
    "text_len",
    "length_bucket",
    "score_raw",
    "prompt_version",
    "model",
    "annotated_at",
    "error",
]


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def load_dotenv_files() -> None:
    for path in (DATA_DIR / ".env", ROOT / ".env", ROOT / "L1_data_source/collectors/taptap" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def truncate_text(text: str, limit: int = 1500) -> tuple[str, bool]:
    text = text or ""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def build_messages(score_raw: str, text: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for ex in FEW_SHOTS:
        messages.append({"role": "user", "content": ex["user"]})
        messages.append(
            {"role": "assistant", "content": json.dumps(ex["assistant"], ensure_ascii=False)}
        )
    messages.append({"role": "user", "content": f"score_raw: {score_raw}\ntext:\n{text}"})
    return messages


def validate_annotation(obj: dict[str, Any]) -> dict[str, Any]:
    topic = str(obj.get("topic_primary", "")).strip()
    lit = str(obj.get("literal_sentiment", "")).strip()
    intent = str(obj.get("intended_sentiment", "")).strip()
    sent = str(obj.get("sentiment", "")).strip() or intent
    act = str(obj.get("actionable", "")).strip()
    rhetoric = str(obj.get("rhetoric", "")).strip()
    conf = float(obj.get("confidence"))
    rconf = float(obj.get("rhetoric_confidence", conf))
    reason = str(obj.get("reason", "")).strip()
    cues_raw = str(obj.get("incongruity_cues", "none")).strip() or "none"

    if topic not in TOPICS:
        raise ValueError(f"invalid topic_primary: {topic}")
    for name, val in [
        ("literal_sentiment", lit),
        ("intended_sentiment", intent),
        ("sentiment", sent),
    ]:
        if val not in SENTIMENTS:
            raise ValueError(f"invalid {name}: {val}")
    if act not in ACTIONABLES:
        raise ValueError(f"invalid actionable: {act}")
    if rhetoric not in RHETORICS:
        raise ValueError(f"invalid rhetoric: {rhetoric}")
    if not (0.0 <= conf <= 1.0) or not (0.0 <= rconf <= 1.0):
        raise ValueError("confidence out of range")

    cues = [c.strip() for c in cues_raw.replace("，", ",").split(",") if c.strip()]
    if not cues:
        cues = ["none"]
    for c in cues:
        if c not in INCONGRUITY_CUES:
            raise ValueError(f"invalid incongruity_cues token: {c}")
    if "none" in cues and len(cues) > 1:
        cues = [c for c in cues if c != "none"]
    cues_norm = "none" if cues == ["none"] else ",".join(dict.fromkeys(cues))

    # 强制 sentiment == intended
    sent = intent
    if len(reason) > 40:
        reason = reason[:30]
    return {
        "topic_primary": topic,
        "literal_sentiment": lit,
        "intended_sentiment": intent,
        "sentiment": sent,
        "actionable": act,
        "confidence": round(conf, 4),
        "rhetoric": rhetoric,
        "rhetoric_confidence": round(rconf, 4),
        "incongruity_cues": cues_norm,
        "reason": reason,
    }


def extract_json(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    if not content:
        raise ValueError("empty model content")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", content)
        if not m:
            raise
        return json.loads(m.group(0))


def load_done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done = set()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("review_id") and not row.get("error"):
                done.add(str(row["review_id"]))
    return done


def append_ann_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ANN_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in ANN_FIELDS})


def save_checkpoint(data: dict[str, Any], path: Path | None = None) -> None:
    target = path or CHECKPOINT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def call_deepseek(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    reasoning_effort: str,
) -> tuple[dict[str, Any], str]:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        max_tokens=1024,
        reasoning_effort=reasoning_effort,
        extra_body={"thinking": {"type": "enabled"}},
    )
    msg = resp.choices[0].message
    content = msg.content or ""
    raw = {
        "content": content,
        "reasoning_content": getattr(msg, "reasoning_content", None),
        "finish_reason": resp.choices[0].finish_reason,
        "usage": resp.usage.model_dump() if resp.usage else None,
    }
    parsed = validate_annotation(extract_json(content))
    return parsed, json.dumps(raw, ensure_ascii=False)


PROBE_EXAMPLES = [
    {
        "review_id": "probe_gaoji_hei_yichang",
        "score_raw": "5",
        "text": (
            "好玩的游戏，游戏体验感极佳（可能只是因为议长大人带给我的体验），"
            "没出之前我是退过几次坑的，抄攻略不会抄，不抄打不过，但有了议长大人就不一样了，"
            "六炮打不死那就再部署再打六炮😋\n我们方舟最需要脑"
        ),
        "text_len": "0",
        "length_bucket": "mid",
    },
    {
        "review_id": "probe_template_praise",
        "score_raw": "5",
        "text": "音乐是顶级的，画面是优质的，玩法是有意思的，推荐是下载的",
        "text_len": "0",
        "length_bucket": "short",
    },
]


def annotate_one(
    client: OpenAI,
    model: str,
    effort: str,
    row: dict[str, Any],
    out_csv: Path,
    truncate_at: int,
    retries: int,
) -> tuple[bool, str]:
    rid = str(row["review_id"])
    text, truncated = truncate_text(row.get("text") or "", truncate_at)
    if not row.get("text_len") or row.get("text_len") == "0":
        row = {**row, "text_len": str(len(row.get("text") or ""))}
    messages = build_messages(str(row.get("score_raw", "")), text)
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            parsed, raw_json = call_deepseek(client, model, messages, effort)
            (RAW_LLM_DIR / f"{rid}.json").write_text(raw_json, encoding="utf-8")
            append_ann_row(
                out_csv,
                {
                    "review_id": rid,
                    **parsed,
                    "truncated": truncated,
                    "text_len": row.get("text_len", ""),
                    "length_bucket": row.get("length_bucket", ""),
                    "score_raw": row.get("score_raw", ""),
                    "prompt_version": PROMPT_VERSION,
                    "model": model,
                    "annotated_at": now_iso(),
                    "error": "",
                },
            )
            return True, ""
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            time.sleep(min(2 ** attempt, 8))
    append_ann_row(
        out_csv,
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
            "text_len": row.get("text_len", ""),
            "length_bucket": row.get("length_bucket", ""),
            "score_raw": row.get("score_raw", ""),
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "annotated_at": now_iso(),
            "error": last_err[:300],
        },
    )
    return False, last_err


def main(args: argparse.Namespace) -> int:
    load_dotenv_files()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("缺少环境变量 DEEPSEEK_API_KEY", file=sys.stderr)
        return 2

    base_url = os.environ.get("DEEPSEEK_BASE_URL", BASE_URL_DEFAULT).strip() or BASE_URL_DEFAULT
    model = args.model or os.environ.get("DEEPSEEK_MODEL", MODEL_DEFAULT)
    out_csv = Path(args.output) if args.output else ANN_CSV
    checkpoint_path = Path(args.checkpoint) if args.checkpoint else CHECKPOINT

    RAW_LLM_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    if args.include_probes:
        rows.extend(PROBE_EXAMPLES)

    if not args.probes_only:
        input_csv = Path(args.input) if getattr(args, "input", "") else CLEAN_CSV
        if not input_csv.exists():
            print(f"缺少清洗表：{input_csv}", file=sys.stderr)
            return 2
        df_rows = list(csv.DictReader(input_csv.open(encoding="utf-8-sig")))
        if args.score_filter:
            wanted = set(args.score_filter.split(","))
            df_rows = [r for r in df_rows if str(r.get("score_raw")) in wanted]
        if args.bucket:
            df_rows = [r for r in df_rows if r.get("length_bucket") == args.bucket]
        if args.ids:
            wanted_ids = {x.strip() for x in args.ids.split(",") if x.strip()}
            df_rows = [r for r in df_rows if str(r.get("review_id")) in wanted_ids]
        if args.ids_file:
            id_path = Path(args.ids_file)
            wanted_ids = {
                ln.strip()
                for ln in id_path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            }
            df_rows = [r for r in df_rows if str(r.get("review_id")) in wanted_ids]
        done = set() if args.force else load_done_ids(out_csv)
        # probes always force-refresh when included
        probe_ids = {p["review_id"] for p in PROBE_EXAMPLES}
        pending = [r for r in df_rows if str(r.get("review_id")) not in done]
        if args.limit and args.limit > 0:
            pending = pending[: args.limit]
        rows.extend(pending)
        # if probes already in out and force not set, still re-run probes by removing from done logic:
        if args.include_probes:
            rows = [r for r in rows if r["review_id"] in probe_ids] + [
                r for r in rows if r["review_id"] not in probe_ids
            ]

    client = OpenAI(api_key=api_key, base_url=base_url)
    stats = {
        "started_at": now_iso(),
        "model": model,
        "reasoning_effort": args.reasoning_effort,
        "prompt_version": PROMPT_VERSION,
        "total": len(rows),
        "ok": 0,
        "fail": 0,
    }
    print(
        f"[start] v={PROMPT_VERSION} model={model} effort={args.reasoning_effort} n={len(rows)}"
    )

    for i, row in enumerate(rows, 1):
        # probes: delete previous same id lines by rewriting later; append for now
        ok, err = annotate_one(
            client,
            model,
            args.reasoning_effort,
            row,
            out_csv,
            args.truncate_at,
            args.retries,
        )
        if ok:
            stats["ok"] += 1
        else:
            stats["fail"] += 1
            print(f"[fail] {row['review_id']}: {err}")
        if i % 10 == 0 or i == len(rows):
            save_checkpoint(
                {**stats, "last_review_id": row["review_id"], "progress": i},
                checkpoint_path,
            )
            print(f"[progress] {i}/{len(rows)} ok={stats['ok']} fail={stats['fail']}")
        if args.sleep > 0:
            time.sleep(args.sleep)

    stats["ended_at"] = now_iso()
    save_checkpoint(stats, checkpoint_path)
    report = REPORT_DIR / f"annotate_run_v1_4_{datetime.now(TZ_CN).strftime('%Y%m%d_%H%M%S')}.md"
    report.write_text(
        "\n".join(
            [
                "# 标注运行报告（v1.4）",
                "",
                f"- 时间：{stats['started_at']} → {stats['ended_at']}",
                f"- 模型：{model}",
                f"- reasoning_effort：{args.reasoning_effort}",
                f"- thinking：enabled",
                f"- response_format：json_object",
                f"- prompt_version：{PROMPT_VERSION}",
                f"- 成功：{stats['ok']}",
                f"- 失败：{stats['fail']}",
                f"- 输出：`{out_csv.as_posix()}`",
                "- 口径：两段式裂隙 + 五条回归样微调（亲昵/单点槽≠负；辱骂五星仍负）",
            ]
        ),
        encoding="utf-8",
    )
    print(f"[done] ok={stats['ok']} fail={stats['fail']}")
    print(f"[out] {out_csv}")
    print(f"[report] {report}")
    return 0 if stats["fail"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Annotate reviews with DeepSeek v1.4 calibrated rhetoric schema")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--include-probes", action="store_true", help="加入用户讽刺/排比探针例句")
    p.add_argument("--probes-only", action="store_true", help="只跑探针例句")
    p.add_argument("--ids", default="", help="逗号分隔 review_id，只标这些")
    p.add_argument("--ids-file", default="", help="每行一个 review_id 的文本文件")
    p.add_argument("--input", default="", help="输入 reviews_clean.csv（默认 data/processed/reviews/，多游戏按 --data-dir 指定）")
    p.add_argument("--data-dir", default="", help="数据目录（默认 data/raw/taptap）")
    p.add_argument("--output", default="", help="默认 data/annotations/annotations_v1_4.csv")
    p.add_argument("--checkpoint", default="", help="断点文件路径（分片并行时用）")
    p.add_argument("--model", default="")
    p.add_argument("--reasoning-effort", default="high", choices=["high", "max"])
    p.add_argument("--truncate-at", type=int, default=1500)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--sleep", type=float, default=0.15)
    p.add_argument("--score-filter", default="")
    p.add_argument("--bucket", default="")
    return p


if __name__ == "__main__":
    raise SystemExit(main(build_parser().parse_args()))
