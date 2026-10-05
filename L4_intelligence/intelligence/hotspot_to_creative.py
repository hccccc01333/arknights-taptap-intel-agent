#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L4_intelligence/intelligence/hotspot_to_creative.py
   —— 端到端验证：外部热点 → 增长机会 → 增长创意。

★ 为什么有这层桥（2026-10-05 审查）：
  L4 的 opportunity/creative 节点吃的是 `state`（evidence_pack + relevance +
  audiences），上游设计是 L3 的站内事件。而我们现在的主线信号是**站外热点**，
  两者对不上 —— 所以需要一个桥：把「站外正在形成的热点 + 它在 TapTap 的对应讨论」
  组装成 opportunity 节点认得的 state。

★ 这不是最终形态，是**验证**：跑一遍看输出到底是"运营今天能动手的动作"
  还是"正确的废话"。不行就说明缺的是判据而不是模块。

用法：
    python hotspot_to_creative.py                 # 用当前 related 热点跑
    python hotspot_to_creative.py --top 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_L4 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L4)
for _p in (_L4, _ROOT, os.path.join(_ROOT, "L3_trend"),
           os.path.join(_ROOT, "L6_execution")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RELEVANCE_JSON = os.path.join(_ROOT, "data", "state", "hotspot_relevance.json")
EVENTS_JSON = os.path.join(_ROOT, "data", "state", "hot_events.json")
THREADS_JSON = os.path.join(_ROOT, "data", "state", "community_reports.json")


# 游戏识别（归因用）：从 games/*.json 拿 name/aliases，粗粒度足够
def load_game_aliases() -> Dict[str, str]:
    """{别名: 正式游戏名}。用于跨游戏归因——鸣潮的热点只能配鸣潮的语境。"""
    out: Dict[str, str] = {}
    gdir = os.path.join(_ROOT, "games")
    if not os.path.isdir(gdir):
        return out
    for fn in os.listdir(gdir):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(gdir, fn), encoding="utf-8") as f:
                d = json.load(f)
        except (ValueError, OSError):
            continue
        name = d.get("name")
        if not name:
            continue
        for k in [name, d.get("key")] + list(d.get("aliases") or []):
            if k:
                out[str(k).lower()] = name
    return out


GAME_ALIASES = load_game_aliases()


def detect_game(text: str) -> str:
    """一段文本里出现的是哪个游戏（没识别出来返回空）。长别名优先。"""
    low = (text or "").lower()
    for alias in sorted(GAME_ALIASES, key=len, reverse=True):
        if alias and alias in low:
            return GAME_ALIASES[alias]
    return ""


def attribute_context(hot: Dict[str, Any], events: List[Dict[str, Any]]
                      ) -> Dict[str, Any]:
    """给热点配 TapTap 站内语境，**按游戏归因，错配就空手**。

    ★ 为什么不能"匹配不到就回退取全局前 N 条"（2026-10-05 实测踩到）：
      鸣潮的热点配上明日方舟的攻略帖 → 照此投放就是事故。
      **宁可没有语境（提示"该热点在站内无对应讨论"），也不能串味。**
    """
    hot_game = detect_game((hot.get("title") or "") + " " + (hot.get("reason") or ""))
    same = [e for e in events if detect_game(e.get("title") or "") == hot_game] if hot_game else []
    # 站内帖没写游戏名时不能瞎猜 —— 只有明确同游戏才算
    return {"hot_game": hot_game,
            "events": same,
            "note": ("" if same else
                     (f"「{hot_game}」热点在 TapTap 站内没有对应讨论，需人工确认语境"
                      if hot_game else "热点未识别出具体游戏，无法自动匹配站内语境"))}


