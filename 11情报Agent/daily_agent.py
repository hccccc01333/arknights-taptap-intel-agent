#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/daily_agent.py — 每日情报 Agent：感知 → 决策 → 行动 → 简报。

定位：命中「AI 自动化情报系统」——把「采集→标注→分析→报告」的手动 pipeline
升级为**每天跑一次的情报 Agent**：代码算数（facts 锁数）→ 规则/LLM 决策 →
按决策路由行动 → 产出当日情报简报。

架构（四步，每步职责单一）：
  Step 1 感知   调 risk_insight.py 与 anomaly_diagnosis.py（各自是独立 tool），
               汇成 facts JSON——**所有数字由代码算好**，LLM 只见 facts 不见原始评论。
  Step 2 决策   规则引擎兜底（确定性优先）；若设置 DEEPSEEK_API_KEY，LLM 读 facts
               输出 JSON 决策 {mode, focus_topics, rationale}；解析失败自动降级规则。
  Step 3 行动   deep_dive → 主题贡献瀑布 + 高风险分层样例；routine → 常规数字段。
  Step 4 简报   Markdown 情报简报。数字全部来自 facts（代码锁数），LLM 只写定性，
               解析失败/无 key 时走模板——**数字幻觉被架构性排除**。

用法：
  python 11情报Agent/daily_agent.py                # 无 key：规则决策 + 模板简报
  DEEPSEEK_API_KEY=sk-... python 11情报Agent/daily_agent.py   # LLM 决策 + LLM 定性
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
LAB = Path(__file__).resolve().parent
RISK_SCRIPT = LAB / "risk_insight.py"
ANOMALY_SCRIPT = LAB / "anomaly_lite.py"  # 零依赖感知层；完整版见 10分析实验室（需 pandas）
ANOMALY_JSON = LAB / "outputs" / "anomaly_lite.json"
RISK_JSON = LAB / "outputs" / "risk_insight.json"
REPORT_DIR = LAB / "reports"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DECISION_SCHEMA = {"mode": "deep_dive|routine", "focus_topics": ["..."], "rationale": "<=60字"}


# ---------------------------------------------------------------- Step 1 感知

def run_tool(script: Path) -> None:
    """运行分析 tool（同解释器；失败抛异常，不静默吞错）。"""
    if not script.exists():
        raise FileNotFoundError(f"tool 不存在: {script}")
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(f"{script.name} 运行失败:\n{r.stderr[-800:]}")


def friendly_reason(e: Exception) -> str:
    """异常 → 面向报告的因果短句（不暴露 traceback / 具体包名堆栈）。"""
    msg = str(e)
    if isinstance(e, ModuleNotFoundError) or "ModuleNotFoundError" in msg or "ImportError" in msg:
        return "分析依赖未安装，已自动降级为常规监测模式"
    if isinstance(e, subprocess.TimeoutExpired) or "Timeout" in type(e).__name__:
        return "分析运行超时，已自动降级为常规监测模式"
    if isinstance(e, FileNotFoundError):
        return "分析脚本缺失，已自动降级为常规监测模式"
    return "分析运行异常，已自动降级为常规监测模式"


def log_traceback(e: Exception, tool: str) -> None:
    """完整 traceback 只进本地日志（*.log 已被 .gitignore 排除），不进公开报告。"""
    import traceback

    LOG_DIR = LAB / "outputs"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "agent_run.log").open("a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now(TZ).isoformat(timespec='seconds')}] {tool} failed\n")
        f.write(traceback.format_exc())


