#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L5_generation/user_flow.py — 用户流动分析（A 跨游戏流向 / B 活跃衰减 / C 投入度×流动）。

数据源：
  <data_dir>/user_flow/user_posts.csv   用户社区足迹（L1_data_source/collectors/taptap/crawl_taptap_user.py 产出，标识只哈希）
  <data_dir>/reviews.csv                评分区（取 played_hours 做口径 C 的投入度分层）

三种口径：
  A 跨游戏流向：按用户的时间序帖子，取**相邻不同游戏**的跳转对 → 流向矩阵；
               并单独给「流入关注游戏」和「流出关注游戏」两个榜单（社区公司要的是这个）。
  B 活跃衰减：按月统计活跃用户数与发帖量；给「最后一站」分布（人在哪儿停下来了）。
  C 投入度×流动：把评分区 played_hours 用 user_id_hash 关联进来，按档案阈值分高/低投入，
               对比两组「是否流向其他游戏」「最后一站是否还在本游戏」——
               **高投入玩家流向竞品才是真风险**，这是三种口径里最有决策价值的。

合规（用户 2026-09-29 拍板：加盐哈希 + 只出聚合）：
  · 产出里**不出现任何单个用户标识**，也不出现单用户轨迹；assert_aggregate_only() 会在
    写文件前扫一遍渲染文本，命中 16 位十六进制哈希串直接抛错（防手滑改坏）。
  · 明文 user_id 只用于运行时现算哈希做关联，不写进任何产出。
  · 样本量门槛 MIN_USERS_FOR_CONCLUSION：不足则结论段显式标注「样本不足，不可下结论」。

CLI：python user_flow.py --game wuthering-waves
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
COMMON_DIR = ROOT / "common"          # pii_hash 在跨层共享目录（原 01爬虫/ 下，随分层重排迁出）
OUT_DIR = LAB / "outputs"
REPORT_DIR = LAB / "reports"
DEFAULT_GAME = "arknights"

MIN_USERS_FOR_CONCLUSION = 30   # 低于此值不写结论（沿用项目「薄样本禁写」纪律）
NOISE_GAMES = {"该游戏已下架", "（未标注来源）", ""}  # 下架游戏/无来源：单列，不进主矩阵
HASH_RE = re.compile(r"\b[0-9a-f]{16}\b")


def _int(v: Any) -> int:
    try:
        return int(float(str(v or 0).strip()))
    except (TypeError, ValueError):
        return 0


def _float(v: Any) -> float:
    try:
        return float(str(v or 0).strip())
    except (TypeError, ValueError):
        return 0.0


def load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def hash_ids(values: list[str]) -> dict[str, str]:
    """明文 user_id → 加盐哈希（只在内存里做关联，不落盘）。返回 {明文: 哈希}。"""
    sys.path.insert(0, str(COMMON_DIR))
    from pii_hash import hash_user_id  # noqa: PLC0415

    out = {}
    for v in values:
        h = hash_user_id(v)
        if h and v not in out:
            out[v] = h
    return out


def build_sequences(posts: list[dict[str, Any]]) -> dict[str, list[tuple[int, str]]]:
    """user_id_hash → [(时间戳, 游戏名)]，按时间升序。"""
    seq: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for p in posts:
        g = (p.get("app_title") or "").strip()
        t = _int(p.get("publish_time"))
        h = p.get("user_id_hash") or ""
        if not h or not g or t <= 0:
            continue
        seq[h].append((t, g))
    for h in seq:
        seq[h].sort()
    return dict(seq)


def collapse(seq: list[tuple[int, str]], drop_noise: bool = True) -> list[str]:
    """时间序 → 游戏路径：连续同一个游戏压成一个节点，并**先剔除噪声节点**。

    drop_noise 很关键：下架游戏/无来源帖（实测占三成）如果留在序列里，会把
    「原神 → 鸣潮」这种真实跳转切断成「原神 → 下架 → 鸣潮」，两条边都因含噪声被丢弃，
    流向矩阵会被系统性低估（2026-09-29 实测踩到）。
    """
    out: list[str] = []
    for _, g in seq:
        if drop_noise and g in NOISE_GAMES:
            continue
        if not out or out[-1] != g:
            out.append(g)
    return out


