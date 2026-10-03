# -*- coding: utf-8 -*-
"""Command layer —— 斜杠命令（coding-agent 交互范式）。

★ 为什么加它：UI 只有按钮的话，动作空间被钉死在页面上那几个按钮；
  命令层把**动作与界面解耦**——今天 CLI 敲 /approve，明天 UI 加按钮，后端零改动。
  解析 → 调 services（复用 L6 模块）→ 广播 → 时间线刷新。

★ 诚实边界：本层是**确定性命令解析**，不是 LLM 对话。
  「自然语言 → 动作」需要模型 + 兜底 + 审计三件套，那是路线图后续；
  现在不假装有：命令不认识就报 usage，不猜。
"""

from __future__ import annotations

import shlex
from typing import Any, Dict, List, Optional, Tuple

USAGE: Dict[str, str] = {
    "follow":        "/follow <event_id> [--owner name] [--deadline ISO]",
    "submit":        "/submit <creative_id>        # AI_READY → REVIEWING（operator 即可）",
    "approve":       "/approve <creative_id> [--note reason]",
    "reject":        "/reject <creative_id> [--note reason]",
    "too-late":      "/too-late <creative_id> [--note reason]",
    "not-relevant":  "/not-relevant <creative_id> [--note reason]",
    "research":      "/research <creative_id>        # need more research（reviewer）",
    "assign":        "/assign [--owner n] [--reviewer n] [--deadline ISO]   (选中对象)",
    "assets":        "/assets <creative_id> [--kinds push_copy,campaign_brief]",
    "plan":          "/plan <creative_id> [--channels a,b] [--experiment]",
    "launch":        "/launch <plan_id>",
    "pause":         "/pause <plan_id>",
    "resume":        "/resume <plan_id>",
    "stop":          "/stop <plan_id> [--reason reason]",
    "complete":      "/complete <plan_id>",
    "observe":       "/observe <exp_id> --metric m --class primary|secondary|guardrail"
                     " --xt 68 --nt 1000 [--xc 42 --nc 1000]"
                     " | --base 0.10 --value 0.17",
    "finish":        "/finish <exp_id>      # 结果五态判定（§30）",
    "ack":           "/ack <alert_id>",
    "set":           "/set [--dev 0] [--design 1] [--ops 3] [--budget low] [--ttl 24]",
    "help":          "/help",
}

_DECISION = {"approve": "approve", "reject": "reject", "too-late": "too_late",
             "not-relevant": "not_relevant", "research": "need_more_research"}


def parse_args(raw: str) -> Tuple[List[str], Dict[str, str]]:
    """`/approve idea_2 --note "机制清晰"` → (['idea_2'], {'note': '机制清晰'})"""
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()
    positional: List[str] = []
    flags: Dict[str, str] = {}
    i = 0
    while i < len(parts):
        p = parts[i]
        if p.startswith("--"):
            key = p[2:]
            if i + 1 < len(parts) and not parts[i + 1].startswith("--"):
                flags[key] = parts[i + 1]
                i += 2
            else:
                flags[key] = "true"
                i += 1
        else:
            positional.append(p)
            i += 1
    return positional, flags


def _usage(msg: str) -> Dict[str, Any]:
    return {"ok": False, "kind": "usage", "message": msg}


