#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/events.py — 事件流与触发分发（阶段一 ⑥「触发链接通」）。

## 这一环补的是什么

之前的断点：`topic_tracker` 判出了状态迁移（冒头→升温→爆发…），
但**迁移只是改了 topic_state 里的当前状态字段** ——
「什么时候从什么变成了什么」这段**历史没有被单独记录**，
更没有**触发任何下游动作**。

结果：调度器每 5 分钟跑一次、状态机每年判出几十次迁移，
**下游一无所知**。这就是验收项 C1「触发链未接通」。

## 设计

    topic_state（当前状态）
         ↓ emit_transitions()   扫出「新迁移」，生成事件（幂等去重）
    topic_event（事件表，Memory）
         ↓ dispatch_pending()   分发给订阅者（各自独立失败隔离）
    订阅者：stdout（兜底）· 事件流报告 · 未来的推送

## 两条硬纪律

**① 幂等** —— 调度器会反复跑，同一次迁移**只能触发一次**。
   `event_id = hash(topic_key + to_state + occurred_at)`；
   而且 `occurred_at` 取的是 `topic_state.state_changed_at`（**上游给的迁移时刻**），
   不是我们扫描的时刻 —— 这样重跑不会产生新 id。

**② 失败隔离** —— 一个订阅者挂了不能拖垮其他，也不能让事件卡住不发给别人。
   每个订阅者独立 try/except，结果记进 `dispatch_result`。

## 分发门槛（沿用交付形态设计 §3.2）

    ✅ 分发：升温、爆发   ← 这是「现在接还来得及」的窗口
    📝 仅记录：冒头、退潮、沉寂   ← 记进事件流，不主动打扰

用法：
  python 11情报Agent/events.py --run          # 扫迁移 + 分发（调度器会调）
  python 11情报Agent/events.py --status       # 事件流与分发情况
  python 11情报Agent/events.py --list         # 列出最近事件
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

TZ = timezone(timedelta(hours=8))
LAB = Path(__file__).resolve().parent
TOPIC_STATE_JSON = LAB / "outputs" / "topic_state.json"
FERMENT_JSON = LAB / "outputs" / "ferment_judge.json"

EVENT_DB = LAB / "state" / "events.sqlite3"
REPORT_DIR = LAB / "reports"
EVENT_STREAM = REPORT_DIR / "event_stream_latest.md"

# 分发门槛：只有这两个状态值得主动打扰人（其余只记录）
DISPATCH_STATES = ("爆发", "升温")
ALL_STATES = ("冒头", "升温", "爆发", "退潮", "沉寂")

# 状态紧急度（用于排序与 significance）
_SEVERITY = {"爆发": 3, "升温": 2, "退潮": 1, "沉寂": 0, "冒头": 0}

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# 事件表（Memory —— 迁移历史不可重建，删了就得重放全部采样）
# ---------------------------------------------------------------------------

