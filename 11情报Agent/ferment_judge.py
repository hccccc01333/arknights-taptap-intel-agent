#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/ferment_judge.py — 话题「社区可发酵度」判断（社区平台视角的核心判据）。

## 为什么要有这个模块

旧判据 `matched_games` 问的是「这个话题属于不属于我建档的那两款游戏」——
实测 0/6 命中。但客户是**社区平台**：一个话题哪怕不属于我们建档的游戏，
玩家照样会在社区里讨论它，照样值得平台建话题、做活动、引导 UGC。

> **「未命中建档游戏」≠「与社区无关」。** 旧判据测的是游戏归属，
> 平台真正需要的是**社区可发酵度**——两回事。

新判据：

| | 旧（测错了） | 新（本模块） |
|---|---|---|
| 问法 | 属于我建档的游戏吗？ | **能在 TapTap 社区发酵吗、值得建话题吗？** |
| 实现 | 别名库字面匹配 | LLM 语义判断（非字面映射正是它强项） |

## 双路与降级纪律

- LLM 可用 → 语义判断（thinking 开启，因需要多步推理与联想）
- 无 API key → **规则兜底**，但必须显式标注 `degraded=True`：
  规则只能给「热度代理分」，**不等于可发酵度**。严禁把代理分当结论用。

输出：`outputs/ferment_judge.json` + `reports/ferment_judge_latest.md`
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
OUT_DIR = LAB / "outputs"
REPORT_DIR = LAB / "reports"
PLATFORM_JSON = OUT_DIR / "platform_insight.json"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
# 可发酵度判断需要语义联想与多步推理 → 开 thinking；区别于纯分类任务（关 thinking）
MODEL_FLASH = os.environ.get("DEEPSEEK_MODEL_FLASH", "deepseek-flash")
THINKING_ON = {"type": "enabled"}
REASONING_EFFORT = os.environ.get("DEEPSEEK_REASONING_CREATIVE", "high")

# 规则兜底用的话题类型信号（只是热度/类型代理，不是可发酵度本身）
FERMENT_KIND_HINTS = {
    "技术测试": 20, "测试招募": 20, "定档": 15, "公测": 18, "首曝": 15,
    "联动": 15, "周年": 12, "换帅": 12, "新版本": 10, "庆典": 10,
    "争议": 15, "停运": 15, "回档": 15, "道歉": 15,
}
VERDICT_THRESHOLD = {"act": 60, "watch": 35}  # ≥60 值得建话题；35-60 观察；<35 忽略


def _int(v: Any) -> int:
    try:
        return int(str(v or 0).strip())
    except (TypeError, ValueError):
        return 0


