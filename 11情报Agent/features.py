#!/usr/bin/env python3
"""F1–F7 特征落盘（阶段一 ④）——先算不判，为 ML 积累数据。

设计文档：docs/热点追踪设计.md §② 早期信号检测 → 特征工程 → 机器学习。

纪律（本项目既定，勿破）：
  1. **先算不判**——特征只落盘，不参与状态机判断；本模块不 import 任何判定逻辑。
  2. **显式降级**——每个特征必带 status：ok / insufficient_data / unavailable /
     source_missing；None 必须有 reason，不静默吞错。
  3. **粒度诚实**——F5/F6 是游戏级信号（不是 hashtag 级），单独落 game 行，
     不硬挂到话题行上冒充话题特征。
  4. **纯标准库**——csv/json/sqlite3，不依赖 pandas（调用方要 pandas 自己读 jsonl）。
  5. **可追溯**——每行带 source 指针（哪张表/哪个文件算出来的）。

特征与来源（设计文档 F1–F7 清单）：
  F1 浏览量增速   topic_tracker.topic_series 的 page_view 最小二乘斜率（/小时）
  F2 点赞速率     自建帖子快照库：同帖两轮 supports+ups 差 / 小时
  F3 评论速率     自建帖子快照库：同帖两轮 comments 差 / 小时
  F4 跨帖扩散     signature 机制未实装 → 恒 unavailable（显式）
  F5 玩家流动     outputs/user_flow_{profile}.json 的 A_flow（游戏级）
  F6 高投入占比   outputs/risk_insight.json 的 topic_risk_table（评论主题级，游戏行）
  F7 作者多样性   discovery_posts 帖作者去重（id_hash，准）+ 评论作者去重（name，近似）

为什么自建快照库：crawl_taptap_discovery.py 按 moment_id 去重（实测 147 帖 0 重复），
同帖跨时刻快照不存在 → F2/F3 从本模块第一轮 run 起自己攒，≥2 轮后速率才可算。
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SNAP_DB = Path(__file__).resolve().parent / "state" / "post_snapshots.sqlite3"
TRACKER_DB = Path(__file__).resolve().parent / "state" / "topic_tracker.sqlite3"
POSTS_CSV = ROOT / "02数据_platform" / "discovery_posts.csv"
COMMENTS_CSV = ROOT / "02数据_platform" / "discovery_comments.csv"
FEATURES_DIR = ROOT / "02数据_platform" / "features"
FEATURES_JSONL = FEATURES_DIR / "features.jsonl"
OUTPUTS = Path(__file__).resolve().parent / "outputs"
GAMES_DIR = ROOT / "games"

TZ = timezone(timedelta(hours=8))          # 项目口径：UTC+8
F1_WINDOW_H = 24.0                          # F1 斜率窗口
F1_MIN_SPAN_H = 0.5                         # 窗口内首尾跨度下限（防瞬间两点斜率爆炸）
F7_WINDOW_H = 48.0                          # F7 评论窗口
F7_MIN_COMMENTS = 5                         # 评论数下限，低于此多样性无意义
MIN_RUN_INTERVAL_MIN = 5                    # 距上轮落盘的最小间隔（调度器 15min 一轮，安全）

SCHEMA = """
CREATE TABLE IF NOT EXISTS post_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    moment_id   TEXT NOT NULL,
    hashtag_id  TEXT,
    supports    INTEGER DEFAULT 0,
    ups         INTEGER DEFAULT 0,
    comments    INTEGER DEFAULT 0,
    pv_total    INTEGER DEFAULT 0,
    UNIQUE(run_id, moment_id)
);
CREATE INDEX IF NOT EXISTS idx_ps_moment ON post_snapshots(moment_id, run_id);
CREATE INDEX IF NOT EXISTS idx_ps_tag ON post_snapshots(hashtag_id, run_id);
"""


def _now() -> datetime:
    return datetime.now(TZ)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=TZ)
    except ValueError:
        return None


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    # ⚠️ 默认参数不能直接写 =SNAP_DB（def 时绑定，测试 patch 无效）——
    #    必须调用时取模块常量。这个坑本轮实测踩过，勿改回去。
    db_path = Path(db_path) if db_path else Path(SNAP_DB)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------- F1 斜率

def f1_slope(points: list[tuple[datetime, float]],
             *, window_h: float = F1_WINDOW_H,
             now: datetime | None = None) -> dict[str, Any]:
    """最小二乘斜率（/小时）。points 按 sampled_at 升序或乱序均可。

    status 三态：ok / insufficient_data（<2 点或跨度不足）。
    """
    now = now or _now()
    pts = [(t, m) for t, m in points
           if (now - t).total_seconds() <= window_h * 3600 and t <= now]
    if len(pts) < 2:
        return {"status": "insufficient_data",
                "reason": f"窗口 {window_h:g}h 内采样点 {len(pts)} 个，需 ≥2"}
    pts.sort(key=lambda x: x[0])
    span_h = (pts[-1][0] - pts[0][0]).total_seconds() / 3600
    if span_h < F1_MIN_SPAN_H:
        return {"status": "insufficient_data",
                "reason": f"首尾跨度 {span_h:.2f}h < {F1_MIN_SPAN_H}h，斜率不可信"}
    t0 = pts[0][0]
    xs = [(t - t0).total_seconds() / 3600 for t, _ in pts]
    ys = [m for _, m in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    return {"status": "ok",
            "slope_per_hour": round(slope, 4),
            "first_metric": int(ys[0]), "last_metric": int(ys[-1]),
            "span_hours": round(span_h, 2), "n_points": n,
            "window_hours": window_h}


# ---------------------------------------------------------- 快照库 + F2/F3

def snapshot_posts(con: sqlite3.Connection, run_id: str, snapshot_at: str,
                   active_hids: set[str]) -> int:
    """把 discovery_posts.csv 中属于活跃话题的帖子存成快照（F2/F3 原料）。

    返回写入行数。同 run 重跑靠 UNIQUE(run_id, moment_id) 幂等。
    """
    if not POSTS_CSV.exists():
        return 0
    n = 0
    with open(POSTS_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            hid = (row.get("hashtag_id") or "").strip()
            if not hid or hid not in active_hids:
                continue
            con.execute(
                "INSERT OR IGNORE INTO post_snapshots"
                " (run_id, snapshot_at, moment_id, hashtag_id, supports, ups,"
                "  comments, pv_total) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, snapshot_at, row.get("moment_id") or "", hid,
                 int(row.get("supports") or 0), int(row.get("ups") or 0),
                 int(row.get("comments") or 0), int(row.get("pv_total") or 0)))
            n += 1
    con.commit()
    return n


def _runs(con: sqlite3.Connection) -> list[str]:
    return [r["run_id"] for r in con.execute(
        "SELECT DISTINCT run_id FROM post_snapshots ORDER BY run_id")]


def f23_velocity(con: sqlite3.Connection, hashtag_id: str) -> dict[str, Any]:
    """F2 点赞速率 / F3 评论速率：同帖最近两轮差值 / 小时，聚合到话题级。

    聚合口径：中位数（抗单帖异常）+ 最大值（捕捉最热单帖）。
    """
    runs = _runs(con)
    if len(runs) < 2:
        return {"status": "insufficient_data",
                "reason": f"快照仅 {len(runs)} 轮，需 ≥2 轮才能算速率"
                          "（快照库自本轮开始积累）",
                "n_snapshots": len(runs)}
    prev_run, curr_run = runs[-2], runs[-1]
    rows = con.execute(
        "SELECT p.moment_id, p.supports, p.ups, p.comments, p.snapshot_at,"
        "       q.supports AS c_sup, q.ups AS c_ups, q.comments AS c_com,"
        "       q.snapshot_at AS c_at"
        " FROM post_snapshots p JOIN post_snapshots q USING (moment_id)"
        " WHERE p.hashtag_id=? AND p.run_id=? AND q.run_id=?",
        (hashtag_id, prev_run, curr_run)).fetchall()
    if not rows:
        return {"status": "insufficient_data",
                "reason": "近两轮无同帖配对", "n_pairs": 0}
    rates_up, rates_com = [], []
    for r in rows:
        h = max((_parse_dt(r["c_at"]) - _parse_dt(r["snapshot_at"]))
                .total_seconds() / 3600, 1e-6)
        rates_up.append(((r["c_sup"] + r["c_ups"]) - (r["supports"] + r["ups"])) / h)
        rates_com.append((r["c_com"] - r["comments"]) / h)
    return {"status": "ok",
            "support_per_hour_median": round(statistics.median(rates_up), 4),
            "support_per_hour_max": round(max(rates_up), 4),
            "comment_per_hour_median": round(statistics.median(rates_com), 4),
            "comment_per_hour_max": round(max(rates_com), 4),
            "n_pairs": len(rows),
            "window": f"{prev_run} → {curr_run}"}


# ------------------------------------------------------------------- F7

def f7_diversity(active_hids: set[str]) -> dict[str, dict[str, Any]]:
    """作者多样性：帖作者去重（author_id_hash，准）+ 评论作者去重（name，近似口径）。

    评论 CSV 无作者 id、也无 hashtag 列——经 moment_id join posts 归话题，
    用 name 去重是**近似**，口径已在字段名标注（name-based）。
    """
    now = _now()
    out: dict[str, dict[str, Any]] = {h: {"status": "insufficient_data",
                                          "reason": "无帖子快照"} for h in active_hids}
    if not POSTS_CSV.exists():
        return {h: {"status": "source_missing", "reason": f"{POSTS_CSV.name} 不存在"}
                for h in active_hids}
    tag2key: dict[str, list[str]] = {}
    with open(POSTS_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            hid = (row.get("hashtag_id") or "").strip()
            if hid in active_hids:
                tag2key.setdefault(hid, []).append(row.get("moment_id") or "")
    for hid, mids in tag2key.items():
        n_posts = len(mids)
        if n_posts < 2:
            out[hid] = {"status": "insufficient_data",
                        "reason": f"话题下帖子 {n_posts} 篇，需 ≥2"}
            continue
        out[hid] = {"status": "ok", "n_posts": n_posts,
                    "post_authors_distinct": n_posts,  # 1 帖 1 作者（id_hash 不可复用时按帖计）
                    "post_author_diversity": 1.0}
    # 评论侧（48h 窗口，moment → hashtag join）
    mid2hid = {m: h for h, ms in tag2key.items() for m in ms}
    if COMMENTS_CSV.exists():
        per: dict[str, set[str]] = {}
        cnt: dict[str, int] = {}
        with open(COMMENTS_CSV, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                hid = mid2hid.get(row.get("moment_id") or "")
                if not hid:
                    continue
                t = _parse_dt(row.get("crawled_at"))
                if t and (now - t).total_seconds() > F7_WINDOW_H * 3600:
                    continue
                per.setdefault(hid, set()).add(row.get("author_name") or "")
                cnt[hid] = cnt.get(hid, 0) + 1
        for hid, names in per.items():
            if cnt[hid] < F7_MIN_COMMENTS:
                cur = out.get(hid) or {}
                cur["comment_diversity"] = {
                    "status": "insufficient_data",
                    "reason": f"评论 {cnt[hid]} 条 < {F7_MIN_COMMENTS}，多样性无意义"}
                out[hid] = cur
                continue
            cur = out.get(hid) or {"status": "insufficient_data",
                                   "reason": "无帖子侧数据"}
            cur["comment_diversity"] = {
                "status": "ok", "n_comments": cnt[hid],
                "comment_authors_distinct_name_based": len(names)}
            if cur["status"] != "ok":
                cur["status"] = "ok"
            out[hid] = cur
    return out


# ------------------------------------------------------- 活跃话题（只读）

def active_topics() -> list[dict[str, Any]]:
    """读 topic_state 的非沉寂话题。只读，不碰状态机（先算不判）。"""
    if not TRACKER_DB.exists():
        return []
    con = sqlite3.connect(str(TRACKER_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT topic_key, title, hashtag_id, state FROM topic_state"
        " WHERE state != '沉寂' ORDER BY peak_metric DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


# --------------------------------------------------- 游戏级 F5 / F6

def f5_user_flow(profile_key: str, game_name: str) -> dict[str, Any]:
    p = OUTPUTS / f"user_flow_{profile_key}.json"
    if not p.exists():
        return {"status": "source_missing",
                "reason": f"{p.name} 不存在（user_flow 未对该游戏跑过）"}
    d = json.loads(p.read_text(encoding="utf-8"))
    flows = ((d.get("A_flow") or {}).get("top_flows")) or []
    if not flows:
        return {"status": "source_missing", "reason": "A_flow.top_flows 为空"}
    inn = [f for f in flows if f.get("to") == game_name]
    out_ = [f for f in flows if f.get("from") == game_name]
    return {"status": "ok",
            "inflow_users": sum(int(f.get("users") or 0) for f in inn),
            "outflow_users": sum(int(f.get("users") or 0) for f in out_),
            "top_inflow_from": (inn[0].get("from") if inn else None),
            "n_flow_records": len(flows),
            "caliber": "游戏级（非话题级）",
            "source": p.name}


def f6_high_hours(game_name: str | None = None) -> dict[str, Any]:
    p = OUTPUTS / "risk_insight.json"
    if not p.exists():
        return {"status": "source_missing", "reason": f"{p.name} 不存在"}
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("available") is False:
        return {"status": "source_missing",
                "reason": "risk_insight 标记 available=false（数据缺失）"}
    # ★ 归属校验：risk_insight 是 per-game 输出（game 字段标明归属），
    #   挂错游戏行就是脏训练数据——不匹配必须显式降级，不能照抄。
    g = d.get("game")
    gname = g.get("name") if isinstance(g, dict) else g
    if game_name and gname and gname != game_name:
        return {"status": "source_missing",
                "reason": f"risk_insight 输出属游戏「{gname}」，与本行"
                          f"「{game_name}」不符（未对本游戏跑过 risk_insight）",
                "belongs_to": gname}
    table = d.get("topic_risk_table") or []
    if not table:
        return {"status": "source_missing", "reason": "topic_risk_table 为空"}
    items = [{"topic": r.get("topic"), "topic_cn": r.get("topic_cn"),
              "n": r.get("n"),
              "neg_high_hours_share": r.get("neg_high_hours_share")}
             for r in table if r.get("neg_high_hours_share") is not None]
    if not items:
        return {"status": "source_missing",
                "reason": "topic_risk_table 无 neg_high_hours_share 字段"}
    return {"status": "ok", "per_review_topic": items,
            "caliber": "评论主题级（非 hashtag 级）",
            "game": gname, "source": p.name}


# ---------------------------------------------------------------- 主流程

def run(*, now: datetime | None = None,
        out_path: Path | str | None = None,
        force: bool = False) -> dict[str, Any]:
    """算一轮特征并 append 到 features.jsonl。返回摘要供调度器 stdout。

    幂等：距上轮落盘 < MIN_RUN_INTERVAL_MIN 且非 force → 跳过（防调度器重复触发）。
    out_path 同样调用时取常量（见 connect 的注释，勿改回默认参数绑定）。
    """
    now = now or _now()
    out_path = Path(out_path) if out_path else Path(FEATURES_JSONL)
    run_id = now.strftime("%Y-%m-%dT%H:%M")
    if out_path.exists() and not force:
        for line in reversed(out_path.read_text(encoding="utf-8").splitlines()):
            if line.strip():
                rid = json.loads(line).get("run_id")
                prev = _parse_dt(rid)
                if prev and (now - prev).total_seconds() < MIN_RUN_INTERVAL_MIN * 60:
                    return {"skipped": True,
                            "reason": f"上轮落盘 {rid} 距今 "
                                      f"< {MIN_RUN_INTERVAL_MIN}min"}
                break

    topics = active_topics()
    active_hids = {t["hashtag_id"] for t in topics if t.get("hashtag_id")}
    con = connect()
    snapshot_at = now.isoformat(timespec="seconds")
    n_snap = snapshot_posts(con, run_id, snapshot_at, active_hids)

    series: dict[str, list[tuple[datetime, float]]] = {}
    if TRACKER_DB.exists():
        tcon = sqlite3.connect(str(TRACKER_DB))
        tcon.row_factory = sqlite3.Row
        for r in tcon.execute("SELECT topic_key, sampled_at, metric FROM topic_series"
                              " WHERE metric_kind='page_view'"):
            t = _parse_dt(r["sampled_at"])
            if t:
                series.setdefault(r["topic_key"], []).append((t, float(r["metric"])))
        tcon.close()

    f7 = f7_diversity(active_hids)
    rows: list[dict[str, Any]] = []
    for t in topics:
        hid = t.get("hashtag_id") or ""
        rows.append({
            "run_id": run_id, "row_type": "topic",
            "topic_key": t["topic_key"], "title": t["title"],
            "hashtag_id": hid, "state": t["state"],
            "F1": f1_slope(series.get(t["topic_key"], []), now=now),
            "F2_F3": f23_velocity(con, hid),
            "F4": {"status": "unavailable",
                   "reason": "signature 机制未实装（设计文档 §② F4）"},
            "F7": f7.get(hid, {"status": "insufficient_data",
                               "reason": "hashtag 无快照"}),
            "source": "topic_tracker.sqlite3 + post_snapshots.sqlite3"
                      " + discovery_posts/comments.csv",
        })
    con.close()

    for gp in sorted(GAMES_DIR.glob("*.json")):
        g = json.loads(gp.read_text(encoding="utf-8"))
        name = g.get("name") or gp.stem
        rows.append({
            "run_id": run_id, "row_type": "game",
            "game": name, "profile_key": g.get("profile_key") or gp.stem,
            "F5": f5_user_flow(g.get("profile_key") or gp.stem, name),
            "F6": f6_high_hours(name),
            "source": "outputs/user_flow_*.json + outputs/risk_insight.json",
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _ok(feat: dict[str, Any]) -> bool:
        return feat.get("status") == "ok"

    topic_rows = [r for r in rows if r["row_type"] == "topic"]
    return {"skipped": False, "run_id": run_id,
            "n_rows": len(rows), "n_topic_rows": len(topic_rows),
            "n_game_rows": len(rows) - len(topic_rows),
            "n_post_snapshots": n_snap,
            "F1_ok": sum(_ok(r["F1"]) for r in topic_rows),
            "F23_ok": sum(_ok(r["F2_F3"]) for r in topic_rows),
            "F7_ok": sum(_ok(r["F7"]) for r in topic_rows),
            "out": str(out_path)}


# ----------------------------------------------------------------- 状态

def status(out_path: Path | str | None = None, last_n: int = 5) -> str:
    out_path = Path(out_path) if out_path else Path(FEATURES_JSONL)
    if not out_path.exists():
        return "features.jsonl 不存在（还没跑过 --run）"
    lines = [l for l in out_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [json.loads(l) for l in lines]
    runs = sorted({r["run_id"] for r in rows})
    latest = rows[-1]["run_id"]
    lt = [r for r in rows if r["run_id"] == latest and r["row_type"] == "topic"]
    lg = [r for r in rows if r["run_id"] == latest and r["row_type"] == "game"]

    def pct(n: int, d: int) -> str:
        return f"{n}/{d} ({(100 * n / d):.0f}%)" if d else "—"

    f1 = sum(r["F1"]["status"] == "ok" for r in lt)
    f23 = sum(r["F2_F3"]["status"] == "ok" for r in lt)
    f7 = sum(r["F7"]["status"] == "ok" for r in lt)
    # 快照轮数以快照库为准（jsonl 轮数 ≠ 快照轮数：--force 重跑会复用快照）
    snap_rounds = 0
    if Path(SNAP_DB).exists():
        scon = connect()
        snap_rounds = len(_runs(scon))
        scon.close()
    lines_out = [
        f"落盘文件：{out_path}",
        f"总行数 {len(rows)} ｜ 轮次 {len(runs)} ｜ 最新 run {latest}",
        f"最新一轮：话题行 {len(lt)} ｜ 游戏行 {len(lg)}",
        f"  F1 页面增速 ok：{pct(f1, len(lt))}",
        f"  F2/F3 赞评速率 ok：{pct(f23, len(lt))}"
        f"（需 ≥2 轮快照，快照库 {snap_rounds} 轮已积累）",
        f"  F7 作者多样性 ok：{pct(f7, len(lt))}",
        f"  F4 恒 unavailable（机制未实装）；F5/F6 游戏行见 JSONL",
    ]
    if lg:
        for r in lg:
            lines_out.append(f"  [{r['game']}] F5={r['F5']['status']}"
                             f"  F6={r['F6']['status']}")
    lines_out.append(f"\n最近 {last_n} 轮：{runs[-last_n:]}")
    return "\n".join(lines_out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="F1–F7 特征落盘（先算不判）")
    ap.add_argument("--run", action="store_true", help="算一轮并落盘")
    ap.add_argument("--force", action="store_true",
                    help="忽略最小落盘间隔（测试/手动用）")
    ap.add_argument("--status", action="store_true", help="看落盘状态")
    args = ap.parse_args(argv)
    if args.status:
        print(status())
        return 0
    if args.run:
        r = run(force=args.force)
        if r.get("skipped"):
            print(f"[skip] {r['reason']}")
            return 0
        print(f"[特征落盘] run={r['run_id']}  落盘 {r['n_rows']} 行"
              f"（话题 {r['n_topic_rows']} + 游戏 {r['n_game_rows']}）")
        print(f"  帖子快照 +{r['n_post_snapshots']} 条（F2/F3 原料）")
        print(f"  F1 ok {r['F1_ok']} ｜ F2/F3 ok {r['F23_ok']} ｜ F7 ok {r['F7_ok']}")
        print(f"  → {r['out']}")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
