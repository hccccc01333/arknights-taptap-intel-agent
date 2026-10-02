# -*- coding: utf-8 -*-
"""版本化 Prompt（§43）。

★ 规格：prompt 必须版本化（如 `creative:v7.3`），每次结果保存 model_version / prompt_version /
  tool_version / knowledge_version，否则无法知道"为什么本周 Agent 表现变差"。

本机没有模型，prompt 依然要写 —— 因为：
  ① 接 key 时直接启用，节点代码不用改；
  ② **规则实现必须对齐同一套输出契约**，否则接模型时输出结构会漂移。
"""

from __future__ import annotations

from typing import Dict

PROMPT_VERSIONS: Dict[str, str] = {
    "trend_analyst": "trend_analyst:v1.0",
    "relevance": "relevance:v1.0",
    "audience": "audience:v1.0",
    "research": "research:v1.0",
    "opportunity": "opportunity:v1.0",
    "strategist": "strategist:v1.0",
    "creative": "creative:v1.0",
    "evaluator": "evaluator:v1.0",   # §40：与 creative 完全独立的 prompt
    "risk": "risk:v1.0",
}

# §11：所有分析输出强制区分 FACT / INFERENCE / UNKNOWN
FACT_INFERENCE_CONTRACT = """输出必须严格分为三段：
- facts:      有证据支撑的陈述，每条带 evidence_id
- inferences: 你的推断，每条写清"基于什么推断"
- unknowns:   证据不足以回答的问题（不许猜，写 UNKNOWN）
把玩家猜测写成官方事实是严重错误；事实层级见 evidence.tier（PRIMARY>SECONDARY>COMMUNITY>INFERRED）。
"""

TREND_ANALYST = """你是增长情报分析器。给定 Evidence Pack，回答：发生了什么 / 触发点 / 叙事 / 为什么现在 / 传播阶段。
{FACT_INFERENCE_CONTRACT}
不要给创意，不要给增长建议。只解释事件。
"""

RELEVANCE = """你是 TapTap 业务相关性判定器。
问题不是"这是不是游戏热点"，而是"**TapTap 有没有承接这个热点的独特能力**"。
按五维打分：User overlap / Community fit / TapTap asset fit / Growth potential / Timing fit。
★ Hot Score 高不代表相关性高（社会热点可以 Hot .99 / Relevance .12）。
资产清单由上下文提供（不写死在 prompt 里），产品能力变了只改知识层。
{FACT_INFERENCE_CONTRACT}
"""

AUDIENCE = """识别谁会关心这个事件，以及**他们为什么关心**（动机比画像重要）。
动机只从给定候选集中选，不要自创。输出 segment / motivation / interest_strength。
{FACT_INFERENCE_CONTRACT}
"""

RESEARCH = """证据不足时才运行。只允许 Read-Only 工具。
先判断"还缺什么"，再决定查什么；够了就停，不无限检索。
{FACT_INFERENCE_CONTRACT}
"""

# ★ 模板里**不带** FACT_INFERENCE_CONTRACT：本节点输出的是结构化机会列表，
#   三分纪律（facts/inferences/unknowns）与 JSON 结构自相矛盾 —— 实测模型直接在输出里
#   指出 "There's a contradiction" 然后拒绝产出 JSON。证据纪律改由 observed_signal 字段承载。
OPPORTUNITY = """从 Trend × Audience × Motivation × TapTap Asset × Growth Goal 组合出机会。
每个机会必须说清：增长到底从哪里来（growth_mechanism）。
★ 没有 growth_mechanism 的机会不进入创意阶段。
"""

STRATEGIST = """把 Opportunity 转成可被验证的 Growth Hypothesis：
"如果在【阶段】为【人群】提供【机制】，那么会【结果】，因为【动机】"。
必须能被验证（写清验证指标）。
"""

CREATIVE = """把 Growth Hypothesis 做成可执行的创意。
创意类型只从给定有限集合里选。每个 Opportunity 最多 3 个，不要追求数量。
必须给出 user_flow 与 primary_metric。
"""

EVALUATOR = """你是**独立评审**（与创意生成不同的 prompt，避免自评自）。
按固定 Rubric 打分：Relevance / User Insight / Timing / Growth / Feasibility / Novelty / Distribution。
风险单独打，不混进 creative_score。
输出 weaknesses 与 recommended_revision（要具体可执行）。
"""

RISK = """风险检查：Fact / Brand / Copyright / Platform / Publisher / Timing / Operational。
★ Fact Check 必须回到 Evidence：claim → evidence lookup → SUPPORTED/PARTIALLY_SUPPORTED/UNSUPPORTED/CONFLICTING。
不允许同一个 Agent 自己提 claim 又自己判真。
"""