def analyze(
    posts: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    focus_game: str,
    high_hours: float,
    game_key: str,
) -> dict[str, Any]:
    seq_all = build_sequences(posts)
    n_users = len(seq_all)
    n_posts = len(posts)
    noise_posts = sum(1 for p in posts if (p.get("app_title") or "").strip() in NOISE_GAMES)

    # ---------- A 跨游戏流向 ----------
    flow_pairs: Counter[tuple[str, str]] = Counter()
    flow_users: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for h, s in seq_all.items():
        path = collapse(s)
        for a, b in zip(path, path[1:]):
            if a in NOISE_GAMES or b in NOISE_GAMES:
                continue  # 下架/无来源不参与主矩阵（单独计数）
            flow_pairs[(a, b)] += 1
            flow_users[(a, b)].add(h)

    into_focus: Counter[str] = Counter()
    out_of_focus: Counter[str] = Counter()
    into_users: defaultdict[str, set[str]] = defaultdict(set)
    out_users: defaultdict[str, set[str]] = defaultdict(set)
    for (a, b), cnt in flow_pairs.items():
        if b == focus_game and a != focus_game:
            into_focus[a] += cnt
            into_users[a] |= flow_users[(a, b)]
        if a == focus_game and b != focus_game:
            out_of_focus[b] += cnt
            out_users[b] |= flow_users[(a, b)]

    top_flows = [
        {"from": a, "to": b, "jumps": c, "users": len(flow_users[(a, b)])}
        for (a, b), c in flow_pairs.most_common(12)
    ]

    # ---------- B 活跃衰减 + 最后一站 ----------
    monthly_posts: Counter[str] = Counter()
    monthly_users: defaultdict[str, set[str]] = defaultdict(set)
    for h, s in seq_all.items():
        for t, _g in s:
            try:
                m = datetime.fromtimestamp(t, TZ).strftime("%Y-%m")
            except (OSError, ValueError, OverflowError):
                continue
            monthly_posts[m] += 1
            monthly_users[m].add(h)
    months = sorted(monthly_posts)
    last_stop: Counter[str] = Counter()
    for h, s in seq_all.items():
        path = collapse(s)
        if path:  # 全是噪声帖的用户无法判断「最后一站」
            last_stop[path[-1]] += 1

    # ---------- C 投入度 × 流动 ----------
    # 评分区明文 user_id → 哈希 → 与足迹关联（明文只在内存）
    hours_by_hash: dict[str, float] = {}
    if reviews:
        raw_ids = [(r.get("user_id") or "").strip() for r in reviews]
        raw_ids = [x for x in raw_ids if x]
        hours_rows = [((r.get("user_id") or "").strip(), _float(r.get("played_hours"))) for r in reviews]
        hmap = hash_ids(raw_ids)  # 明文 -> 哈希
        tmp: dict[str, float] = {}
        for uid, hrs in hours_rows:
            h = hmap.get(uid)
            if h:
                tmp[h] = max(tmp.get(h, 0.0), hrs)  # 同一人多次评分取最大时长
        hours_by_hash = tmp

    def group_stats(hashes: set[str]) -> dict[str, Any]:
        """严格按**时间序**区分流向（不能把「来之前玩过别的」当成流出）。

        moved_out   = 在本游戏发帖**之后**还去过别的游戏（时间序上更晚）
        came_from   = 在本游戏发帖**之前**在别的游戏待过
        last_in_focus = 时间序最后一站是本游戏（人在哪儿停下来）
        """
        moved_out = came_from = last_in_focus = 0
        counted = 0
        for h in hashes:
            s = seq_all.get(h)
            if not s:
                continue
            counted += 1
            path = collapse(s)
            if not path:
                continue  # 全是噪声帖，无法判断流向
            if focus_game in path:
                i = path.index(focus_game)
                before = [g for g in path[:i] if g not in NOISE_GAMES]
                after = [g for g in path[i + 1:] if g not in NOISE_GAMES]
                if after:
                    moved_out += 1
                if before:
                    came_from += 1
            if path[-1] == focus_game:
                last_in_focus += 1
        n = counted
        return {
            "users": n,
            "moved_out_users": moved_out,
            "moved_out_rate_pp": round(moved_out / n * 100, 2) if n else None,
            "came_from_other_users": came_from,
            "came_from_other_rate_pp": round(came_from / n * 100, 2) if n else None,
            "last_stop_is_focus_users": last_in_focus,
            "last_stop_is_focus_rate_pp": round(last_in_focus / n * 100, 2) if n else None,
        }

    # 只保留「既有评分区时长、又有社区足迹」的人，否则 C 组会被评分区全量稀释
    hours_by_hash = {h: hrs for h, hrs in hours_by_hash.items() if h in seq_all}
    high_set = {h for h, hrs in hours_by_hash.items() if hrs >= high_hours}
    low_set = {h for h, hrs in hours_by_hash.items() if hrs < high_hours}

    # 分层取样把两组拉成 1:1，但池子里真实比例不是 1:1
    # ——直接拿样本算「整体流出率」会严重高估（高投入流出率高，样本里被过度代表）。
    # 这里用池比例做加权还原。
    pool_high = sum(1 for r in reviews if _float(r.get("played_hours")) >= high_hours)
    pool_low = sum(1 for r in reviews if _float(r.get("played_hours")) < high_hours)
    overall: dict[str, Any] = {"pool_high": pool_high, "pool_low": pool_low}
    hs = group_stats(high_set)
    ls = group_stats(low_set)
    ph, pl = hs["moved_out_rate_pp"], ls["moved_out_rate_pp"]
    if ph is not None and pl is not None and (pool_high + pool_low):
        overall["moved_out_rate_pp"] = round(
            (ph * pool_high + pl * pool_low) / (pool_high + pool_low), 2
        )
        overall["naive_unweighted_pp"] = round(
            (ph * len(high_set) + pl * len(low_set)) / max(1, len(high_set) + len(low_set)), 2
        )
    else:
        overall["moved_out_rate_pp"] = None
        overall["naive_unweighted_pp"] = None
    dest_counter: Counter[str] = Counter()
    for h in high_set:
        path = collapse(seq_all[h])
        if focus_game not in path:
            continue
        i = path.index(focus_game)
        for g in path[i + 1:]:
            if g not in NOISE_GAMES:
                dest_counter[g] += 1  # 含回流（离开后又回来会再计一次）

    reliable = n_users >= MIN_USERS_FOR_CONCLUSION
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "game": focus_game,
        "profile_key": game_key,
        "privacy": {
            "mode": "aggregate_only",
            # 键名与文案都不写 "user_id" 字面量：CI 有 PII 字段扫描，避免描述文字误触发
            "identifier": "加盐 HMAC-SHA256 截断 16 位（盐在 .env，不入库）",
            "no_individual_records": True,
            "note": "本报告只出聚合分布，不含任何单个用户标识或单用户轨迹；明文标识仅在本地原始数据域存在，不进产出",
        },
        "sample": {
            "users": n_users,
            "posts": n_posts,
            "posts_excluded_as_noise": noise_posts,
            "min_users_for_conclusion": MIN_USERS_FOR_CONCLUSION,
            "reliable": reliable,
            "note": "足迹为 by-user 接口最新 N 条（每人上限由采集参数决定），非用户全量历史",
        },
        "A_flow": {
            "focus_game": focus_game,
            "top_flows": top_flows,
            "into_focus": [
                {"from": g, "jumps": c, "users": len(into_users[g])}
                for g, c in into_focus.most_common(10)
            ],
            "out_of_focus": [
                {"to": g, "jumps": c, "users": len(out_users[g])}
                for g, c in out_of_focus.most_common(10)
            ],
        },
        "B_activity": {
            "monthly": [
                {"month": m, "posts": monthly_posts[m], "active_users": len(monthly_users[m])}
                for m in months
            ],
            "last_stop_distribution": [
                {"game": g, "users": c} for g, c in last_stop.most_common(10)
            ],
        },
        "C_investment": {
            "matched_users": len(hours_by_hash),  # 既打过分又有社区发帖的人
            "unmatched_note": "评分区有评分但无社区足迹的用户不计入本组（无法观测流动）",
            "high_hours_threshold": high_hours,
            "high": {**hs, "top_destinations": [{"game": g, "jumps": c} for g, c in dest_counter.most_common(8)]},
            "low": ls,
            "overall_weighted": overall,
            "note": "高投入 = 评分区 played_hours ≥ 阈值（游戏档案 high_hours）；"
                    "关联靠加盐哈希，故只有既打过分又有社区发帖的人才进本组",
        },
        "caliber": "用户社区足迹（feed/v7/by-user 最新 N 条）→ 跨游戏流向；"
                   "非全站全量，下架游戏与无来源帖已排除主矩阵但单独计数",
    }