def perceive(skip_anomaly: bool = False) -> dict[str, Any]:
    """跑两个 tool 并汇总 facts。tool 失败 → 该维度显式降级；完整异常只留本地日志。"""
    # 风险分层 tool（纯标准库；失败则整个情报不可产出）
    try:
        run_tool(RISK_SCRIPT)
    except Exception as e:
        log_traceback(e, "risk_insight")
        raise RuntimeError(f"risk_insight 运行失败（无降级路径，缺少核心 facts）: {friendly_reason(e)}") from e
    risk = json.loads(RISK_JSON.read_text(encoding="utf-8"))

    anomaly: dict[str, Any] | None = None
    if not skip_anomaly:
        try:
            run_tool(ANOMALY_SCRIPT)
        except Exception as e:  # 依赖缺失/超时等：显式降级，不崩溃
            log_traceback(e, "anomaly_diagnosis")
            anomaly = {"available": False, "reason": friendly_reason(e)}
        else:
            anomaly = (
                json.loads(ANOMALY_JSON.read_text(encoding="utf-8"))
                if ANOMALY_JSON.exists()
                else {"available": False, "reason": "异动产出文件缺失，已自动降级为常规监测模式"}
            )
    else:
        anomaly = {"available": False, "reason": "调用方指定 --skip-anomaly"}

    cmp0 = None
    top_contributors: list[dict[str, Any]] = []
    if anomaly and anomaly.get("available", True):
        primary_id = anomaly.get("primary_comparison_id") or "week_vs_prev"
        for c in anomaly.get("comparisons", []):
            if c.get("id") == primary_id:
                cmp0 = c
                break
        top_contributors = anomaly.get("top_contributors") or []
    facts = {
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "risk": {
            "headline": risk.get("headline"),
            "n_total": risk.get("facts", {}).get("n_total"),
            "n_neg": risk.get("facts", {}).get("n_neg"),
            "neg_rate_pp": risk.get("facts", {}).get("neg_rate_pp"),
            "n_high_investment_negative": risk.get("facts", {}).get(
                "n_high_investment_negative"
            ),
            "high_investment_share_of_neg_pp": risk.get("facts", {}).get(
                "high_investment_share_of_neg_pp"
            ),
            "n_rhetoric_disguised": risk.get("facts", {}).get("n_rhetoric_disguised"),
            "n_actionable_negative": risk.get("facts", {}).get("n_actionable_negative"),
            "top_topics": [
                {
                    "topic": r["topic_cn"],
                    "neg_rate_pp": r["neg_rate_pp"],
                    "high_hours_share_pp": r["neg_high_hours_share_pp"],
                    "actionable_share_pp": r["actionable_share_pp"],
                }
                for r in risk.get("topic_risk_table", [])[:3]
            ],
            "support_dimension": (
                "不可用（本切片 support_count 全 0）"
                if not risk.get("availability", {}).get("support_available")
                else "可用"
            ),
        },
        "anomaly": (
            {
                "available": anomaly.get("available", True),
                "reason": anomaly.get("reason"),
                "headline": anomaly.get("headline"),
                "delta_pp": cmp0.get("delta_pp") if cmp0 else None,
                "p_value": cmp0.get("p_value") if cmp0 else None,
                "significant": cmp0.get("significant") if cmp0 else None,
                "verdict": cmp0.get("verdict") if cmp0 else None,
                "top_contributors": top_contributors,
            }
            if anomaly
            else None
        ),
    }
    return facts


# ---------------------------------------------------------------- Step 2 决策

def rule_decision(facts: dict[str, Any]) -> dict[str, Any]:
    """规则引擎兜底：确定性优先。显著负向异动 → deep_dive；否则 routine。"""
    an = facts.get("anomaly") or {}
    if an and an.get("available") is False:
        return {
            "mode": "routine",
            "focus_topics": [],
            "rationale": f"异动维度不可用（{an.get('reason', '未知原因')}），维持常规监测。",
            "decider": "rule",
        }
    sig = an.get("significant") is True
    dpp = an.get("delta_pp") or 0.0
    if sig and dpp > 0:
        focus = [
            t["topic"]
            for t in (an.get("top_contributors") or [])[:2]
            if (t.get("total_pp") or 0) > 0
        ]
        return {
            "mode": "deep_dive",
            "focus_topics": focus or ["综合/其他"],
            "rationale": f"本周负向率显著上升 {dpp:+.2f}pp（p={an.get('p_value')}），触发深挖。",
            "decider": "rule",
        }
    return {
        "mode": "routine",
        "focus_topics": [],
        "rationale": (
            f"负向率变化 {dpp:+.2f}pp 未达显著（p={an.get('p_value')}），维持常规监测。"
            if an
            else "异动模块不可用，维持常规监测。"
        ),
        "decider": "rule",
    }


