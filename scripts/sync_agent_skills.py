#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Sync portable Agent Skills (skills/) into tool-specific discovery folders.

Canonical source: skills/<name>/
Optional targets:
  - .cursor/skills/<name>/   (Cursor)
  - .claude/skills/<name>/   (Claude Code style; created on demand)

Usage:
  python scripts/sync_agent_skills.py
  python scripts/sync_agent_skills.py --also-claude
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "skills"
SKILL_NAMES = (
    "arknights-taptap-crawl",
    "arknights-llm-annotate-v14",
    "arknights-taptap-yuqing-report",
    "arknights-cross-channel-facts",
)


def sync_one(name: str, dest_root: Path) -> None:
    src = SRC / name
    if not (src / "SKILL.md").is_file():
        raise SystemExit(f"missing skill: {src}")
    dest = dest_root / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    print(f"synced {name} -> {dest.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--also-claude",
        action="store_true",
        help="also copy into .claude/skills/",
    )
    args = parser.parse_args()

    targets = [ROOT / ".cursor" / "skills"]
    if args.also_claude:
        targets.append(ROOT / ".claude" / "skills")

    for dest_root in targets:
        dest_root.mkdir(parents=True, exist_ok=True)
        # remove stale non-portable skills that should not be published
        for child in list(dest_root.iterdir()):
            if child.is_dir() and child.name not in SKILL_NAMES:
                if child.name == "prompt-engineering-review":
                    shutil.rmtree(child)
                    print(f"removed local-only {child.relative_to(ROOT)}")
        for name in SKILL_NAMES:
            sync_one(name, dest_root)

    print("done. Canonical package remains skills/")


if __name__ == "__main__":
    main()