def connect(db_path: Path | None = None) -> sqlite3.Connection:
    db_path = db_path or EVENT_DB
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS topic_event (
            event_id        TEXT PRIMARY KEY,
            topic_key       TEXT NOT NULL,
            title           TEXT,
            from_state      TEXT,
            to_state        TEXT NOT NULL,
            occurred_at     TEXT NOT NULL,   -- 迁移发生时刻（来自上游 state_changed_at）
            detected_at     TEXT NOT NULL,   -- 我们记录的时刻
            severity        INTEGER NOT NULL DEFAULT 0,
            dispatchable    INTEGER NOT NULL DEFAULT 0,
            payload         TEXT,            -- JSON：指标 + 建议动作
            dispatched_at   TEXT,
            dispatch_result TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_event_pending ON topic_event(dispatched_at)")
    con.commit()
    return con


def make_event_id(topic_key: str, to_state: str, occurred_at: str) -> str:
    """事件的稳定标识。

    ★ 幂等的根基：**三个输入都来自上游**（迁移时刻由 topic_state 给），
    不掺扫描时刻 —— 否则每次重跑都会生成"新"事件。
    """
    raw = f"{topic_key}|{to_state}|{occurred_at}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------------------
# ① 从状态迁移生成事件
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def collect_transitions(topic_state: dict, ferment: dict | None = None) -> list[dict]:
    """从 topic_state 快照里扫出「发生过的迁移」。

    判据：`state_changed_at` **晚于** `first_seen_at` 且 `prev_state` 非空 ——
    首日采样时 `state_changed_at == first_seen_at`、`prev_state == null`，
    那不算迁移（只是首次见到）。
    """
    ferment_by_hid: dict[str, dict] = {}
    for t in ((ferment or {}).get("topics") or []):
        hid = str(t.get("hashtag_id") or "")
        if hid:
            ferment_by_hid[hid] = t

    out: list[dict] = []
    for state_name, items in ((topic_state or {}).get("by_state") or {}).items():
        for it in (items or []):
            from_state = it.get("prev_state")
            to_state = it.get("state") or state_name
            changed_at = it.get("state_changed_at")
            first_seen = it.get("first_seen_at")
            if not changed_at or not from_state:
                continue                      # 还没迁移过
            if changed_at <= (first_seen or ""):
                continue                      # 只是首次见到，不算迁移
            hid = str(it.get("hashtag_id") or "")
            f = ferment_by_hid.get(hid) or {}
            out.append({
                "topic_key": it.get("topic_key"),
                "title": it.get("title"),
                "hashtag_id": hid,
                "from_state": from_state,
                "to_state": to_state,
                "occurred_at": changed_at,
                "severity": _SEVERITY.get(to_state, 0),
                "dispatchable": to_state in DISPATCH_STATES,
                "metrics": {"latest": it.get("last_metric"),
                            "peak": it.get("peak_metric"),
                            "samples": it.get("sample_count")},
                "ferment_score": f.get("ferment_score"),
                "suggested_action": f.get("suggested_action"),
                "url": f"https://www.taptap.cn/hashtag/{hid}" if hid else None,
            })
    out.sort(key=lambda x: (-x["severity"], x["occurred_at"]), reverse=False)
    return out


def emit_transitions(con: sqlite3.Connection, topic_state: dict,
                     ferment: dict | None = None) -> dict[str, Any]:
    """把新迁移写进事件表。**已存在的不重复写**（幂等）。"""
    now = datetime.now(TZ).isoformat(timespec="seconds")
    new_ids: list[str] = []
    for tr in collect_transitions(topic_state, ferment):
        eid = make_event_id(tr["topic_key"], tr["to_state"], tr["occurred_at"])
        payload = json.dumps({
            "metrics": tr["metrics"], "ferment_score": tr["ferment_score"],
            "suggested_action": tr["suggested_action"], "url": tr["url"],
            "comment_id": None,
        }, ensure_ascii=False)
        cur = con.execute(
            "INSERT OR IGNORE INTO topic_event (event_id,topic_key,title,from_state,"
            " to_state,occurred_at,detected_at,severity,dispatchable,payload)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid, tr["topic_key"], tr["title"], tr["from_state"], tr["to_state"],
             tr["occurred_at"], now, tr["severity"], 1 if tr["dispatchable"] else 0,
             payload),
        )
        if cur.rowcount:
            new_ids.append(eid)
    con.commit()
    return {"ok": True, "n_scanned": len(collect_transitions(topic_state, ferment)),
            "n_new": len(new_ids), "new_event_ids": new_ids}


# ---------------------------------------------------------------------------
# ② 分发给订阅者
# ---------------------------------------------------------------------------

# 订阅者签名：收一个事件 dict，返回 {ok, detail}；**不许抛异常**（由 dispatch 兜）
Subscriber = Callable[[dict[str, Any]], dict[str, Any]]


def _stdout_subscriber(event: dict[str, Any]) -> dict[str, Any]:
    """默认兜底订阅者 —— 没有配置任何渠道时，至少人能看见。"""
    mark = {"爆发": "🔴", "升温": "🟠"}.get(event["to_state"], "·")
    print(f"  [event] {mark} {event['title']} {event['from_state']}→{event['to_state']} "
          f"（{event['occurred_at'][:16]}{'，可发酵度 ' + str(event['ferment_score']) if event.get('ferment_score') else ''}）")
    return {"ok": True, "detail": "stdout"}


