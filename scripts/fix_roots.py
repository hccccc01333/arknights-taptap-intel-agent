#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修正"上溯层数"：分层后目录变深，原来 parents[1] = 项目根 的写法全部错位。

规则：把 `ROOT|BASE_DIR|PROJECT_ROOT|ROOT_DIR = Path(__file__).resolve().parents[N]`
中的 N 改成"该文件到项目根的真实层数"。tests/ 里的 LAB 本意是层目录，不在修改范围内。

用法：python scripts/fix_roots.py [--dry-run]
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAT = re.compile(
    r"\b(ROOT|BASE_DIR|PROJECT_ROOT|ROOT_DIR|PROJECT_DIR)\s*=\s*Path\(__file__\)\.resolve\(\)\.parents\[(\d+)\]"
)
SKIP = {"data", ".git", ".workbuddy", "__pycache__", "node_modules", "docs"}


def main() -> int:
    dry = "--dry-run" in sys.argv
    changed = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            try:
                rel = p.parent.relative_to(ROOT)
            except ValueError:
                continue
            depth = len(rel.parts) or 0
            text = p.read_text(encoding="utf-8", errors="replace")
            new = PAT.sub(lambda m: f"{m.group(1)} = Path(__file__).resolve().parents[{depth}]", text)
            if new != text:
                if not dry:
                    p.write_text(new, encoding="utf-8", newline="")
                changed += 1
                print(f"  {rel}/{fn}: -> parents[{depth}]")
    print(f"{'[dry-run] ' if dry else ''}修正文件数: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
