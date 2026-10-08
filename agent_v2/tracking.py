from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def compare_observations(store, evidence_ids, hours=24):
    """Same content, same metric and actual observation interval only."""
    hours = max(1, min(168, int(hours)))
    cutoff = (datetime.now(timezone.utc)-timedelta(hours=hours)).isoformat(timespec="seconds")
    results = []
    for evidence in store.evidence(evidence_ids):
        rows = store.conn.execute("SELECT observed_at,metrics FROM observation WHERE evidence_id=? AND observed_at>=? ORDER BY observed_at",
                                  (evidence["evidence_id"],cutoff)).fetchall()
        all_points = [(r[0],json.loads(r[1])) for r in rows]
        latest_allowed=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec="seconds")
        points = [(t,m) for t,m in all_points if m.get("quality") != "migration_time_ambiguous" and t<=latest_allowed]
        metrics = {}
        for metric in ("views","likes","comments","shares","rank","hot_score"):
            values = [(t,m[metric]) for t,m in points if metric in m]
            if len(values) < 2:
                metrics[metric] = {"status":"insufficient","points":len(values)}
                continue
            first, last = values[0], values[-1]
            elapsed = (datetime.fromisoformat(last[0])-datetime.fromisoformat(first[0])).total_seconds()/3600
            delta = last[1]-first[1]
            status = "comparable" if elapsed >= 0.5 else "interval_too_short"
            if metric in ("views","likes","comments","shares") and any(b[1]<a[1] for a,b in zip(values,values[1:])):
                status = "counter_decreased_or_reset"
            info = {"status":status,"points":len(values),"first_at":first[0],"last_at":last[0],
                    "first":first[1],"last":last[1],"delta":delta,"elapsed_hours":round(elapsed,3)}
            if status == "comparable" and metric in ("views","likes","comments","shares"):
                info["per_hour"] = round(delta/elapsed,3)
                if first[1] > 0:
                    info["relative_change"] = round(delta/first[1],4)
            metrics[metric] = info
        results.append({"evidence_id":evidence["evidence_id"],"title":evidence["title"],"platform":evidence["platform"],
                        "published_at":evidence["published_at"],"last_seen_at":evidence["last_seen_at"],"metrics":metrics,
                        "excluded_ambiguous_observations":len(all_points)-len(points)})
    return {"hours":hours,"comparisons":results,
            "note":"这里只比较相同内容的同一指标。采样量不是全网热度；排名变化不是互动增长。两点增量不证明爆发或加速。"}


def event_changes(versions):
    changes = []
    for previous,current in zip(versions,versions[1:]):
        old,new = previous["assessment"],current["assessment"]
        old_quotes = {f["quote"] for f in old.get("facts",[])}
        changes.append({"created_at":current["created_at"],"run_id":current["run_id"],
                        "previous_verdict":old["verdict"],"verdict":new["verdict"],
                        "previous_summary":old["summary"],"summary":new["summary"],
                        "added_evidence_ids":sorted(set(new["evidence_ids"])-set(old["evidence_ids"])),
                        "new_facts":[f for f in new.get("facts",[]) if f["quote"] not in old_quotes]})
    return changes
