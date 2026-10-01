#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键刷新演示产物：Skill 日/周报 + 跨渠 facts + 离线看板（不爬、不标、不调 LLM 合成）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(rel: str) -> int:
    script = ROOT / rel
    print(f"\n>>> {rel}")
    r = subprocess.run([sys.executable, str(script)], cwd=str(ROOT))
    return int(r.returncode)


def main() -> int:
    for rel in (
        "L6_delivery/period_reports/build_skill_reports.py",
        "L2_signal/cross_channel/build_channel_facts.py",
        "L6_delivery/dashboard/build_dashboard.py",
    ):
        code = run(rel)
        if code != 0:
            print(f"失败：{rel} (exit={code})")
            return code
    print("\n完成：reports/、facts_cross_channel.json 与 L6_delivery/dashboard/index.html 已刷新。")
    print(
        "跨渠道 AI 简报需单独运行（需 Key）："
        "python L2_signal/cross_channel/synthesize_cross_channel.py"
    )
    print("无 Key 模板演示：python L2_signal/cross_channel/synthesize_cross_channel.py --template-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
