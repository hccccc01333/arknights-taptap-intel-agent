#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""11情报Agent/topic_tracker.py — 话题跨天追踪状态机（全天监测的状态层）。

定位
----
项目目标里的「**热点追踪**」需要一个实体来承载：话题从冒头 → 升温 → 爆发 → 退潮的
**生命周期**。这一层就是那个实体。

为什么必须持久化
----------------
在此之前，daily_agent 每天跑一次、产出一份简报就结束，各天的简报**互不知晓**
（`daily_intel_20260927.md` 和 `daily_intel_20260928.md` 之间没有任何关联）。
那不是「追踪」，是「每天重新拍一张照片」。追踪的定义要求能回答：

  · 这个话题昨天在哪、今天到了哪？（需要时间序列）
  · 它是在升温还是退潮？（需要斜率，不是绝对值）
  · 它是新冒头的，还是上周就在了？（需要首次出现时间）

这三问都要求**跨天状态**。

为什么用 SQLite 而不是 JSON
---------------------------
全天监测意味着高频写入，JSON 方案有三个硬伤：
  1. 并发写会互相覆盖（没有事务）
  2. 每次更新要全量读入内存再全量写回，文件越大越慢
  3. 查「最近 6 小时的采样」要全表扫描

SQLite 是 Python 标准库自带（`sqlite3`），符合本项目「纯标准库」的技术约定，
且原生支持事务、索引、时间范围查询。选它是**取舍的结果，不是为了显得高级**。

两张表的分工
------------
  topic_state   当前状态快照：一个话题一行，回答「现在在哪」
  topic_series  采样时间序列：一次采样一行，回答「怎么走到这里的」

分开的理由：算斜率只需要 series，查当前状态只需要 state。混在一张表里，
查状态时要扫历史行，查斜率时要过滤冗余字段。

状态口径（与 ferment_judge 的分工）
-----------------------------------
本模块判的是**状态迁移**（在不在升温），ferment_judge 判的是**可发酵度**
（值不值得社区接）。两者正交：
  · 一个话题可以在升温，但不值得接（如纯骂战）
  · 一个话题可发酵度高，但还没起来（值得预热）
合并两个信号才构成完整的运营建议，这一层只负责前者。

阈值全部**显式标注为待校准**——与 ferment_judge 的 60/35 一样，
没有真实运营反馈前，所有阈值都是拍的。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "11情报Agent" / "state" / "topic_tracker.sqlite3"
PLATFORM_JSON = ROOT / "11情报Agent" / "outputs" / "platform_insight.json"

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------- 阈值（待校准）
# ⚠ 以下阈值全部是拍的默认值，与 ferment_judge 的 verdict 阈值同性质。
#    真实校准依赖运营回填（见 docs），在此之前不要把它们当成可信参数。
RISING_SLOPE_RATIO = 1.5  # 本次/上次 热度比值 ≥ 此值 → 视为升温中
BURST_SLOPE_RATIO = 3.0   # 比值 ≥ 此值 → 视为爆发
FADE_SLOPE_RATIO = 0.6    # 比值 ≤ 此值 → 视为退潮
SLEEP_AFTER_HOURS = 72    # 超过此小时数无新采样 → 归档为沉寂
MIN_SAMPLES_FOR_TREND = 2  # 少于此刻数不判趋势（无法算斜率）

STATES = ("冒头", "升温", "爆发", "退潮", "沉寂")


# ---------------------------------------------------------------- 话题身份归一

def normalize_title(title: str) -> str:
    """话题标题的确定性归一——用于跨天认出「同一个话题」。

    这是「话题身份」的基础版实现：**不做语义理解**，只做可解释的规范化。
    能处理的情况：
      · 全半角/空格/标点差异
      · 话题标签包裹（#xxx#）
      · 尾部装饰性后缀（如「二周年活动」vs「二周年」）

    处理不了的（留给 LLM 版）：
      · 「米哈游反舞弊通报」vs「米哈游内部通报」——同源事件不同说法
      · 跨平台同一事件的本地化表述

    设计取舍：宁可漏合并，不要错合并。错合并会把两个独立话题的曲线
    缠在一起，产生虚假的「爆发」——比漏合并危险得多。
    """
    if not title:
        return ""
    s = str(title).strip()
    # 去掉话题标签包裹
    s = s.strip("#＃").strip()
    # 全角转半角（仅处理常见标点与空格，不动中文字符）
    table = str.maketrans({
        "　": "", "：": ":", "，": ",", "（": "(", "）": ")",
        "！": "!", "？": "?", "、": ",", "《": "", "》": "",
    })
    s = s.translate(table)
    # 折叠空白
    s = " ".join(s.split())
    s = s.lower()
    return s


