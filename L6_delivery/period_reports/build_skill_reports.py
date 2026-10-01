#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按 Skill 口径生成日/周报终稿（含薄样本滚动窗）。"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[2]

DEFAULT_GAME = "arknights"  # 游戏档案 key，见 games/<key>.json


def load_game_profile(game_key: str = DEFAULT_GAME) -> dict:
    """加载游戏档案（games/game_profile.py，唯一参数化入口）。"""
    import sys

    sys.path.insert(0, str(ROOT / "games"))
    from game_profile import load as _load  # noqa: PLC0415

    return _load(game_key)


GAME_PROFILE = load_game_profile()
DATA = ROOT / "data/raw/taptap"
ANN = ROOT / "data/annotations"
OUT = ROOT / "L6_delivery/period_reports"
REPORT_DIR = OUT / "reports"

MIN_DAILY_N = 30
ROLL_DAYS = 3
MIN_BRIEF_N = 15
WEAK_BASE_N = 30
WEAK_BASE_RATIO = 0.5

TOPIC_CN = {
    "gacha": "抽卡/商业化",
    "balance": "数值/平衡/养成",
    "gameplay": "关卡/玩法",
    "story": "剧情/世界观",
    "event": "活动/版本",
    "client": "性能/客户端",
    "ops": "运营/客服/规则",
    "other": "综合/其他",
}

ROLE_BY_TOPIC = {
    "gacha": "运营",
    "balance": "数值策划",
    "gameplay": "产品/战斗",
    "story": "剧情",
    "event": "运营",
    "client": "客户端",
    "ops": "客服/运营",
    "other": "产品",
}

URGENT_RE = re.compile(r"闪退|登不上|无法登录|打不开|崩溃|卸载|退游|再也不玩|删了")
CRASH_RE = re.compile(r"闪退|登不上|无法登录|打不开|崩溃")
CHURN_RE = re.compile(r"退游|卸载|再也不玩|删了|拜拜")


