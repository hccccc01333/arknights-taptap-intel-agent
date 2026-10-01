#!/usr/bin/env python3
"""任务契约（机器可读）—— harness 的任务注册表。

设计依据
  · `docs/任务契约.md`（§2 schema 16 项 + 四条写契约纪律）
  · `docs/Agent-v2-架构设计.md` §6 第 10–15 条（控制面议题）

★ 本文件是**纯数据**，不含任何执行逻辑（执行逻辑在 `harness.py`）。
  这样契约可以被单独评审、单独测试、被前端/文档直接读取。

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
#   事实：四个任务都要读文本，区别只在**读多少**（粒度）与**谁读**（代码/LLM）。
#   所以要求每个任务显式声明：读哪些源、什么粒度、是否送 LLM、**读完只回传什么**。
#   这三条把两件事同时管住：① 不读会盲目（禁止 granularity=none 却声称能判断语义）
#   ② 读了会失控（禁止把原文回传进 State/轨迹，必须回传结论 + source id）
TEXT_GRANULARITIES: tuple[str, ...] = ("none", "short", "full")
# 回传值里不许出现的"原文"字段名（与 agent_graph 的 State 守卫同一张黑名单）
RAW_TEXT_MARKS: tuple[str, ...] = ("raw_text", "content", "body", "review_text",
                                   "comment_text", "full_text", "original", "summary")
# 需要 LLM 的工具 → 该任务必须声明 text_access（否则就是"声称会语义判断却没声明读什么"）
LLM_TOOLS: tuple[str, ...] = ("llm_classify",)


# ---------------------------------------------------------------- 契约

TASKS: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------ T2
    "hotspot_track": {
        "task_id": "hotspot_track",
        "name": "热点追踪识别",
        "goal": ("从 TapTap 平台热榜与话题帖子流中识别值得关注的社区热点，"
                 "持续追踪其生命周期（冒头→升温→爆发→退潮→沉寂），"
                 "并在状态迁移时产生事件。"),
        "inputs": [
            "data/raw/taptap/hot_hashtags.csv",
            "data/raw/taptap/discovery_posts.csv",
            "data/outputs/agent/platform_insight.json",
        ],
        "tools": [                       # 顺序即执行顺序（本任务是一条链）
            "crawl_hot_hashtags",
            "platform_facts",
            "topic_sample",
            "events_run",
            "features_run",
        ],
        "outputs": [
            "data/state/topic_tracker.sqlite3::topic_state",
            "data/state/topic_tracker.sqlite3::topic_series",
            "data/state/events.sqlite3::topic_event",
            "data/state/post_snapshots.sqlite3::post_snapshots",
            "data/raw/taptap/features/features.jsonl",
        ],
        "artifact_contract": {
            "audience": "运营侧（看板 / 前端）",
            "format": "json",
            "fields": ["title", "state", "peak_metric", "first_seen_at", "last_transition"],
            "granularity": "每话题一行；状态迁移另出事件流",
            "note": "前端话题列表直读这里，不需要额外推送层",
        },
        # ★ 文本读取声明：不读文本就没有判断，读法必须写清楚
        "text_access": {
            "sources": ["hot_hashtags.title", "discovery_posts.title"],
            "granularity": "short",
            "via_llm": False,
            "returns": ["topic_key", "title", "state", "peak_metric", "series_delta"],
            "note": ("本链只读**标题**（短标识，~13 字）——追踪判的是变化率；"
                     "「这热点值不值得做」需要读语义，那一步在 `ferment_judge`"
                     "（读 title 送 LLM，旧判据字面匹配 0/10 → 语义 5/10）"),
            # ★ 语义判断的交接点（不是缺口！）：探测链不读语义，判断由**深采决策**消费
            "ferment_handoff": ("语义判断由**深采决策**消费：`scheduler.decide_drill` 读 "
                                "`outputs/ferment_judge.json` 的 ferment_score/verdict，"
                                "决定挖哪些话题（日志里「冒头但可发酵度 act（60）」就是它）。"
                                "**发现高频便宜、判断低频贵 —— 刻意如此，不是没接上**"),
        },
        # ★ 全部可代码判定
        "success": {
            "min_series_rows": 1,        # topic_series 本轮新增 ≥1 行
            "min_sample_count": 2,       # 至少一个话题 sample_count ≥2 才能判迁移
            "event_idempotent": True,    # 同 event_id 不重复入库
            "empty_is_success": True,    # ★「本轮没有热点/没有迁移」也算成功
        },
        "degrade": {
            "hot_board_fail": "skip_round_mark_stale",
            "no_topic_hit": "output_zero_topics_not_error",
        },
        "schedule": {"mode": "interval", "minutes": 15,
                     "drill": "event_triggered", "drill_cooldown_min": 60},
        # ★ max_net_calls 才是真正要控的（反爬）；max_tool_calls 防跑飞
        "budget": {"max_tool_calls": 8, "max_net_calls": 2,
                   "timeout": 600, "max_retries": 2, "token": 0},
        "idempotency_key": "hash(topic_key+to_state+occurred_at)",
        "on_fail": {"network": "retry", "empty": "degrade",
                    "schema": "reject", "waf": "abort"},
        "human": False,
        "depends_on": [],
        "status": "built",
        "pending": [],
    },

    # ------------------------------------------------------------ T5
    "material_extract": {
        "task_id": "material_extract",
        "name": "素材获取",
        "goal": ("从已采集的帖子与评论中抽取 4 类素材（原帖引用 / 热评金句 / 梗 / 二创角度），"
                 "每条素材带可验证的溯源；够不上溯源门槛的一律丢弃。"),
        "inputs": [
            "data/raw/taptap/discovery_posts.csv",
            "data/raw/taptap/discovery_comments.csv",
            "← 上游 T2 产出的 topic_key / topic_title",
            "← Thread 结构（moment_id + 评论束）",
        ],
        "tools": ["material_extract_code", "llm_classify"],
        "outputs": [
            "data/raw/taptap/materials/materials.jsonl",
            "data/raw/taptap/materials/threads.jsonl",
        ],
        "artifact_contract": {
            "audience": "运营侧（前端「素材页」）",
            "format": "json（素材卡片）",
            "fields": ["material_id", "type", "text", "topic_key", "thread_id",
                       "provenance.moment_id", "provenance.url", "metrics"],
            "granularity": "一条素材一卡；四类可筛选",
            "note": "前端素材页直读这里",
        },
        "text_access": {
            "sources": ["discovery_posts.summary", "discovery_comments.content（Thread 束）"],
            "granularity": "full",
            "via_llm": True,
            "returns": ["material_id", "type", "topic_key", "count"],
            "note": ("原文经 Thread 检索后作为**那一次调用的 prompt**，用完即弃；"
                     "产物（素材卡片含 text）落 materials.jsonl；State 只留 id 与计数"),
            "source_id_required": True,     # 用完即弃 → 结论必须带 id，否则不可回放
        },
        "success": {
            "traceable_rate": 1.0,       # ★ 溯源 100%：assert_traceable 全通过
            "drop_on_untraceable": True,  # 不通过的丢弃并计数（不是打回）
            "min_types_code_only": 2,    # LLM 不可用时仍须出 2 类（原帖引用 + 金句选取）
            "pii_not_persisted": True,   # 作者名可展示，绝不入库
        },
        "degrade": {
            # ★ 分层降级：这是本任务最容易做错的地方
            "llm_unavailable": "emit_code_only_types_and_label_missing",
            "label_missing": ["meme", "remix_angle"],
        },
        "schedule": {"mode": None, "note": PENDING},          # ⬜ 待拍板
        "budget": {"max_tool_calls": 20, "max_net_calls": 0,
                   "timeout": 300, "max_retries": 2, "token": PENDING},
        "idempotency_key": "hash(moment_id+comment_id+type)",
        "on_fail": {"llm": "degrade", "schema": "reject", "empty": "degrade"},
        "human": PENDING,                # ⬜ 金句分类仲裁是否要人工点
        "depends_on": ["hotspot_track"],
        "status": "partial",          # ★ 2026-10-01：纯代码两类已实现（梗/二创角度仍需 LLM）
        "pending": ["schedule", "budget.token", "human"],
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