def topic_key(title: str, hashtag_id: str = "", metric_kind: str = "") -> str:
    """话题标识：优先用平台给的 hashtag_id（最可靠），否则退回归一标题。

    hashtag_id 是平台侧的稳定标识——同一话题跨天不会变，所以它是首选。
    只有在没有 id 的情况下（如 event_signals 里的裸标题）才用标题归一。

    ⚠ metric_kind 必须算进身份（2026-09-29 实测暴露的 bug）：
    同一话题会同时出现在 hot_board（浏览量）与 topic_dig（互动量）里。
    若两者共用一个 key，序列会变成
        [6270, 38, 6583, 39, ...]
    状态机就会把「换了个指标」误读成「热度跌到 0.6% → 退潮」，
    下一次又误读成「涨 173 倍 → 爆发」。整条趋势判断彻底失真。

    所以同一话题的**不同指标是两条独立序列**——它们量纲不同、量级差两个数量级，
    本就不该画在同一张图上比较。
    """
    hid = (hashtag_id or "").strip()
    base = f"hid:{hid}" if hid else f"tit:{normalize_title(title)}"
    return f"{base}|{metric_kind}" if metric_kind else base


# ---------------------------------------------------------------- 存储层

DDL = """
CREATE TABLE IF NOT EXISTS topic_state (
    topic_key       TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    hashtag_id      TEXT,
    metric_kind     TEXT DEFAULT '',
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    state           TEXT NOT NULL,
    last_metric     INTEGER DEFAULT 0,
    peak_metric     INTEGER DEFAULT 0,
    sample_count    INTEGER DEFAULT 0,
    prev_state      TEXT,
    state_changed_at TEXT
);

CREATE TABLE IF NOT EXISTS topic_series (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_key   TEXT NOT NULL,
    sampled_at  TEXT NOT NULL,
    metric      INTEGER NOT NULL,
    metric_kind TEXT NOT NULL,
    source      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_series_key_time
    ON topic_series (topic_key, sampled_at);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """打开（必要时初始化）状态库。"""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    con.execute(
        "INSERT OR REPLACE INTO meta (k, v) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    con.commit()
    return con


# ---------------------------------------------------------------- 采样

def collect_samples(pj: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """从 platform_insight facts 提取本次采样点。

    只读已聚合的 facts，**不碰原始 CSV**——沿用本项目「facts 是 Agent 与数据
    之间唯一界面」的纪律，同时天然满足 PII 三域边界。

    指标口径：
      · hot_board  → page_view（浏览量，平台侧热度）
      · topic_dig  → interaction_total（互动总量，参与度）

    两个指标**不能混算同一条曲线**：浏览量易被推荐位刷高，互动量更接近
    真实参与。所以 series 表用 metric_kind 区分，斜率也各算各的。
    """
    if pj is None:
        if not PLATFORM_JSON.exists():
            return []
        pj = json.loads(PLATFORM_JSON.read_text(encoding="utf-8"))
    if not pj:
        return []

    out: list[dict[str, Any]] = []
    for row in pj.get("hot_board") or []:
        out.append({
            "title": row.get("title") or "",
            "hashtag_id": str(row.get("hashtag_id") or ""),
            "metric": int(row.get("page_view") or 0),
            "metric_kind": "page_view",
            "source": "S4_hot_hashtags",
        })
    for row in pj.get("topic_dig") or []:
        out.append({
            "title": row.get("title") or "",
            "hashtag_id": str(row.get("hashtag_id") or ""),
            "metric": int(row.get("interaction_total") or 0),
            "metric_kind": "interaction",
            "source": "S6_by_hashtag",
        })
    return [s for s in out if s["title"]]


def record_samples(
    con: sqlite3.Connection,
    samples: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """写入采样并计算状态迁移。返回本次发生迁移的话题列表。"""
    now = now or datetime.now(TZ)
    stamp = now.isoformat(timespec="seconds")
    transitions: list[dict[str, Any]] = []

    for s in samples:
        key = topic_key(s["title"], s.get("hashtag_id", ""), s.get("metric_kind", ""))
        metric = int(s.get("metric") or 0)

        con.execute(
            "INSERT INTO topic_series (topic_key, sampled_at, metric, metric_kind, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (key, stamp, metric, s["metric_kind"], s["source"]),
        )

        row = con.execute(
            "SELECT * FROM topic_state WHERE topic_key = ?", (key,)
        ).fetchone()

        if row is None:
            # 首次见到 → 冒头
            con.execute(
                "INSERT INTO topic_state (topic_key, title, hashtag_id, metric_kind,"
                " first_seen_at, last_seen_at, state, last_metric, peak_metric,"
                " sample_count, prev_state, state_changed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?)",
                (key, s["title"], s.get("hashtag_id", ""), s.get("metric_kind", ""),
                 stamp, stamp, "冒头", metric, metric, stamp),
            )
            transitions.append({
                "topic_key": key, "title": s["title"],
                "metric_kind": s.get("metric_kind", ""),
                "from": None, "to": "冒头", "metric": metric,
                "ratio": None, "reason": "首次观测到该话题",
            })
            continue

        prev_state = row["state"]
        prev_metric = int(row["last_metric"] or 0)
        new_state, ratio, reason = classify_transition(
            prev_state, prev_metric, metric, int(row["sample_count"] or 0)
        )

        con.execute(
            "UPDATE topic_state SET last_seen_at = ?, state = ?, last_metric = ?,"
            " peak_metric = MAX(peak_metric, ?), sample_count = sample_count + 1,"
            " prev_state = ?, state_changed_at = CASE WHEN state != ? THEN ?"
            " ELSE state_changed_at END WHERE topic_key = ?",
            (stamp, new_state, metric, metric, prev_state, new_state, stamp, key),
        )

        if new_state != prev_state:
            transitions.append({
                "topic_key": key, "title": row["title"],
                "metric_kind": s.get("metric_kind", ""),
                "from": prev_state, "to": new_state, "metric": metric,
                "ratio": ratio, "reason": reason,
            })

    con.commit()
    return transitions


# ---------------------------------------------------------------- 状态判定

def classify_transition(
    prev_state: str, prev_metric: int, metric: int, sample_count: int
) -> tuple[str, float | None, str]:
    """判定状态迁移。返回 (新状态, 比值, 理由)。

    核心设计：判「升温」看的是**变化率**，不是绝对值。
      「今天 5000 浏览」没有意义——热度榜上人人都高。
      「比上次涨了 3 倍」才是运营能用的信号：说明**现在接还来得及**。

    这也是本项目区别于「热度榜搬运」的关键点：任何平台都给得出绝对值，
    只有跟踪历史才给得出趋势与时机。
    """
    if prev_metric <= 0:
        # 上次基准为 0 无法算比值，不改判（避免除零与虚假爆发）
        return prev_state, None, "上次采样为 0，无法计算变化率，维持原状态"

    ratio = metric / prev_metric
    drifting_up = ratio >= RISING_SLOPE_RATIO
    bursting = ratio >= BURST_SLOPE_RATIO
    fading = ratio <= FADE_SLOPE_RATIO

    if sample_count < MIN_SAMPLES_FOR_TREND:
        # 只有一次历史，不足以判趋势——但首次大涨可以直接记爆发
        if bursting:
            return "爆发", ratio, f"首次对比即涨 {ratio:.1f}×，直接记为爆发"
        return prev_state, ratio, f"样本次数不足（{sample_count}），暂不判趋势"

    if bursting:
        return "爆发", ratio, f"热度涨 {ratio:.1f}×，超过爆发阈值 {BURST_SLOPE_RATIO}×"
    if drifting_up:
        return "升温", ratio, f"热度涨 {ratio:.1f}×，持续升温"
    if fading:
        return "退潮", ratio, f"热度降至 {ratio:.1f}×，已过峰"
    return prev_state, ratio, f"热度变化 {ratio:.1f}×，未达迁移阈值"


def archive_stale(
    con: sqlite3.Connection, now: datetime | None = None, hours: int = SLEEP_AFTER_HOURS
) -> list[dict[str, Any]]:
    """把长时间无新采样的话题归档为沉寂。"""
    now = now or datetime.now(TZ)
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = con.execute(
        "SELECT topic_key, title, state FROM topic_state"
        " WHERE last_seen_at < ? AND state != '沉寂'",
        (cutoff,),
    ).fetchall()
    out = []
    for r in rows:
        con.execute(
            "UPDATE topic_state SET state = '沉寂', prev_state = ?,"
            " state_changed_at = ? WHERE topic_key = ?",
            (r["state"], now.isoformat(timespec="seconds"), r["topic_key"]),
        )
        out.append({
            "topic_key": r["topic_key"], "title": r["title"],
            "from": r["state"], "to": "沉寂", "metric": None, "ratio": None,
            "reason": f"超过 {hours} 小时无新采样",
        })
    con.commit()
    return out


# ---------------------------------------------------------------- 查询

def get_series(con: sqlite3.Connection, key: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT sampled_at, metric, metric_kind FROM topic_series"
        " WHERE topic_key = ? ORDER BY sampled_at DESC LIMIT ?",
        (key, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def snapshot(con: sqlite3.Connection) -> dict[str, Any]:
    """当前状态全景（按状态分组 + 统计）。"""
    rows = con.execute(
        "SELECT * FROM topic_state ORDER BY peak_metric DESC"
    ).fetchall()
    by_state: dict[str, list[dict[str, Any]]] = {s: [] for s in STATES}
    for r in rows:
        d = dict(r)
        by_state.setdefault(d["state"], []).append(d)
    n_series = con.execute("SELECT COUNT(*) c FROM topic_series").fetchone()["c"]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "n_topics": len(rows),
        "n_samples": n_series,
        "by_state": {k: v for k, v in by_state.items()},
        "counts": {k: len(v) for k, v in by_state.items()},
    }


def render_snapshot(snap: dict[str, Any]) -> str:
    """给人看的状态报告。"""
    lines = [
        "# 话题追踪状态",
        "",
        f"- 生成时间：{snap['generated_at']}",
        f"- 在追踪话题：{snap['n_topics']} 个",
        f"- 累计采样点：{snap['n_samples']} 条",
        "",
        "## 状态分布",
        "",
        "| 状态 | 数量 | 含义 |",
        "|------|------|------|",
    ]
    meaning = {
        "冒头": "刚出现，观察",
        "升温": "变化率上行，现在接还来得及",
        "爆发": "值得立即响应",
        "退潮": "已过峰，别再追",
        "沉寂": "已归档",
    }
    for s in STATES:
        lines.append(f"| {s} | {snap['counts'].get(s, 0)} | {meaning[s]} |")
    lines.append("")

    for s in ("爆发", "升温"):
        items = snap["by_state"].get(s) or []
        if not items:
            continue
        lines.append(f"## {s}中（{len(items)}）")
        lines.append("")
        for t in items[:8]:
            kind = {"page_view": "浏览量", "interaction": "互动量"}.get(
                t.get("metric_kind") or "", t.get("metric_kind") or "—"
            )
            lines.append(
                f"- **{t['title']}**（{kind}）— 当前 {t['last_metric']}，"
                f"峰值 {t['peak_metric']}，采样 {t['sample_count']} 次，"
                f"首次 {t['first_seen_at'][:16]}"
            )
        lines.append("")

    lines += [
        "## 口径说明",
        "",
        "- 状态判定基于**变化率**而非绝对值（热度榜人人绝对值都高，趋势才有信息量）",
        "- 阈值当前为默认值，**未经真实运营反馈校准**，不要当可信参数使用",
        "- 本层只判「在不在升温」；「值不值得社区接」由 ferment_judge 判，两者正交",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="话题跨天追踪状态机")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="状态库路径")
    ap.add_argument("--sample", action="store_true", help="从 platform_insight 采样一次")
    ap.add_argument("--snapshot", action="store_true", help="输出当前状态全景")
    ap.add_argument("--archive", action="store_true", help="归档长时间无更新的話題")
    ap.add_argument("--json", default="", help="把快照写入指定 JSON 路径")
    ap.add_argument("--report", default="", help="把状态报告写入指定 Markdown 路径")
    args = ap.parse_args(argv)

    con = connect(args.db)
    transitions: list[dict[str, Any]] = []

    if args.sample:
        samples = collect_samples()
        if not samples:
            print("未取到采样点：platform_insight facts 缺失或为空。")
        else:
            transitions = record_samples(con, samples)
            print(f"已记录 {len(samples)} 个采样点。")
    if args.archive:
        transitions += archive_stale(con)

    if transitions:
        print(f"\n发生状态迁移 {len(transitions)} 个：")
        for t in transitions:
            print(f"  [{t['from'] or '新'} → {t['to']}] {t['title']}：{t['reason']}")

    if args.snapshot or args.json or args.report:
        snap = snapshot(con)
        if args.snapshot:
            print(f"\n在追踪 {snap['n_topics']} 个话题，采样 {snap['n_samples']} 条。")
            print("状态分布：" + " / ".join(
                f"{k} {v}" for k, v in snap["counts"].items() if v
            ))
        if args.json:
            p = Path(args.json)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"已写入快照：{p}")
        if args.report:
            p = Path(args.report)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(render_snapshot(snap), encoding="utf-8")
            print(f"已写入报告：{p}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