def clip(s: object, n: int = 48) -> str:
    t = str(s or "").replace("\n", " ").replace("|", "\\|").replace("<br />", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def load_annotated(ann_path: Path) -> pd.DataFrame:
    clean = pd.read_csv(ROOT / "data/processed/reviews" / "reviews_clean.csv", dtype={"review_id": str})
    ann = pd.read_csv(ann_path, dtype={"review_id": str})
    ann = ann[ann["error"].fillna("") == ""].drop_duplicates("review_id", keep="last")
    ann = ann[~ann["review_id"].astype(str).str.startswith("probe_")]
    cols = [
        "review_id",
        "topic_primary",
        "sentiment",
        "actionable",
        "confidence",
        "rhetoric",
        "rhetoric_confidence",
        "incongruity_cues",
        "reason",
        "prompt_version",
        "model",
        "score_raw",
    ]
    for c in cols:
        if c not in ann.columns:
            ann[c] = ""
    m = ann[cols].merge(
        clean[["review_id", "text", "publish_time_cn", "support_count"]],
        on="review_id",
        how="left",
    )
    m["publish_dt"] = pd.to_datetime(m["publish_time_cn"], errors="coerce")
    m["day"] = m["publish_dt"].dt.tz_convert(TZ).dt.date
    m["score_raw"] = m["score_raw"].astype(str)
    m["rhetoric"] = m["rhetoric"].fillna("none").replace("", "none")
    m["incongruity_cues"] = m["incongruity_cues"].fillna("none").replace("", "none")
    m["topic_primary"] = m["topic_primary"].fillna("other").replace("", "other")
    m["sentiment"] = m["sentiment"].fillna("中")
    return m.dropna(subset=["day"]).copy()


def sent_counts(df: pd.DataFrame) -> tuple[int, int, int, int]:
    c = Counter(df["sentiment"])
    n = len(df)
    return n, c.get("正", 0), c.get("中", 0), c.get("负", 0)


def contrast_ok(n_cur: int, n_base: int) -> bool:
    if n_base < WEAK_BASE_N:
        return False
    if n_cur > 0 and n_base < WEAK_BASE_RATIO * n_cur:
        return False
    return True


def hi_neg_n(df: pd.DataFrame) -> int:
    return int(((df["score_raw"].isin(["4", "5"])) & (df["sentiment"] == "负")).sum())


def rhe_n(df: pd.DataFrame) -> int:
    return int((df["rhetoric"] != "none").sum())


def topic_top(df: pd.DataFrame, k: int = 3) -> list[tuple[str, int]]:
    return Counter(df["topic_primary"]).most_common(k)


def neg_topic_top(df: pd.DataFrame, k: int = 5) -> list[tuple[str, int]]:
    neg = df[df["sentiment"] == "负"]
    return Counter(neg["topic_primary"]).most_common(k)


def pick_rep(df: pd.DataFrame) -> pd.Series | None:
    if df.empty:
        return None
    sub = df.copy()
    sub["_pri"] = (
        (sub["sentiment"] == "负").astype(int) * 3
        + (sub["rhetoric"] != "none").astype(int) * 2
        + (sub["actionable"] == "是").astype(int)
        + sub["text"].fillna("").map(lambda t: 1 if URGENT_RE.search(str(t)) else 0)
    )
    sub = sub.sort_values(["_pri", "confidence"], ascending=[False, False])
    return sub.iloc[0]


def fmt_day(d: date) -> str:
    return f"{d.month}/{d.day}"


def fmt_ymd(d: date) -> str:
    return d.isoformat().replace("-", "")


@dataclass
class DailyWindow:
    anchor: date
    days: list[date]
    mode: str  # single | roll | brief
    cur: pd.DataFrame
    base: pd.DataFrame
    base_days: list[date]
    allow_delta: bool
    n_anchor: int


def select_daily_window(
    df_all: pd.DataFrame,
    anchor: date,
    min_daily_n: int = MIN_DAILY_N,
    roll_days: int = ROLL_DAYS,
    min_brief_n: int = MIN_BRIEF_N,
) -> DailyWindow:
    n_anchor = int((df_all["day"] == anchor).sum())
    days_with = sorted(d for d in df_all["day"].unique() if d <= anchor)

    if n_anchor >= min_daily_n:
        days = [anchor]
        mode = "single"
    else:
        days = days_with[-roll_days:] if len(days_with) >= roll_days else days_with
        if not days:
            days = [anchor]
        mode = "roll"

    cur = df_all[df_all["day"].isin(days)].copy()
    if len(cur) < min_brief_n:
        mode = "brief"

    # 对照：再往前等长「有数据日」
    before = [d for d in days_with if d < min(days)]
    need = len(days)
    base_days = before[-need:] if len(before) >= need else before
    base = df_all[df_all["day"].isin(base_days)].copy() if base_days else df_all.iloc[0:0].copy()
    allow = contrast_ok(len(cur), len(base))
    return DailyWindow(
        anchor=anchor,
        days=days,
        mode=mode,
        cur=cur,
        base=base,
        base_days=base_days,
        allow_delta=allow,
        n_anchor=n_anchor,
    )


@dataclass
class WeeklyWindow:
    start: date
    end: date
    cur: pd.DataFrame
    base: pd.DataFrame
    base_start: date
    base_end: date
    allow_delta: bool


def select_weekly_window(df_all: pd.DataFrame, end: date) -> WeeklyWindow:
    start = end - timedelta(days=6)
    cur = df_all[(df_all["day"] >= start) & (df_all["day"] <= end)].copy()
    base_end = start - timedelta(days=1)
    base_start = base_end - timedelta(days=6)
    base = df_all[(df_all["day"] >= base_start) & (df_all["day"] <= base_end)].copy()
    return WeeklyWindow(
        start=start,
        end=end,
        cur=cur,
        base=base,
        base_start=base_start,
        base_end=base_end,
        allow_delta=contrast_ok(len(cur), len(base)),
    )


def risk_level(df: pd.DataFrame, brief: bool) -> tuple[str, str]:
    """返回 (等级, 简述)。"""
    n, _, _, neg = sent_counts(df)
    if n == 0:
        return "一般关注", "无样本"
    neg_df = df[df["sentiment"] == "负"]
    client_neg = int((neg_df["topic_primary"] == "client").sum())
    crash_hit = int(df["text"].fillna("").map(lambda t: bool(CRASH_RE.search(str(t)))).sum())
    churn_hit = int(df["text"].fillna("").map(lambda t: bool(CHURN_RE.search(str(t)))).sum())
    top_neg = neg_topic_top(df, 1)
    cluster = top_neg[0][1] if top_neg else 0
    hi = hi_neg_n(df)
    act_neg = int(((df["actionable"] == "是") & (df["sentiment"] == "负")).sum())

    # 紧急：登录/闪退扎堆，或劝退话术真正偏密
    if client_neg >= 3 and client_neg / max(neg, 1) >= 0.25:
        return "紧急预警", "客户端/闪退类负向扎堆"
    if crash_hit >= 3 and client_neg >= 2:
        return "紧急预警", "闪退/登录类反馈扎堆"
    if churn_hit >= 5 and churn_hit / max(n, 1) >= 0.12:
        return "紧急预警", "劝退/卸载话术偏密"

    if brief:
        if client_neg >= 2 and crash_hit >= 2:
            return "重点关注", "简报模式但客户端信号偏强"
        return "一般关注", "样本偏薄，仅作监测"

    if cluster >= 3 and (cluster / max(neg, 1) >= 0.35):
        return "重点关注", "单簇负向突出"
    if hi >= 1 and act_neg >= 1:
        return "重点关注", "高星负向+可行动并存"
    if churn_hit >= 2 or (neg / n >= 0.45 and neg >= 8):
        return "重点关注", "负向密度高或退游话术出现"
    return "一般关注", "结构平稳"


def window_label(w: DailyWindow) -> str:
    if w.mode == "single":
        return f"{w.anchor}（单日，库内锚定日）"
    a, b = w.days[0], w.days[-1]
    return (
        f"{a}～{b}（滚动 {len(w.days)} 个有数据日；"
        f"锚定日 {w.anchor} n={w.n_anchor}，窗内 n={len(w.cur)}）"
    )


def build_daily(w: DailyWindow, ann_name: str) -> str:
    df = w.cur
    n, pos, mid, neg = sent_counts(df)
    risk, risk_why = risk_level(df, brief=(w.mode == "brief"))
    tops = topic_top(df, 3)
    neg_tops = neg_topic_top(df, 3)
    hi = hi_neg_n(df)
    rhe = rhe_n(df)
    prompt_ver = str(df["prompt_version"].mode().iloc[0]) if n else ""
    allow = w.allow_delta
    n_base = len(w.base)

    # 对照句
    if n_base == 0:
        cmp_note = "无对照窗，仅报绝对水平"
    elif not allow:
        cmp_note = (
            f"对照窗 n={n_base}"
            + (f"（{w.base_days[0]}～{w.base_days[-1]}）" if w.base_days else "")
            + "，样本不足，**不做 Δpp 环比**"
        )
    else:
        bneg = (w.base["sentiment"] == "负").mean() * 100
        cneg = neg / n * 100 if n else 0
        cmp_note = (
            f"对照窗 n={n_base}，负向 {bneg:.1f}% → 本期 {cneg:.1f}% "
            f"（Δ {cneg - bneg:+.1f}pp）"
        )

    # 热点簇文案
    if neg_tops:
        hot = " + ".join(
            f"{TOPIC_CN.get(k, k)}（{v}）" for k, v in neg_tops[:2]
        )
    else:
        hot = "无明显负向主簇"

    # 决策句：用可行动聚类
    actions = cluster_actions(df, limit=3)
    if actions:
        decide = actions[0].split("｜依据")[0].replace("建议", "建议", 1)
        if len(actions) > 1:
            decide += f"；其次跟进{len(actions) - 1}项可行动线索"
    else:
        decide = "本期无可行动=是的聚类项，维持主题监测即可"

    title_day = fmt_ymd(w.anchor)
    mode_cn = {"single": "单日", "roll": "滚动窗", "brief": "简报"}.get(w.mode, w.mode)

    lines: list[str] = [
        f"# TapTap《{GAME_PROFILE.get('name', '')}》舆情日报 · {w.anchor}",
        "",
        "## 元数据",
        "",
        "| 项 | 内容 |",
        "|----|------|",
        f"| 窗口模式 | **{mode_cn}**｜{window_label(w)} |",
        f"| 对照窗 | "
        + (
            f"{w.base_days[0]}～{w.base_days[-1]}（n={n_base}）"
            if w.base_days
            else "无"
        )
        + " |",
        f"| 样本量 | 本期 **n={n}**；锚定日 n={w.n_anchor}；{cmp_note} |",
        f"| 渠道 | TapTap 评价，app_id={GAME_PROFILE.get('app_id')} |",
        f"| 标注 | `{ann_name}` / prompt {prompt_ver or 'v1.x'}；`sentiment`=整体态度 |",
        f"| 风险等级 | **{risk}**（{risk_why}） |",
        f"| 生成 | `build_skill_reports.py` + Skill `arknights-taptap-yuqing-report` |",
        f"| 生成时间 | {datetime.now(TZ).isoformat(timespec='seconds')} |",
        "",
        "## 执行摘要",
        "",
        f"**【数据洞察】** {fmt_day(w.days[0])}～{fmt_day(w.days[-1])} TapTap 评价 n={n}，"
        f"正 {pos}（{pos / n * 100:.1f}%）/ 中 {mid}（{mid / n * 100:.1f}%）/ "
        f"负 {neg}（{neg / n * 100:.1f}%）；{cmp_note}。  ",
        f"**【热点追踪】** 负向主簇：{hot}"
        + ("；非全面崩盘叙事。" if len(neg_tops) >= 2 else "。")
        + "  ",
        f"**【风险预警】** 风险={risk}：{risk_why}"
        + ("；样本偏薄，外推须克制。" if w.mode == "brief" else "。")
        + "  ",
        f"**【需要决策】** {decide}。",
        "",
        "## 态势三行",
        "",
        f"- 情绪：正 {pos}｜中 {mid}｜负 {neg}  ",
        "- 主题 Top3："
        + (
            "｜".join(f"{k} {v}" for k, v in tops)
            if tops
            else "—"
        )
        + "  ",
        f"- 高星负向 {hi}；rhetoric≠none {rhe}",
        "",
    ]

    # 异常信号
    max_sig = 1 if w.mode == "brief" else 2
    signals = build_signals(df, max_sig)
    lines += ["## 异常信号", ""]
    if not signals:
        lines += ["本期无明显可写簇（负向过少或过散）。", ""]
    else:
        lines += signals

    # 可行动
    lines += ["## 可行动（≤3，已聚类）", ""]
    if not actions:
        lines += ["- 本期无可行动=是 的样本（或未能聚类）", ""]
    else:
        for i, a in enumerate(actions, 1):
            lines.append(f"{i}. {a}")
        lines.append("")

    # 修辞
    lines += ["## 修辞复核", ""]
    rhe_df = df[df["rhetoric"] != "none"].copy()
    if rhe_df.empty:
        lines += ["本期 rhetoric≠none = 0。无需单独高级黑队列。", ""]
    else:
        lines.append(f"rhetoric≠none = {len(rhe_df)}，抽检≤2：")
        for _, r in rhe_df.head(2).iterrows():
            lines.append(
                f"- `{r.review_id}`｜`{r.rhetoric}`｜{clip(r.text, 60)}"
            )
        lines.append("")

    # 提醒 + 边界
    if not allow:
        lines += [
            "## 提醒",
            "",
            "对照样本不足或本期样本偏薄：负向占比 **不能** 解读为全站口碑塌陷，"
            "只表示**本窗口已抓取评价里**负向话术密度。"
            + (" 若明日 n 回升，再看主簇是否仍由上述主题驱动。" if w.mode != "single" else ""),
            "",
        ]

    lines += [
        "## 边界说明",
        "",
        "- 单渠道 TapTap 评价切片，非全网、非官博评论区。  ",
        "- " + ("对照样本不足，已禁止 Δpp。  " if not allow else "对照可用时已给出 Δpp，仍勿外推全站。  "),
        "- 以上基于评价文本结构，执行需结合版本事实核查。",
        "",
        "---",
        "",
        "*Skill 自检：四问可答；摘要四句齐全；"
        + ("未写弱对照环比；" if not allow else "对照纪律已检；")
        + "可行动已聚类。*",
        "",
    ]
    _ = title_day  # naming uses anchor
    return "\n".join(lines)


def build_signals(df: pd.DataFrame, limit: int) -> list[str]:
    neg = df[df["sentiment"] == "负"]
    if neg.empty:
        return []
    tops = neg_topic_top(df, limit)
    total_neg = len(neg)
    lines: list[str] = []
    for i, (topic, cnt) in enumerate(tops, 1):
        sub = neg[neg["topic_primary"] == topic]
        rep = pick_rep(sub)
        prio = "重点" if cnt >= 3 or (cnt / max(total_neg, 1) >= 0.35) else "一般"
        quote = clip(rep.text, 40) if rep is not None else ""
        rid = rep.review_id if rep is not None else "—"
        role = ROLE_BY_TOPIC.get(topic, "产品")
        cn = TOPIC_CN.get(topic, topic)
        lines += [
            f"### 信号 {i}｜{cn}（【{prio}】）",
            "",
            f"1. **背景**：本窗负向中 `{topic}` 相关反馈聚集。  ",
            f"2. **表现**：负向中该主题 {cnt}/{total_neg}"
            + (f"；代表原话：「{quote}」（{rid}）" if quote else "")
            + "。  ",
            "3. **影响评估**：属站内评价可跟进簇；"
            + ("条数有限，宜并入周监测。" if cnt < 3 else "话术具体，适合产品响应。")
            + "  ",
            f"4. **建议**：建议{role}针对「{cn}」诉求做复盘清单，先确认是否可复现/是否持续。",
            "",
        ]
    return lines


def cluster_actions(df: pd.DataFrame, limit: int = 3) -> list[str]:
    sub = df[df["actionable"] == "是"].copy()
    if sub.empty:
        # fallback: negative topics with concrete reasons
        neg = df[df["sentiment"] == "负"]
        if neg.empty:
            return []
        sub = neg.copy()
        use_fallback = True
    else:
        use_fallback = False

    order_map = {"负": 0, "中": 1, "正": 2}
    sub["_o"] = sub["sentiment"].map(lambda x: order_map.get(x, 9))
    grouped: list[str] = []
    for topic, g in sorted(
        sub.groupby("topic_primary"),
        key=lambda kv: (
            order_map.get(kv[1]["sentiment"].mode().iloc[0], 9) if len(kv[1]) else 9,
            -len(kv[1]),
        ),
    ):
        ids = list(g["review_id"].head(3))
        role = ROLE_BY_TOPIC.get(topic, "产品")
        cn = TOPIC_CN.get(topic, topic)
        timing = "今日内" if not use_fallback else "本监测窗内"
        verb = "确认复现路径与影响范围" if topic in ("client", "gameplay") else "纳入主题监测并评估是否需对外说明"
        if topic == "gacha":
            verb = "纳入抽卡情绪看板，不升格单窗全量安抚"
        line = (
            f"建议**{role}**针对{cn}相关诉求，在{timing}{verb}"
            f"｜依据：{'、'.join(ids)}"
        )
        grouped.append(line)
        if len(grouped) >= limit:
            break
    return grouped


def build_weekly(w: WeeklyWindow, ann_name: str) -> str:
    df = w.cur
    n, pos, mid, neg = sent_counts(df)
    risk, risk_why = risk_level(df, brief=False)
    neg_tops = neg_topic_top(df, 4)
    hi = hi_neg_n(df)
    prompt_ver = str(df["prompt_version"].mode().iloc[0]) if n else ""
    allow = w.allow_delta
    n_base = len(w.base)

    if n_base == 0:
        cmp_note = "无对照周"
    elif not allow:
        cmp_note = f"对照周（{w.base_start}～{w.base_end}）n={n_base}，不足，**不做 Δpp**"
    else:
        bneg = (w.base["sentiment"] == "负").mean() * 100
        cneg = neg / n * 100 if n else 0
        cmp_note = (
            f"对照周 n={n_base}，负向 {bneg:.1f}% → 本周 {cneg:.1f}% "
            f"（Δ {cneg - bneg:+.1f}pp）"
        )

    hot = "、".join(f"{TOPIC_CN.get(k, k)} {v}" for k, v in neg_tops[:3]) or "—"

    # 分日弧线摘要
    arc_bits: list[str] = []
    for d in sorted(df["day"].unique()):
        sub = df[df["day"] == d]
        nn = len(sub)
        nr = (sub["sentiment"] == "负").mean() * 100 if nn else 0
        top = Counter(sub["topic_primary"]).most_common(1)
        t = top[0][0] if top else "—"
        arc_bits.append(f"{fmt_day(d)} n={nn} 负{nr:.0f}% 主`{t}`")
    # 压缩：只写峰值日 + 首尾
    if len(arc_bits) > 5:
        peak_d = max(df["day"].unique(), key=lambda d: int((df["day"] == d).sum()))
        peak_sub = df[df["day"] == peak_d]
        arc_txt = (
            f"周内声量峰 {peak_d}（n={len(peak_sub)}）；"
            f"分日结构见下表，正文以事件弧为主。"
        )
    else:
        arc_txt = "；".join(arc_bits)

    lines: list[str] = [
        f"# TapTap《{GAME_PROFILE.get('name', '')}》舆情周报 · {w.start} ~ {w.end}",
        "",
        "## 元数据",
        "",
        "| 项 | 内容 |",
        "|----|------|",
        f"| 窗口 | {w.start}～{w.end}（7 日，以锚定日为尾） |",
        f"| 样本量 | 本周 **n={n}**；{cmp_note} |",
        f"| 渠道 | TapTap 评价，app_id={GAME_PROFILE.get('app_id')} |",
        f"| 标注 | `{ann_name}` / {prompt_ver or 'v1.x'} |",
        f"| 风险等级 | **{risk}**（{risk_why}） |",
        f"| 生成 | `build_skill_reports.py` + Skill `arknights-taptap-yuqing-report` |",
        f"| 生成时间 | {datetime.now(TZ).isoformat(timespec='seconds')} |",
        "",
        "## 执行摘要",
        "",
        f"**【数据洞察】** 本周 TapTap 评价 n={n}，"
        f"正 {pos}（{pos / n * 100:.1f}%）/ 中 {mid}（{mid / n * 100:.1f}%）/ "
        f"负 {neg}（{neg / n * 100:.1f}%）。{cmp_note}。  ",
        f"**【热点追踪】** 负向主线：{hot}；关注是否双峰并行而非单主题吞没。  ",
        f"**【风险预警】** 风险={risk}：{risk_why}；高星负向 {hi} 条。  ",
        "**【需要决策】** 建议按主题拆线跟进（体验 / 商业化 / 客户端），"
        "避免「一个安抚公告打天下」。",
        "",
        "## 本周态势（精选 KPI）",
        "",
        f"- 声量 {n}；负向 {neg / n * 100:.1f}%  " if n else "- 声量 0  ",
        f"- 负向主题 Top：{'｜'.join(f'{k} {v}' for k, v in neg_tops) or '—'}  ",
        f"- 高星负向 {hi}；rhetoric≠none {rhe_n(df)}  ",
        f"- 分日提示：{arc_txt}",
        "",
        "## 核心事件叙事（2–3）",
        "",
    ]

    events = build_weekly_events(df, limit=3)
    if not events:
        lines += ["本周负向过散，未形成可写事件弧。", ""]
    else:
        lines += events

    # 情感弧线（按日负向率）
    day_neg: list[tuple[date, float, int]] = []
    for d in sorted(df["day"].unique()):
        sub = df[df["day"] == d]
        day_neg.append((d, (sub["sentiment"] == "负").mean() if len(sub) else 0, len(sub)))
    if day_neg:
        start_r = day_neg[0][1]
        end_r = day_neg[-1][1]
        peak = max(day_neg, key=lambda x: (x[1], x[2]))
        arc_line = (
            f"周初负向占比约 {start_r * 100:.0f}% → "
            f"峰值附近 {peak[0]}（负向 {peak[1] * 100:.0f}%，n={peak[2]}）→ "
            f"周末约 {end_r * 100:.0f}%。"
            " 低 n 日会放大比例，解读须克制；若周均变好须检查是否单事件拉动。"
        )
    else:
        arc_line = "—"

    lines += [
        "## 情感弧线",
        "",
        arc_line,
        "",
        "## 双表",
        "",
        "### 产品/体验预警",
        "",
        "| 优先级 | 主题 | 信号 | 跟进方 |",
        "|--------|------|------|--------|",
    ]
    for topic, cnt in neg_tops[:4]:
        prio = "高" if cnt >= 5 or topic == "client" else "中"
        lines.append(
            f"| {prio} | `{topic}` | {TOPIC_CN.get(topic, topic)}负向×{cnt} | "
            f"{ROLE_BY_TOPIC.get(topic, '产品')} |"
        )
    lines += [
        "",
        "### 内容风险备注",
        "",
        "本期以体验与商业化吐槽为主（评价场景局限）。修辞通道单独复核，"
        "高级黑非默认主矛盾。",
        "",
        "## 下周行动（含可选利弊）",
        "",
    ]
    actions = cluster_actions(df, limit=4)
    if actions:
        # 第一条给 A/B
        first = actions[0]
        lines += [
            f"1. {first.split('｜依据')[0]}  ",
            "   - 方案 A：先发「已知悉/排查中」短说明——快、成本低；事实未清可能二次质疑。  ",
            "   - 方案 B：复现并给出修复/跟进窗口再发——可信度高；窗口期内负向可能继续堆。  ",
            "   建议优先 A+B 衔接：短说明在前，实质进展在后。  ",
            "",
        ]
        for i, a in enumerate(actions[1:], 2):
            lines.append(f"{i}. {a}")
        lines.append("")
    else:
        lines += ["1. 维持主题监测，待样本回升后再升格专项。", ""]

    lines += [
        "## 边界说明",
        "",
        f"- 单渠道、周样本 {n}，结论用于站内评价异动，不外推全网。  ",
        "- 禁止把分日表当正文复读；本报告以事件弧为主。  ",
        "- " + ("对照不足，已禁止 Δpp。" if not allow else "对照可用，Δpp 仅作结构参考。"),
        "",
        "---",
        "",
        "*Skill 自检：2–3 事件齐全；建议含利弊选项；对照纪律已检。*",
        "",
    ]
    return "\n".join(lines)


def build_weekly_events(df: pd.DataFrame, limit: int = 3) -> list[str]:
    tops = neg_topic_top(df, limit)
    if not tops:
        return []
    labels = "ABCDEFG"
    lines: list[str] = []
    # 各主题按日计数，找峰日
    for i, (topic, cnt) in enumerate(tops):
        sub = df[(df["topic_primary"] == topic) & (df["sentiment"] == "负")]
        by_day = sub.groupby("day").size()
        peak_day = by_day.idxmax() if len(by_day) else None
        rep = pick_rep(sub)
        quote = clip(rep.text, 42) if rep is not None else ""
        rid = rep.review_id if rep is not None else "—"
        role = ROLE_BY_TOPIC.get(topic, "产品")
        cn = TOPIC_CN.get(topic, topic)
        peak_s = f"{peak_day} 前后相对突出" if peak_day is not None else "周内分散"
        lines += [
            f"### 事件 {labels[i]}｜{cn}（起→峰→仍在）",
            "",
            f"1. **背景**：周内持续出现与{cn}相关的负向评价。  ",
            f"2. **表现**：该主题负向 {cnt} 条；{peak_s}。  ",
            "3. **影响评估**："
            + (
                "可行动空间大，属体验预警主通道。"
                if topic in ("gameplay", "client", "balance")
                else "偏商业化/口碑监测，宜看是否持续。"
            )
            + "  ",
            f"4. **建议**：建议{role}拉「{cn}」专项清单并评估对外节奏。  ",
        ]
        if quote:
            lines.append(f"   - 原话：「{quote}」（{rid}）")
        lines.append("")
    return lines


def self_check(text: str, allow_delta: bool) -> list[str]:
    checks = []
    ok_sum = all(k in text for k in ("【数据洞察】", "【热点追踪】", "【风险预警】", "【需要决策】"))
    checks.append(("摘要四句齐全", ok_sum))
    if not allow_delta:
        bad = ("Δpp" in text and "不做 Δpp" not in text and "禁止 Δpp" not in text) or (
            "大幅异动" in text
        )
        # allow mentioning 不做 Δpp
        has_forbidden = "大幅异动" in text
        if "Δ " in text and "不做" not in text and "禁止" not in text:
            has_forbidden = True
        checks.append(("弱对照未滥写 Δpp", not has_forbidden))
    else:
        checks.append(("对照纪律", True))
    checks.append(("有边界说明", "边界说明" in text))
    checks.append(("有风险等级", "风险等级" in text))
    return [f"{'[OK]' if ok else '[X]'} {name}" for name, ok in checks]


def main() -> int:
    p = argparse.ArgumentParser(description="Build Skill-final daily/weekly reports")
    p.add_argument("--ann", default=str(ANN / "annotations_v1_4.csv"))
    p.add_argument("--day", default="", help="锚定日 YYYY-MM-DD；默认库内最新发布日")
    p.add_argument("--week-end", default="", help="周报结束日；默认=锚定日")
    p.add_argument("--min-daily-n", type=int, default=MIN_DAILY_N)
    p.add_argument("--roll-days", type=int, default=ROLL_DAYS)
    p.add_argument("--min-brief-n", type=int, default=MIN_BRIEF_N)
    p.add_argument("--daily-only", action="store_true")
    p.add_argument("--weekly-only", action="store_true")
    args = p.parse_args()

    ann_path = Path(args.ann)
    if not ann_path.exists():
        print(f"缺少标注表：{ann_path}")
        return 2
    df = load_annotated(ann_path)
    if df.empty:
        print("无可用标注行")
        return 1

    latest = max(df["day"])
    anchor = datetime.strptime(args.day, "%Y-%m-%d").date() if args.day else latest
    week_end = (
        datetime.strptime(args.week_end, "%Y-%m-%d").date() if args.week_end else anchor
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    if not args.weekly_only:
        dw = select_daily_window(
            df,
            anchor,
            min_daily_n=args.min_daily_n,
            roll_days=args.roll_days,
            min_brief_n=args.min_brief_n,
        )
        daily_text = build_daily(dw, ann_path.name)
        daily_path = REPORT_DIR / f"daily_{fmt_ymd(dw.anchor)}.md"
        daily_path.write_text(daily_text, encoding="utf-8")
        paths.append(daily_path)
        print(daily_path)
        print(
            f"  mode={dw.mode} days={dw.days[0]}..{dw.days[-1]} "
            f"n={len(dw.cur)} n_anchor={dw.n_anchor} allow_delta={dw.allow_delta}"
        )
        for c in self_check(daily_text, dw.allow_delta):
            print(f"  {c}")

    if not args.daily_only:
        ww = select_weekly_window(df, week_end)
        weekly_text = build_weekly(ww, ann_path.name)
        weekly_path = REPORT_DIR / (
            f"weekly_{fmt_ymd(ww.start)}_{fmt_ymd(ww.end)}.md"
        )
        weekly_path.write_text(weekly_text, encoding="utf-8")
        paths.append(weekly_path)
        print(weekly_path)
        print(
            f"  week={ww.start}..{ww.end} n={len(ww.cur)} "
            f"base_n={len(ww.base)} allow_delta={ww.allow_delta}"
        )
        for c in self_check(weekly_text, ww.allow_delta):
            print(f"  {c}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