def load_platform() -> dict[str, Any] | None:
    if not PLATFORM_JSON.exists():
        return None
    try:
        return json.loads(PLATFORM_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def collect_topics(pj: dict[str, Any]) -> list[dict[str, Any]]:
    """从 platform_insight facts 里抽出待判话题（热榜 + 已下钻话题）。

    注意：抽的是 facts，不是原始 CSV——锁数纪律，LLM 只见已算好的数。
    """
    topics: list[dict[str, Any]] = []
    seen: set[str] = set()

    for h in pj.get("hot_board") or []:
        hid = str(h.get("hashtag_id") or h.get("title") or "")
        if hid in seen:
            continue
        seen.add(hid)
        topics.append(
            {
                "title": h.get("title"),
                "hashtag_id": h.get("hashtag_id"),
                "source": "S4_hot_board",
                "page_view": _int(h.get("page_view")),
                "interaction": _int(h.get("comment_count")),
                "matched_games": h.get("matched_games") or [],
            }
        )

    for d in pj.get("topic_dig") or []:
        hid = str(d.get("hashtag_id") or d.get("title") or "")
        if hid in seen:
            continue
        seen.add(hid)
        topics.append(
            {
                "title": d.get("title"),
                "hashtag_id": d.get("hashtag_id"),
                "source": "S6_topic_dig",
                "page_view": _int(d.get("pv_total")),
                "interaction": _int(d.get("interaction_total")),
                "matched_games": d.get("matched_games") or [],
            }
        )
    return topics


def rule_score(t: dict[str, Any]) -> tuple[int, str]:
    """规则兜底分。⚠ 这只是「热度 + 话题类型」的代理分，**不是可发酵度**。

    保留它是为了让链路在无 key 时也能跑通并产出结构，但必须显式标注降级。
    """
    pv = _int(t.get("page_view"))
    it = _int(t.get("interaction"))
    score = 0
    reasons = []
    if pv >= 10000:
        score += 40
        reasons.append(f"浏览 {pv} 属平台级热度")
    elif pv >= 3000:
        score += 20
        reasons.append(f"浏览 {pv} 中等")
    if it >= 500:
        score += 25
        reasons.append(f"互动 {it} 较高")
    elif it >= 50:
        score += 10
        reasons.append(f"互动 {it} 偏低")
    title = str(t.get("title") or "")
    for kw, w in FERMENT_KIND_HINTS.items():
        if kw in title:
            score += w
            reasons.append(f"话题类型含「{kw}」，通常可引发讨论")
            break
    return min(score, 100), "；".join(reasons) or "无可观测信号"


def llm_judge(topics: list[dict[str, Any]], api_key: str) -> dict[str, int | str] | None:
    """LLM 语义判断可发酵度。一次批量判断全部话题（省调用）。

    铁律：LLM 只能基于给定 facts 判断，不得引入外部数字。
    """
    payload = [
        {"idx": i, "title": t["title"], "page_view": t["page_view"], "interaction": t["interaction"]}
        for i, t in enumerate(topics)
    ]
    sys_prompt = (
        "你是游戏社区（TapTap）的运营策划。判断下列全网热点话题"
        "「能否在本社区发酵、值不值得社区建话题引导讨论」。\n"
        "判据：1) 玩家是否有讨论动机（争议/期待/情怀/新鲜感）；"
        "2) 是否适合转化为社区话题或活动；3) 热度只作参考，不作决定项。\n"
        "只输出 JSON：{\"results\":[{\"idx\":0,\"score\":0-100,"
        "\"verdict\":\"act|watch|skip\",\"reason\":\"不超过30字\","
        "\"suggested_action\":\"建话题/活动的具体建议，不超过30字\"}]}。"
        "禁止编造任何数字。"
    )
    body = {
        "model": MODEL_FLASH,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "thinking": THINKING_ON,
        "reasoning_effort": REASONING_EFFORT,
        "max_tokens": 2000,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    try:
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None


def verdict_of(score: int) -> str:
    if score >= VERDICT_THRESHOLD["act"]:
        return "act"
    if score >= VERDICT_THRESHOLD["watch"]:
        return "watch"
    return "skip"


def judge(topics: list[dict[str, Any]], api_key: str | None) -> dict[str, Any]:
    llm_out = llm_judge(topics, api_key) if api_key else None
    mode = "llm" if llm_out else "rule_fallback"
    degraded = llm_out is None
    by_idx: dict[int, dict[str, Any]] = {}
    if llm_out:
        for r in llm_out.get("results") or []:
            try:
                by_idx[int(r.get("idx"))] = r
            except (TypeError, ValueError):
                continue

    out_topics = []
    for i, t in enumerate(topics):
        r = by_idx.get(i)
        if r:
            score = max(0, min(100, _int(r.get("score"))))
            reason = str(r.get("reason") or "")
            action = str(r.get("suggested_action") or "")
            v = str(r.get("verdict") or verdict_of(score))
        else:
            score, reason = rule_score(t)
            v = verdict_of(score)
            action = "（降级模式，无具体建议）"
        out_topics.append(
            {
                **t,
                "ferment_score": score,
                "verdict": v,
                "reason": reason,
                "suggested_action": action,
            }
        )

    out_topics.sort(key=lambda x: -x["ferment_score"])
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "scope": "taptap",
        "judge_criterion": "社区可发酵度：能否在本社区发酵 / 值不值得建话题",
        "mode": mode,
        "degraded": degraded,
        "degrade_reason": (
            "未配置 DEEPSEEK_API_KEY（或调用失败）：当前分数为「热度+话题类型」代理分，"
            "不等于可发酵度，不可作为结论使用"
            if degraded
            else None
        ),
        "note": (
            "旧判据 matched_games（游戏归属）已不作为判据，仅保留作对照——"
            "未命中建档游戏 ≠ 与社区无关"
        ),
        "verdict_threshold": VERDICT_THRESHOLD,
        "topics": out_topics,
        "summary": {
            "total": len(out_topics),
            "act": sum(1 for t in out_topics if t["verdict"] == "act"),
            "watch": sum(1 for t in out_topics if t["verdict"] == "watch"),
            "skip": sum(1 for t in out_topics if t["verdict"] == "skip"),
            "matched_games_nonempty": sum(1 for t in out_topics if t["matched_games"]),
        },
    }


def render_report(res: dict[str, Any]) -> str:
    L = [
        "# 话题可发酵度判断 · TapTap",
        "",
        f"> 生成：{res['generated_at']} · 判据：{res['judge_criterion']}",
        "",
    ]
    if res["degraded"]:
        L += [
            f"⚠ **降级模式**：{res['degrade_reason']}",
            "",
        ]
    s = res["summary"]
    L += [
        f"- 话题 {s['total']} 个：值得建话题 **{s['act']}** / 观察 {s['watch']} / 忽略 {s['skip']}",
        f"- 旧判据（游戏归属）命中的仅 **{s['matched_games_nonempty']}** 个，"
        f"而新判据认为 **{s['act'] + s['watch']}** 个可发酵"
        "——直接印证：**未命中建档游戏 ≠ 与社区无关**",
        "",
        "| 话题 | 可发酵度 | 结论 | 理由 | 建议动作 | 旧判据命中 |",
        "|------|----------|------|------|----------|------------|",
    ]
    for t in res["topics"]:
        v = {"act": "建话题", "watch": "观察", "skip": "忽略"}.get(t["verdict"], t["verdict"])
        mg = "/".join(t["matched_games"]) or "—"
        L.append(
            f"| {t['title']} | {t['ferment_score']} | {v} | {t['reason']} | "
            f"{t['suggested_action']} | {mg} |"
        )
    L += ["", "---", "", "*报告结束*", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="话题社区可发酵度判断")
    ap.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY"))
    ap.add_argument("--skip-report", action="store_true")
    args = ap.parse_args()

    pj = load_platform()
    if pj is None:
        print("[ferment_judge] 缺少 outputs/platform_insight.json，"
              "请先跑 crawl_taptap_discovery.py + platform_insight.py", file=sys.stderr)
        return 2
    topics = collect_topics(pj)
    if not topics:
        print("[ferment_judge] 无可判话题", file=sys.stderr)
        return 2

    res = judge(topics, args.api_key)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "ferment_judge.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not args.skip_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "ferment_judge_latest.md").write_text(
            render_report(res), encoding="utf-8"
        )
    print(
        f"[ferment_judge] {res['mode']} · 话题 {res['summary']['total']} · "
        f"建话题 {res['summary']['act']} / 观察 {res['summary']['watch']} / "
        f"忽略 {res['summary']['skip']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
