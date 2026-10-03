# -*- coding: utf-8 -*-
"""第四层 LangGraph 图（§51）+ 纯标准库等价执行器。

★ 降级纪律（与 `runtime/agent_graph.py` 同源）：
  langgraph **未安装**时不能假装跑过图。所以：
    - `build_langgraph()`：装了才构建真 StateGraph（含条件边 / 有界修订环 / interrupt 人工闸门）
    - `run_stdlib()`：同一批节点函数 + **同一批路由函数**的纯标准库执行器，
      保证在没有依赖的机器上也能跑通并产出真实结果
  两者共用 `routing.py` 的判定逻辑，**不会出现两套业务规则**。
  跑哪条路会写进产物的 `engine` 字段，不许含糊。
"""

from __future__ import annotations

import importlib.util
from typing import Any, Callable, Dict, List, Optional

from ..nodes import evidence as N_evidence
from ..nodes import analysis as N_analysis
from ..nodes import research as N_research
from ..nodes import opportunity as N_opportunity
from ..nodes import creative as N_creative
from ..nodes import evaluation as N_evaluation
from ..llm import ModelRouter, stamp
from ..nodes import llm_augment as AUG
from ..prompts import PROMPT_VERSIONS
from ..state import assert_state_clean
from .routing import (route_after_relevance, route_evidence_gate, route_quality,
                      route_after_review)

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
GRAPH_VERSION = "graph-1.0"
MAX_ITERATION = N_evaluation.MAX_ITERATION

NodeFn = Callable[[Dict[str, Any], "Ctx"], Dict[str, Any]]


class Ctx:
    """节点执行的共享上下文（不进 State：它是运行时依赖，不是业务状态）。"""

    def __init__(self, upstream: Any, tools: Optional[Dict[str, Any]] = None,
                 router: Optional[ModelRouter] = None, max_opportunities: int = 4,
                 max_creatives: int = 8, human_review: bool = True):
        self.upstream = upstream
        self.tools = tools
        self.router = router or ModelRouter()
        self.max_opportunities = max_opportunities
        self.max_creatives = max_creatives
        self.human_review = human_review
        self.trace: List[Dict[str, Any]] = []

    def log(self, node: str, **kw: Any) -> None:
        self.trace.append({"node": node, **kw})


# ---------------------------------------------------------------- 节点

