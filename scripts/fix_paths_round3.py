#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三轮路径修复：修正第二轮"拼接式引用"的错层。

背景：旧 `02数据/` 是个混合工作区（reviews.csv + processed/ + figures/），
第二轮按目录名整体替换成 data/raw/taptap，导致
`ROOT / "data/raw/taptap" / "processed" / "reviews_clean.csv"` 这种拼接路径错位 ——
processed 实际搬到了 data/processed/reviews。

本轮只改这类"语义错层"，不动已经正确的引用。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (旧串, 新串) —— 按顺序执行
REPLACES = [
    # processed / figures 已从"数据根"拆到 data/processed 下
    ('ROOT / "data/raw/taptap" / "processed"', 'ROOT / "data/processed/reviews"'),
    ('DATA / "processed" / "reviews_clean.csv"', 'ROOT / "data/processed/reviews" / "reviews_clean.csv"'),
    ('"data/raw/taptap" / "processed"', '"data/processed/reviews"'),
    # 采集侧 features 产物归 data/facts
    ('ROOT / "data/raw/taptap" / "features"', 'ROOT / "data/facts" / "features"'),
    # annotate_reviews.py 是 L3 语义理解层的脚本，不是原始数据
    ('ROOT / "data/raw/taptap" / "annotate_reviews.py"', 'ROOT / "L3_semantic" / "annotate_reviews.py"'),
    ('sys.path.insert(0, str(ROOT / "data/raw/taptap"))', 'sys.path.insert(0, str(ROOT / "L3_semantic"))'),
    ('data/raw/taptap/annotate_reviews.py', 'L3_semantic/annotate_reviews.py'),
]

# 整行替换（用于变量定义）
LINE_REPLACES = {
    "L2_signal/preprocess_reviews.py": [
        ('PROCESSED_DIR = DATA_DIR / "processed"', 'PROCESSED_DIR = ROOT / "data/processed/reviews"'),
        ('REPORT_DIR = DATA_DIR / "reports"', 'REPORT_DIR = ROOT / "data/processed/reviews" / "reports"'),
        ('FIG_DIR = DATA_DIR / "figures"', 'FIG_DIR = ROOT / "data/processed" / "figures"'),
    ],
    "L3_semantic/annotate_reviews.py": [
        ('PROCESSED = DATA_DIR / "processed"', 'PROCESSED = ROOT / "data/processed/reviews"'),
    ],
}

SKIP = {"data", ".git", ".workbuddy", "__pycache__", "node_modules", "docs", "scripts"}


def main() -> int:
    dry = "--dry-run" in sys.argv
    changed = []

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            text = p.read_text(encoding="utf-8", errors="replace")
            orig = text
            for old, new in REPLACES:
                text = text.replace(old, new)
            rel = p.relative_to(ROOT).as_posix()
            for old, new in LINE_REPLACES.get(rel, []):
                text = text.replace(old, new)
            if text != orig:
                if not dry:
                    p.write_text(text, encoding="utf-8", newline="")
                changed.append(rel)

    for c in changed:
        print("  fixed", c)
    print(f"{'[dry-run] ' if dry else ''}修正文件数: {len(changed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