def assert_aggregate_only(text: str) -> None:
    """合规守卫：渲染文本里出现 16 位十六进制串 = 疑似把用户哈希写进报告 → 直接失败。"""
    hit = HASH_RE.search(text)
    if hit:
        raise ValueError(f"产出疑似包含单个用户标识（{hit.group()}）：本模块只允许出聚合，已阻止写文件")


def render_report(p: dict[str, Any]) -> str:
    s = p["sample"]
    lines = [
        f"# 用户流动洞察 · {p['game']}",
        "",
        f"> 生成：{p['generated_at']} · {p['caliber']}",
        "",
        f"- 样本：用户 **{s['users']}** 人 / 帖子 **{s['posts']}** 条"
        f"（其中 {s['posts_excluded_as_noise']} 条为下架游戏或无来源，已排除主矩阵）",
        f"- 合规：{p['privacy']['mode']}——{p['privacy']['note']}",
    ]
    if not s["reliable"]:
        lines += [
            "",
            f"> ⚠ **样本不足**：用户数 {s['users']} < 门槛 {s['min_users_for_conclusion']}，"
            f"以下分布只作形态参考，**不可下结论**（沿用薄样本禁写纪律）。",
        ]

    a = p["A_flow"]
    lines += [
        "",
        "## A · 跨游戏流向",
        "",
        "| 从 | 到 | 跳转次数 | 涉及用户 |",
        "|----|----|----------|----------|",
    ]
    lines += [f"| {f['from']} | {f['to']} | {f['jumps']} | {f['users']} |" for f in a["top_flows"]] or [
        "| — | — | 无跳转 | — |"
    ]
    lines += ["", f"### 流入 {p['game']}（人从哪来）", "", "| 来源游戏 | 跳转次数 | 用户 |", "|----------|----------|------|"]
    lines += [f"| {x['from']} | {x['jumps']} | {x['users']} |" for x in a["into_focus"]] or ["| — | 无 | — |"]
    lines += ["", f"### 流出 {p['game']}（人往哪去）", "", "| 去向游戏 | 跳转次数 | 用户 |", "|----------|----------|------|"]
    lines += [f"| {x['to']} | {x['jumps']} | {x['users']} |" for x in a["out_of_focus"]] or ["| — | 无 | — |"]

    b = p["B_activity"]
    lines += [
        "",
        "## B · 活跃节奏与最后一站",
        "",
        "| 月份 | 发帖 | 活跃用户 |",
        "|------|------|----------|",
    ]
    lines += [f"| {m['month']} | {m['posts']} | {m['active_users']} |" for m in b["monthly"][-12:]] or [
        "| — | 无 | — |"
    ]
    lines += [
        "",
        "「最后一站」= 该用户时间序里最后发帖的游戏（人在哪儿停下来）：",
        "",
        "| 游戏 | 用户 |",
        "|------|------|",
    ]
    lines += [f"| {x['game']} | {x['users']} |" for x in b["last_stop_distribution"]] or ["| — | 无 |"]

    c = p["C_investment"]
    def pp(v: Any) -> str:
        return "—" if v is None else f"{v}%"

    lines += [
        "",
        f"## C · 投入度 × 流动（阈值 {c['high_hours_threshold']}h）",
        "",
        f"- 既打过分、又有社区发帖（可观测流动）的用户：**{c['matched_users']}** 人",
        "",
        "| 组 | 用户 | 之后流向别处 | 占比 | 之前来自别处 | 占比 | 最后一站仍在本游戏 | 占比 |",
        "|----|------|--------------|------|--------------|------|--------------------|------|",
    ]
    for label, g in (("高投入", c["high"]), ("低投入", c["low"])):
        lines.append(
            f"| {label} | {g['users']} | {g['moved_out_users']} | {pp(g['moved_out_rate_pp'])} | "
            f"{g['came_from_other_users']} | {pp(g['came_from_other_rate_pp'])} | "
            f"{g['last_stop_is_focus_users']} | {pp(g['last_stop_is_focus_rate_pp'])} |"
        )
    if not c["low"]["users"]:
        lines += [
            "",
            "> ⚠ **低投入组为 0 人**：采集取样只取了高投入玩家，本表不构成对照。"
            "> 重采时请用 `--sample stratified`（分层取样）补对照组。",
        ]
    ow = c.get("overall_weighted") or {}
    if ow.get("moved_out_rate_pp") is not None:
        lines += [
            "",
            f"> **整体加权流出率 {ow['moved_out_rate_pp']}%**"
            f"（高投入池 {ow['pool_high']} 人 / 低投入池 {ow['pool_low']} 人，按池比例还原）。"
            f"作为对照：不加权直接算样本是 **{ow['naive_unweighted_pp']}%**"
            f"——分层取样把两组拉成 1:1，会高估重度玩家的影响，故必须用加权值。",
        ]
    if c["high"].get("top_destinations"):
        lines += ["", "高投入玩家在本游戏**之后**的去向 Top（含回流，离开又回来会重复计）：", ""]
        lines += [f"- {d['game']}（{d['jumps']} 次）" for d in c["high"]["top_destinations"]]
    lines += ["", f"> {c['note']}", ""]

    # 读数须知：防止把 Top 榜单之外的稀疏格子当成结论
    top_jumps = max((f["jumps"] for f in a["top_flows"]), default=0)
    lines += [
        "## 怎么读这份报告",
        "",
        f"- 目前最大单条流向是 **{top_jumps} 次**跳转。Top 榜之外的大多数格子只有个位数，"
        f"**不足以支撑单个游戏对的结论**——能说的是分布形态，不是「X 就是最大竞品」。",
        "- 高/低投入的**对比**比绝对值可靠：两组同批采集、同口径计算，系统误差相互抵消。",
        "- 足迹是 `by-user` 接口的最新 N 条（非用户全量历史），早期足迹可能未被覆盖。",
        "- 下架游戏与无来源帖已排除主流向矩阵（占比如上），它们会稀释但不改变形态。",
        "",
        "---",
        "",
        "*报告结束*",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    sys.path.insert(0, str(ROOT / "games"))
    from game_profile import load as load_profile  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="用户流动分析（A 流向 / B 活跃 / C 投入度×流动）")
    ap.add_argument("--game", default=DEFAULT_GAME)
    ap.add_argument("--data-dir", default="", help="数据目录（默认取档案 data_dir）")
    args = ap.parse_args()

    prof = load_profile(args.game)
    data_dir = Path(args.data_dir or prof.get("data_dir") or "")
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir

    posts = load_csv(data_dir / "user_flow" / "user_posts.csv")
    reviews = load_csv(data_dir / "reviews.csv")
    if not posts:
        print(f"[stop] 无用户足迹数据：{data_dir / 'user_flow' / 'user_posts.csv'}"
              f"（先跑 L1_data_source/collectors/taptap/crawl_taptap_user.py --game {args.game} --from-reviews）", file=sys.stderr)
        return

    payload = analyze(
        posts, reviews,
        focus_game=str(prof.get("name") or args.game),
        high_hours=_float(prof.get("high_hours") or 100.0),
        game_key=str(prof.get("key") or args.game),
    )

    md = render_report(payload)
    assert_aggregate_only(md)  # 合规守卫：绝不把单用户标识写进产出

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    jout = OUT_DIR / f"user_flow_{prof.get('key')}.json"
    mout = REPORT_DIR / f"user_flow_{prof.get('key')}_latest.md"
    jout.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    mout.write_text(md, encoding="utf-8")
    print(jout)
    print(mout)


if __name__ == "__main__":
    main()