def load_events(top: int = 5) -> List[Dict[str, Any]]:
    """载入**跨平台合并后**的事件（event_resolver.py 的产物）。

    ★ 这是"全网追踪"相对"单平台抓取"的唯一结构性优势：
      同一件事在微博/百度/B站各有一条，合并成一个事件只出一次创意，
      并带上「哪个平台先起、多久跟进」的时间轴。
    """
    if not os.path.exists(EVENTS_JSON):
        return []
    with open(EVENTS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    evs = [e for e in (data.get("events") or []) if e.get("cross_platform")]
    return evs[:top]


def load_related(top: int = 6) -> List[Dict[str, Any]]:
    if not os.path.exists(RELEVANCE_JSON):
        return []
    with open(RELEVANCE_JSON, encoding="utf-8") as f:
        data = json.load(f)
    return [v for v in (data.get("verdicts") or []) if v.get("verdict") == "related"][:top]


def build_state(hot: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把一个外部热点组装成 opportunity 节点认得的 state。

    ★ 事实全部来自我们已采到的数据（热度/排名/趋势/理由），
      **不编数字** —— 创意节点生成的每条都要能回溯到这里。
    """
    evid: List[Dict[str, Any]] = []
    for e in events[:6]:
        evid.append({
            "evidence_id": f"ev_{e.get('event_id')}",
            "excerpt": e.get("title") or "",
            "tier": "COMMUNITY",
            "source_platform": e.get("platform") or "taptap",
        })
    return {
        "evidence_pack": {
            "event": {
                "event_id": f"hs_{abs(hash(hot.get('id', ''))) % 10**10}",
                "canonical_title": hot.get("title") or "",
                "lifecycle": "EMERGING",          # 站外新进榜 = 形成中
                "novelty_score": 0.8,            # 新词/在涨 = 新颖
                "confidence": 0.75,
                "event_type": "external_hotspot",
            },
            "evidence": evid,
            "n_members": len(events),
            "platforms": {"external": 1, "taptap": max(1, len(events))},
            "primary_ratio": 0.6,
        },
        # 相关性维度：站外热点的天然优势就是"站外扩散"，劣势是"不确定能否挂到具体游戏"
        "relevance": {
            "score": 0.72,
            "dimensions": {"timing": 0.9, "platform": 0.8, "user": 0.7, "content": 0.6},
            "reason": f"站外热度：{hot.get('reason', '')}",
        },
        # ★ 字段名/动机 ID 必须对齐 knowledge.py 的真实表：
        #   节点读 segment/motivation/motivation_id；动机 ID 是 meme/info_seeking 这类，
        #   自己编 "m_play" 会匹配不到 MOTIVATION_BY_ID → 创意类型退化成兜底。
        "audiences": [
            {"segment": "玩梗/吃瓜人群", "motivation": "玩梗", "motivation_id": "meme", "share": 0.35},
            {"segment": "找攻略的核心玩家", "motivation": "获取信息与攻略", "motivation_id": "info_seeking", "share": 0.35},
            {"segment": "社区归属感人群", "motivation": "参与社区", "motivation_id": "community_belonging", "share": 0.3},
        ],
    }


def timeliness_gate(hot: Dict[str, Any], ideas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """★ 时效闸门（2026-10-05）：创意不是想出来就能上，得来得及。

    Push 的提前期是 2 小时、热点专题 4 小时、厂商合作 72 小时。
    一个已经火了三天的热点去建议"厂商合作"= 正确的废话。
    所以每条创意都要过：热点年龄 vs 该类型的提前期，赶不上就标 window_missed。
    """
    from intelligence.knowledge import CREATIVE_TYPES

    obs = hot.get("observed_at") or ""
    age_h = None
    try:
        if obs:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(obs)).total_seconds() / 3600
    except (ValueError, TypeError):
        age_h = None
    if age_h is None:
        return {"hot_age_h": None, "viable": len(ideas), "missed": [],
                "note": "热点无采集时刻，无法校验时效（按可行处理，但要知道这一点）"}

    viable, missed = [], []
    for c in ideas:
        ct = c.get("creative_type") or c.get("type")
        lead = float((CREATIVE_TYPES.get(ct) or {}).get("lead_time_hours", 24))
        c["lead_time_h"] = lead
        c["hot_age_h"] = round(age_h, 1)
        # 留 1/3 缓冲：创意本身也要时间做，热点等不了
        if lead * 1.34 <= age_h:
            c["window_missed"] = True
            missed.append(c)
        else:
            c["window_missed"] = False
            viable.append(c)
    return {"hot_age_h": round(age_h, 1), "viable": len(viable), "missed": missed,
            "note": ""}



# ============================================================ 结构化创意 schema
# ★ 为什么要定死结构（2026-10-05 用户要求："最好结构化输出，方便后续展示"）：
#   LLM 自由输出的文本没法渲染、没法比对、没法进工作流。
#   下面这份是**前端契约** —— webapp 的创意卡片按这几个字段渲染，
#   新增字段只改这里和前端，不改 LLM 提示词。
CREATIVE_SCHEMA: Dict[str, Any] = {
    "idea_id": "",                  # 稳定 id（热点+类型派生，便于去重/追踪）
    "name": "",                     # 创意名，必须含热点关键词
    "creative_type": "",            # content|community|ugc|crm|h5|social|creator|publisher
    "audience": "",                 # 面向谁
    "hotspot": {"title": "", "platform": "", "game": "", "reason": ""},
    "execution": {
        "where": "",                # 在哪做（TapTap 具体位置）
        "steps": [],                # 步骤（可执行，不是抽象描述）
        "owner": "",                # 谁做：设计/运营/研发
        "cost": "",                 # low|medium|high
        "lead_time_hours": 0,       # 从决定到上线要多久
        "window_missed": False,     # 时效闸门判定：赶不赶得上
    },
    "copy": {
        "headline": "",             # 主标题/文案主句
        "body": "",                 # 展开
        "push_title": "",           # Push 标题（crm 类型必填）
    },
    "assets": [],                   # 素材清单：[{type,desc,source}]
    "primary_metric": "",           # 观测指标
    "kpi_target": "",               # 目标值（LLM 不知道就说空，不许编）
    "risks": [],                    # [{level,warning}]
    "evidence": [],                 # 溯源：热点关键词 + 站内语境帖
    "generated_by": "llm",
}


def _empty_creative() -> Dict[str, Any]:
    return json.loads(json.dumps(CREATIVE_SCHEMA))     # 深拷贝


def normalize_creative(raw: Dict[str, Any], hot: Dict[str, Any],
                       game: str, ctype: str, lead_h: float,
                       window_missed: bool, evidence: List[str]) -> Dict[str, Any]:
    """把 LLM 的自由输出收敛成 CREATIVE_SCHEMA。

    ★ 缺失字段一律留空/默认值，**不用默认值伪造事实**
      （比如 kpi_target 宁可空着，运营自己填，也不要 LLM 编一个数）。
    """
    c = _empty_creative()
    c["idea_id"] = f"hc_{abs(hash((hot.get('id') or '') + ctype)) % 10**8}"
    c["name"] = str(raw.get("name") or raw.get("idea_name") or "").strip()[:60]
    c["creative_type"] = str(raw.get("creative_type") or ctype or "content").strip()
    c["audience"] = str(raw.get("audience") or "").strip()[:40]
    c["hotspot"] = {"title": (hot.get("title") or "")[:60],
                    "platform": hot.get("platform") or "",
                    "game": game or "", "reason": (hot.get("reason") or "")[:80]}

    ex = c["execution"]
    ex["where"] = str(raw.get("where") or raw.get("placement") or "").strip()[:80]
    steps = raw.get("steps") or []
    if isinstance(steps, str):
        steps = [steps]
    ex["steps"] = [str(s).strip()[:100] for s in steps if str(s).strip()][:5]
    action = str(raw.get("action") or "").strip()
    if action and not ex["steps"]:
        ex["steps"] = [action[:140]]
    if action and not ex["where"]:
        # 从动作描述里抠位置线索（"在TapTap鸣潮游戏页顶部Banner位" → where）
        m = re.search("(TapTap[^，。；]{0,30}(?:页|位|区|池|入口|话题))", action)
        ex["where"] = m.group(1)[:60] if m else ""
    ex["owner"] = str(raw.get("owner") or "").strip()[:30]
    ex["cost"] = str(raw.get("cost") or "").strip()[:6]
    ex["lead_time_hours"] = lead_h
    ex["window_missed"] = bool(window_missed)

    cp = c["copy"]
    if isinstance(raw.get("copy"), dict):
        cp["headline"] = str(raw["copy"].get("headline") or "").strip()[:80]
        cp["body"] = str(raw["copy"].get("body") or "").strip()[:300]
        cp["push_title"] = str(raw["copy"].get("push_title") or "").strip()[:40]
    else:
        txt = str(raw.get("copy") or raw.get("headline") or "").strip()
        cp["headline"] = txt[:80]
        cp["body"] = txt[:300] if len(txt) > 80 else ""
    if c["creative_type"] == "crm" and not cp["push_title"]:
        cp["push_title"] = cp["headline"][:40]

    assets = raw.get("assets") or []
    c["assets"] = ([{"type": str(a.get("type") or ""), "desc": str(a.get("desc") or "")[:80],
                     "source": str(a.get("source") or "")[:40]} for a in assets
                    if isinstance(a, dict)][:6]
                   if isinstance(assets, list) else [])
    c["primary_metric"] = str(raw.get("primary_metric") or "").strip()[:24]
    c["kpi_target"] = str(raw.get("kpi_target") or "").strip()[:24]   # 空就是空，不编

    risks = raw.get("risks") or raw.get("risk") or []
    if isinstance(risks, str):
        risks = [risks]
    c["risks"] = [{"level": (r.get("level") if isinstance(r, dict) else "medium"),
                   "warning": str(r.get("warning") if isinstance(r, dict) else r)[:100]}
                  for r in risks if str(r).strip()][:4]
    c["evidence"] = evidence
    return c


# ============================================================ ① 热点→具体动作（LLM）
# ★ 为什么必须有这层（2026-10-05 实测）：
#   纯规则生成出来的创意**两个不同热点完全一样**——"丹瑾配音"和"0.1版本PV"
#   都只产出「热点专题/讨论活动/Push 触达 × 2 人群」，因为规则只用了
#   增长目标 + 人群 + 窗口三个维度，**热点的具体内容根本没进去**。
#   运营拿到会问：所以我到底发什么？
#   这一层让 LLM 看着热点原文和站内语境，给出「具体发什么」。

CREATIVE_PROMPT = """你是 TapTap（游戏社区）的增长创意策划。

现在有一个**正在形成的外部热点**，请为 TapTap 设计可以直接执行的运营动作。

【热点】{title}
【来源平台】{platform}
【为什么判定它与我们相关】{reason}
【TapTap 站内语境（同一游戏的讨论）】
{context}

【可用的人群体】{audiences}
【可用的创意类型及提前期】{types}
【团队资源】设计 {design} 人 / 研发 {dev} 人 / 运营 {ops} 人 / 预算 {budget}
（研发为 0 时不要出现需要开发新功能的方案）

【硬性要求】
1. 每条创意必须**针对这个具体热点**，说得出"发什么内容/推什么文案/做什么话题"，
   不许输出"做一个专题讨论"这种放之四海皆准的废话；
2. 必须符合提前期和团队资源约束，做不到的不要写；
3. 每条给：一句能直接用的文案/标题、可执行动作（在哪做、做什么）、预期指标、
   风险（可能出什么问题）。

【本次只做这一种】{one_type}（{one_lead}h 提前期）

严格按 JSON 输出（只输出 1 条，不要数组）：
{{"name":"创意名（必须含热点关键词）",
  "creative_type":"{one_type}",
  "audience":"面向谁",
  "where":"在 TapTap 的哪个具体位置做（页面/位/入口）",
  "steps":["可执行步骤1","步骤2"],
  "owner":"谁来（设计/运营）",
  "copy":"可以直接用的文案主句",
  "assets":[{{"type":"image|video|h5|copy","desc":"需要什么素材","source":"素材从哪来（B站原视频/UP主/自己画）"}}],
  "primary_metric":"engagement_rate|reach|install|game_follow|ugc",
  "kpi_target":"不知道就留空字符串，不许编数字",
  "risks":[{{"level":"high|medium|low","warning":"风险"}}]
}}"""


def llm_creatives(hot: Dict[str, Any], events: List[Dict[str, Any]],
                  audiences: List[str], enabled: bool = True,
                  want_types: Optional[List[str]] = None,
                  hot_age_h: Optional[float] = None,
                  game: str = "") -> List[Dict[str, Any]]:
    """针对具体热点生成**结构化**创意。

    ★ 为什么逐类型循环生成（2026-10-05 实测）：
      免费模型一次只做好**一条** —— 问它"给 5 条"时，它会把 2000 token
      全花在第 1 条上，后面 4 条截断/糊弄。改成「一次只做一种类型」后：
        · 每条都有完整 token 预算 → 质量稳定
        · 类型不同 → 动作天然多样（不会三条都是"做个专题"）
        · 顺带满足时效闸门：赶不上的类型直接不生成，省调用

    返回结构见 CREATIVE_SCHEMA（前端按那个字段渲染）。
    """
    if not enabled:
        return []
    try:
        # ★ 必须先加载 .env：脚本直接跑时环境里没有 FREE_API_KEY，
        #   Router 会判定 enabled=False 然后静默返回空（看起来像"LLM 不可用"）
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(_ROOT, ".env"))
            load_dotenv(os.path.join(_ROOT, "common", ".env"))
        except ImportError:
            pass
        from intelligence.llm import ModelRouter
        from intelligence.knowledge import CREATIVE_TYPES
        from execution import ops_context
        router = ModelRouter(enabled=True)
        if not router.enabled:
            print(f"[llm] Router 判定不可用（last_errors={router.last_errors[:2]}）", file=sys.stderr)
            return []
        ctx = ops_context.load()
        res = ctx.get("resources") or {}

        # 只做「低成本 + 来得及」的类型：没研发就别让模型提开发方案
        types: List[str] = []
        blocked: List[str] = []
        for t in (want_types or ["content", "community", "crm", "social"]):
            spec = CREATIVE_TYPES.get(t) or {}
            if spec.get("cost") == "high" and int(res.get("dev", 0)) == 0:
                continue
            lead = float(spec.get("lead_time_hours", 24))
            if hot_age_h is not None and lead * 1.34 <= hot_age_h:
                blocked.append(t)             # 赶不上 → 压根不生成（不做无用功）
                continue
            types.append(t)
        if not types:
            print(f"[llm] 热点已 {round(hot_age_h,1) if hot_age_h else '?'}h，"
                  f"所有创意类型都赶不上（{blocked}），不生成 LLM 创意", file=sys.stderr)
            return []

        type_txt = "、".join(
            "{}({}h{})".format(v.get("name"), v.get("lead_time_hours"),
                               " 低成本" if v.get("cost") == "low" else " 中成本")
            for v in CREATIVE_TYPES.values())
        ctx_txt = "\n".join("  - " + (e.get("title") or "")[:60] for e in events[:5]) \
            or "  （TapTap 站内暂无对应讨论，请基于热点本身策划）"
        evidence = ["热点:" + (hot.get("title") or "")[:50]] + \
                   ["站内:" + (e.get("title") or "")[:50] for e in events[:3]]

        out_items: List[Dict[str, Any]] = []
        for t in types:
            spec = CREATIVE_TYPES.get(t) or {}
            lead = float(spec.get("lead_time_hours", 24))
            prompt = CREATIVE_PROMPT.format(
                title=hot.get("title"), platform=hot.get("platform"),
                reason=hot.get("reason"), context=ctx_txt,
                audiences="、".join(audiences), types=type_txt,
                design=res.get("design", 0), dev=res.get("dev", 0),
                ops=res.get("ops", 0), budget=ctx.get("budget_level", "low"),
                one_type=t, one_lead=lead)
            try:
                out = router.call_json("creative", prompt, max_tokens=1600)
            except Exception as e:
                print(f"[llm] 类型 {t} 生成失败：{str(e)[:60]}", file=sys.stderr)
                continue
            raw = out if isinstance(out, dict) else {}
            raw = {k: v for k, v in raw.items() if k != "_llm"}
            if not (raw.get("name") or raw.get("action") or raw.get("steps")):
                print(f"[llm] 类型 {t} 返回空内容，跳过", file=sys.stderr)
                continue
            c = normalize_creative(raw, hot, game, t, lead, False, evidence)
            c["execution"]["cost"] = c["execution"]["cost"] or spec.get("cost", "")
            out_items.append(c)
            time.sleep(0.5)
        return out_items
    except Exception as e:
        print(f"[warn] LLM 创意生成失败（{str(e)[:60]}），回退规则版本", file=sys.stderr)
        return []


def _event_as_hotspot(ev: Dict[str, Any]) -> Dict[str, Any]:
    """把 Event 对象转成创意链路认识的 hotspot 形状（带上跨平台信息）。"""
    plats = "、".join(p.get("platform_zh") or p.get("platform") for p in ev.get("platforms") or [])
    tl = " → ".join("{}@{}".format(t.get("platform"), (t.get("at") or "")[11:16])
                     for t in (ev.get("timeline") or []))
    return {
        "id": ev.get("event_id"),
        "title": ev.get("title"),
        "platform": plats,
        "observed_at": ev.get("first_seen_at"),
        "reason": "跨平台合并事件（{}）：{}".format(ev.get("platform_count"), tl),
        "cross_platform": True,
        "platform_count": ev.get("platform_count"),
        "member_titles": [i.get("title") for p in (ev.get("platforms") or [])
                          for i in (p.get("items") or [])][:5],
    }


def run_one(hot: Dict[str, Any], events: List[Dict[str, Any]],
            game: str = "") -> Dict[str, Any]:
    from intelligence.nodes import opportunity as opp_node
    from intelligence.nodes import creative as cre_node
    from intelligence.knowledge import CREATIVE_TYPES as CREATIVE_COST

    state = build_state(hot, events)
    opps = opp_node.opportunity(state, max_opportunities=4)
    hyps = opp_node.strategist(state, opportunities=opps) or []
    ideas = cre_node.generate(state, opportunities=opps, hypotheses=hyps, max_per_opp=2)
    tl = timeliness_gate(hot, ideas)      # 先过闸门，再生成展示数据（标记才对得上）

    # ① LLM 针对该热点生成具体动作（规则版本继续保留作兜底/对照）
    llm_items = llm_creatives(
        hot, events, [a.get("segment") or "" for a in state["audiences"]],
        hot_age_h=(tl or {}).get("hot_age_h"), game=game or "")

    # 团队资源约束（L6 的运营上下文）：没研发就只有低成本方案
    from execution import ops_context
    ctx = ops_context.load()
    res = ctx.get("resources") or {}

    return {
        "hotspot": {"title": hot.get("title"), "platform": hot.get("platform"),
                    "reason": hot.get("reason")},
        "taptap_context": [e.get("title") for e in events[:3]],
        "opportunities": [{"id": o.get("opportunity_id"), "audience": o.get("audience"),
                           "goal": o.get("growth_goal"), "window": o.get("opportunity_window"),
                           "mechanism": (o.get("growth_mechanism") or "")[:40],
                           "score": o.get("opportunity_score"),
                           "platform_advantage": (o.get("platform_advantage") or "")[:40]}
                          for o in opps],
        "creatives": [{"name": c.get("idea_name"), "type": c.get("creative_type"),
                       "goal": c.get("primary_metric"), "window": c.get("launch_window"),
                       "cost": (CREATIVE_COST.get(c.get("creative_type")) or {}).get("cost"),
                       "mechanism": (c.get("growth_mechanism") or "")[:34],
                       "refs": len(c.get("source_refs") or []),
                       "hypothesis": (c.get("growth_hypothesis") or "")[:70],
                       "lead_time_h": c.get("lead_time_h"),
                       "window_missed": bool(c.get("window_missed"))}
                      for c in ideas],
        "timeliness": tl,
        "llm_creatives": llm_items,        # ★ 已是 CREATIVE_SCHEMA 结构，前端直接渲染
        "hypotheses": [h.get("growth_hypothesis") for h in hyps],
        "ops_context": {"resources": res, "budget": ctx.get("budget_level")},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="端到端验证：外部热点 → 增长创意")
    ap.add_argument("--top", type=int, default=3, help="跑前几个 related 热点")
    args = ap.parse_args()

    # 优先走「事件」：跨平台合并后的事件；没有就退回单热点
    events = load_events(args.top)
    unit_label = "跨平台事件" if events else "热点"
    units: List[Dict[str, Any]] = events or load_related(args.top)
    if not units:
        print("[skip] 没有可用的热点/事件。顺序："
              "① L3_trend/event_resolver.py（跨平台合并）"
              " ② L3_trend/hotspot_filter.py（相关性判定）")
        return 0

    # 每个热点配 TapTap 站内语境（用社区报告里的相关话题）
    ctx_events: List[Dict[str, Any]] = []
    if os.path.exists(THREADS_JSON):
        with open(THREADS_JSON, encoding="utf-8") as f:
            for rep in (json.load(f).get("reports") or []):
                ctx_events.extend(rep.get("events") or [])

    print(f"拿 {len(units)} 个{unit_label}跑 →机会→创意\n")
    for i, raw_unit in enumerate(units, 1):
        hot = _event_as_hotspot(raw_unit) if events else raw_unit
        # 语境匹配：优先取标题里含热点关键词的站内话题
        key = (hot.get("title") or "")[:6]
        attr = attribute_context(hot, ctx_events)
        ev = attr["events"][:6]
        r = run_one(hot, ev, attr.get("hot_game", ""))
        r["attribution"] = attr
        print("=" * 66)
        print(f"{i}. 【{r['hotspot']['platform']}】{r['hotspot']['title'][:38]}")
        print(f"   判定理由：{r['hotspot']['reason'][:52]}")
        at = r.get("attribution") or {}
        if at.get("hot_game"):
            print(f"   游戏归因：{at['hot_game']}", end="")
            print(f"（站内同游戏语境 {len(at['events'])} 条）" if at["events"]
                  else f" ⚠ {at.get('note','')}")
        if r["taptap_context"]:
            for t in r["taptap_context"][:3]:
                print(f"     · {t[:46]}")
        print(f"   → 增长机会 {len(r['opportunities'])} 条：")
        for o in r["opportunities"]:
            print(f"     · {o['audience']:<10} 目标={o['goal']:<20} 窗口={o['window']:<10} 机制={o['mechanism']} 分={o['score']}")
        tl = r.get("timeliness") or {}
        print(f"   时效闸门：热点已 {tl.get('hot_age_h')}h → 可行 {tl.get('viable')}/{len(r['creatives'])}"
              + (f" | 窗口已过 {len(tl['missed'])} 条" if tl.get("missed") else "")
              + (f" | {tl['note']}" if tl.get("note") else ""))
        print(f"   → 增长创意 {len(r['creatives'])} 条：")
        for c in r["creatives"]:
            flag = "✗窗口已过" if c.get("window_missed") else "✓赶得上"
            print(f"     {flag} {c['name'][:26]:<28} 类型={c['type']:<10} "
                  f"提前{c.get('lead_time_h')}h 成本={c['cost']} 溯源={c['refs']}条")
        for h in r["hypotheses"][:2]:
            if h:
                print(f"   假设：{str(h)[:66]}")
        lcs = r.get("llm_creatives") or []
        if lcs:
            print(f"   → LLM 结构化创意 {len(lcs)} 条：")
            for c in lcs:
                ex, cp = c["execution"], c["copy"]
                print(f"     ◆ [{c['creative_type']}] {c['name'][:36]}")
                print(f"       位置：{ex['where'] or '（未指定）'}  负责：{ex['owner'] or '—'}  "
                      f"成本：{ex['cost']}  提前：{ex['lead_time_hours']}h")
                for s in ex["steps"][:2]:
                    print(f"       步骤：{s[:76]}")
                print(f"       文案：{cp['headline'][:60]}")
                if cp["push_title"]:
                    print(f"       Push标题：{cp['push_title'][:40]}")
                if c["assets"]:
                    print(f"       素材：" + "、".join(
                        f"{a.get('type')}({a.get('desc','')[:18]})" for a in c["assets"][:3]))
                print(f"       指标：{c['primary_metric']}  目标：{c['kpi_target'] or '—'}")
                for rk in c["risks"][:2]:
                    print(f"       风险[{rk['level']}]：{rk['warning'][:60]}")
        else:
            print("   （LLM 不可用，以上为规则版本）")
        print(f"   团队约束：资源={r['ops_context']['resources']} 预算={r['ops_context']['budget']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())