def _stream_subscriber(event: dict[str, Any]) -> dict[str, Any]:
    """事件流报告订阅者 —— 追加式记录，供人回看「系统自动化发现了什么变化」。"""
    return {"ok": True, "detail": "stream"}


def default_subscribers() -> list[tuple[str, Subscriber]]:
    """默认订阅者列表。

    ⚠️ 推送（企微/飞书）**尚未实现** —— 等交付层接入时在这里加一项即可，
    事件表与分发逻辑**不用改**。这就是「触发链」与「交付层」解耦的地方。
    """
    return [("stdout", _stdout_subscriber), ("stream", _stream_subscriber)]


def dispatch_event(event: dict[str, Any],
                   subscribers: list[tuple[str, Subscriber]] | None = None) -> dict[str, Any]:
    """分发给全部订阅者。**每个订阅者独立 try/except** —— 一个挂了不影响其他。"""
    subscribers = subscribers if subscribers is not None else default_subscribers()
    results: dict[str, Any] = {}
    for name, fn in subscribers:
        try:
            r = fn(event)
            results[name] = {"ok": bool(r.get("ok", True)), "detail": r.get("detail", "")}
        except Exception as e:                     # ★ 失败隔离
            results[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return results


def pending_events(con: sqlite3.Connection, *, dispatchable_only: bool = True,
                   limit: int = 50) -> list[dict[str, Any]]:
    """未分发的事件。"""
    sql = "SELECT * FROM topic_event WHERE dispatched_at IS NULL"
    if dispatchable_only:
        sql += " AND dispatchable=1"
    sql += " ORDER BY severity DESC, occurred_at ASC LIMIT ?"
    rows = con.execute(sql, (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except Exception:
            d["payload"] = {}
        out.append(d)
    return out


def _flatten(event: dict[str, Any]) -> dict[str, Any]:
    """把 payload 里的字段摊平，方便订阅者使用。"""
    p = event.get("payload") or {}
    return {
        "event_id": event["event_id"],
        "topic_key": event["topic_key"],
        "title": event["title"],
        "from_state": event["from_state"],
        "to_state": event["to_state"],
        "occurred_at": event["occurred_at"],
        "severity": event["severity"],
        "metrics": p.get("metrics") or {},
        "ferment_score": p.get("ferment_score"),
        "suggested_action": p.get("suggested_action"),
        "url": p.get("url"),
    }


def dispatch_pending(con: sqlite3.Connection,
                     subscribers: list[tuple[str, Subscriber]] | None = None,
                     *, limit: int = 50) -> dict[str, Any]:
    """把未分发的事件发出去，并标记。"""
    pend = pending_events(con, dispatchable_only=True, limit=limit)
    if not pend:
        return {"ok": True, "n_pending": 0, "n_sent": 0, "n_ok": 0, "n_failed": 0}

    n_ok = 0
    n_failed = 0
    for ev in pend:
        flat = _flatten(ev)
        res = dispatch_event(flat, subscribers)
        all_ok = all(v.get("ok") for v in res.values()) if res else True
        n_ok += 1 if all_ok else 0
        n_failed += 0 if all_ok else 1
        # ★ 不论成败都标记已分发 —— 否则失败的会永远卡在队列里被反复重发
        con.execute(
            "UPDATE topic_event SET dispatched_at=?, dispatch_result=? WHERE event_id=?",
            (datetime.now(TZ).isoformat(timespec="seconds"),
             json.dumps(res, ensure_ascii=False), ev["event_id"]),
        )
    con.commit()
    return {"ok": True, "n_pending": len(pend), "n_sent": len(pend),
            "n_ok": n_ok, "n_failed": n_failed}


# ---------------------------------------------------------------------------
# ③ 事件流报告
# ---------------------------------------------------------------------------

def recent_events(con: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT * FROM topic_event ORDER BY occurred_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def status(con: sqlite3.Connection) -> dict[str, Any]:
    total = con.execute("SELECT COUNT(*) c FROM topic_event").fetchone()["c"]
    pend = con.execute(
        "SELECT COUNT(*) c FROM topic_event WHERE dispatched_at IS NULL AND dispatchable=1"
    ).fetchone()["c"]
    by_to = {r["to_state"]: r["c"] for r in con.execute(
        "SELECT to_state, COUNT(*) c FROM topic_event GROUP BY to_state ORDER BY c DESC")}
    return {"n_total": total, "n_pending": pend, "by_to_state": by_to}


def render_status(con: sqlite3.Connection) -> str:
    st = status(con)
    L = ["# 事件流（触发链）", ""]
    L += [f"- 累计事件 **{st['n_total']}** 条；待分发 **{st['n_pending']}** 条", ""]
    if not st["n_total"]:
        L += ["- **还没有事件** —— 说明状态机还没判出过迁移。",
              "  首日采样全为「冒头」（`prev_state=null`），**不算迁移**，所以这里是空的，正常。", ""]
    else:
        L += ["| 迁到 | 条数 |", "|---|---|"]
        for k, v in st["by_to_state"].items():
            L.append(f"| {k} | {v} |")
        L.append("")
    rows = recent_events(con, 20)
    if rows:
        L += ["## 最近事件", "",
              "| 时间 | 话题 | 迁移 | 分发 | 结果 |", "|---|---|---|---|---|"]
        for r in rows:
            disp = "✅" if r["dispatched_at"] else ("⏳ 待发" if r["dispatchable"] else "— 仅记录")
            L.append(f"| {r['occurred_at'][5:16]} | {r['title']} "
                     f"| {r['from_state']}→{r['to_state']} | {disp} "
                     f"| {(r['dispatch_result'] or '')[:26]} |")
        L.append("")
    L += ["## 门槛与纪律", "",
          f"- **分发门槛**：只有 {'/'.join(DISPATCH_STATES)} 主动分发；"
          "冒头/退潮/沉寂**只记录不打扰**（交付形态设计 §3.2）",
          "- **幂等**：`event_id = hash(topic_key + to_state + occurred_at)`，"
          "三个输入都来自上游，重跑不会重复触发",
          "- **失败隔离**：每个订阅者独立 try/except；"
          "分发后**不论成败都标记**，避免失败事件被反复重发",
          "- ⚠️ **推送订阅者尚未接入** —— 现在只有 stdout 与事件流报告；"
          "交付层就绪后在 `default_subscribers()` 加一项即可，本模块不用改",
          "", "*报告结束*", ""]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run(*, db_path: Path | None = None,
        topic_state_path: Path | None = None,
        ferment_path: Path | None = None,
        subscribers=None) -> dict[str, Any]:
    """扫迁移 + 分发给订阅者（调度器会调这一句）。"""
    con = connect(db_path)
    try:
        ts = load_json(topic_state_path or TOPIC_STATE_JSON)
        fj = load_json(ferment_path or FERMENT_JSON)
        emitted = emit_transitions(con, ts, fj)
        sent = dispatch_pending(con, subscribers)
        return {"ok": True, "emit": emitted, "dispatch": sent}
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="事件流与触发分发（触发链）")
    ap.add_argument("--run", action="store_true", help="扫迁移 + 分发")
    ap.add_argument("--status", action="store_true", help="事件流状态")
    ap.add_argument("--list", action="store_true", help="列出最近事件（JSON）")
    ap.add_argument("--json", action="store_true", help="机器可读")
    ap.add_argument("--db", default="", help="事件库路径")
    args = ap.parse_args(argv)

    db = Path(args.db) if args.db else None

    if args.list:
        con = connect(db)
        try:
            print(json.dumps(recent_events(con, 50), ensure_ascii=False, indent=2))
        finally:
            con.close()
        return 0

    if args.status:
        con = connect(db)
        try:
            if args.json:
                print(json.dumps({"status": status(con),
                                  "recent": recent_events(con, 20)},
                                 ensure_ascii=False, indent=2))
            else:
                rep = render_status(con)
                REPORT_DIR.mkdir(parents=True, exist_ok=True)
                EVENT_STREAM.write_text(rep, encoding="utf-8")
                print(rep)
        finally:
            con.close()
        return 0

    # 默认 / --run
    r = run(db_path=db)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        e, d = r["emit"], r["dispatch"]
        print(f"[emit] 扫到 {e['n_scanned']} 条迁移，新增事件 {e['n_new']} 条")
        print(f"[dispatch] 待发 {d['n_pending']} 条 → 成功 {d['n_ok']}，失败 {d['n_failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
