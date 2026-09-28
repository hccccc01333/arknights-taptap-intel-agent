#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/cross_game_compare.py — 跨游戏同口径对比（平台视角）。

两种模式（可叠加）：
  - 标注层：各游戏 risk_insight.json（--risk，LLM 标注产物；需标注完成后提供）
  - 结构层：各游戏 reviews_clean.csv（--clean，TapTap 原生字段：评分 / 推荐 /
    时长 / rating_tags 分维度 up-down）——**不依赖 LLM 标注**，任何游戏可跑

输出：
  outputs/cross_game_compare.json
  reports/cross_game_compare_latest.md

口径边界：各游戏样本量与采集时间窗不同，对比是**同口径参照**，
不是同期 A/B；结论以「参照」措辞呈现。
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
LAB = Path(__file__).resolve().parent
OUT_DIR = LAB / "outputs"
REPORT_DIR = LAB / "reports"


def round2(x: float | None) -> float | None:
    return round(float(x), 2) if x is not None else None


def load_risk(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_clean_stats(path: Path) -> dict[str, Any]:
    """结构层统计：均分、不推荐率、时长中位数、rating_tags 分维度 up 率。"""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    n = len(rows)
    scores: list[float] = []
    rec_known = rec_false = 0
    hours: list[float] = []
    tag_counter: Counter = Counter()
    n_with_tags = 0
    for r in rows:
        try:
            scores.append(float(r.get("score_norm") or ""))
        except ValueError:
            pass
        rec = str(r.get("is_recommend") or "").strip().lower()
        if rec in ("true", "false"):
            rec_known += 1
            rec_false += rec == "false"
        try:
            hours.append(float(r.get("played_hours_num") or ""))
        except ValueError:
            pass
        raw = (r.get("rating_tags") or "").strip()
        if raw and raw != "[]":
            try:
                tags = json.loads(raw)
                n_with_tags += 1
                for t in tags:
                    lb, v = t.get("label"), t.get("value")
                    if lb and v in ("up", "down"):
                        tag_counter[(lb, v)] += 1
            except Exception:
                pass
    hours.sort()

    dims: dict[str, dict[str, Any]] = {}
    for (lb, v), c in tag_counter.items():
        d = dims.setdefault(lb, {"up": 0, "down": 0})
        d[v] += c
    dim_rows = []
    for lb, d in dims.items():
        total = d["up"] + d["down"]
        dim_rows.append(
            {
                "dimension": lb,
                "up": d["up"],
                "down": d["down"],
                "up_rate": round2(d["up"] / total) if total else None,
                "total": total,
            }
        )
    dim_rows.sort(key=lambda r: -(r["up_rate"] if r["up_rate"] is not None else -1))

    hours_median = hours[len(hours) // 2] if hours else None
    return {
        "n": n,
        "n_with_tags": n_with_tags,
        "mean_score_norm": round2(sum(scores) / len(scores)) if scores else None,
        "not_recommend_rate": round2(rec_false / rec_known) if rec_known else None,
        "played_hours_median": round2(hours_median) if hours_median is not None else None,
        "tag_dimensions": dim_rows,
    }


def build_payload(entries: list[dict[str, Any]]) -> dict[str, Any]:
    games: list[dict[str, Any]] = []
    for e in entries:
        g: dict[str, Any] = {"game": e["name"]}
        if e.get("risk") is not None:
            risk = e["risk"]
            f = risk.get("facts", {})
            g.update(
                {
                    "n_total": f.get("n_total"),
                    "n_neg": f.get("n_neg"),
                    "neg_rate_pp": f.get("neg_rate_pp"),
                    "n_high_investment_negative": f.get("n_high_investment_negative"),
                    "high_investment_share_of_neg_pp": f.get(
                        "high_investment_share_of_neg_pp"
                    ),
                    "n_rhetoric_disguised": f.get("n_rhetoric_disguised"),
                    "n_actionable_negative": f.get("n_actionable_negative"),
                    "headline": risk.get("headline", ""),
                }
            )
        if e.get("clean_stats") is not None:
            g["structure"] = e["clean_stats"]
        games.append(g)

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "games": games,
        "caliber": "同口径参照对比（各游戏样本量与采集时间窗不同，非同期 A/B）",
        "boundaries": [
            "各游戏样本量不同（如 3000 vs 300），占比对比需结合 n 看",
            "标注层（负向率 / 修辞伪装）仅在 LLM 标注完成后可比；结构层为 TapTap 原生字段，任何游戏可跑",
            "rating_tags 为玩家自选维度，不同游戏玩家打标签习惯不同（维度覆盖不均匀），对比以同维度内 up 率为准",
        ],
    }


def render_report(p: dict[str, Any]) -> str:
    games = p["games"]
    has_risk = any("n_total" in g for g in games)
    lines: list[str] = [
        "# 跨游戏同口径对比（11情报Agent）",
        "",
        f"> 生成：{p['generated_at']} · {p['caliber']}",
        "",
        "## 一句话画像",
        "",
    ]
    for g in games:
        if g.get("headline"):
            lines.append(f"- **{g['game']}**：{g['headline']}")
        elif "structure" in g:
            s = g["structure"]
            lines.append(
                f"- **{g['game']}**：n={s.get('n')}，均分 {s.get('mean_score_norm')}，"
                f"不推荐率 {s.get('not_recommend_rate') if s.get('not_recommend_rate') is not None else '—'}"
                f"（结构层口径，标注待补）"
            )
    lines.append("")

    if has_risk:
        lines += [
            "## 标注层对比",
            "",
            "| 指标 | " + " | ".join(g["game"] for g in games) + " |",
            "|" + "------|" * (len(games) + 1),
        ]
        for label, key in [
            ("样本 n", "n_total"),
            ("负向 n", "n_neg"),
            ("负向率 %", "neg_rate_pp"),
            ("高投入负向 n", "n_high_investment_negative"),
            ("高投入占负向 %", "high_investment_share_of_neg_pp"),
            ("修辞伪装负向 n", "n_rhetoric_disguised"),
            ("可行动差评 n", "n_actionable_negative"),
        ]:
            lines.append(
                f"| {label} | " + " | ".join(str(g.get(key, "—")) for g in games) + " |"
            )
        lines.append("")

    # 结构层
    struct_games = [g for g in games if "structure" in g]
    if struct_games:
        lines += [
            "## 结构层对比（TapTap 原生字段，不依赖标注）",
            "",
            "| 指标 | " + " | ".join(g["game"] for g in struct_games) + " |",
            "|" + "------|" * (len(struct_games) + 1),
        ]
        for label, key, fmt in [
            ("均分（0-1）", "mean_score_norm", "{:.2f}"),
            ("不推荐率", "not_recommend_rate", "{:.1%}"),
            ("时长中位数 h", "played_hours_median", "{:.0f}"),
            ("含维度标签样本 n", "n_with_tags", "{}"),
        ]:
            vals = []
            for g in struct_games:
                v = g["structure"].get(key)
                vals.append(fmt.format(v) if isinstance(v, (int, float)) else "—")
            lines.append(f"| {label} | " + " | ".join(vals) + " |")

        # 分维度 up 率（union of dimensions）
        dim_names: list[str] = []
        for g in struct_games:
            for d in g["structure"].get("tag_dimensions", []):
                if d["dimension"] not in dim_names:
                    dim_names.append(d["dimension"])
        if dim_names:
            lines += [
                "",
                "### 分维度 up 率（玩家选「好」占比；同维度内横向可比）",
                "",
                "| 维度 | " + " | ".join(g["game"] for g in struct_games) + " |",
                "|" + "------|" * (len(struct_games) + 1),
            ]
            for dim in dim_names:
                vals = []
                for g in struct_games:
                    d = next(
                        (
                            x
                            for x in g["structure"].get("tag_dimensions", [])
                            if x["dimension"] == dim
                        ),
                        None,
                    )
                    if d and d["up_rate"] is not None:
                        vals.append(
                            f"{d['up_rate']:.0%}（{d['up']}/{d['total']}）"
                        )
                    else:
                        vals.append("—")
                lines.append(f"| {dim} | " + " | ".join(vals) + " |")

    lines += ["", "## 边界", ""]
    for b in p["boundaries"]:
        lines.append(f"- {b}")
    lines += ["", "---", "", "*报告结束*", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="跨游戏同口径对比")
    ap.add_argument(
        "--risk",
        action="append",
        default=[],
        help="格式: 游戏名=risk_insight.json 路径（可多次；需标注完成）",
    )
    ap.add_argument(
        "--clean",
        action="append",
        default=[],
        help="格式: 游戏名=reviews_clean.csv 路径（可多次；结构层，不依赖标注）",
    )
    args = ap.parse_args()
    if not args.risk and not args.clean:
        ap.error("至少提供 --risk 或 --clean 之一")

    entries: list[dict[str, Any]] = []
    for spec in args.risk:
        name, _, path = spec.partition("=")
        entries.append(
            {"name": name.strip(), "risk": load_risk(Path(path.strip())), "clean_stats": None}
        )
    for spec in args.clean:
        name, _, path = spec.partition("=")
        existing = next((e for e in entries if e["name"] == name.strip()), None)
        if existing:
            existing["clean_stats"] = load_clean_stats(Path(path.strip()))
        else:
            entries.append(
                {"name": name.strip(), "risk": None, "clean_stats": load_clean_stats(Path(path.strip()))}
            )

    payload = build_payload(entries)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUT_DIR / "cross_game_compare.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md = REPORT_DIR / "cross_game_compare_latest.md"
    out_md.write_text(render_report(payload), encoding="utf-8")
    print(out_json)
    print(out_md)


if __name__ == "__main__":
    main()
