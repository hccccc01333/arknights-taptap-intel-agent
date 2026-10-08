"""Decision briefs assembled from saved research, history and human feedback."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .store import stable_id, now_iso


def brief(store, days=7):
    days = int(days)
    if days not in (1,7):
        raise ValueError("交付周期须为 1 天或 7 天")
    end = now_iso()
    start = (datetime.fromisoformat(end)-timedelta(days=days)).isoformat(timespec="seconds")
    events = []
    for row in store.conn.execute("SELECT event_id FROM event WHERE updated_at>=? ORDER BY updated_at DESC",(start,)):
        event = store.get_event(row[0])
        event["changes"] = [c for c in event["changes"] if c["created_at"]>=start]
        event["new_in_period"] = event["created_at"]>=start
        events.append(event)
    feedback = [{**dict(r),"outcome":json.loads(r["outcome"])} for r in store.conn.execute("SELECT * FROM feedback WHERE created_at>=? ORDER BY created_at DESC",(start,))]
    actions = []
    for event in events:
        if event["verdict"] != "related":
            continue
        rows = store.conn.execute("SELECT * FROM creative WHERE event_id=? ORDER BY created_at DESC",(event["event_id"],)).fetchall()
        current_proposals = event["assessment"].get("creatives",[])
        for row in rows:
            payload = json.loads(row["payload"])
            if {k:v for k,v in payload.items() if k not in ("review_status","evidence_ids")} not in current_proposals:
                continue
            latest = store.conn.execute("SELECT decision,reason,outcome FROM feedback WHERE creative_id=? ORDER BY created_at DESC LIMIT 1",(row["creative_id"],)).fetchone()
            actions.append({"creative_id":row["creative_id"],"event_id":event["event_id"],"event_title":event["title"],
                            "proposal":payload,"decision":latest[0] if latest else "unreviewed",
                            "reason":latest[1] if latest else None})
    first = store.conn.execute("SELECT MIN(created_at) FROM event_version").fetchone()[0]
    coverage = store.overview()["sources"]
    limitations = ["研究结论仍需运营审核；引用校验只能证明文本引用存在，不能独立证明源消息为真。",
                   "采样与搜索不能代表全网规模；未执行的提案没有已实现增长。"]
    if not first or first>start:
        limitations.append("事件研究历史尚未覆盖完整周期，不能据此比较完整七天的事件量或趋势。")
    stale = [s for s in coverage if s["freshness"] != "recent" or s["status"] != "ok"]
    if stale:
        limitations.append(f"{len(stale)} 个迁移来源文件数据过时、为空或读取失败；实时连接器状态另列。")
    failures = store.conn.execute("SELECT COUNT(*) FROM run WHERE created_at>=? AND status IN ('failed','budget_exhausted','interrupted')",(start,)).fetchone()[0]
    result = {"report_id":stable_id("brief_",start+end),"title":"热点行动日报" if days==1 else "热点行动周报",
              "period":{"start":start,"end":end,"days":days,"timezone":"Asia/Shanghai","basis":"研究版本保存时间；不等于事件发生时间"},
              "counts":{"new_events":sum(e["new_in_period"] for e in events),"updated_events":sum(bool(e["changes"]) for e in events),
                        "proposals":len(actions),"feedback_records":len(feedback),"failed_runs":failures},
              "events":events,"actions":actions,"feedback":feedback,"limitations":limitations,
              "connector_health":[dict(r) for r in store.conn.execute("SELECT * FROM connector_health ORDER BY platform")],
              "generated_at":end}
    result["markdown"] = render(result)
    return result


def render(report):
    lines = ["# "+report["title"],"",f"周期：{report['period']['start']} 至 {report['period']['end']}（时间为 UTC，界面显示北京时间）", "",
             "## 可以采取的行动", ""]
    actionable = [a for a in report["actions"] if a["decision"] != "rejected"]
    if not actionable:
        lines.append("当前没有经研究形成的可行动提案；不能用泛泛建议替代缺失交付。")
    for action in actionable:
        p = action["proposal"]
        lines += ["### "+p["title"],"",f"事件：{action['event_title']}（{action['event_id']}）",f"人群：{p['audience']}",
                  f"位置与动作：{p['placement']}；{p['user_action']}",f"时效：{p['timing']}",f"文案：{p['copy']}","", "步骤：",""]
        lines += [f"{i+1}. {s}" for i,s in enumerate(p["steps"])]
        lines += ["",f"验证：{p['measurement']}",f"运营判断：{action['decision']}；{action['reason'] or '尚未审核'}","", "素材：", ""]
        lines += [f"- {m['description']}；{m['rights_status']}；证据 {m['evidence_id']}" for m in p["materials"]]
        lines += ["", "风险："+"；".join(p["risks"]), ""]
    lines += ["## 新事件与变化", ""]
    if not report["events"]:
        lines.append("此周期尚无已完成的研究事件。")
    for e in report["events"]:
        lines += ["### "+e["title"],"",e["assessment"]["summary"],"", "来源引文：", ""]
        source = {s["evidence_id"]:s for s in [*e["evidence"],*e["source_snapshots"]]}
        for fact in e["assessment"]["facts"]:
            evidence = source.get(fact["evidence_id"],{})
            lines.append(f"- {fact['quote']}（{evidence.get('url') or fact['evidence_id']}）")
        lines += ["", "推断："+"；".join(e["assessment"]["inferences"]),"未知："+"；".join(e["assessment"]["unknowns"]), ""]
        for change in e["changes"]:
            lines += [f"- {change['created_at']}：{change['previous_verdict']} → {change['verdict']}；新增 {len(change['added_evidence_ids'])} 条证据。", "  之前："+change["previous_summary"], "  当前："+change["summary"]]
    lines += ["", "## 运营反馈", ""]
    lines += [f"- {f['decision']}：{f['reason']}；执行记录：{json.dumps(f['outcome'],ensure_ascii=False)}" for f in report["feedback"]] or ["尚无实际反馈。"]
    lines += ["", "## 覆盖与限制", ""] + ["- "+s for s in report["limitations"]]
    for channel in report["connector_health"]:
        lines.append(f"- {channel['platform']}：{channel['status']}；最近成功 {channel['last_success'] or '无'}；{channel['error'] or ''}")
    return "\n".join(lines)+"\n"