def n_evidence(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    up = ctx.upstream
    ev = up.event(state["event_id"])
    if ev is None:
        ctx.log("evidence", ok=False, reason="event_missing")
        return {"status": "archived", "route": "event_missing"}
    members = up.members(state["event_id"])
    pack = N_evidence.build(ev, members)
    stamp(state, "evidence", ctx.router.resolve("evidence"))
    ctx.log("evidence", ok=True, n_evidence=pack["n_evidence"])
    return {"evidence_pack": pack, "event_summary": pack["event"]["title"]}


def n_trend_analyst(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    info = ctx.router.resolve("trend_analyst")
    out = N_analysis.trend_analyst(state.get("evidence_pack") or {}, {})
    out = AUG.augment_trend_analyst(out, state.get("evidence_pack") or {}, ctx.router)
    out.setdefault("mode", info["mode"])
    stamp(state, "trend_analyst", info, PROMPT_VERSIONS["trend_analyst"])
    ctx.log("trend_analyst", ok=True, mode=info["mode"])
    return {"trend_analysis": out}


def n_relevance(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    info = ctx.router.resolve("relevance")
    out = N_analysis.relevance(state.get("evidence_pack") or {}, state.get("trend_analysis"))
    out = AUG.augment_relevance(out, state.get("evidence_pack") or {}, ctx.router)
    out.setdefault("mode", info["mode"])
    stamp(state, "relevance", info, PROMPT_VERSIONS["relevance"])
    ctx.log("relevance", ok=True, score=out["score"], route=out["route"])
    return {"relevance": out, "route": out["route"]}


def n_research(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    from ..tools import build_tools
    tools = ctx.tools or build_tools(ctx.upstream)
    out = N_research.research(state, ctx.upstream, tools)
    stamp(state, "research", ctx.router.resolve("research"), PROMPT_VERSIONS["research"])
    ctx.log("research", ok=True, status=out["status"],
            blocked=len(out.get("blocked_tools") or []))
    return {"research_result": out}


def n_audience(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    info = ctx.router.resolve("audience")
    out = N_analysis.audience(state.get("evidence_pack") or {}, state.get("trend_analysis") or {})
    out = AUG.augment_audience(out, state.get("evidence_pack") or {}, ctx.router)
    stamp(state, "audience", info, PROMPT_VERSIONS["audience"])
    ctx.log("audience", ok=True, n=len(out))
    return {"audiences": out}


def n_opportunity(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    info = ctx.router.resolve("opportunity")
    cap = 2 if state.get("route") == "light_analysis" else ctx.max_opportunities
    out = N_opportunity.opportunity(state, max_opportunities=cap)
    out = AUG.augment_opportunity(out, state, ctx.router)
    for o in out:
        o.setdefault("mode", info["mode"])
    stamp(state, "opportunity", info, PROMPT_VERSIONS["opportunity"])
    ctx.log("opportunity", ok=True, n=len(out))
    return {"opportunities": out}


def n_strategist(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    info = ctx.router.resolve("strategist")
    out = N_opportunity.strategist(state)
    stamp(state, "strategist", info, PROMPT_VERSIONS["strategist"])
    ctx.log("strategist", ok=True, n=len(out))
    return {"growth_hypotheses": out}


def n_creative(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    info = ctx.router.resolve("creative")
    out = N_creative.generate(state, max_per_opp=2 if state.get("route") == "light_analysis" else 3)
    out = AUG.augment_creative(out, state, ctx.router)
    out = out[:ctx.max_creatives]
    stamp(state, "creative", info, PROMPT_VERSIONS["creative"])
    ctx.log("creative", ok=True, n=len(out))
    return {"creatives": out}


def n_evaluator(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    info = ctx.router.resolve("evaluator")
    creatives = state.get("creatives") or []
    rule_evals = [N_evaluation.evaluate(c, state, peers=creatives) for c in creatives]
    evals = AUG.augment_evaluation(rule_evals, creatives, state, ctx.router)
    for c, e in zip(creatives, evals):
        c["score"] = e["creative_score"]
        c["evaluation"] = e
    passed = [e for e in evals if e["pass"]]
    stamp(state, "evaluator", info, PROMPT_VERSIONS["evaluator"])
    ctx.log("evaluator", ok=True, n=len(evals), passed=len(passed),
            iteration=state.get("iteration", 0))
    return {"creatives": creatives,
            "evaluation": {"n": len(evals), "passed": len(passed),
                           "mean_score": round(sum(e["creative_score"] for e in evals) / len(evals), 3)
                           if evals else None,
                           "items": evals}}


def n_revise(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    """§31 修订：按 Evaluator 给的 recommended_revision 改，不重新瞎生成。"""
    creatives = state.get("creatives") or []
    notes: List[str] = []
    for c in creatives:
        e = c.get("evaluation") or {}
        if e.get("pass"):
            continue
        for rev in (e.get("recommended_revision") or [])[:2]:
            notes.append(f"{c['idea_id']}: {rev}")
        # 可执行的两条：赶不上窗口 → 换低成本类型；机制链太短 → 标注需补全
        if any("窗口" in w or "上线" in w for w in (e.get("weaknesses") or [])):
            # ★ 换类型就必须同步改名，否则会出现"名字叫 Push 触达、类型是热点专题"的自相矛盾产物
            from ..knowledge import CREATIVE_TYPES
            c["creative_type"] = "content"
            c["creative_type_name"] = CREATIVE_TYPES["content"]["name"]
            c["idea_name"] = f"{CREATIVE_TYPES['content']['name']}·{c.get('target_audience')}"
            c["implementation_cost"] = "low"
            c["lead_time_hours"] = 4
            c["user_flow"] = ["看到专题", "阅读/浏览", "进入游戏详情", "关注/预约"]
            c["distribution_channels"] = ["推荐", "搜索", "动态流"]
        if (e.get("dimensions") or {}).get("growth", 1) < 0.6:
            c["growth_mechanism"] = "热点 → 游戏讨论 → 详情页访问 → 关注游戏 → 预约/下载"
    ctx.log("revise", ok=True, notes=len(notes))
    return {"creatives": creatives,
            "iteration": int(state.get("iteration", 0)) + 1,
            "revision_notes": (state.get("revision_notes") or []) + notes}


def n_risk(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    info = ctx.router.resolve("risk")
    out = N_evaluation.risk(state)
    out = AUG.augment_risk(out, state, ctx.router)
    stamp(state, "risk", info, PROMPT_VERSIONS["risk"])
    ctx.log("risk", ok=True, level=out["risk_level"])
    return {"risk": out}


def n_human_review(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    """§34 末端人工闸门。langgraph 下用 interrupt() 暂停；fallback 下置 awaiting_review 停下。"""
    # ⚠️ 不写 route：route 是 §14 相关性门禁的决策（archive/light/opportunity），
    #    被人工节点覆盖会让"为什么进了这条分支"变得不可解释。工作流位置用 status 表达。
    if not ctx.human_review:
        return {"status": "approved"}
    return {"status": "awaiting_review"}


NODES: Dict[str, NodeFn] = {
    "evidence": n_evidence,
    "trend_analyst": n_trend_analyst,
    "relevance": n_relevance,
    "research": n_research,
    "audience": n_audience,
    "opportunity": n_opportunity,
    "strategist": n_strategist,
    "creative": n_creative,
    "evaluator": n_evaluator,
    "revise": n_revise,
    "risk": n_risk,
    "human_review": n_human_review,
}


# ---------------------------------------------------------------- 标准库执行器

def run_stdlib(state: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    """按 §51 的图顺序执行（与 langgraph 版本共用节点与路由）。"""
    for name in ("evidence", "trend_analyst", "relevance"):
        state.update(NODES[name](state, ctx))
        if state.get("status") == "archived":
            return state

    route = route_after_relevance(state)
    state["route"] = route
    if route == "archive":
        state["status"] = "archived"
        ctx.log("gate.relevance", ok=True, route="archive")
        return state

    # Evidence Gate（§15）
    pack = state.get("evidence_pack") or {}
    suff = N_research.evidence_sufficiency(pack)
    if route_evidence_gate(state, suff) == "research":
        state["research_required"] = True
        state.update(NODES["research"](state, ctx))

    for name in ("audience", "opportunity", "strategist", "creative"):
        state.update(NODES[name](state, ctx))

    # §31 有界修订环
    for _ in range(MAX_ITERATION + 1):
        state.update(NODES["evaluator"](state, ctx))
        if route_quality(state) == "pass":
            break
        if int(state.get("iteration", 0)) >= MAX_ITERATION:
            ctx.log("gate.quality", ok=False, reason="达到 MAX_ITERATION，停止修订")
            break
        state.update(NODES["revise"](state, ctx))

    state.update(NODES["risk"](state, ctx))
    state.update(NODES["human_review"](state, ctx))
    return state


# ---------------------------------------------------------------- 真 LangGraph

def build_langgraph(ctx: Ctx):
    """装了 langgraph 才返回编译好的图；没装抛 RuntimeError（不降级成假图）。"""
    if not HAS_LANGGRAPH:
        raise RuntimeError("langgraph 未安装：pip install langgraph langgraph-checkpoint-sqlite")
    from langgraph.graph import END, START, StateGraph
    from ..state import IntelligenceState

    g = StateGraph(IntelligenceState)

    def wrap(fn: NodeFn):
        def _fn(state: Dict[str, Any]) -> Dict[str, Any]:
            return fn(state, ctx)
        return _fn

    for name, fn in NODES.items():
        g.add_node(name, wrap(fn))

    g.add_edge(START, "evidence")
    g.add_edge("evidence", "trend_analyst")
    g.add_edge("trend_analyst", "relevance")
    g.add_conditional_edges("relevance", route_after_relevance,
                            {"archive": END, "light_analysis": "audience", "opportunity": "audience"})
    # Evidence Gate 放在 audience 前：不足则先 research
    g.add_conditional_edges(
        "audience",
        lambda s: route_evidence_gate(s, N_research.evidence_sufficiency(s.get("evidence_pack") or {})),
        {"research": "research", "opportunity": "opportunity"})
    g.add_edge("research", "opportunity")
    g.add_edge("opportunity", "strategist")
    g.add_edge("strategist", "creative")
    g.add_edge("creative", "evaluator")
    g.add_conditional_edges("evaluator", route_quality,
                            {"pass": "risk", "revise": "revise"})
    g.add_edge("revise", "evaluator")
    g.add_edge("risk", "human_review")
    g.add_conditional_edges("human_review", route_after_review,
                            {"publish": END, "end": END})
    return g.compile()


def run(state: Dict[str, Any], ctx: Ctx, engine: str = "auto") -> Dict[str, Any]:
    """统一入口。engine=auto → 有 langgraph 用真图，否则标准库执行器。"""
    if engine == "langgraph":
        app = build_langgraph(ctx)
        out = dict(app.invoke(state))
        out["engine"] = "langgraph"
    elif engine == "stdlib":
        out = run_stdlib(state, ctx)
        out["engine"] = "stdlib_fallback"
    else:
        if HAS_LANGGRAPH:
            app = build_langgraph(ctx)
            out = dict(app.invoke(state))
            out["engine"] = "langgraph"
        else:
            out = run_stdlib(state, ctx)
            out["engine"] = "stdlib_fallback"
    out["graph_version"] = GRAPH_VERSION
    out["langgraph_installed"] = HAS_LANGGRAPH
    # ★★ `llm_used` = **是否真的采纳了模型输出**，不是"是否尝试过调用"。
    #    实测：额度熔断 → 0 次调用成功、全部回退规则，产物却标 llm_used=True。
    #    项目纪律「AI 参与度 = 唯一进度指标」+「不许 overclaim」→ 这个字段不能自欺：
    #    以 usage.calls 为准，并把各节点实际 mode 统计一并写进产物。
    ok_calls = int((ctx.router.usage.as_dict() or {}).get("calls") or 0)
    out["llm_enabled"] = bool(getattr(ctx.router, "enabled", False))
    out["llm_used"] = ok_calls > 0
    out["llm_calls_ok"] = ok_calls
    modes = [str((out.get(k) or {}).get("mode") or "") for k in ("trend_analysis", "relevance", "risk")]
    for k in ("audiences", "opportunities", "creatives"):
        modes += [str(x.get("mode") or "") for x in (out.get(k) or [])]
    modes += [str(x.get("mode") or "") for x in ((out.get("evaluation") or {}).get("items") or [])]
    events = list(getattr(ctx.router, "events", None) or [])
    if events:
        out["llm_events"] = events[:60]
        summary: Dict[str, int] = {}
        for e in events:
            k = str(e.get("kind") or "?")
            summary[k] = summary.get(k, 0) + 1
        out["llm_event_summary"] = summary
        # 「规则兜底/失败」类事件单列，一眼看出这一跑到底掉了几次
        out["llm_degraded_events"] = [
            {"node": e.get("node"), "kind": e.get("kind"), "reason": e.get("reason")}
            for e in events if e.get("kind") in
            ("parse_failed", "call_failed", "quota_exhausted", "soft_empty",
             "unwrapped_single", "retry", "fallback")][:20]
    out["llm_single_model"] = bool(getattr(ctx.router, "single_model", False))
    # §40 要求 evaluator 与 creative 尽量不同模型；固定单模型时该性质不成立 → 如实标 False
    out["llm_evaluator_independent"] = not bool(getattr(ctx.router, "single_model", False))
    out["llm_adoption"] = {
        "llm": sum(1 for m in modes if m == "llm"),
        "rule": sum(1 for m in modes if m.startswith("rule")),
        "total": len(modes),
    }
    if getattr(ctx.router, "quota_exhausted", False):
        out["llm_quota_exhausted"] = True
        out["llm_quota_reason"] = ctx.router.quota_reason[:200]
    out["llm_usage"] = ctx.router.usage.as_dict()
    if ctx.router.last_errors:
        out["llm_errors"] = ctx.router.last_errors[:8]
    if ctx.router.last_raw:
        out["llm_raw_on_failure"] = {k: v[:600] for k, v in list(ctx.router.last_raw.items())[:3]}
    out["state_violations"] = assert_state_clean(
        {k: v for k, v in out.items() if k not in ("evidence_pack",)})
    return out