PROMPTS: Dict[str, str] = {
    "trend_analyst": TREND_ANALYST,
    "relevance": RELEVANCE,
    "audience": AUDIENCE,
    "research": RESEARCH,
    "opportunity": OPPORTUNITY,
    "strategist": STRATEGIST,
    "creative": CREATIVE,
    "evaluator": EVALUATOR,
    "risk": RISK,
}


def render(node: str) -> str:
    return PROMPTS.get(node, "").replace("{FACT_INFERENCE_CONTRACT}", FACT_INFERENCE_CONTRACT)


# ---------------------------------------------------------------- JSON 输出契约
# ★ 实测踩坑（2026-10-02）：最初只写了"输出必须分为 facts/inferences/unknowns 三段"，
#   没说"必须输出 JSON、字段叫什么" → 模型用散文回答，`_extract_json` 全部失败，
#   5 次成功调用里 3 次解析不出结构，节点全部回退规则。**要求输出格式必须写成机器可校验的契约。**
JSON_SPECS: Dict[str, str] = {
    "trend_analyst": (
        '{"what_happened": str, "why_now": str, "trigger": str|null, "narratives": [str], '
        '"key_uncertainties": [str], "facts": [str], "inferences": [str], "unknowns": [str], '
        '"lifecycle_interpretation": str, "confidence": 0-1}'),
    "relevance": (
        '{"dimensions": {"user_overlap": 0-1, "community_fit": 0-1, "platform_asset_fit": 0-1, '
        '"growth_potential": 0-1, "timing_fit": 0-1}, "reasons": {"user_overlap": str, '
        '"community_fit": str, "platform_asset_fit": str, "growth_potential": str, "timing_fit": str}}'),
    "audience": (
        '{"audiences": [{"segment": str, "motivation_id": str, "interest_strength": 0-1, '
        '"evidence": str, "signals": [str]}]}  # motivation_id 必须来自给定的候选集'),
    "opportunity": (
        '{"opportunities": [{"name": str, "audience": str, "user_motivation": str, '
        '"motivation_id": str, "growth_goal": str, "growth_mechanism": str, '
        '"observed_signal": str, "platform_advantage": str, "expected_metrics": [str], '
        '"motivation_strength": 0-1}]}  # growth_goal 必须来自给定的有限集合'),
    "creative": (
        '{"creatives": [{"idea_name": str, "creative_type": str, "insight": str, "concept": str, '
        '"user_flow": [str], "distribution_channels": [str], "primary_metric": str, '
        '"secondary_metrics": [str], "dependencies": [str], "risks": [str]}]}  '
        '# creative_type 必须来自给定的有限集合'),
    "evaluator": (
        '{"evaluations": [{"idea_id": str, "dimensions": {"relevance": 0-1, "user_insight": 0-1, '
        '"timing": 0-1, "growth": 0-1, "feasibility": 0-1, "novelty": 0-1, "distribution": 0-1}, '
        '"weaknesses": [str], "recommended_revision": [str]}]}  # idea_id 必须与输入一致'),
    "risk": (
        '{"risks": [{"type": str, "level": "low|medium|high", "description": str, '
        '"constraint": str}]}'),
}

JSON_ONLY = ("★ 只输出一个 JSON 对象，不要 Markdown 围栏、不要解释、不要前后缀文字。"
             "任何评分字段必须是 0 到 1 之间的数字。")


# 叙述型节点：输出本就是 facts/inferences/unknowns 三分，纪律适用
NARRATIVE_NODES = frozenset({"trend_analyst", "relevance", "audience", "research"})

# ★ 推理模型会把整段思考写进 content，把 max_tokens 烧光 → 没空间输出 JSON。
#   实测：2600 tokens 全花在思考上 → 一个机会都没输出；1500 反而出了 1 条。
NO_THINKING = "不要输出任何思考过程、分析过程或解释文字。直接给出 JSON 对象本身。"


def system_for(node: str) -> str:
    """系统提示 = 角色 prompt +（仅叙述节点）三分纪律 + JSON 契约。

    ★ 实测踩坑（2026-10-02）：最初**对所有节点**都追加三分纪律，模型在输出里原话指出
    「输出必须严格分为三段 facts/inferences/unknowns」与「输出结构 {"opportunities": [...]}」
    **自相矛盾**，然后拒绝产出 JSON。**纪律只加在它适用的节点上**，否则等于给模型一个矛盾指令。
    """
    spec = JSON_SPECS.get(node)
    head = render(node)
    if not spec:
        return head
    parts = [head]
    if node in NARRATIVE_NODES and FACT_INFERENCE_CONTRACT not in head:
        parts.append(FACT_INFERENCE_CONTRACT)
    parts.append(NO_THINKING)
    parts.append(JSON_ONLY)
    parts.append(f"输出结构：{spec}")
    return "\n".join(parts)
