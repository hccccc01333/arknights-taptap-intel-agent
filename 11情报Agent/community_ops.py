#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/community_ops.py — 社区运营模块（第一版 4 功能）。

定位：项目目标「服务游戏社区」的第一个落点。**不是给开发者看的日志，是给运营看的行动清单。**

四个功能（对应 docs/社区运营模块设计.md）：
  ① 话题机会   哪些话题现在值得做（抓窗口）
  ② 风险预警   哪里在吵、要不要介入（防事故）
  ③ 内容候选   哪些帖子/评论值得推（做内容）
  ④ 动作闭环   我做了没有 + 同话题不重复推（供校准）

设计纪律（沿用项目既有约定）：
  · 纯标准库（csv/json/sqlite3），不依赖 pandas
  · **每个数据源缺失 → 显式降级并写明原因**，绝不静默变成「无内容」
  · 数字全部来自上游 facts（代码算好的），本模块只做筛选与关联，不重算
  · 阈值全部标注「未校准」
  · **PII**：帖子/评论的作者名一律不进产出（溯源靠可点链接，不靠名字）

用法：
  python 11情报Agent/community_ops.py                  # 出运营日报
  python 11情报Agent/community_ops.py --json           # 机器可读
  python 11情报Agent/community_ops.py --act follow --key "hid:123|page_view"
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
LAB = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[1]

TOPIC_STATE = LAB / "outputs" / "topic_state.json"
FERMENT_JSON = LAB / "outputs" / "ferment_judge.json"
RISK_JSON = LAB / "outputs" / "risk_insight.json"
PLATFORM_JSON = LAB / "outputs" / "platform_insight.json"
POSTS_CSV = ROOT / "02数据_platform" / "discovery_posts.csv"
COMMENTS_CSV = ROOT / "02数据_platform" / "discovery_comments.csv"

OPS_DB = LAB / "state" / "community_ops.sqlite3"
REPORT_DIR = LAB / "reports"

# 可行动的状态（交付形态设计 §3.2 的推送门槛）
ACTIONABLE_STATES = ("升温", "爆发")
# 观察态：还不能行动，但值得盯（首日全冒头属正常——迁移需 ≥2 次采样）
WATCH_STATES = ("冒头",)

