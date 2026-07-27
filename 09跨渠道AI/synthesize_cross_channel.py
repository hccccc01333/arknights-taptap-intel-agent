#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""基于 facts_cross_channel.json 调用 DeepSeek 生成跨渠道简报与议题对齐表。

输入仅允许 facts；引用 n/% 须能在 facts 找回，否则重试 / 模板降级。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

MOD_DIR = Path(__file__).resolve().parent
ROOT = MOD_DIR.parent
FACTS_PATH = MOD_DIR / "facts_cross_channel.json"
REPORT_DIR = MOD_DIR / "reports"
RAW_DIR = MOD_DIR / "raw_llm"

TZ_CN = timezone(timedelta(hours=8))
MODEL_DEFAULT = "deepseek-v4-pro"
BASE_URL_DEFAULT = "https://api.deepseek.com"
CHANNEL_ORDER = ("taptap", "bilibili", "douyin", "weibo")

SYSTEM_PROMPT = """你是游戏舆情分析助手。你只能使用用户提供的 facts JSON 中的数字与原话。
禁止编造样本量、百分比或原话。跨渠道百分比只作结构对照，禁止写成全网 KPI。
若某渠 data_quality.is_degraded=true，必须在叙述中标明「采集降级/演示样本」。
输出必须是 JSON 对象，字段见用户指令。定性文字可以写，但凡出现整数或 x.x% 必须能在 facts.allowed_numbers 中找到。"""


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def load_facts(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def call_llm(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    reasoning_effort: str,
    max_tokens: int = 4096,
) -> tuple[dict[str, Any], str]:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
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
    return extract_json(content), json.dumps(raw, ensure_ascii=False)


def build_user_prompt(facts: dict) -> str:
    # strip sample quotes length to keep prompt lean but keep ids
    slim = json.loads(json.dumps(facts, ensure_ascii=False))
    for ch in slim.get("channels", {}).values():
        for q in ch.get("sample_quotes", []):
            if len(q.get("text", "")) > 60:
                q["text"] = q["text"][:59] + "…"
    return f"""请根据下列 facts 写跨渠道综合分析，只输出 JSON：

{{
  "summary_foursome": [
    "【数据洞察】……（须含各渠 n 与负向% 的正确数字）",
    "【渠道人设】……（评价站/视频评/短视频/微博各吵什么）",
    "【议题对齐】……（点出跨渠同现议题与强度差异，数字正确）",
    "【值班建议】……（概括 2–3 条动作方向）"
  ],
  "channel_personas": {{
    "taptap": "一句话人设",
    "bilibili": "一句话人设",
    "douyin": "一句话人设",
    "weibo": "一句话人设"
  }},
  "duty_advice": [
    {{"role": "角色", "timing": "时机", "action": "动作", "channels": ["taptap"], "basis": "依据哪渠数字/议题"}}
  ],
  "topic_alignment": [
    {{
      "topic": "必须是 facts 中存在的 topic key",
      "topic_label": "中文名",
      "channels_present": ["taptap", "bilibili"],
      "intensity": "哪渠更吵、差多少个百分点（数字须正确）",
      "one_liner": "一句话解释"
    }}
  ],
  "caveats": ["边界与降级说明，1–3 条"]
}}

要求：
1. summary_foursome 恰好 4 句，标签固定。
2. duty_advice 2–3 条；topic_alignment 选 4–6 个跨渠有意义的议题（优先 n_channels>=2）。
3. 不得把对照渠比例写进「全网口碑 KPI」。
4. facts：
{json.dumps(slim, ensure_ascii=False)}
"""


def collect_allowed(facts: dict) -> tuple[set[int], set[float]]:
    an = facts.get("allowed_numbers") or {}
    ints = {int(x) for x in an.get("ints", [])}
    pcts = {round(float(x), 1) for x in an.get("pcts", [])}
    return ints, pcts


def find_number_violations(text: str, allowed_ints: set[int], allowed_pcts: set[float]) -> list[str]:
    """扫描正文中的 n=/百分比；放宽纯叙述小整数已在 allowed 中。"""
    violations: list[str] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", text):
        val = round(float(m.group(1)), 1)
        if val not in allowed_pcts:
            # tolerate xx.0 vs xx
            if round(val) == val and int(val) in {int(p) for p in allowed_pcts if p == int(p)}:
                continue
            violations.append(f"pct {m.group(0)}")
    for m in re.finditer(r"(?:n\s*[=＝]\s*|样本量\s*|标注\s*|评论\s*)(\d{2,})", text, flags=re.I):
        val = int(m.group(1))
        if val not in allowed_ints:
            violations.append(f"n-like {val}")
    # bare large integers that look like sample sizes (100+)
    for m in re.finditer(r"(?<![\d.])(\d{3,})(?![\d.%])", text):
        val = int(m.group(1))
        if val not in allowed_ints:
            violations.append(f"int {val}")
    return violations


def validate_payload(parsed: dict, facts: dict) -> list[str]:
    errs: list[str] = []
    allowed_ints, allowed_pcts = collect_allowed(facts)
    fours = parsed.get("summary_foursome") or []
    if len(fours) != 4:
        errs.append(f"summary_foursome len={len(fours)}")
    tags = ["【数据洞察】", "【渠道人设】", "【议题对齐】", "【值班建议】"]
    for i, tag in enumerate(tags):
        if i < len(fours) and not str(fours[i]).startswith(tag):
            errs.append(f"foursome[{i}] missing tag {tag}")

    personas = parsed.get("channel_personas") or {}
    for ck in CHANNEL_ORDER:
        if ck not in personas or not str(personas[ck]).strip():
            errs.append(f"missing persona {ck}")

    advice = parsed.get("duty_advice") or []
    if not (2 <= len(advice) <= 3):
        errs.append(f"duty_advice len={len(advice)}")

    known_topics = set()
    for ch in facts["channels"].values():
        for t in ch["topics"]:
            known_topics.add(t["topic"])
    aligns = parsed.get("topic_alignment") or []
    if not (3 <= len(aligns) <= 8):
        errs.append(f"topic_alignment len={len(aligns)}")
    for row in aligns:
        topic = row.get("topic")
        if topic not in known_topics:
            errs.append(f"unknown topic {topic}")

    blob = json.dumps(parsed, ensure_ascii=False)
    violations = find_number_violations(blob, allowed_ints, allowed_pcts)
    if violations:
        errs.append("number_mismatch:" + ",".join(violations[:12]))
    return errs


def template_fallback(facts: dict) -> dict:
    """无 Key / 校验失败时的确定性降级：数字全部来自 facts。"""
    ch = facts["channels"]
    lines = []
    parts = []
    for ck in CHANNEL_ORDER:
        c = ch[ck]
        deg = "（降级样本）" if c["data_quality"].get("is_degraded") else ""
        parts.append(
            f"{c['label']}{deg} n={c['n_annotated']} 负向 {c['sentiment']['pct']['负']}%"
        )
    lines.append("【数据洞察】" + "；".join(parts) + "。对照渠不作全网 KPI。")

    persona_bits = []
    for ck in CHANNEL_ORDER:
        c = ch[ck]
        top = c["topics"][0]["label"] if c["topics"] else "—"
        persona_bits.append(f"{c['label']}偏「{top}」")
    lines.append("【渠道人设】" + "；".join(persona_bits) + "。")

    seed = facts["cross"]["topic_alignment_seed"]
    multi = [r for r in seed if r["n_channels"] >= 2][:3]
    if multi:
        bits = []
        for r in multi:
            ranks = sorted(r["presence"], key=lambda x: -x["pct"])
            head = " / ".join(f"{p['label']}{p['pct']}%" for p in ranks[:3])
            bits.append(f"{r['topic_label']}（{head}）")
        lines.append("【议题对齐】跨渠同现：" + "；".join(bits) + "。")
    else:
        lines.append("【议题对齐】本样本窗跨渠同现议题有限，宜分渠解读勿硬同比。")

    deg_chs = facts["cross"].get("degraded_channels") or []
    deg_note = ""
    if deg_chs:
        labels = "、".join(ch[k]["label"] for k in deg_chs)
        deg_note = f"注意 {labels} 含采集降级样本，值班动作以 TapTap / B站 为主。"
    lines.append(
        "【值班建议】运营跟进跨渠同现负向主题；产品/数值优先看 TapTap 可行动；"
        + (deg_note or "对照渠只作场景互证。")
    )

    personas = {}
    role_hint = {
        "taptap": "商店评价：是否推荐/继续玩",
        "bilibili": "视频评论：即时反应与二创语境",
        "douyin": "短视频：短梗与情绪化传播",
        "weibo": "公开热议：话题切片与围观",
    }
    for ck in CHANNEL_ORDER:
        c = ch[ck]
        top = c["topics"][0]["label"] if c["topics"] else "—"
        flag = "（降级演示）" if c["data_quality"].get("is_degraded") else ""
        personas[ck] = f"{role_hint[ck]}；本样本主簇「{top}」{flag}"

    advice = [
        {
            "role": "运营",
            "timing": "活动/版本窗口",
            "action": "对跨渠同现主题做口径统一与客服预案，不以对照渠%作考核",
            "channels": ["taptap", "bilibili"],
            "basis": f"TapTap 负向 {ch['taptap']['sentiment']['pct']['负']}%；B站 n={ch['bilibili']['n_annotated']}",
        },
        {
            "role": "产品/数值",
            "timing": "周会复盘",
            "action": "下钻 TapTap 负向主簇与可行动原话，对照渠只作场景参考",
            "channels": ["taptap"],
            "basis": f"主链 n={ch['taptap']['n_annotated']}",
        },
    ]
    if deg_chs:
        advice.append(
            {
                "role": "数据",
                "timing": "下次采集前",
                "action": "补 Cookie/降速重采抖音或微博直播样本，替换降级切片",
                "channels": list(deg_chs),
                "basis": "facts.data_quality.is_degraded",
            }
        )
    else:
        advice.append(
            {
                "role": "传播",
                "timing": "热点发酵 24h 内",
                "action": "用 B站/短视频评论核对「外面在吵什么」是否与商店同向",
                "channels": ["bilibili", "douyin"],
                "basis": "传播对照纪律",
            }
        )

    aligns = []
    for r in (multi or seed)[:6]:
        ranks = sorted(r["presence"], key=lambda x: -x["pct"])
        if len(ranks) >= 2:
            intensity = (
                f"{ranks[0]['label']} {ranks[0]['pct']}% > "
                f"{ranks[1]['label']} {ranks[1]['pct']}%"
            )
        elif ranks:
            intensity = f"仅见 {ranks[0]['label']} {ranks[0]['pct']}%"
        else:
            intensity = "—"
        aligns.append(
            {
                "topic": r["topic"],
                "topic_label": r["topic_label"],
                "channels_present": [p["channel"] for p in r["presence"]],
                "intensity": intensity,
                "one_liner": "结构对照，禁止直接跨渠同比为 KPI 异动。",
            }
        )

    caveats = [
        "主链 KPI 仅 TapTap；B站/抖音/微博为传播对照切片。",
        "facts 数字由代码聚合；本报告为模板降级或校验失败回退。",
    ]
    if deg_chs:
        caveats.append(
            "降级渠道："
            + "、".join(ch[k]["label"] for k in deg_chs)
            + "，勿写成「全网实时监测」。"
        )

    return {
        "summary_foursome": lines,
        "channel_personas": personas,
        "duty_advice": advice[:3],
        "topic_alignment": aligns,
        "caveats": caveats,
        "_fallback": True,
    }


def render_brief(facts: dict, parsed: dict, *, model: str, mode: str) -> str:
    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    ch = facts["channels"]
    lines = [
        f"# 跨渠道 AI 综合简报（{day}）",
        "",
        "## 0. 范围与边界",
        "",
        f"- 生成：`{facts['meta']['generated_at']}` · 模式：{mode} · 模型：{model or 'template'}",
        f"- 边界：{facts['meta']['boundary']}",
        "- 数字来源：`facts_cross_channel.json`（代码聚合）；LLM 只写定性综合。",
        "",
        "## 1. 执行摘要四句",
        "",
    ]
    for s in parsed["summary_foursome"]:
        lines.append(f"- {s}")

    lines += ["", "## 2. 四渠锁定数字（代码）", "", "| 渠道 | 角色 | n | 正 | 中 | 负 | 降级 |", "|------|------|---|----|----|----|------|"]
    for ck in CHANNEL_ORDER:
        c = ch[ck]
        s = c["sentiment"]["pct"]
        deg = "是" if c["data_quality"].get("is_degraded") else "否"
        lines.append(
            f"| {c['label']} | {c['role']} | {c['n_annotated']} | "
            f"{s['正']}% | {s['中']}% | {s['负']}% | {deg} |"
        )

    lines += ["", "## 3. 渠道人设", ""]
    for ck in CHANNEL_ORDER:
        c = ch[ck]
        lines.append(f"- **{c['label']}**：{parsed['channel_personas'].get(ck, '—')}")

    lines += ["", "## 4. 值班建议（角色 × 时机 × 动作）", ""]
    for i, a in enumerate(parsed.get("duty_advice") or [], 1):
        chans = "、".join(a.get("channels") or [])
        lines.append(
            f"{i}. **{a.get('role', '')}** · {a.get('timing', '')} → {a.get('action', '')}"
            f"（依据渠道：{chans}；{a.get('basis', '')}）"
        )

    lines += ["", "## 5. 边界与降级", ""]
    for c in parsed.get("caveats") or []:
        lines.append(f"- {c}")

    lines += [
        "",
        "## 6. 复现",
        "",
        "```bash",
        "python 09跨渠道AI/build_channel_facts.py",
        "python 09跨渠道AI/synthesize_cross_channel.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def render_alignment(facts: dict, parsed: dict, *, model: str, mode: str) -> str:
    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    ch = facts["channels"]
    label = {ck: ch[ck]["label"] for ck in CHANNEL_ORDER}
    lines = [
        f"# 跨渠道议题对齐表（{day}）",
        "",
        f"- 模式：{mode} · 模型：{model or 'template'}",
        "- 强度数字取自各渠主题份额（facts）；解释句来自 LLM/模板。",
        "",
        "## 对齐总表",
        "",
        "| 议题 | 出现渠道 | 强度差异 | 一句话 |",
        "|------|----------|----------|--------|",
    ]
    for row in parsed.get("topic_alignment") or []:
        present = "、".join(label.get(c, c) for c in row.get("channels_present") or [])
        tlabel = row.get("topic_label") or row.get("topic")
        intensity = str(row.get("intensity") or "").replace("|", "/")
        one = str(row.get("one_liner") or "").replace("|", "/")
        lines.append(f"| {tlabel} | {present} | {intensity} | {one} |")

    lines += ["", "## 各渠主题 Top（代码）", ""]
    for ck in CHANNEL_ORDER:
        c = ch[ck]
        lines.append(f"### {c['label']}（n={c['n_annotated']}）")
        lines.append("")
        for t in c["topics"][:6]:
            lines.append(f"- {t['label']}：{t['n']}（{t['pct']}%）")
        lines.append("")

    lines += ["## 代表原话（带 id）", ""]
    for ck in CHANNEL_ORDER:
        c = ch[ck]
        lines.append(f"### {c['label']}")
        lines.append("")
        qs = c.get("sample_quotes") or []
        if not qs:
            lines.append("- （无）")
        for q in qs:
            lines.append(
                f"- [{q['sentiment']}/{q['topic_label']}] `{q['id']}` 「{q['text']}」"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Synthesize cross-channel AI brief from facts")
    p.add_argument("--facts", default=str(FACTS_PATH))
    p.add_argument("--model", default="")
    p.add_argument("--reasoning-effort", default="high", choices=["high", "max"])
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--template-only", action="store_true", help="跳过 LLM，只用模板")
    args = p.parse_args()

    facts_path = Path(args.facts)
    if not facts_path.exists():
        print(f"缺少 facts：{facts_path}，请先运行 build_channel_facts.py", file=sys.stderr)
        return 2
    facts = load_facts(facts_path)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    parsed: dict | None = None
    mode = "template"
    model = args.model or os.environ.get("DEEPSEEK_MODEL", MODEL_DEFAULT)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()

    if args.template_only or not api_key:
        if not api_key and not args.template_only:
            print("[warn] 无 DEEPSEEK_API_KEY，使用模板降级")
        parsed = template_fallback(facts)
        mode = "template_fallback"
        model = ""
    else:
        base_url = os.environ.get("DEEPSEEK_BASE_URL", BASE_URL_DEFAULT).strip() or BASE_URL_DEFAULT
        client = OpenAI(api_key=api_key, base_url=base_url)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(facts)},
        ]
        last_errs: list[str] = []
        for attempt in range(1, args.retries + 1):
            try:
                parsed_try, raw = call_llm(client, model, messages, args.reasoning_effort)
                stamp = datetime.now(TZ_CN).strftime("%Y%m%d_%H%M%S")
                (RAW_DIR / f"synthesize_{stamp}_try{attempt}.json").write_text(raw, encoding="utf-8")
                errs = validate_payload(parsed_try, facts)
                if not errs:
                    parsed = parsed_try
                    mode = "llm"
                    print(f"[ok] llm attempt={attempt}")
                    break
                last_errs = errs
                print(f"[retry] attempt={attempt} errs={errs}")
                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(parsed_try, ensure_ascii=False),
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "校验失败："
                            + "; ".join(errs)
                            + "。请修正：所有整数与百分比必须来自 facts.allowed_numbers；"
                            "topic 必须是 facts 已有 key；保持 JSON schema。"
                        ),
                    }
                )
            except Exception as e:  # noqa: BLE001
                last_errs = [str(e)]
                print(f"[err] attempt={attempt}: {e}")
        if parsed is None:
            print(f"[fallback] template after failures: {last_errs}")
            parsed = template_fallback(facts)
            mode = "template_after_fail"

    day = datetime.now(TZ_CN).strftime("%Y%m%d")
    brief_path = REPORT_DIR / f"cross_channel_brief_{day}.md"
    align_path = REPORT_DIR / f"topic_alignment_{day}.md"
    brief_path.write_text(render_brief(facts, parsed, model=model, mode=mode), encoding="utf-8")
    align_path.write_text(
        render_alignment(facts, parsed, model=model, mode=mode), encoding="utf-8"
    )

    # machine-readable summary for dashboard
    summary_path = MOD_DIR / "latest_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": now_iso(),
                "mode": mode,
                "model": model,
                "brief_path": str(brief_path.relative_to(ROOT)).replace("\\", "/"),
                "alignment_path": str(align_path.relative_to(ROOT)).replace("\\", "/"),
                "summary_foursome": parsed["summary_foursome"],
                "channel_personas": parsed.get("channel_personas"),
                "fallback": bool(parsed.get("_fallback")),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"[brief] {brief_path}")
    print(f"[align] {align_path}")
    print(f"[summary] {summary_path}")
    print(f"[mode] {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
