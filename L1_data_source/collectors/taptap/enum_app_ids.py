#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/taptap/enum_app_ids.py — 枚举 app_id 补全游戏社区索引。

★ 为什么需要（用户 2026-10-05 指出：游戏 ID 没收全是我的问题）：
  groups/game 接口声明 total=2208，但 from≥1010 一律 HTTP 400 —— 只能拿到前 ~1016 个。
  而 /app/<id>/topic 页面**没这个限制**：能拿到 group_id 就说明该 app 有社区。
  所以用「枚举 app_id + 解析页面里的 /group/<id>」补全，命中即入库。

★ 断点续扫：进度写 data/raw/taptap/community/enum_progress.json，
  中断后重跑从上次位置继续（长跑必需 —— 扫 20000 个 id 按 1.2s/个约 6-7 小时）。

用法：
    python enum_app_ids.py --start 1 --end 20000          # 扫一段
    python enum_app_ids.py --start 1 --end 20000 --watch # 循环扫
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_taptap_community import CommunityCrawler, load_xua, BASE, GROUP_MAP_FIELDS, save_csv, load_existing  # noqa: E402

TZ_CN = timezone(timedelta(hours=8))
MAP_CSV = ROOT / "data" / "raw" / "taptap" / "community" / "community_map.csv"
PROGRESS = ROOT / "data" / "raw" / "taptap" / "community" / "enum_progress.json"


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def load_progress() -> int:
    if PROGRESS.exists():
        try:
            return int(json.loads(PROGRESS.read_text(encoding="utf-8")).get("next_id") or 1)
        except (ValueError, OSError):
            return 1
    return 1


def save_progress(next_id: int, found: int) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps({"next_id": next_id, "found": found,
                                   "updated_at": now_iso()}, ensure_ascii=False),
                        encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="枚举 app_id 补全 TapTap 游戏社区索引")
    ap.add_argument("--start", type=int, default=0, help="0 = 从断点继续")
    ap.add_argument("--end", type=int, default=20000)
    ap.add_argument("--sleep", type=float, default=1.2, help="每个 id 的间隔秒")
    ap.add_argument("--watch", action="store_true", help="扫完一段后从头再来")
    args = ap.parse_args()

    cw = CommunityCrawler(load_xua(), 1.0, 1.8)
    index = load_existing(MAP_CSV, "group_id")
    start = args.start or load_progress()
    if start < 1:
        start = 1
    total_new = 0
    print(f"[enum] 索引已有 {len(index)} 个社区；从 app_id={start} 扫到 {args.end}", flush=True)

    aid = start
    # 单次网络异常（ConnectionReset / ReadTimeout）不该让 6 小时的长扫前功尽弃，
    # 重试上限拉高到 8 次后再放弃该 id 并继续下一个。
    fails = Counter()
    while aid <= args.end:
        try:
            r = cw.s.get(f"{BASE}/app/{aid}/topic",
                         headers={"Referer": f"{BASE}/"}, timeout=15)
            gids = set(re.findall(r"/group/([0-9]+)", r.text))
            # 页面里可能混进导航栏的其他 group 链接 —— 只认正文区出现的
            if len(gids) == 1:
                gid = gids.pop()
                m = re.search(r"<title>([^<]+)</title>", r.text)
                name = (m.group(1).split(" - ")[0].strip() if m else f"app_{aid}")[:40]
                if gid not in index:
                    index[gid] = {
                        "group_id": gid, "app_id": str(aid), "title": name,
                        "has_official": "", "intro": "", "stat_json": "{}",
                        "web_url": f"/app/{aid}/topic", "last_seen_at": now_iso(),
                        "source": "app_id_enum", "favorite_count": 0,
                        "topic_count": 0, "recent_topic_count": 0,
                        "official_topic_count": 0,
                    }
                    total_new += 1
                    print(f"  [新] app_id={aid:<7} group_id={gid:<9} {name[:30]}", flush=True)
                    save_csv(MAP_CSV, index, GROUP_MAP_FIELDS)
            fails[aid] = 0
        except Exception as e:
            fails[aid] += 1
            if fails[aid] <= 8:
                print(f"[warn] app_id={aid} 第{fails[aid]}次 {type(e).__name__}，稍后重试",
                      file=sys.stderr, flush=True)
                aid -= 1
                time.sleep(min(2 ** fails[aid], 30))
                aid += 1
                continue
            fails.pop(aid, None)
            print(f"[skip] app_id={aid} 重试 8 次仍失败：{type(e).__name__}", file=sys.stderr, flush=True)
        if aid % 100 == 0:
            save_progress(aid + 1, len(index))
            print(f"  … 扫到 {aid}，索引 {len(index)} 个社区", flush=True)
        aid += 1
        time.sleep(args.sleep)

    save_progress(args.end + 1, len(index))
    print(f"[done] 本轮新增 {total_new} 个，索引共 {len(index)} 个", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
