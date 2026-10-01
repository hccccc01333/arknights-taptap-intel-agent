#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/taptap/crawl_taptap_user.py — 用户社区足迹采集（「用户流动」的数据源）。

回答的问题：某个玩家在社区里都去过哪些游戏、什么时候去的。
接口：feed/v7/by-user?user_id=&from=&limit=10（2026-09-29 实测）
      · 返回该用户的社区发帖**时间序**，每条带来源游戏（moment.app）
      · **limit 上限 10**（填 20/30 直接 HTTP 400）；from 翻页有效；data.total 是总量

用法：
  # 从评分区取样：把给鸣潮打过分的人，拿去查他们的社区足迹（口径 C：投入度 × 流动）
  python crawl_taptap_user.py --game wuthering-waves --from-reviews --limit-users 50
  # 指定用户
  python crawl_taptap_user.py --game wuthering-waves --user-ids 418132051,64202615
  # 续跑（跳过已完成）
  python crawl_taptap_user.py --game wuthering-waves --from-reviews --resume --limit-users 200

输出：<data_dir>/user_flow/
  user_posts.csv          用户 × 帖子（用户标识只有哈希，见下）
  user_flow_checkpoint.json

PII 分层（重要，别破坏）：
  明文 user_id **只从本地原始数据读**（data/raw/taptap_*/reviews.csv，该目录已 gitignore），
  且**不写进任何产出文件**——产出只存 author/user 的加盐哈希（pii_hash.hash_user_id）。
  因为哈希不可逆，没有明文就查不了 by-user；所以明文只存在于本地原始域，产出域只有哈希。
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[3]
CRAWLER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CRAWLER_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))  # pii_hash 已移到跨层共享目录

from crawl_taptap_community import BASE, CommunityCrawler, load_existing, load_xua, now_cn_iso, save_csv  # noqa: E402
from pii_hash import hash_user_id  # noqa: E402

TZ_CN = timezone(timedelta(hours=8))
DEFAULT_GAME = "arknights"
USER_FEED_URL = f"{BASE}/webapiv2/feed/v7/by-user"
MAX_LIMIT = 10  # 实测：>10 返回 HTTP 400

POST_FIELDS = [
    "user_id_hash", "moment_id", "app_id", "app_title", "group_id",
    "title", "summary", "comments", "supports", "ups", "pv_total",
    "publish_time", "publish_time_cn", "crawled_at",
]


def ts_to_cn_iso(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts), TZ_CN).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


