#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L4_decision/freshness.py — 时效指标（验收项 H2 的第一个可测指标）。

问题：验收集 H2 要求「覆盖率 / 时效 / 准确率」三个指标，但一直没定义。
      其中**时效最好测**（字段已具备），所以先做它。

时效的定义（可测量）：
    时效滞后 = state_changed_at − first_seen_at
    即「话题首次出现」到「我们判出状态迁移」之间过了多久。

⚠️ 关键：**时效的理论下限 = 采样间隔**。
   话题在 t0 出现，最快也只能在 t0 + 一个间隔被采到。
   所以报告必须把滞后换算成「采样间隔的倍数」——
   才知道是「采样太慢」还是「判定太慢」。

   滞后 ≈ 1 个间隔  → 采样卡住了（要更快）
   滞后 >> 1 个间隔 → 判定卡住了（要调阈值或补采样）

用法：
  python L4_decision/freshness.py              # 人看
  python L4_decision/freshness.py --json       # 机器读
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
LAB = Path(__file__).resolve().parent
TOPIC_DB = LAB / "state" / "topic_tracker.sqlite3"
REPORT_DIR = LAB / "reports"

# 采样间隔（分钟）：与调度器保持一致。**时效指标的分母基准。**
# 从 scheduler 读；读不到就用这个兜底值（并在报告里注明是兜底）。
FALLBACK_INTERVAL_MIN = 15


