#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 README 可视化资产（docs/assets/*.png）—— 全部读真实库，可重跑。

    python scripts/generate_assets.py

三个图：
  architecture.png  六层架构总览（Capture→Act + 控制面 + 两个回写闭环）
  funnel.png        §34 转化漏斗（L2/L3/L4/L5/L6 五个库的真实计数）
  signal_scores.png L3 事件信号散点（hot × confidence，颜色=生命周期，真实快照）
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
STATE = ROOT / "data" / "state"

# 深色主题（与 L6 工作台同色系）
BG, CARD, INK, DIM = "#0f172a", "#1e293b", "#e2e8f0", "#94a3b8"
LAYER_COLORS = ["#38bdf8", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#fb923c"]

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False


def _db(db: str, sql: str, args: tuple = ()):
    conn = sqlite3.connect(str(STATE / db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _card(ax, x, y, w, h, color, tag, title, lines, sub=None):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
                                fc=CARD, ec=color, lw=2.2, mutation_aspect=1.0))
    ax.text(x + w / 2, y + h - 0.075, tag, ha="center", va="center",
            fontsize=15, fontweight="bold", color=color)
    ax.text(x + w / 2, y + h - 0.155, title, ha="center", va="center",
            fontsize=13.5, fontweight="bold", color=INK)
    for i, line in enumerate(lines):
        ax.text(x + w / 2, y + h - 0.245 - i * 0.062, line, ha="center", va="center",
                fontsize=8.6, color=DIM)


def architecture() -> None:
    fig, ax = plt.subplots(figsize=(16, 9), dpi=160)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(0.5, 0.955, "TapTap Growth Intelligence OS · 六层架构",
            ha="center", va="center", fontsize=21, fontweight="bold", color=INK)
    ax.text(0.5, 0.905, "Capture → Understand → Detect → Reason → Learn → Act",
            ha="center", va="center", fontsize=12.5, color="#7dd3fc", family="monospace")

    layers = [
        ("L1", "采集", "哪里出现了新信号？", ["Registry · Planner", "Connector · Event Bus"]),
        ("L2", "加工", "每条信号是什么意思？", ["Canonical Model", "清洗 · 去重 · 实体"]),
        ("L3", "趋势", "什么事件正在爆？", ["聚类 + 八信号", "生命周期状态机"]),
        ("L4", "情报", "对 TapTap 意味着什么？", ["LangGraph 推理图", "Evidence→Creative→Risk"]),
        ("L5", "记忆", "过去什么有效？", ["6 类 Memory", "检索 · Case 蒸馏"]),
        ("L6", "执行", "现在做什么、效果如何？", ["Feed · 决策工作流", "实验 · 告警 · 漏斗"]),
    ]
    n = len(layers)
    w, h, gap, y0 = 0.138, 0.42, 0.0145, 0.36
    x0 = (1 - n * w - (n - 1) * gap) / 2
    centers = []
    for i, (tag, title, q, mods) in enumerate(layers):
        x = x0 + i * (w + gap)
        c = LAYER_COLORS[i]
        _card(ax, x, y0, w, h, c, tag, title, mods)
        ax.text(x + w / 2, y0 + 0.045, q, ha="center", va="center",
                fontsize=8.8, color=c, fontweight="bold")
        centers.append(x + w / 2)
        if i < n - 1:  # 主链箭头
            ax.add_patch(FancyArrowPatch((x + w + 0.001, y0 + h / 2),
                                         (x + w + gap - 0.001, y0 + h / 2),
                                         arrowstyle="-|>", mutation_scale=22,
                                         color=INK, lw=2))

    # 控制面横带
    ax.add_patch(FancyBboxPatch((x0, 0.14), n * w + (n - 1) * gap, 0.10,
                                boxstyle="round,pad=0.012,rounding_size=0.02",
                                fc="#0b1220", ec="#64748b", lw=1.8))
    ax.text(x0 + (n * w + (n - 1) * gap) / 2, 0.208,
            "runtime 控制面 —— 时钟驱动的任务链", ha="center", fontsize=12.5,
            fontweight="bold", color=INK)
    ax.text(x0 + (n * w + (n - 1) * gap) / 2, 0.168,
            "任务契约 · harness（校验/预算/幂等/轨迹）· LangGraph 巡检图 · 调度器 15min",
            ha="center", fontsize=9, color=DIM)
    ax.add_patch(FancyArrowPatch((0.5, 0.24), (0.5, y0 - 0.001),
                                 arrowstyle="-|>", mutation_scale=20,
                                 color="#64748b", lw=1.8, linestyle=(0, (4, 3))))

    # 闭环①：L4/L6 人工闸门 → L5 记忆（Human Feedback Loop）
    ax.add_patch(FancyArrowPatch((centers[4] + 0.02, y0 - 0.02), (centers[3] + 0.06, y0 - 0.02),
                                 connectionstyle="arc3,rad=0.35", arrowstyle="-|>",
                                 mutation_scale=18, color=LAYER_COLORS[4], lw=2))
    ax.text((centers[3] + centers[4]) / 2 + 0.02, y0 - 0.115,
            "闭环① 人工决策 / 实验结果 → 记忆", ha="center", fontsize=9.2, color=LAYER_COLORS[4])
    # 闭环②：L6 运营约束 → L4（Operational Loop）
    ax.add_patch(FancyArrowPatch((centers[5] - 0.01, y0 + h + 0.02), (centers[3] + 0.05, y0 + h + 0.02),
                                 connectionstyle="arc3,rad=-0.3", arrowstyle="-|>",
                                 mutation_scale=18, color=LAYER_COLORS[5], lw=2))
    ax.text((centers[3] + centers[5]) / 2, y0 + h + 0.085,
            "闭环② 运营约束（ops_context）回灌 Agent", ha="center",
            fontsize=9.2, color=LAYER_COLORS[5])

    ax.text(0.5, 0.045, "数据湖 data/：raw · events · state（L1..L6 各层 SQLite，运行时库不入 git）"
                        "     |     游戏档案 games/<key>.json 参数化换游戏",
            ha="center", fontsize=9.5, color=DIM)
    ASSETS.mkdir(parents=True, exist_ok=True)
    fig.savefig(ASSETS / "architecture.png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)


def funnel() -> None:
    stages = [
        ("原始信号", None), ("趋势事件", None), ("已分析事件", None),
        ("增长机会", None), ("创意", None), ("人工采纳", None),
        ("实际上线", None), ("实验 WIN", None),
    ]
    n_signals = _db("l2_processed.sqlite3", "SELECT COUNT(*) c FROM content")[0]["c"]
    n_events = _db("l3_trend.sqlite3", "SELECT COUNT(*) c FROM trend_event"
                    " WHERE status='active'")[0]["c"]
    n_analyzed = _db("l4_intelligence.sqlite3",
                     "SELECT COUNT(DISTINCT event_id) c FROM intelligence_analysis")[0]["c"]
    n_opp = _db("l4_intelligence.sqlite3", "SELECT COALESCE(SUM(n_opportunities),0) c"
                " FROM intelligence_analysis")[0]["c"]
    n_cre = _db("l4_intelligence.sqlite3", "SELECT COUNT(*) c FROM intelligence_creative")[0]["c"]
    n_adopt = _db("l5_memory.sqlite3", "SELECT COUNT(*) c FROM decision_memory"
                  " WHERE object_type='creative' AND decision='approve'")[0]["c"]
    n_launch = _db("l6_execution.sqlite3", "SELECT COUNT(*) c FROM execution_plan"
                   " WHERE status IN ('live','completed')")[0]["c"]
    n_win = _db("l6_execution.sqlite3", "SELECT COUNT(*) c FROM experiment"
                " WHERE result_state='WIN'")[0]["c"]
    values = [n_signals, n_events, n_analyzed, n_opp, n_cre, n_adopt, n_launch, n_win]

    fig, ax = plt.subplots(figsize=(12.5, 7), dpi=160)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    vmax = max(values) or 1
    colors = plt.get_cmap("Blues_r")([0.25 + 0.55 * (1 - i / (len(values) - 1))
                                     for i in range(len(values))])
    y = len(values)
    for i, ((name, _), v) in enumerate(zip(stages, values)):
        half = max(v, vmax * 0.012) / vmax * 0.42
        ax.barh(y - i, half * 2, left=0.5 - half, height=0.72,
                color=colors[i], edgecolor=BG, lw=1.5)
        bar_lum = 0.25 + 0.55 * (1 - i / (len(values) - 1))
        ax.text(0.5, y - i, f"{v:,}" if v >= 1000 else str(v),
                ha="center", va="center", fontsize=13, fontweight="bold",
                color="#0f172a" if bar_lum > 0.55 else "white")
        ax.text(0.015, y - i, name, ha="left", va="center",
                fontsize=12, fontweight="bold", color=INK)
        if i > 0 and values[i - 1]:
            rate = v / values[i - 1] * 100
            ax.text(0.945, y - i + 0.42, f"→ 转化 {rate:.1f}%", ha="right",
                    va="center", fontsize=9, color="#7dd3fc")
    ax.set_xlim(0, 1)
    ax.set_ylim(0.2, y + 0.9)
    ax.axis("off")
    ax.text(0.5, y + 0.62, "AI Growth System 转化漏斗（§34）—— 全真实库计数",
            ha="center", fontsize=17, fontweight="bold", color=INK)
    ax.text(0.5, 0.06, "0 就是 0：漏斗断点如实可见（Won=0 是因为没有已完成的实验，不是缺数据）"
                       "  |  python L6_execution/execution/pipeline.py --funnel",
            ha="center", fontsize=9, color=DIM)
    fig.savefig(ASSETS / "funnel.png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)


def signal_scores() -> None:
    rows = _db("l3_trend.sqlite3",
               "SELECT hot_score, confidence_score, momentum_score, content_count,"
               " lifecycle FROM trend_event WHERE status='active'")
    cmap = {"EMERGING": "#34d399", "GROWING": "#38bdf8", "REACTIVATED": "#22d3ee",
            "PEAKING": "#fbbf24", "DECLINING": "#fb923c", "DORMANT": "#f87171"}
    fig, ax = plt.subplots(figsize=(12.5, 7), dpi=160)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    seen = set()
    for r in rows:
        life = r["lifecycle"] or "UNKNOWN"
        label = life if life not in seen else None
        seen.add(life)
        ax.scatter(r["hot_score"], r["confidence_score"],
                   s=18 + 3 * ((r["content_count"] or 0) ** 0.5),
                   c=cmap.get(life, "#64748b"), alpha=0.75, label=label,
                   edgecolors="none")
    ax.set_ylim(0.28, 0.82)
    ax.axhline(0.6, color="#475569", lw=1, linestyle=(0, (5, 4)))
    ax.text(0.985, 0.615, "confidence 0.6（L4 Event Gate 降级口径线）",
            ha="right", fontsize=8.6, color="#64748b", transform=ax.get_yaxis_transform())
    ax.set_xlabel("Hot Score", color=INK, fontsize=12)
    ax.set_ylabel("Confidence Score", color=INK, fontsize=12)
    ax.tick_params(colors=DIM)
    for sp in ax.spines.values():
        sp.set_color("#334155")
    ax.set_title(f"L3 事件信号全景（{len(rows)} 个进行中事件 · 真实快照）—— "
                 "气泡大小 = 内容量，颜色 = 生命周期",
                 color=INK, fontsize=14.5, fontweight="bold", pad=14)
    leg = ax.legend(loc="upper left", framealpha=0.15, labelcolor=INK, fontsize=10,
                    title="生命周期", title_fontproperties={"weight": "bold"})
    leg.get_title().set_color(INK)
    ax.text(0.5, -0.13, "诚实标注：当前数据无时间分辨率 → Hot Score 结构性偏低（L3 evaluation 已证明），"
                        "评估结论见 L4/L6 的降级口径",
            ha="center", fontsize=9, color=DIM, transform=ax.transAxes)
    fig.savefig(ASSETS / "signal_scores.png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    os.chdir(str(ROOT))
    architecture()
    funnel()
    signal_scores()
    for f in ("architecture.png", "funnel.png", "signal_scores.png"):
        p = ASSETS / f
        print(f"{p}  {p.stat().st_size // 1024}KB" if p.exists() else f"{f} 缺失")