# ⚠️ 全部未校准：这些阈值是拍的，没有真实运营反馈校准过，不得当可信参数使用
RISK_NEG_RATE_MIN_PP = 50.0       # 负向率 ≥ 该值才入预警候选
RISK_HIGH_HOURS_MIN_PP = 60.0     # 高投入负向占比 ≥ 该值视为「真在乎」
CONTENT_MIN_LIKE = 1              # 内容候选的最低点赞（>0 才有社区认可信号）

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# 通用：读 JSON（缺失/损坏一律显式降级）
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "reason": f"未生成（{path.name}）"}
    try:
        return {"available": True, "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as e:
        return {"available": False, "reason": f"{path.name} 解析失败：{type(e).__name__}"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _int(v: Any) -> int:
    try:
        return int(str(v or "0").strip() or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# ① 话题机会：哪些话题现在值得做
# ---------------------------------------------------------------------------

def topic_opportunities(limit: int = 5) -> dict[str, Any]:
    """话题机会。

    **门控**（交付形态设计 §3.2）：只有「升温/爆发」才算可行动的机会——
    「冒头」首日全是（迁移需 ≥2 次采样），推了等于推噪声。
    但「冒头 + 高可发酵度」仍有观察价值，所以分两级呈现，不混为一谈。
    """
    ts = load_json(TOPIC_STATE)
    if not ts["available"]:
        return {"available": False, "reason": ts["reason"]}

    by_state = (ts["data"].get("by_state") or {})
    fj = load_json(FERMENT_JSON)
    ferment_by_hid: dict[str, dict] = {}
    if fj["available"]:
        for t in (fj["data"].get("topics") or []):
            hid = str(t.get("hashtag_id") or "")
            if hid:
                ferment_by_hid[hid] = t
    ferment_available = fj["available"]

    def dedup_by_topic(items: list[dict], key) -> list[dict]:
        """同一话题只留一条。

        为什么需要：话题状态库里**浏览量与互动量是两条独立序列**
        （topic_key 含 metric_kind，见 topic_tracker 的实测修复）。
        对运营来说「白银之城」是一个话题，不该在清单里出现两次——
        所以按话题去重，并**优先保留浏览量序列**（它是主指标，量级也更可比）。
        """
        best: dict[str, dict] = {}
        for it in items:
            k = it.get("hashtag_id") or it.get("title") or it.get("topic_key")
            cur = best.get(k)
            if cur is None:
                best[k] = it
                continue
            # 浏览量优先；同为浏览量取样本更多的
            if (it.get("_metric_kind") == "page_view"
                    and cur.get("_metric_kind") != "page_view"):
                best[k] = it
            elif (it.get("_metric_kind") == cur.get("_metric_kind")
                  and (it.get("samples") or 0) > (cur.get("samples") or 0)):
                best[k] = it
        return list(best.values())

    def pack(item: dict[str, Any]) -> dict[str, Any]:
        hid = str(item.get("hashtag_id") or "")
        f = ferment_by_hid.get(hid) or {}
        return {
            "title": item.get("title"),
            "topic_key": item.get("topic_key"),
            "hashtag_id": hid,
            "_metric_kind": item.get("metric_kind"),
            "state": item.get("state"),
            "latest": item.get("last_metric"),
            "peak": item.get("peak_metric"),
            "samples": item.get("sample_count"),
            "ferment_score": f.get("ferment_score"),
            "verdict": f.get("verdict"),
            # 建议动作来自 ferment_judge 的 LLM 路；降级时它是占位文案，要如实透传
            "suggested_action": f.get("suggested_action"),
            "reason": f.get("reason"),
            "url": f"https://www.taptap.cn/hashtag/{hid}" if hid else None,
        }

    actionable: list[dict] = []
    for st in ACTIONABLE_STATES:
        actionable += [pack(x) for x in (by_state.get(st) or [])]

    watch: list[dict] = []
    for st in WATCH_STATES:
        for x in (by_state.get(st) or []):
            p = pack(x)
            # 只把「可发酵度明确为 act/watch」的冒头话题放进观察位
            if p.get("verdict") in ("act", "watch"):
                watch.append(p)

    actionable = dedup_by_topic(actionable, None)
    watch = dedup_by_topic(watch, None)
    # 排序：**先按状态紧急度（爆发 > 升温），再按可发酵度**。
    # 为什么不只按可发酵度：可发酵度缺失时（LLM 未跑）它会全部为 None，
    # 排序退化成插入顺序，可能把「爆发」排在「升温」后面 —— 紧急的先做。
    _prio = {s: i for i, s in enumerate(ACTIONABLE_STATES)}   # 升温0 爆发1
    actionable.sort(key=lambda x: (-_prio.get(x.get("state"), 9),
                                   -(x.get("ferment_score") or 0)))
    watch.sort(key=lambda x: -(x.get("ferment_score") or 0))

    return {
        "available": True,
        "n_topics": ts["data"].get("n_topics"),
        "by_state_counts": {k: len(v or []) for k, v in by_state.items()},
        "actionable": actionable[:limit],
        "watch": watch[:limit],
        "ferment_available": ferment_available,
        "ferment_note": None if ferment_available else "可发酵度产物缺失，建议动作无法关联",
    }


# ---------------------------------------------------------------------------
# ② 风险预警：哪里在吵、要不要介入
# ---------------------------------------------------------------------------

def risk_alerts(limit: int = 5) -> dict[str, Any]:
    """风险预警。

    **闸门是负向率**（够多才算风险），**高投入占比是优先级标记**（不是闸门）——
    因为「高负向但高投入少」仍可能是真问题，滤掉会漏；
    而「高投入玩家也在骂」是最该先处理的信号，所以标出来而不是筛掉。

    干预建议直接取 risk_insight 的 intervention_map（已按主题归类）。
    """
    r = load_json(RISK_JSON)
    if not r["available"]:
        return {"available": False, "reason": r["reason"]}

    data = r["data"]
    if data.get("available") is False:
        return {"available": False, "reason": data.get("reason") or "上游标注不可用"}

    table = data.get("topic_risk_table") or []
    facts = data.get("facts") or {}

    alerts = []
    for row in table:
        neg_pp = float(row.get("neg_rate_pp") or 0)
        hi_pp = float(row.get("neg_high_hours_share_pp") or 0)
        # 两个条件：负面够多 + 高投入玩家也在其中（后者才是「真在乎」的信号）
        if neg_pp < RISK_NEG_RATE_MIN_PP:
            continue
        alerts.append({
            "topic": row.get("topic_cn"),
            "neg_rate_pp": neg_pp,
            "high_hours_share_pp": hi_pp,
            "actionable_share_pp": row.get("actionable_share_pp"),
            "n_neg": row.get("n_neg"),
            "intervention": row.get("intervention"),
            "high_stakes": hi_pp >= RISK_HIGH_HOURS_MIN_PP,
        })
    alerts.sort(key=lambda x: -x["neg_rate_pp"])

    return {
        "available": True,
        "alerts": alerts[:limit],
        "n_candidates": len(alerts),
        "overall": {
            "n_total": facts.get("n_total"),
            "n_neg": facts.get("n_neg"),
            "neg_rate_pp": facts.get("neg_rate_pp"),
            "n_high_investment_negative": facts.get("n_high_investment_negative"),
            "high_investment_share_of_neg_pp": facts.get("high_investment_share_of_neg_pp"),
        },
        "threshold": {"neg_rate_min_pp": RISK_NEG_RATE_MIN_PP,
                      "high_hours_min_pp": RISK_HIGH_HOURS_MIN_PP},
    }


# ---------------------------------------------------------------------------
# ③ 内容候选：哪些帖子/评论值得推
# ---------------------------------------------------------------------------

def content_picks(limit: int = 6) -> dict[str, Any]:
    """内容候选。

    「值得推」的判据是**点赞**（社区认可度），不掺主观判断——
    它与「是不是金句/段子」是两件事（后者需 LLM 仲裁，见素材层设计 §4.0），
    所以这里**只声称「高赞候选」，不声称「已分类的素材」**。

    ⚠️ 作者名一律不出（PII 三域纪律）；溯源靠可点链接。
    """
    posts = _read_csv(POSTS_CSV)
    comments = _read_csv(COMMENTS_CSV)
    if not posts and not comments:
        return {"available": False,
                "reason": "未找到平台发现流数据（需先跑 crawl_taptap_discovery.py）"}

    post_by_mid = {p.get("moment_id"): p for p in posts}
    # 帖子点赞在 ups，评论在 supports —— 字段名不一致，是上游的坑
    post_items = [{
        "kind": "post",
        "text": (p.get("title") or "").strip() or (p.get("summary") or "")[:40].strip(),
        "excerpt": (p.get("summary") or "").strip()[:80],
        "likes": _int(p.get("ups")),
        "replies": _int(p.get("comments")),
        "views": _int(p.get("pv_total")),
        "app": p.get("app_title"),
        "url": f"https://www.taptap.cn/moment/{p.get('moment_id')}",
    } for p in posts if (p.get("title") or p.get("summary"))]

    comment_items = []
    for c in comments:
        txt = (c.get("content") or "").strip()
        if not txt:
            continue
        mid = c.get("moment_id")
        host = post_by_mid.get(mid) or {}
        comment_items.append({
            "kind": "comment",
            "text": txt[:60],
            "excerpt": txt[:120],
            "likes": _int(c.get("supports")),
            "app": host.get("app_title"),
            "host_title": (host.get("title") or "").strip() or None,
            "url": f"https://www.taptap.cn/moment/{mid}",
        })

    def top(items: list[dict], n: int) -> list[dict]:
        return sorted([x for x in items if x["likes"] >= CONTENT_MIN_LIKE],
                      key=lambda x: (-x["likes"], -x.get("replies", 0)))[:n]

    return {
        "available": True,
        "posts": top(post_items, limit),
        "comments": top(comment_items, limit),
        "pool": {"n_posts": len(post_items), "n_comments": len(comment_items)},
        "min_like": CONTENT_MIN_LIKE,
        "note": "仅按点赞排序的候选，非已分类素材（金句/段子判定需 LLM 仲裁）",
    }


# ---------------------------------------------------------------------------
# ④ 动作闭环：我做了没有 + 同话题不重复推
# ---------------------------------------------------------------------------

def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """打开运营状态库。

    ⚠️ 默认参数用 None 而非直接写 `=OPS_DB`：默认值在定义时绑定，
    会让 `mock.patch.object(co, "OPS_DB", ...)` 失效（测试改不动路径）。
    """
    db_path = db_path or OPS_DB
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS ops_action (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_key   TEXT NOT NULL,
            title       TEXT,
            action      TEXT NOT NULL,      -- follow | ignore | done
            state       TEXT,               -- 系统当时判定的状态（供校准对比）
            actor_role  TEXT NOT NULL DEFAULT '运营',
            note        TEXT,
            acted_at    TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ops_push (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_key  TEXT NOT NULL,
            state      TEXT NOT NULL,
            pushed_at  TEXT NOT NULL,
            UNIQUE(topic_key, state)
        )
    """)
    con.commit()
    return con


def record_action(topic_key: str, action: str, *, title: str = "",
                  state: str = "", actor_role: str = "运营", note: str = "",
                  db_path: Path | None = None) -> dict[str, Any]:
    """记录运营动作。这是闭环的起点，也是将来校准阈值的唯一数据来源。"""
    if action not in ("follow", "ignore", "done"):
        raise ValueError(f"未知动作：{action}（应为 follow / ignore / done）")
    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO ops_action (topic_key,title,action,state,actor_role,note,acted_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (topic_key, title, action, state, actor_role, note,
             datetime.now(TZ).isoformat(timespec="seconds")),
        )
        con.commit()
        return {"ok": True, "topic_key": topic_key, "action": action}
    finally:
        con.close()


def mark_pushed(topic_key: str, state: str, *, db_path: Path | None = None) -> bool:
    """标记已推送。同一话题同一状态只推一次（否则第一周就没人看了）。"""
    con = connect(db_path)
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO ops_push (topic_key,state,pushed_at) VALUES (?,?,?)",
            (topic_key, state, datetime.now(TZ).isoformat(timespec="seconds")),
        )
        con.commit()
        return cur.rowcount > 0     # True=首次推送；False=已推过
    finally:
        con.close()


def should_push(topic_key: str, state: str, *, db_path: Path | None = None) -> dict[str, Any]:
    """该不该推。状态升级（升温→爆发）允许再推；同状态不重复。"""
    con = connect(db_path)
    try:
        row = con.execute(
            "SELECT pushed_at FROM ops_push WHERE topic_key=? AND state=?",
            (topic_key, state),
        ).fetchone()
        return {"should": row is None,
                "reason": "首次推送" if row is None else f"{state} 状态已推过（{row['pushed_at']}）"}
    finally:
        con.close()


def list_actions(limit: int = 20, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT topic_key,title,action,state,actor_role,note,acted_at"
            " FROM ops_action ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def render_report(opp: dict, risk: dict, content: dict, actions: list[dict]) -> str:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    L = [f"# 社区运营日报 · {now}", ""]

    L += ["## 一、话题机会（今天可以做的）", ""]
    if not opp.get("available"):
        L += [f"- 不可用：{opp.get('reason')}（显式降级，不估算）", ""]
    else:
        act = opp.get("actionable") or []
        if act:
            L += ["| 话题 | 状态 | 当前 | 峰值 | 可发酵度 | 建议动作 |",
                  "|------|------|------|------|----------|----------|"]
            for t in act:
                L.append(
                    f"| [{t['title']}]({t['url']}) | {t['state']} | {t['latest']} "
                    f"| {t['peak']} | {t.get('ferment_score') or '—'} "
                    f"| {t.get('suggested_action') or '—'} |")
        else:
            counts = opp.get("by_state_counts") or {}
            dist = " · ".join(f"{k} {v}" for k, v in counts.items() if v) or "无"
            L += [f"- **今日无「升温/爆发」话题**（当前状态分布：{dist}）。", ""]
            L += ["- 说明：话题需 ≥2 次采样才能判迁移，首日全为「冒头」属正常。", ""]
        watch = opp.get("watch") or []
        if watch:
            L += ["**观察位**（仍是冒头，未到行动时机）", "",
                  "| 话题 | 可发酵度 | 判定 | 当前 |", "|------|----------|------|------|"]
            for t in watch:
                L.append(f"| [{t['title']}]({t['url']}) | {t.get('ferment_score') or '—'} "
                         f"| {t.get('verdict') or '—'} | {t['latest']} |")
            L.append("")
        if not opp.get("ferment_available"):
            L += [f"- ⚠️ {opp.get('ferment_note')}", ""]

    L += ["## 二、风险预警（要不要介入）", ""]
    if not risk.get("available"):
        L += [f"- 不可用：{risk.get('reason')}（显式降级，不估算）", ""]
    else:
        ov = risk.get("overall") or {}
        L += [f"- 整体：负向 {ov.get('n_neg')}/{ov.get('n_total')}（{ov.get('neg_rate_pp')}%）；"
              f"其中高投入玩家 {ov.get('n_high_investment_negative')} 条"
              f"（占负向 {ov.get('high_investment_share_of_neg_pp')}%）", ""]
        al = risk.get("alerts") or []
        if al:
            L += ["| 主题 | 负向率 | 高投入占比 | 干预建议 |", "|------|--------|-----------|----------|"]
            for a in al:
                mark = "**" if a["high_stakes"] else ""
                L.append(f"| {mark}{a['topic']}{mark} | {a['neg_rate_pp']}% "
                         f"| {a['high_hours_share_pp']}% | {a.get('intervention') or '—'} |")
            L += ["", "> 加粗项 = 高投入玩家也集中不满（比单纯负向率更值得优先处理）。", ""]
        else:
            L += ["- 无主题同时满足「负向率 + 高投入」双阈值。", ""]
        th = risk.get("threshold") or {}
        L += [f"> ⚠️ 阈值（负向率 ≥{th.get('neg_rate_min_pp')}% / 高投入 ≥{th.get('high_hours_min_pp')}%）"
              "为默认值，**未经真实运营反馈校准**，不要当可信参数使用。", ""]

    L += ["## 三、内容候选（可以推的）", ""]
    if not content.get("available"):
        L += [f"- 不可用：{content.get('reason')}（显式降级，不估算）", ""]
    else:
        pool = content.get("pool") or {}
        L += [f"- 候选池：帖 {pool.get('n_posts')} · 评论 {pool.get('n_comments')}"
              f"（按点赞 ≥{content.get('min_like')} 筛）", ""]
        for label, key in (("帖子", "posts"), ("评论", "comments")):
            items = content.get(key) or []
            if not items:
                continue
            L += [f"**{label}**", ""]
            for it in items:
                host = f"（来自「{it['host_title']}」）" if it.get("host_title") else ""
                L.append(f"- [{it['likes']} 赞] {it['text']}{host} — [看原帖]({it['url']})")
            L.append("")
        L += [f"> ⚠️ {content.get('note')}", ""]

    L += ["## 四、动作记录（闭环起点）", ""]
    if actions:
        L += ["| 时间 | 话题 | 动作 | 角色 | 备注 |", "|------|------|------|------|------|"]
        for a in actions[:10]:
            L.append(f"| {a['acted_at'][5:16]} | {a.get('title') or a['topic_key']} "
                     f"| {a['action']} | {a['actor_role']} | {a.get('note') or ''} |")
        L += ["", "> 这些记录是**将来校准阈值的唯一数据来源**——"
              "系统说 act、你点了 ignore，就说明阈值太松。", ""]
    else:
        L += ["- 尚无动作记录。用 `--act follow --key <topic_key>` 记一条。", ""]

    L += ["## 边界", "",
          "- 阈值全部未校准（话题状态 1.5×/3.0×/0.6×、风险双阈值）；未校准前不得当可信参数。",
          "- 内容候选仅按点赞排序，**未做「金句/段子」分类**（需 LLM 仲裁）。",
          "- 只出聚合与公开链接，不含作者名等标识（PII 三域纪律）。",
          "- 本模块只做筛选与关联，不重算数字；所有数字来自上游 facts。",
          "", "*日报结束*", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="社区运营模块（第一版 4 功能）")
    ap.add_argument("--limit", type=int, default=5, help="每节条数上限")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--act", choices=["follow", "ignore", "done"], help="记录一个运营动作")
    ap.add_argument("--key", help="话题 key（配合 --act）")
    ap.add_argument("--title", default="", help="话题标题（配合 --act）")
    ap.add_argument("--note", default="", help="备注")
    args = ap.parse_args(argv)

    if args.act:
        if not args.key:
            print("--act 需要配合 --key", file=sys.stderr)
            return 2
        r = record_action(args.key, args.act, title=args.title, note=args.note)
        print(json.dumps(r, ensure_ascii=False))
        return 0

    opp = topic_opportunities(args.limit)
    risk = risk_alerts(args.limit)
    content = content_picks(args.limit + 1)
    actions = list_actions()

    if args.json:
        out = {"schema_version": SCHEMA_VERSION,
               "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
               "topic_opportunities": opp, "risk_alerts": risk,
               "content_picks": content, "recent_actions": actions}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    report = render_report(opp, risk, content, actions)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TZ).strftime("%Y%m%d")
    (REPORT_DIR / f"community_ops_{today}.md").write_text(report, encoding="utf-8")
    (REPORT_DIR / "community_ops_latest.md").write_text(report, encoding="utf-8")
    (LAB / "outputs" / "community_ops.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION,
                    "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
                    "topic_opportunities": opp, "risk_alerts": risk,
                    "content_picks": content}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(REPORT_DIR / "community_ops_latest.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
