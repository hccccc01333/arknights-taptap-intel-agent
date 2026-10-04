#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抓取游戏封面（TapTap 公开图床）→ spatial/public/covers/ + games.json

为什么需要：3D 卡片要有真实封面，而不是色块占位。
数据来源：L3 事件里的 game_* 实体标签（10 个真实游戏，覆盖 253 个话题）。

用法：
    python scripts/fetch_game_covers.py            # 抓取（已存在则跳过）
    python scripts/fetch_game_covers.py --force    # 重抓

产物：
    spatial/public/covers/<game>.jpg
    spatial/public/covers/games.json   { key: {name, cover, url} }
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "spatial" / "public" / "covers"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 20

# L3 实体标签 → TapTap app id（app 详情页 SSR 里直接带 image 字段，最稳）
APP_IDS: dict[str, int] = {
    "arknights": 167982,
    "endfield": 2177636,
    "wuthering-waves": 171704,
    "delta_force": 2353155,
    "genshin": 170601,
    "star_rail": 2291588,
    "zenless": 254456,
    "honor_of_kings": 5566,
    "black_myth_wukong": 2703815,
    "lol": 23976,
}

# L3 实体标签 → 中文名（用于卡片标题与检索兜底）
SEARCH: dict[str, str] = {
    "arknights": "明日方舟",
    "endfield": "明日方舟终末地",
    "wuthering-waves": "鸣潮",
    "delta_force": "三角洲行动",
    "genshin": "原神",
    "star_rail": "崩坏：星穹铁道",
    "zenless": "绝区零",
    "honor_of_kings": "王者荣耀",
    "black_myth_wukong": "黑神话：悟空",
    "lol": "英雄联盟",
}


def games_from_l3() -> list[str]:
    """从 L3 事件里取出出现过的游戏实体标签（按话题数排序）。"""
    from collections import Counter
    db = ROOT / "data" / "state" / "l3_trend.sqlite3"
    if not db.exists():
        sys.exit("L3 库不存在：先跑 L3_trend/trend_engine/pipeline.py --run")
    con = sqlite3.connect(str(db))
    c: Counter = Counter()
    for (e,) in con.execute('SELECT entity_ids FROM trend_event WHERE status="active"'):
        for t in json.loads(e or "[]"):
            if t.startswith("game_"):
                c[t[5:]] += 1
    con.close()
    return [k for k, _ in c.most_common()]


def fetch_app(app_id: int) -> dict | None:
    """游戏详情页 → 封面图 + 标题 + 评分（SSR 的 __NUXT__ 数据里）。

    ★ 为什么不用搜索页：搜索页 SSR 不渲染 image 字段（实测 0 命中），
      详情页稳定带 "image":"..._tap_appicon.jpg"。踩过一次，不重复踩。
    """
    url = f"https://www.taptap.cn/app/{app_id}"
    req = urllib.request.Request(url, headers=UA)
    try:
        html = urllib.request.urlopen(req, timeout=TIMEOUT).read().decode("utf-8", "replace")
    except Exception as e:
        print(f"  ! 打开失败 app/{app_id}: {e}")
        return None
    m = re.search(r'"image"\s*:\s*"(https://img-tc\.tapimg\.com/[^"]+?_tap_appicon[^"]*)"', html) \
        or re.search(r'(https://img-tc\.tapimg\.com/[^"\\]+?/_tap_appicon[^"\\]*)', html)
    if not m:
        return None
    title = re.search(r'"title"\s*:\s*"([^"]{1,40})"', html)
    stat = re.search(r'"stat"\s*:\s*\{[^}]*?"score"\s*:\s*([0-9.]+)', html)
    return {"cover": m.group(1).replace("\\u002F", "/").replace("\\/", "/"),
            "title": title.group(1) if title else "",
            "score": float(stat.group(1)) if stat else None}


def download(url: str, dest: Path) -> int:
    req = urllib.request.Request(url, headers=UA)
    data = urllib.request.urlopen(req, timeout=TIMEOUT).read()
    dest.write_bytes(data)
    return len(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict] = {}
    index_path = OUT / "games.json"
    if index_path.exists() and not args.force:
        index = json.loads(index_path.read_text(encoding="utf-8"))

    keys = games_from_l3()
    print(f"L3 里出现 {len(keys)} 个游戏：{', '.join(keys)}")
    for key in keys:
        target = OUT / f"{key}.jpg"
        if target.exists() and key in index and not args.force:
            print(f"  = {key:18s} 已存在")
            continue
        app_id = APP_IDS.get(key)
        if not app_id:
            print(f"  - {key:18s} 没有登记 app id")
            continue
        hit = fetch_app(app_id)
        if not hit:
            print(f"  - {key:18s} 没找到封面")
            continue
        try:
            size = download(hit["cover"], target)
        except Exception as e:
            print(f"  ! {key:18s} 下载失败: {e}")
            continue
        index[key] = {"name": hit["title"] or SEARCH.get(key, key),
                      "file": f"covers/{key}.jpg", "app_id": app_id,
                      "score": hit.get("score"), "source": hit["cover"],
                      "bytes": size}
        sc = f" {hit['score']}" if hit.get("score") else ""
        print(f"  + {key:18s} {(hit['title'] or key)[:14]:16s}{sc}  {size//1024}KB")

    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成：{len(index)}/{len(keys)} 个游戏有封面 → {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