def llm_decision(facts: dict[str, Any], api_key: str) -> dict[str, Any] | None:
    """LLM 决策：读 facts JSON → JSON 决策。失败返回 None（调用方降级规则）。"""
    sys_prompt = (
        "你是舆情情报 Agent 的决策层。只输出一个 JSON 对象，schema："
        '{"mode": "deep_dive"|"routine", "focus_topics": ["主题中文名", ...], '
        '"rationale": "不超过60字的决策理由"}。'
        "判断依据：异动是否统计显著、高风险主题是否聚集。数字一律引用 facts，不得编造。"
    )
    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 200,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        out = json.loads(content)
        mode = out.get("mode")
        if mode not in ("deep_dive", "routine"):
            return None
        out["decider"] = "llm"
        return out
    except Exception:
        return None


# ---------------------------------------------------------------- Step 3 行动

def act(facts: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """按决策路由行动：deep_dive 组装深挖素材；routine 只带常规数字。"""
    risk = json.loads(RISK_JSON.read_text(encoding="utf-8"))
    payload: dict[str, Any] = {"facts": facts, "decision": decision}
    if decision["mode"] == "deep_dive":
        an = facts.get("anomaly") or {}
        payload["deep_dive"] = {
            "contributor_table": an.get("top_contributors") or [],
            "high_risk_samples": (
                risk.get("layers", {}).get("high_investment_negative", {}).get("top_samples")
                or []
            ),
            "disguised_samples": (
                risk.get("layers", {}).get("rhetoric_disguised", {}).get("top_samples")
                or []
            ),
        }
    return payload


# ---------------------------------------------------------------- Step 4 简报

TEMPLATE_ROUTINE = (
    "今日常规监测：负向率 {neg_rate_pp}%（n={n_neg}/{n_total}），"
    "高投入负向 {n_high_inv} 条（占负向 {high_share_pp}%）。{verdict}"
)
TEMPLATE_DEEP = (
    "今日触发深挖：本周负向率显著上升（{delta_pp:+.2f}pp，p={p_value}）。"
    "主贡献主题：{contributors}。高风险分层：负向中高投入占 {high_share_pp}%，"
    "修辞伪装负向 {n_disguised} 条、可行动差评 {n_actionable} 条。"
)


def qualitative_text(facts: dict[str, Any], decision: dict[str, Any], api_key: str | None) -> str:
    """定性段：LLM 可用则生成，否则模板。数字占位符全部由 facts 填充。"""
    r = facts["risk"]
    an = facts.get("anomaly") or {}
    if api_key and decision.get("decider") == "llm":
        body = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是舆情情报 Agent 的执笔层。基于给定 facts 写 3-5 句中文定性简报。"
                        "铁律：只能引用 facts 中出现的数字，禁止自造任何数字或百分比；"
                        "不给确定性承诺；结尾一句给方向性建议。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"facts": facts, "decision": decision}, ensure_ascii=False
                    ),
                },
            ],
            "temperature": 0.3,
            "max_tokens": 300,
        }
        req = urllib.request.Request(
            DEEPSEEK_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except Exception:
            pass  # 降级模板
    # 模板（数字由 facts 填，不经过 LLM）
    if decision["mode"] == "deep_dive":
        return TEMPLATE_DEEP.format(
            delta_pp=an.get("delta_pp") or 0.0,
            p_value=an.get("p_value") or "—",
            contributors="、".join(
                f"{t['topic']} {t['total_pp']:+.2f}pp"
                for t in (an.get("top_contributors") or [])[:3]
            )
            or "—",
            high_share_pp=r.get("high_investment_share_of_neg_pp") or "—",
            n_disguised=r.get("n_rhetoric_disguised") or 0,
            n_actionable=r.get("n_actionable_negative") or 0,
        )
    return TEMPLATE_ROUTINE.format(
        neg_rate_pp=r.get("neg_rate_pp") or "—",
        n_neg=r.get("n_neg") or 0,
        n_total=r.get("n_total") or 0,
        n_high_inv=r.get("n_high_investment_negative") or 0,
        high_share_pp=r.get("high_investment_share_of_neg_pp") or "—",
        verdict=(an.get("verdict") or "") if an else "",
    )


def render_brief(action: dict[str, Any], qual: str) -> str:
    d = action["decision"]
    f = action["facts"]
    r = f["risk"]
    lines = [
        f"# 每日情报简报 · {datetime.now(TZ).strftime('%Y-%m-%d')}",
        "",
        f"> 决策：**{d['mode']}**（by {d['decider']}）· {d['rationale']}",
        "",
        "## 定性简报",
        "",
        qual,
        "",
        "## 锁数事实（代码计算，LLM 不可篡改）",
        "",
        "| 指标 | 值 |",
        "|------|----|",
        f"| 样本 n | {r.get('n_total')} |",
        f"| 负向 | {r.get('n_neg')}（{r.get('neg_rate_pp')}%） |",
        f"| 高投入负向 | {r.get('n_high_investment_negative')}"
        f"（占负向 {r.get('high_investment_share_of_neg_pp')}%） |",
        f"| 修辞伪装负向 | {r.get('n_rhetoric_disguised')} |",
        f"| 可行动差评 | {r.get('n_actionable_negative')} |",
        f"| 传播维度 | {r.get('support_dimension')} |",
        "",
        "## 高风险主题 Top3（按负向率）",
        "",
        "| 主题 | 负向率 | 负向中高投入 | 可行动占比 |",
        "|------|--------|--------------|------------|",
    ]
    for t in r.get("top_topics", []):
        lines.append(
            f"| {t['topic']} | {t['neg_rate_pp']}% | {t['high_hours_share_pp'] or '—'}% | "
            f"{t['actionable_share_pp'] or '—'}% |"
        )
    if "deep_dive" in action:
        dd = action["deep_dive"]
        lines += ["", "## 深挖：主题负向变化（Top，按负向数变化排序）", ""]
        for t in dd.get("contributor_table", []):
            dpp = t.get("total_pp")
            rate_bit = f"，负向率 {dpp:+.2f}pp" if dpp is not None else ""
            lines.append(
                f"- {t['topic']}：负向 {t.get('neg_delta', 0):+d} 条{rate_bit}"
            )
        lines += ["", "### 高投入负向代表样本（脱敏）", ""]
        for s in dd.get("high_risk_samples", []):
            hours = f"{s['played_hours']:.0f}h" if s.get("played_hours") is not None else "—"
            lines.append(f"- `{s['review_id']}` {s['topic']} · {hours} · {s['rhetoric']}：{s['text_excerpt']}")
        lines += ["", "### 修辞伪装样本（字面/意图极性分离）", ""]
        for s in dd.get("disguised_samples", []):
            hours = f"{s['played_hours']:.0f}h" if s.get("played_hours") is not None else "—"
            lines.append(f"- `{s['review_id']}` {s['topic']} · {hours} · {s['rhetoric']}：{s['text_excerpt']}")
    lines += [
        "",
        "## 边界",
        "",
        "- 舆情侧风险信号，非流失预测；数字全部来自 facts JSON（代码计算）。",
        "- 传播维度数据不可用时显式标注，不估算。",
        "",
        "---",
        "",
        "*简报结束*",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="每日情报 Agent（感知→决策→行动→简报）")
    ap.add_argument("--skip-anomaly", action="store_true", help="跳过异动 tool（调试用）")
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY") or None

    # Step 1 感知（tool 失败在 perceive 内显式降级）
    facts = perceive(skip_anomaly=args.skip_anomaly)

    # Step 2 决策（LLM 可选，规则兜底）
    decision = (llm_decision(facts, api_key) if api_key else None) or rule_decision(facts)

    # Step 3 行动
    action = act(facts, decision)

    # Step 4 简报
    qual = qualitative_text(facts, decision, api_key)
    brief = render_brief(action, qual)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TZ).strftime("%Y%m%d")
    out_today = REPORT_DIR / f"daily_intel_{today}.md"
    out_latest = REPORT_DIR / "daily_intel_latest.md"
    out_today.write_text(brief, encoding="utf-8")
    out_latest.write_text(brief, encoding="utf-8")

    print(out_today)
    print(out_latest)
    print(f"decision={decision['mode']} (by {decision['decider']})")


if __name__ == "__main__":
    main()