def read_user_ids_from_reviews(
    csv_path: Path,
    limit: int = 0,
    sample: str = "stratified",
    high_hours: float = 100.0,
) -> tuple[list[str], dict[str, int]]:
    """从评分区 reviews.csv 取明文 user_id（本地原始数据域）。

    取样方式很关键，选错会让「投入度 × 流动」直接失效：
      stratified（默认） 高投入 / 低投入各半 —— **C 口径必须有对照组**，否则全是高投入，
                        算出来的「流向率」没有可比对象（2026-09-29 踩过这个坑）
      top_hours         只取投入时长最高的（看重度玩家去向）
      random            随机（无偏但不保证两组都够）
    """
    if not csv_path.exists():
        print(f"[stop] 评分区数据不存在：{csv_path}", file=sys.stderr)
        return [], {}
    seen: dict[str, float] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            uid = (r.get("user_id") or "").strip()
            if not uid:
                continue
            try:
                hours = float(r.get("played_hours") or 0)
            except ValueError:
                hours = 0.0
            seen[uid] = max(seen.get(uid, 0.0), hours)  # 多次评分取最大时长

    high = [(h, u) for u, h in seen.items() if h >= high_hours]
    low = [(h, u) for u, h in seen.items() if h < high_hours]
    high.sort(reverse=True)
    low.sort(reverse=True)
    stat = {"pool_users": len(seen), "pool_high": len(high), "pool_low": len(low)}

    if sample == "top_hours":
        picked = high + low
    elif sample == "random":
        picked = high + low
        random.shuffle(picked)
    else:  # stratified：两组各自随机，保证可比（取 top 会让低投入组变成「接近阈值的重度玩家」）
        half = max(1, (limit or 100) // 2)
        random.shuffle(high)
        random.shuffle(low)
        picked = high[:half] + low[:half]
        stat["stratified_high"] = min(half, len(high))
        stat["stratified_low"] = min(half, len(low))
        random.shuffle(picked)
    ids = [u for _, u in picked]
    return (ids[:limit] if limit > 0 else ids), stat


class UserCrawler(CommunityCrawler):
    def fetch_user_feed(self, user_id: str, from_off: int = 0, limit: int = MAX_LIMIT) -> tuple[list[dict], int]:
        """返回 (本页帖子, total)。limit 硬上限 10，接口填更大值会 400。"""
        d = self._get(
            USER_FEED_URL,
            {"user_id": user_id, "from": from_off, "limit": min(limit, MAX_LIMIT)},
            f"{BASE}/user/{user_id}",
        )
        data = (d or {}).get("data") or {}
        return data.get("list") or [], int(data.get("total") or 0)


def parse_user_post(item: dict[str, Any], uid_hash: str, crawled_at: str) -> dict[str, Any] | None:
    m = item.get("moment") or {}
    mid = m.get("id_str")
    if not mid:
        return None
    topic = m.get("topic") or {}
    stat = m.get("stat") or {}
    app = m.get("app") or {}
    group = m.get("group") or {}
    pub = m.get("publish_time") or m.get("created_time") or ""
    return {
        "user_id_hash": uid_hash,
        "moment_id": str(mid),
        "app_id": app.get("id") or group.get("app_id") or "",
        "app_title": (app.get("title") or group.get("title") or "（未标注来源）"),
        "group_id": group.get("id") or "",
        "title": (m.get("title") or topic.get("title") or "").strip(),
        "summary": (m.get("summary") or topic.get("summary") or "").strip(),
        "comments": int(stat.get("comments") or 0),
        "supports": int(stat.get("supports") or 0),
        "ups": int(stat.get("ups") or 0),
        "pv_total": int(stat.get("pv_total") or 0),
        "publish_time": pub,
        "publish_time_cn": ts_to_cn_iso(pub),
        "crawled_at": crawled_at,
    }


def run(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "games"))
    from game_profile import load as load_profile  # noqa: PLC0415

    prof = load_profile(args.game)
    if args.high_hours is None:
        args.high_hours = float(prof.get("high_hours") or 100.0)
    data_dir = Path(args.data_dir or prof.get("data_dir") or "")
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    out_dir = data_dir / "user_flow"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[game] {prof.get('name')} data_dir={data_dir}")

    # ---- 取明文 user_id（只在这一步存在，不落任何产出文件）----
    if args.user_ids:
        users = [u.strip() for u in args.user_ids.split(",") if u.strip()]
        src = "--user-ids"
    elif args.from_reviews:
        users, sstat = read_user_ids_from_reviews(
            data_dir / "reviews.csv", args.limit_users, args.sample, args.high_hours
        )
        src = f"reviews.csv（{args.sample}）"
        print(f"[pool] 评分区独立用户 {sstat.get('pool_users')} 人"
              f"（高投入≥{args.high_hours}h {sstat.get('pool_high')} / 低投入 {sstat.get('pool_low')}）"
              + (f"；分层取 {sstat.get('stratified_high')} + {sstat.get('stratified_low')}"
                 if "stratified_high" in sstat else ""))
    else:
        print("[stop] 需指定 --user-ids 或 --from-reviews", file=sys.stderr)
        return 2
    if not users:
        print("[stop] 没有可用的 user_id", file=sys.stderr)
        return 3
    print(f"[users] 来源={src} 数量={len(users)}")

    cw = UserCrawler(load_xua(), args.sleep_min, args.sleep_max)
    posts_path = out_dir / "user_posts.csv"
    cp_path = out_dir / "user_flow_checkpoint.json"
    posts = load_existing(posts_path, "moment_id")

    done: set[str] = set()
    if cp_path.exists() and args.resume:
        done = set(json.loads(cp_path.read_text(encoding="utf-8")).get("done_hash") or [])
        print(f"[resume] 已完成 {len(done)} 个用户")

    stats = {"users": 0, "users_with_posts": 0, "posts_added": 0, "calls": 0}
    for uid in users:
        uid_hash = hash_user_id(uid)
        if uid_hash in done:
            continue
        crawled_at = now_cn_iso()
        total = None
        got = 0
        frm = 0
        page = 0
        while page < args.max_pages:
            items, total = cw.fetch_user_feed(uid, frm)
            stats["calls"] += 1
            page += 1
            if not items:
                break
            for it in items:
                row = parse_user_post(it, uid_hash, crawled_at)
                if row and row["moment_id"] not in posts:
                    posts[row["moment_id"]] = row
                    stats["posts_added"] += 1
                    got += 1
            if total and frm + MAX_LIMIT >= total:
                break
            frm += MAX_LIMIT
            time.sleep(random.uniform(0.4, 0.9))
        done.add(uid_hash)
        stats["users"] += 1
        if got:
            stats["users_with_posts"] += 1
        if stats["users"] % 10 == 0:
            save_csv(posts_path, posts, POST_FIELDS)
            cp_path.write_text(
                json.dumps({"done_hash": sorted(done), "updated_at": now_cn_iso()}, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[progress] 用户 {stats['users']}/{len(users)} 帖子 {len(posts)}")
        if stats["users"] >= args.limit_users > 0 and not args.user_ids:
            pass  # limit_users 已在取样阶段生效
        cw.polite_sleep()

    save_csv(posts_path, posts, POST_FIELDS)
    cp_path.write_text(
        json.dumps({"done_hash": sorted(done), "updated_at": now_cn_iso()}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        f"[done] 用户 {stats['users']}（有帖 {stats['users_with_posts']}）"
        f" 帖子 {len(posts)}（新增 {stats['posts_added']}） 接口调用 {stats['calls']}"
    )
    print(f"[out] {posts_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="用户社区足迹采集（用户流动数据源）")
    p.add_argument("--game", default=DEFAULT_GAME, help="游戏档案 key（决定 data_dir）")
    p.add_argument("--data-dir", default="", help="数据目录（默认取档案 data_dir）")
    p.add_argument("--from-reviews", action="store_true", help="从评分区 reviews.csv 取 user_id")
    p.add_argument("--user-ids", default="", help="直接指定 user_id（逗号分隔）")
    p.add_argument("--limit-users", type=int, default=50, help="本轮最多处理多少用户")
    p.add_argument("--sample", default="stratified", choices=["stratified", "top_hours", "random"],
                   help="取样方式：stratified 高低投入各半（保证 C 口径有对照组）/ top_hours / random")
    p.add_argument("--high-hours", type=float, default=None, help="高投入阈值（默认取游戏档案 high_hours）")
    p.add_argument("--max-pages", type=int, default=3, help="每个用户最多翻几页（每页 10 条）")
    p.add_argument("--resume", action="store_true", help="跳过已完成用户")
    p.add_argument("--sleep-min", type=float, default=0.8)
    p.add_argument("--sleep-max", type=float, default=1.5)
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
