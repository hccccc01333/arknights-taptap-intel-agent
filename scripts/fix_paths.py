#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分层重排后的路径修复（旧编号目录 → 新分层目录）。

三条原则：
1. **只改代码与活跃文档**（.py + skills/ + README），**不改历史**：
   `data/`（原始数据）、`docs/`（历史设计文档）、`.workbuddy/memory/`（工作日志）一律跳过 ——
   它们记录的是"当时的事实"，改了溯源就断了。
2. **长串优先**：先替换精确到文件名的串，再替换目录级短串，
   否则 `06对照_B站/crawl_bili_comments.py` 会被误改成 `data/raw/bilibili/crawl_...py`。
3. **防自伤**：脚本必须跳过自己 —— 它体内就写着这些旧路径。
   第一轮执行时我把它自己的映射表改坏了（键被替换成了值），已修复并加入 SELF 跳过。

用法：python scripts/fix_paths.py [--dry-run] [--verbose]
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# —— 1. 精确到文件的替换（必须最先执行）——
EXACT: List[Tuple[str, str]] = [
    ("02数据_platform/discovery_posts.csv",    "data/raw/taptap/discovery_posts.csv"),
    ("02数据_platform/discovery_comments.csv", "data/raw/taptap/discovery_comments.csv"),
    ("02数据_platform/hot_hashtags.csv",       "data/raw/taptap/hot_hashtags.csv"),
    ("02数据/processed/reviews_clean.csv",     "data/processed/reviews/reviews_clean.csv"),

    ("01爬虫/crawl_taptap_discovery.py",  "L1_data_source/collectors/taptap/crawl_taptap_discovery.py"),
    ("01爬虫/crawl_taptap_community.py",  "L1_data_source/collectors/taptap/crawl_taptap_community.py"),
    ("01爬虫/crawl_taptap_reviews.py",    "L1_data_source/collectors/taptap/crawl_taptap_reviews.py"),
    ("01爬虫/crawl_taptap_user.py",       "L1_data_source/collectors/taptap/crawl_taptap_user.py"),

    ("06对照_B站/crawl_bili_comments.py",      "L1_data_source/collectors/bilibili/crawl_bili_comments.py"),
    ("06对照_B站/annotate_bili_sample.py",     "L3_semantic/channels/bilibili/annotate_bili_sample.py"),
    ("06对照_B站/build_contrast_report.py",    "L6_delivery/contrast/bilibili/build_contrast_report.py"),
    ("07对照_抖音/crawl_douyin_comments.py",   "L1_data_source/collectors/douyin/crawl_douyin_comments.py"),
    ("07对照_抖音/annotate_douyin_sample.py",  "L3_semantic/channels/douyin/annotate_douyin_sample.py"),
    ("08对照_微博/crawl_weibo_posts.py",       "L1_data_source/collectors/weibo/crawl_weibo_posts.py"),
    ("08对照_微博/annotate_weibo_sample.py",   "L3_semantic/channels/weibo/annotate_weibo_sample.py"),

    ("09跨渠道AI/synthesize_cross_channel.py", "L2_signal/cross_channel/synthesize_cross_channel.py"),
    ("09跨渠道AI/build_channel_facts.py",      "L2_signal/cross_channel/build_channel_facts.py"),
    ("10分析实验室/model_eval.py",             "L2_signal/lab/model_eval.py"),
    ("10分析实验室/anomaly_diagnosis.py",      "L2_signal/lab/anomaly_diagnosis.py"),
    ("10分析实验室/event_impact.py",           "L2_signal/lab/event_impact.py"),
    ("10分析实验室/events.csv",                "L2_signal/lab/events.csv"),
    ("03标注结果/qc/score_qc.py",              "L3_semantic/qc_orig/score_qc.py"),
]

# —— 2. 旧 11情报Agent 拆散后的模块归属（一个目录拆成了四层）——
MODULE_TO_LAYER: Dict[str, str] = {
    "topic_tracker": "L3_trend", "ferment_judge": "L3_trend", "events": "L3_trend",
    "freshness": "L3_trend", "anomaly_lite": "L3_trend", "intel_stats": "L3_trend",
    "materials": "L5_generation", "community_insight": "L5_generation", "community_ops": "L5_generation",
    "platform_insight": "L5_generation", "risk_insight": "L5_generation",
    "user_flow": "L5_generation", "cross_game_compare": "L5_generation",
    "daily_agent": "L6_delivery/briefing",
    "harness": "runtime", "task_contracts": "runtime", "agent_graph": "runtime", "scheduler": "runtime",
}

# —— 3. 目录级替换（放在最后兜底）——
DIRS: List[Tuple[str, str]] = [
    ("02数据_platform", "data/raw/taptap"),
    ("02数据/processed", "data/processed/reviews"),
    ("02数据",          "data/raw/taptap"),
    ("03标注结果",      "data/annotations"),
    ("01爬虫",          "L1_data_source/collectors/taptap"),
    ("09跨渠道AI",      "L2_signal/cross_channel"),
    ("10分析实验室",    "L2_signal/lab"),
    ("04日报周报",      "L6_delivery/period_reports"),
    ("05展示页",        "L6_delivery/dashboard"),
    ("11提示词工程",    "docs/prompt_engineering"),
    ("06对照_B站",      "data/raw/bilibili"),
    ("07对照_抖音",     "data/raw/douyin"),
    ("08对照_微博",     "data/raw/weibo"),
]

SCAN_EXT = (".py", ".md")
# 历史与数据不动，避免篡改事实
SKIP_DIRS = {"data", "docs", ".git", ".workbuddy", "__pycache__", "node_modules", "scripts"}
SELF = os.path.abspath(__file__)


def iter_files() -> List[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if fn.endswith(SCAN_EXT) and os.path.abspath(p) != SELF:
                out.append(p)
    return out


def rewrite_intel_agent(text: str) -> str:
    """11情报Agent/<mod>.py → <新层>/<mod>.py；reports/ 归交付层。"""
    for mod, layer in MODULE_TO_LAYER.items():
        text = text.replace(f"11情报Agent/{mod}.py", f"{layer}/{mod}.py")
    text = text.replace("11情报Agent/reports/", "L6_delivery/briefing/reports/")
    text = text.replace("11情报Agent/state/", "data/state/")
    text = text.replace("11情报Agent/outputs/", "data/outputs/agent/")
    return text


def fix_file(path: str, dry: bool) -> int:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    orig = text
    for old, new in EXACT:
        text = text.replace(old, new)
    text = rewrite_intel_agent(text)
    for old, new in DIRS:
        text = text.replace(old, new)
    text = text.replace("11情报Agent", "L3_trend")     # 兜底：说不清归属的按决策层
    if text != orig:
        if not dry:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        return 1
    return 0


def main() -> int:
    dry = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv
    n = 0
    for f in iter_files():
        if fix_file(f, dry):
            n += 1
            if verbose:
                print("  fixed", os.path.relpath(f, ROOT))
    print(f"{'[dry-run] ' if dry else ''}修改文件数: {n}")
    left: Dict[str, int] = {}
    for f in iter_files():
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            t = fh.read()
        for pat in ("02数据", "03标注结果", "01爬虫", "06对照_B站", "07对照_抖音",
                    "08对照_微博", "09跨渠道AI", "10分析实验室", "11情报Agent", "04日报周报", "05展示页"):
            c = t.count(pat)
            if c:
                left[pat] = left.get(pat, 0) + c
    print("残留引用统计:", left or "无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