def _probe_interval_min() -> tuple[int, str]:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("scheduler", str(LAB / "scheduler.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        v = int(getattr(mod, "PROBE_INTERVAL_MIN"))
        return v, "scheduler.PROBE_INTERVAL_MIN"
    except Exception:
        return FALLBACK_INTERVAL_MIN, "兜底值（未读到 scheduler）"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
        return d if d.tzinfo else d.replace(tzinfo=TZ)
    except ValueError:
        return None


def load_transitions(db_path: Path | None = None) -> dict[str, Any]:
    """读话题状态库里的迁移记录。

    只有 `state_changed_at` 晚于 `first_seen_at` 的记录才算**真实迁移** ——
    首日采样时两者相等（还没迁移过），不能算进时效样本。
    """
    db_path = db_path or TOPIC_DB
    if not db_path.exists():
        return {"available": False,
                "reason": f"话题状态库不存在（{db_path.name}）—— 需先跑 topic_tracker --sample"}

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT topic_key,title,metric_kind,first_seen_at,state_changed_at,"
            " state,prev_state,sample_count FROM topic_state"
        ).fetchall()
    except sqlite3.Error as e:
        con.close()
        return {"available": False, "reason": f"状态库读取失败：{type(e).__name__}"}
    con.close()

    moved: list[dict[str, Any]] = []      # 发生过真实迁移的
    unmoved: list[dict[str, Any]] = []    # 还没迁移的（首日全在这）
    for r in rows:
        t0, t1 = _parse(r["first_seen_at"]), _parse(r["state_changed_at"])
        item = {"topic_key": r["topic_key"], "title": r["title"],
                "metric_kind": r["metric_kind"], "state": r["state"],
                "prev_state": r["prev_state"], "samples": r["sample_count"],
                "first_seen_at": r["first_seen_at"],
                "state_changed_at": r["state_changed_at"]}
        if t0 and t1 and t1 > t0:
            item["lag_hours"] = round((t1 - t0).total_seconds() / 3600, 2)
            moved.append(item)
        else:
            item["lag_hours"] = None
            unmoved.append(item)

    return {"available": True, "n_total": len(rows),
            "moved": moved, "unmoved": unmoved}


def compute(db_path: Path | None = None,
            interval_min: int | None = None) -> dict[str, Any]:
    """算时效指标。"""
    interval, interval_src = (interval_min, "显式传入") if interval_min \
        else _probe_interval_min()

    d = load_transitions(db_path)
    if not d["available"]:
        return {"available": False, "reason": d["reason"],
                "interval_min": interval, "interval_source": interval_src}

    moved = d["moved"]
    # 按目标状态分组：升温和爆发的滞后最关心（那才是行动的窗口）
    by_state: dict[str, list[float]] = {}
    for m in moved:
        by_state.setdefault(m["state"], []).append(m["lag_hours"])

    lag_list = [m["lag_hours"] for m in moved]
    intervals = [round(h * 60 / interval, 1) for h in lag_list]   # 换算成「几个采样间隔」

    def stats(xs: list[float]) -> dict[str, float] | None:
        if not xs:
            return None
        s = sorted(xs)
        return {"n": len(xs), "min": round(min(s), 2),
                "p25": round(s[len(s) // 4], 2),
                "median": round(statistics.median(s), 2),
                "p75": round(s[(3 * len(s)) // 4], 2),
                "max": round(max(s), 2)}

    MIN_SAMPLES = 5
    return {
        "available": True,
        "interval_min": interval,
        "interval_source": interval_src,
        "n_topics": d["n_total"],
        "n_moved": len(moved),
        "n_unmoved": len(d["unmoved"]),
        "enough_samples": len(moved) >= MIN_SAMPLES,
        "min_samples_required": MIN_SAMPLES,
        "lag_stats": stats(lag_list),                    # 单位：小时
        "interval_multiple_stats": stats(intervals),     # 单位：采样间隔的倍数
        "by_target_state": {k: stats(v) for k, v in by_state.items()},
        "slowest": sorted(moved, key=lambda x: -x["lag_hours"])[:5],
        "recent": sorted(moved, key=lambda x: x["state_changed_at"], reverse=True)[:5],
    }


def render(r: dict[str, Any]) -> str:
    L = ["# 时效指标（H2）—— 从话题出现到判出迁移，滞后多久", ""]
    if not r.get("available"):
        return "\n".join(L + [f"- 不可用：{r.get('reason')}（显式降级，不估算）", ""])

    iv = r["interval_min"]
    L += [f"- 采样间隔基准：**{iv} 分钟**（来源：{r['interval_source']}）",
          f"- 在追踪话题：{r['n_topics']} 个；其中**发生过状态迁移的 {r['n_moved']} 个**"
          f"（其余 {r['n_unmoved']} 个还没动过）", ""]

    if not r["enough_samples"]:
        L += [f"- ⏳ **样本不足**（{r['n_moved']}/{r['min_samples_required']} 条迁移记录）——"
              f"先让它跑够，再回来读结论。", "",
              "> 为什么要有最小样本：首日采样时所有话题的 `state_changed_at == first_seen_at`"
              "（还没迁移过），",
              "> 此时滞后恒为 0，不能据此说「时效很好」。**样本够之前不给结论。**", ""]
        return "\n".join(L + ["", "## 边界", "",
                              "- 时滞字段来自 `topic_state`；判定与采样都影响它，本报告只做呈现。",
                              "", "*报告结束*", ""])

    ls = r["lag_stats"]
    ims = r["interval_multiple_stats"]
    L += ["## 滞后分布（单位：小时）", "",
          "| 指标 | 最小 | p25 | 中位数 | p75 | 最大 |",
          "|------|------|-----|--------|-----|------|",
          f"| 滞后 | {ls['min']} | {ls['p25']} | **{ls['median']}** | {ls['p75']} | {ls['max']} |",
          "",
          f"换算成采样间隔：中位数 **{ims['median']} × {iv}min**"
          f"（最小 {ims['min']}×，最大 {ims['max']}×）", ""]

    med_mult = ims["median"]
    L += ["**怎么读（诊断是采样卡住还是判定卡住）**", ""]
    if med_mult <= 1.5:
        L += [f"> 中位滞后约 **{med_mult} × 采样间隔** → **瓶颈在采样频率**："
              f"话题一出现就被采到了，判定也及时。想让时效更好，只能提高采样频率。", ""]
    elif med_mult <= 4:
        L += [f"> 中位滞后约 **{med_mult} × 采样间隔** → 采样与判定各占一部分，"
              f"当前间隔大体合适。", ""]
    else:
        L += [f"> 中位滞后约 **{med_mult} × 采样间隔** → **瓶颈可能在判定**："
              f"采到了但状态没迁移，要先看阈值（1.5×/3.0×/0.6×）是否偏严。", ""]

    if r["by_target_state"]:
        L += ["## 按目标状态分组", "",
              "| 迁移到 | 条数 | 中位滞后(h) | 中位滞后(×间隔) |", "|---|---|---|---|"]
        for st, s in r["by_target_state"].items():
            if not s:
                continue
            mult = round(s["median"] * 60 / r["interval_min"], 1)
            L.append(f"| {st} | {s['n']} | {s['median']} | {mult} |")
        L.append("")

    if r["slowest"]:
        L += ["## 最慢的几条（值得看是不是漏了什么）", "",
              "| 话题 | 迁到 | 滞后(h) | 采样次数 |", "|---|---|---|---|"]
        for m in r["slowest"]:
            L.append(f"| {m['title']} | {m['state']} | {m['lag_hours']} | {m['samples']} |")
        L.append("")

    L += ["## 边界", "",
          "- **时效的理论下限 = 采样间隔**：话题最快也只能在一个间隔后被采到。",
          "  所以「中位滞后 1× 间隔」已经是当前频率下的最好结果，不是问题。",
          "- 这里只测「出现 → 判出迁移」；**不含「判出 → 员工看到」**（那要等交付层）。",
          "- 迁移记录来自 `topic_state`；首日采样（未迁移）不计入样本。",
          "", "*报告结束*", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="时效指标（H2）")
    ap.add_argument("--json", action="store_true", help="机器可读")
    ap.add_argument("--db", default="", help="状态库路径")
    ap.add_argument("--interval", type=int, default=0, help="采样间隔分钟（默认读 scheduler）")
    args = ap.parse_args(argv)

    r = compute(Path(args.db) if args.db else None,
                args.interval or None)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    rep = render(r)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "freshness_latest.md").write_text(rep, encoding="utf-8")
    print(rep)
    print(f"\n（已写入 {REPORT_DIR / 'freshness_latest.md'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
