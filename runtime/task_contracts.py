#!/usr/bin/env python3
"""任务契约（机器可读）—— harness 的任务注册表。

设计依据
  · `docs/任务契约.md`（§2 schema 16 项 + 四条写契约纪律）
  · `docs/Agent-v2-架构设计.md` §6 第 10–15 条（控制面议题）

★ 本文件是**纯数据**，不含任何执行逻辑（执行逻辑在 `harness.py`）。
  这样契约可以被单独评审、单独测试、被前端/文档直接读取。

★ 2026-10-03 重接线：注册表从旧「探测链（热榜→采样→触发→特征）」整体换到
  六层 Growth Intelligence OS 的主链 —— trend_intelligence → intelligence_run
  → memory_ingest → ops_alerts，依赖顺序即数据流。旧任务（hotspot_track /
  material_extract）随旧舆情平台存量一起移除（git 历史可回溯）。

四条纪律（写契约时必守，validate() 会检查可检查的部分）
  1. `success` 必须**可代码判定** —— 空 dict 直接判非法
  2. `on_fail` 必须**按错误类型分派**，不许只有一种动作
  3. `degrade` 不是失败 —— 部分产出 + 显式标注 > 整任务失败
  4. 不知道的值写 `PENDING`，**不许拍脑袋填**

⚠️ 全局约定（本项目已踩过的坑）：
  模块级常量**绝不能**做函数默认参数（def 时绑定 → 测试 patch 无效、污染真实数据）。
  读常量必须在函数体里读。
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- 常量

PENDING = "需拍板"                 # 未拍板的值统一标记
PENDING_CODE = "__PENDING__"       # 需要 None 时用（保留字面量便于 JSON 往返）

REQUIRED_FIELDS: tuple[str, ...] = (
    "task_id", "name", "goal",
    "inputs", "tools", "outputs", "artifact_contract",
    "text_access",                       # ★ 读哪些文本、读完回传什么（防"盲目"也防"失控"）
    "success", "degrade",
    "schedule", "budget", "idempotency_key",
    "on_fail", "human", "depends_on", "status",
)

ERROR_CLASSES: tuple[str, ...] = ("network", "empty", "schema", "waf", "llm", "budget")
FAIL_ACTIONS: tuple[str, ...] = ("retry", "degrade", "reject", "abort")
STATUSES: tuple[str, ...] = ("built", "partial", "blank")
SCHEDULE_MODES: tuple[str, ...] = ("interval", "event", "both", None)
BUDGET_KEYS: tuple[str, ...] = ("max_tool_calls", "max_net_calls",
                                "timeout", "max_retries", "token")

# ---- text_access：文本读取声明 ------------------------------------------------
# ★ 为什么必须有这个字段（2026-10-01 用户追问「这四个还是要原文的，不然就是盲目的」）：
#   披露"原文不进 State"很容易被误读成"系统不读文本" —— 那会做出**盲目**的系统。
#   所以要求每个任务显式声明：读哪些源、什么粒度、是否送 LLM、**读完只回传什么**。
TEXT_GRANULARITIES: tuple[str, ...] = ("none", "short", "full")
# 回传值里不许出现的"原文"字段名（与 agent_graph 的 State 守卫同一张黑名单）
RAW_TEXT_MARKS: tuple[str, ...] = ("raw_text", "content", "body", "review_text",
                                   "comment_text", "full_text", "original", "summary")
# 需要 LLM 的工具 → 该任务必须声明 text_access（否则就是"声称会语义判断却没声明读什么"）
LLM_TOOLS: tuple[str, ...] = ("llm_classify",)


# ---------------------------------------------------------------- 契约

TASKS: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------ L1→L3 主链
    "trend_intelligence": {
        "task_id": "trend_intelligence",
        "name": "趋势情报（采集→加工→聚类评分）",
        "goal": ("跑一轮六层链的 L1→L3：采集到期数据源 → 事件加工与语义标准化 → "
                 "事件聚类与八信号/三套评分/生命周期。产出第三层 Event，供第四层推理。"),
        "inputs": [
            "L1 source registry（enabled 数据源，按 plan 到期判断）",
            "data/events/*.jsonl（L1 事件总线输出）",
        ],
        "tools": ["l1_collect", "l2_process", "l3_trend_run"],
        "outputs": [
            "data/state/l2_processed.sqlite3::content",
            "data/state/l3_trend.sqlite3::trend_event",
        ],
        "artifact_contract": {
            "audience": "第四层 Intelligence（机器可读）；运营经 L6 Feed 消费",
            "format": "sqlite（结构化事件）",
            "fields": ["event_id", "canonical_title", "lifecycle", "hot_score",
                       "momentum_score", "confidence_score", "content_count"],
            "granularity": "每事件一行；分数历史/生命周期另表",
            "note": "第四层 upstream 直读此库；不再是给前端的话题 JSON",
        },
        # ★ 文本读取声明：L3 聚类与信号计算在层内读文本，只落结构化分数与 id
        "text_access": {
            "sources": ["l1.events.normalized_text（L3 层内读，不进 State/轨迹）"],
            "granularity": "full",
            "via_llm": False,
            "returns": ["event_id", "n_members", "similarity", "scores"],
            "note": ("「这热点值不值得做」的语义判断在第四层 Relevance（§14），"
                     "本任务只回答「什么事件正在发生、多热、什么阶段」"),
        },
        "success": {
            "min_event_rows": 1,          # trend_event 本轮新增 ≥1 行
            "empty_is_success": True,     # ★ 本轮没有新事件也算成功（数据源未更新是常态）
        },
        "degrade": {
            "source_fail": "skip_and_continue",       # 单源失败不杀整轮（L1 DLQ 兜底）
            "no_new_event": "output_zero_not_error",
        },
        "schedule": {"mode": "interval", "minutes": 15,
                     "note": "15min 承自探测层推导（实测特征时间 24min÷2=12min 下界，留余量）；"
                             "**反爬上界未实测**，跑几天看 --status 再校准"},
        # ★ max_net_calls 才是真正要控的（反爬）；max_tool_calls 防跑飞
        "budget": {"max_tool_calls": 8, "max_net_calls": 4,
                   "timeout": 900, "max_retries": 2, "token": 0},
        "idempotency_key": "hash(source+cursor+window)",
        "on_fail": {"network": "retry", "empty": "degrade",
                    "schema": "reject", "waf": "abort"},
        "human": False,
        "depends_on": [],
        "status": "built",
        "pending": [],
    },

    # ------------------------------------------------------------ L4
    "intelligence_run": {
        "task_id": "intelligence_run",
        "name": "AI 情报推理（Evidence→Relevance→Opportunity→Creative→Risk）",
        "goal": ("对第三层达标事件跑第四层推理图（Event Gate 分级 → 证据 → 机会 → 创意 → "
                 "评审 → 风险），产出可验证的情报包；无 LLM key 时规则兜底，产物如实标 mode。"),
        "inputs": ["data/state/l3_trend.sqlite3::trend_event（达标事件，T0 跳过）"],
        "tools": ["l4_intel_run"],
        "outputs": ["data/state/l4_intelligence.sqlite3::intelligence_analysis"],
        "artifact_contract": {
            "audience": "第六层 Execution（Feed/工作流/人工闸门）",
            "format": "sqlite + JSON payload",
            "fields": ["analysis_id", "event_id", "relevance_score", "n_opportunities",
                       "n_creatives", "risk_level"],
            "granularity": "每事件每次分析一行（§44 版本化，不覆盖）",
        },
        "text_access": {
            "sources": ["l2.processed_content（节点内按 content_id 临时取原文）"],
            "granularity": "full",
            "via_llm": True,
            "returns": ["analysis_id", "relevance", "opportunity_ids", "mode"],
            "note": "原文不进 State（State 守卫强制），读完只留结论 + source id",
        },
        "success": {
            "min_analysis_rows": 1,
            "empty_is_success": True,     # 没有达标事件（全部 T0 跳过）也算成功
        },
        "degrade": {
            "llm_unavailable": "rule_fallback_and_label_mode",
            "no_eligible_event": "output_zero_not_error",
        },
        "schedule": {"mode": "interval", "minutes": 60,
                     "note": "频率未校准；L4 自带 input_hash 缓存，重跑安全"},
        "budget": {"max_tool_calls": 4, "max_net_calls": 0,
                   "timeout": 1800, "max_retries": 1, "token": 50},
        "idempotency_key": "hash(event_id+input_hash)（L4 自管缓存）",
        "on_fail": {"network": "retry", "empty": "degrade",
                    "schema": "reject", "waf": "abort", "llm": "degrade"},
        "human": False,
        "depends_on": ["trend_intelligence"],
        "status": "built",
        "pending": [],
    },

    # ------------------------------------------------------------ L5
    "memory_ingest": {
        "task_id": "memory_ingest",
        "name": "记忆回填（趋势/创意/决策 → 第五层）",
        "goal": ("把 L3 事件与 L4 分析/人工反馈回填第五层记忆：闭合事件入 Trend Memory，"
                 "进行中热点入短期记忆（§18），创意入 Creative Memory（agent_generated），"
                 "人工反馈入 Decision Memory。全幂等，重跑安全。"),
        "inputs": [
            "data/state/l3_trend.sqlite3",
            "data/state/l4_intelligence.sqlite3",
        ],
        "tools": ["l5_memory_ingest"],
        "outputs": ["data/state/l5_memory.sqlite3（6 类 Memory 表）"],
        "artifact_contract": {
            "audience": "第四层检索（Retrieval Service，§24 统一入口）",
            "format": "sqlite",
            "fields": ["trend_id", "creative_id", "decision_id", "tier", "authority"],
            "granularity": "每条记忆一行；tier/authority 标信任级别（§39/§53）",
        },
        "text_access": {
            "sources": [],
            "granularity": "none",
            "via_llm": False,
            "returns": ["ingested", "short_term"],
            "note": "只读写结构化记录，不读原文（PII 扫描在写入侧强制，§55）",
        },
        "success": {
            "idempotent_rerun": True,     # 重跑行数不变 = 成功（幂等是特性不是失败）
            "empty_is_success": True,
        },
        "degrade": {
            "upstream_db_missing": "skip_with_reason",
        },
        "schedule": {"mode": "interval", "minutes": 60,
                     "note": "跟随 intelligence_run 之后即可；频率未校准"},
        "budget": {"max_tool_calls": 2, "max_net_calls": 0,
                   "timeout": 300, "max_retries": 1, "token": 0},
        "idempotency_key": "hash(源库内容+ingest 版本)（层内确定性主键）",
        "on_fail": {"network": "retry", "empty": "degrade",
                    "schema": "reject", "waf": "abort"},
        "human": False,
        "depends_on": ["intelligence_run"],
        "status": "built",
        "pending": [],
    },

    # ------------------------------------------------------------ L6
    "ops_alerts": {
        "task_id": "ops_alerts",
        "name": "运营执行面（告警分级 + 工作台刷新）",
        "goal": ("对 Feed 全量卡片跑 P0/P1/P2 分级告警（同事件同级别同日去重）并刷新"
                 "离线工作台，让运营 30 秒知道今天先干什么（§36）。"),
        "inputs": [
            "L3 事件 + L4 分析（经 L6 Feed 组装）",
            "data/state/ops_context.json（运营约束，可选，§39）",
        ],
        "tools": ["l6_ops_run"],
        "outputs": [
            "data/state/l6_execution.sqlite3::alert",
            "L6_execution/workbench/index.html（生成物，不入 git）",
        ],
        "artifact_contract": {
            "audience": "运营侧（工作台/通知）",
            "format": "sqlite（告警）+ 单文件 HTML（只读镜像）",
            "fields": ["alert_id", "tier", "title", "recommended_action", "basis"],
            "granularity": "每事件同级别同日一条告警；工作台为全量快照",
        },
        "text_access": {
            "sources": [],
            "granularity": "none",
            "via_llm": False,
            "returns": ["n_alerts", "tiers"],
            "note": "分级用结构化分数与窗口（§16 规格阈值/降级口径如实标 basis），不读原文",
        },
        "success": {
            "min_alert_rows": 0,
            "empty_is_success": True,     # ★ 没有达 P0/P1 门槛的事件 → 0 告警是正确结果
        },
        "degrade": {
            "upstream_missing": "skip_with_reason",
        },
        "schedule": {"mode": "interval", "minutes": 60,
                     "note": "跟随 intelligence_run；频率未校准"},
        "budget": {"max_tool_calls": 2, "max_net_calls": 0,
                   "timeout": 300, "max_retries": 1, "token": 0},
        "idempotency_key": "hash(event+tier+date)（层内已去重）",
        "on_fail": {"network": "retry", "empty": "degrade",
                    "schema": "reject", "waf": "abort"},
        "human": False,
        "depends_on": ["memory_ingest"],
        "status": "built",
        "pending": [],
    },
}


# ---------------------------------------------------------------- 接口

def all_tasks() -> dict[str, dict[str, Any]]:
    """全部契约（返回副本，防调用方改到注册表）。"""
    return {k: dict(v) for k, v in TASKS.items()}


def get(task_id: str) -> dict[str, Any]:
    """取单个契约；不存在时抛 KeyError（不静默返回空）。"""
    if task_id not in TASKS:
        raise KeyError(f"未注册的任务：{task_id}；已注册：{sorted(TASKS)}")
    return TASKS[task_id]


def validate(contract: dict[str, Any]) -> list[str]:
    """校验契约是否满足 16 项 + 四条纪律。返回问题列表（空 = 通过）。

    只检查**可自动检查**的部分；语义（判据是否真的够用）仍需人审。
    """
    problems: list[str] = []

    for f in REQUIRED_FIELDS:
        if f not in contract:
            problems.append(f"缺必填字段：{f}")
    if problems:
        return problems           # 字段都不全，后面的检查没意义

    # success 必须可代码判定
    success = contract["success"]
    if not isinstance(success, dict) or not success:
        problems.append("success 为空：判据必须可代码判定，不许留空")
    for k, v in (success or {}).items():
        if isinstance(v, str) and v == PENDING:
            problems.append(f"success.{k} 仍是待拍板：判据不能是占位符")

    # on_fail 必须按错误类型分派，且不只一种动作
    on_fail = contract["on_fail"] or {}
    if not isinstance(on_fail, dict) or not on_fail:
        problems.append("on_fail 为空：必须按错误类型分派")
    else:
        bad_keys = [k for k in on_fail if k not in ERROR_CLASSES]
        if bad_keys:
            problems.append(f"on_fail 含未知错误类型：{bad_keys}（合法：{list(ERROR_CLASSES)}）")
        bad_vals = [v for v in on_fail.values() if v not in FAIL_ACTIONS]
        if bad_vals:
            problems.append(f"on_fail 含未知动作：{bad_vals}（合法：{list(FAIL_ACTIONS)}）")
        if len(set(on_fail.values())) < 2:
            problems.append("on_fail 只有一种动作：必须按错误类型分派（纪律 2）")

    # ---- text_access：读文本的声明（防盲目 + 防失控）----
    ta = contract.get("text_access")
    if not isinstance(ta, dict) or not ta:
        problems.append("text_access 缺失：必须声明读哪些文本源、什么粒度、读完回传什么")
    else:
        gran = ta.get("granularity")
        src = ta.get("sources") or []
        if gran not in TEXT_GRANULARITIES:
            problems.append(f"text_access.granularity 非法：{gran!r}（合法 {list(TEXT_GRANULARITIES)}）")
        if gran == "none" and src:
            problems.append("granularity=none 却声明了 sources（自相矛盾）")
        if gran in ("short", "full") and not src:
            problems.append(f"granularity={gran} 必须声明 sources —— 读哪里都不写，就是盲目")
        if ta.get("via_llm") and not (ta.get("returns") or []):
            problems.append("via_llm=True 却没有 returns：必须回传结论，不许把原文回传")
        for r in ta.get("returns") or []:
            hit = [m for m in RAW_TEXT_MARKS if m in str(r).lower()]
            if hit:
                problems.append(f"text_access.returns 含原文类字段 {r!r}（命中 {hit}）："
                                "只能回传判定量与 id，原文落盘")
        # ★ 最要紧的一条：声称要语义判断（挂了 LLM 工具）就必须真读文本
        if any(t in LLM_TOOLS for t in (contract.get("tools") or [])) and not ta.get("via_llm"):
            problems.append("工具含 LLM 类，但 text_access.via_llm 为假 —— "
                            "声称做语义判断却没声明读文本，会做成盲目的系统")

    # budget 五项齐全
    budget = contract["budget"] or {}
    for k in BUDGET_KEYS:
        if k not in budget:
            problems.append(f"budget 缺 {k}")

    # schedule
    mode = (contract["schedule"] or {}).get("mode")
    if mode not in SCHEDULE_MODES:
        problems.append(f"schedule.mode 非法：{mode!r}")
    if mode is None and "schedule" not in (contract.get("pending") or []):
        problems.append("schedule.mode 为 None 但未登记进 pending（纪律 4）")

    # status / 其他枚举
    if contract["status"] not in STATUSES:
        problems.append(f"status 非法：{contract['status']!r}")
    if contract["human"] == PENDING and "human" not in (contract.get("pending") or []):
        problems.append("human 为待拍板但未登记进 pending")

    # 待拍板项必须真实存在（防止 pending 与内容脱节）
    for p in contract.get("pending") or []:
        if p.startswith("budget."):
            key = p.split(".", 1)[1]
            if budget.get(key) != PENDING:
                problems.append(f"pending 列了 {p}，但 budget.{key} 不是待拍板")
        elif p == "schedule":
            if mode is not None:
                problems.append("pending 列了 schedule，但 schedule.mode 已填")
        elif p == "human":
            if contract["human"] != PENDING:
                problems.append("pending 列了 human，但 human 已填")

    # 依赖必须指向已注册任务
    for d in contract["depends_on"] or []:
        if d not in TASKS:
            problems.append(f"depends_on 指向未注册任务：{d}")

    return problems


def validate_all() -> dict[str, list[str]]:
    """校验全部契约。返回 {task_id: [问题]}，只含**有问题**的任务。"""
    out: dict[str, list[str]] = {}
    for tid, c in TASKS.items():
        p = validate(c)
        if p:
            out[tid] = p
    return out


def summary() -> str:
    """人类可读的注册表概览（给 --tasks 用）。"""
    lines = ["任务注册表（harness 的靶子）", ""]
    for tid, c in TASKS.items():
        lines.append(f"· {tid}  「{c['name']}」  状态 {c['status']}"
                     f"  依赖 {c['depends_on'] or '无'}")
        lines.append(f"    工具链：{' → '.join(c['tools'])}")
        sch = c["schedule"]
        sched = (f"{sch.get('minutes')}min/轮" if sch.get("mode") == "interval"
                 else f"{sch.get('mode')}")
        lines.append(f"    调度：{sched}  预算："
                     f"工具≤{c['budget']['max_tool_calls']} · "
                     f"网络≤{c['budget']['max_net_calls']} · "
                     f"超时{c['budget']['timeout']}s · "
                     f"重试{c['budget']['max_retries']}")
        if c.get("pending"):
            lines.append(f"    ⬜ 待拍板：{', '.join(c['pending'])}")
        lines.append("")
    bad = validate_all()
    lines.append("契约校验：" + ("全部通过 ✅" if not bad else f"❌ {len(bad)} 个任务有问题"))
    for tid, probs in bad.items():
        for p in probs:
            lines.append(f"  · {tid}: {p}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        import json
        print(json.dumps(all_tasks(), ensure_ascii=False, indent=2))
    else:
        print(summary())