def run(raw: str, user: Dict[str, Any],
        selected: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """执行一条命令 → {ok, kind, message, data?}。

    ★ 权限不在这里判：services → L6 模块的 require_role 才是唯一边界（§43）。
    """
    import webapp.services as S
    from execution import ops_context

    text = (raw or "").strip()
    if not text:
        return {"ok": False, "kind": "empty", "message": "输入命令；/help 查看用法"}
    if not text.startswith("/"):
        return {"ok": False, "kind": "not_command",
                "message": "命令以 / 开头（/help 看全部用法）"}
    parts = text[1:].split()
    name = parts[0].lower() if parts else "help"
    args, flags = parse_args(" ".join(parts[1:]))
    sel = selected or {}
    actor, role = user["actor"], user["role"]
    pick = lambda i, key: args[i] if len(args) > i else sel.get(key)   # noqa: E731

    try:
        if name in ("help", "h", "?"):
            return {"ok": True, "kind": "help", "data": USAGE,
                    "message": "\n".join(USAGE.values())}

        # ---------------- 决策（§37）----------------
        if name in _DECISION:
            oid = pick(0, "creative_id")
            if not oid:
                return _usage(f"缺少 creative_id —— 用法 {USAGE[name]}")
            out = S.decide("creative", oid, _DECISION[name], actor, role,
                           note=flags.get("note", ""))
            return {"ok": True, "kind": "decide", "data": out,
                    "message": f"{oid}: {_DECISION[name]} → {out['to']}"}

        # ---------------- 跟进 / 指派 ----------------
        if name == "follow":
            eid = pick(0, "event_id")
            if not eid:
                return _usage(f"缺少 event_id —— 用法 {USAGE['follow']}")
            out = S.follow_event(eid, actor, role, owner=flags.get("owner"),
                                 deadline=flags.get("deadline"))
            extra = f"，owner={flags['owner']}" if flags.get("owner") else ""
            return {"ok": True, "kind": "follow", "data": out,
                    "message": f"{eid}: follow → {out['to']}{extra}"}

        if name == "assign":
            ot = sel.get("object_type") or "event"
            oi = pick(0, "object_id") or sel.get("event_id")
            if not oi:
                return _usage("缺少目标 —— 先在页面上选中对象")
            if not any(flags.get(k) for k in ("owner", "reviewer", "deadline")):
                return _usage(f"至少给 --owner/--reviewer/--deadline 之一 —— 用法 {USAGE['assign']}")
            out = S.assign(ot, oi, flags.get("owner"), flags.get("reviewer"),
                           flags.get("deadline"), actor, role)
            return {"ok": True, "kind": "assign", "data": out,
                    "message": f"{oi}: owner={out['owner']} reviewer={out['reviewer']}"}

        # ---------------- 创意 → 素材 → 计划 → 上线 ----------------
        if name == "assets":
            oid = pick(0, "creative_id")
            if not oid:
                return _usage(f"缺少 creative_id —— 用法 {USAGE['assets']}")
            kinds = [k for k in (flags.get("kinds") or "").split(",") if k] or None
            out = S.generate_assets(oid, kinds, actor, role)
            return {"ok": True, "kind": "assets", "data": out,
                    "message": "生成 " + "、".join(f"{a['kind']} v{a['version']}" for a in out)}

        if name == "plan":
            oid = pick(0, "creative_id")
            if not oid:
                return _usage(f"缺少 creative_id —— 用法 {USAGE['plan']}")
            channels = [c for c in (flags.get("channels") or "community_feed").split(",") if c]
            out = S.create_plan(oid, channels, flags.get("audience", "active_users"),
                                flags.get("start", ""), flags.get("end", ""),
                                flags.get("experiment") == "true", actor, role)
            msg = f"计划 {out['plan_id']} created"
            if "experiment" in out:
                msg += f" + experiment {out['experiment']['experiment_id']}"
            return {"ok": True, "kind": "plan", "data": out, "message": msg}

        if name in ("launch", "pause", "resume", "stop", "complete"):
            pid = pick(0, "plan_id")
            if not pid:
                return _usage(f"缺少 plan_id —— 用法 {USAGE[name]}")
            action = {"launch": "launch", "pause": "pause", "resume": "launch",
                      "stop": "stop", "complete": "complete"}[name]
            out = S.plan_transition(pid, action, actor, role,
                                    reason=flags.get("reason", name))
            return {"ok": True, "kind": f"plan_{action}", "data": out,
                    "message": f"{pid}: {action} → {out['status']}"}

        # ---------------- 实验 ----------------
        if name == "observe":
            eid = pick(0, "experiment_id")
            if not eid:
                return _usage(f"缺少 experiment_id —— 用法 {USAGE['observe']}")
            metric = flags.get("metric")
            cls = flags.get("class", "primary")
            if not metric:
                return _usage("需要 --metric（指标名）")
            body: Dict[str, Any] = {"metric_name": metric, "metric_class": cls}
            if flags.get("xt") is not None:
                body.update({"x_t": int(flags["xt"]), "n_t": int(flags.get("nt", 0)),
                             "x_c": int(flags.get("xc", 0)), "n_c": int(flags.get("nc", 0))})
            elif flags.get("base") is not None:
                body.update({"baseline_value": float(flags["base"]),
                             "treatment_value": float(flags.get("value", 0))})
            else:
                return _usage("需要比例（--xt/--nt）或数值（--base/--value）观测")
            out = S.observe_experiment(eid, metric, cls, body, actor, role)
            return {"ok": True, "kind": "observe", "data": out,
                    "message": f"{metric} ({cls}) lift="
                               f"{out['relative_lift'] if out['relative_lift'] is not None else '—'}"
                               f" p={out['p_value'] if out['p_value'] is not None else '—'}"}

        if name == "finish":
            eid = pick(0, "experiment_id")
            if not eid:
                return _usage(f"缺少 experiment_id —— 用法 {USAGE['finish']}")
            out = S.finish_experiment(eid, actor, role)
            return {"ok": True, "kind": "experiment_finish", "data": out,
                    "message": f"{eid}: {out['result_state']} —— {out['reason']}"}

        # ---------------- 告警 ----------------
        if name == "ack":
            aid = pick(0, "alert_id")
            if not aid:
                return _usage(f"缺少 alert_id —— 用法 {USAGE['ack']}")
            out = S.ack_alert(aid, actor, role)
            return {"ok": True, "kind": "ack", "data": out,
                    "message": f"{aid} acknowledged by {actor}"}

        # ---------------- 设置：运营约束（§39）----------------
        if name == "set":
            from execution.audit import require_role
            require_role(role, "configure")          # 403 → admin only（§43）
            cur = ops_context.load()
            res = cur.get("resources", {})
            path = ops_context.save(
                resources={"design": int(flags.get("design", res.get("design", 1))),
                           "dev": int(flags.get("dev", res.get("dev", 0))),
                           "ops": int(flags.get("ops", res.get("ops", 3)))},
                slots_24h=cur.get("slots_24h") or {"push": 2, "homepage_banner": 1},
                budget_level=flags.get("budget", cur.get("budget_level", "low")),
                channels_allowed=flags.get("channels", "").split(",")
                if flags.get("channels") else cur.get("channels_allowed"),
                ttl_hours=float(flags.get("ttl", 24)),
                notes=cur.get("notes", ""))
            return {"ok": True, "kind": "set", "data": ops_context.load(),
                    "message": f"ops context updated ({path})"}

        return _usage(f"未知命令 /{name} —— /help 看用法")
    except Exception as e:                            # 统一成结构化结果，UI 显示 detail
        kind = type(e).__name__
        return {"ok": False, "kind": kind.lower(), "message": str(e)}