#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/taptap/crawl_taptap_discovery.py — TapTap 平台级发现通道（S4/S5/S6）。

与两个通道平行：
  crawl_taptap_reviews.py    评分区评论（游戏公司视角）
  crawl_taptap_community.py  单游戏社区帖子流（S1/S2/S3，社区公司视角）
  本脚本                     **平台级**发现流（S4/S5/S6）——不看单一游戏，看整个社区在聊什么

接口（2026-09-29 抓包验证）：
  S4 hashtag/v2/hot-hashtags              全站话题热榜（sort / page_view / comment_count / 话题 id）
  S5 discover-categories/v2/feed-list     发现页跨游戏内容流（category_id + next_page 翻页）
  S6 feed/v7/by-hashtag                   话题下帖子流（hashtag_id + next_page 翻页，S4→S6 深挖链路）

用法：
  python crawl_taptap_discovery.py --source hot-hashtags            # S4 热榜快照
  python crawl_taptap_discovery.py --source discover --max-pages 5  # S5 发现页跨游戏流
  python crawl_taptap_discovery.py --source hashtag-feed --from-hot 5 --comment-limit 20
  python crawl_taptap_discovery.py --source all --max-pages 3

输出（默认 data/raw/taptap/，平台级数据不归属单一游戏）：
  hot_hashtags.csv        话题热榜快照（hot_id / hashtag_id / 标题 / 描述 / 排名 / 浏览 / 评论数）
  discovery_posts.csv     发现流帖子（S5 发现页 + S6 话题下，source_type 区分）
  discovery_comments.csv  上述帖子的评论（--comment-limit > 0 时抓取）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[3]
CRAWLER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CRAWLER_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))  # pii_hash 已移到跨层共享目录

from crawl_taptap_community import (  # noqa: E402
    BASE,
    CommunityCrawler,
    load_existing,
    load_xua,
    now_cn_iso,
    parse_comment,
    save_csv,
)
from pii_hash import hash_user_id  # noqa: E402

DEFAULT_DATA_DIR = "data/raw/taptap"

HASHTAG_URL = f"{BASE}/webapiv2/hashtag/v2/hot-hashtags"
DISCOVER_URL = f"{BASE}/webapiv2/discover-categories/v2/feed-list"
HASHTAG_FEED_URL = f"{BASE}/webapiv2/feed/v7/by-hashtag"

HASHTAG_FIELDS = [
    "hot_id", "hashtag_id", "title", "description", "sort",
    "page_view", "comment_count", "is_new", "crawled_at",
]
POST_FIELDS = [
    "moment_id", "group_id", "app_id", "app_title", "author_name", "author_id_hash",
    "title", "summary",
    "comments", "supports", "ups", "pv_total", "publish_time",
    "hashtags_json", "hashtag_id", "hashtag_title", "source_type", "crawled_at",
]
COMMENT_FIELDS = [
    "moment_id", "comment_id", "author_name", "content", "supports", "publish_time",
    "source_type", "crawled_at",
]


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_hashtag(item: dict[str, Any], crawled_at: str) -> dict[str, Any] | None:
    """S4 热榜条目：hot_hashtag（榜单信息）+ Detail.Hashtag（话题实体 id）。"""
    h = item.get("hot_hashtag") or {}
    title = (h.get("title") or "").strip()
    if not title:
        return None
    det = (item.get("Detail") or {}).get("Hashtag") or {}
    return {
        # 注意：少数榜单条目没有话题实体 id（如「游戏十年纪录片」），hashtag_id 为空，
        # 此时由调用方用 t:<标题> 兜底去重，保证不整条丢榜
        "hashtag_id": det.get("id") or "",
        "hot_id": h.get("id") or "",
        "hashtag_id": det.get("id") or "",
        "title": title,
        "description": (h.get("description") or "").strip(),
        "sort": _int(h.get("sort")),
        "page_view": _int(h.get("page_view")),
        "comment_count": _int(h.get("comment_count")),
        "is_new": "true" if h.get("is_new") else "false",
        "crawled_at": crawled_at,
    }


def parse_moment(
    item: dict[str, Any],
    crawled_at: str,
    source_type: str = "discover",
    hashtag_id: str = "",
    hashtag_title: str = "",
) -> dict[str, Any] | None:
    """S5/S6 帖子：统一解析（两条流的 moment 结构一致，字段位置有差异故多处回退）。"""
    m = item.get("moment") or {}
    mid = m.get("id_str")
    if not mid:
        return None
    topic = m.get("topic") or {}
    stat = m.get("stat") or {}
    group = m.get("group") or {}
    app = m.get("app") or {}
    tags = m.get("hashtags") or []
    tag_titles = [t.get("title") for t in tags if isinstance(t, dict) and t.get("title")]
    author = (m.get("author") or {}).get("user") or m.get("author") or {}
    return {
        "moment_id": str(mid),
        "group_id": group.get("id") or "",
        "app_id": app.get("id") or group.get("app_id") or "",
        "app_title": (app.get("title") or group.get("title") or ""),
        "author_name": author.get("name", ""),
        "author_id_hash": hash_user_id(author.get("id")),
        # S5 正文常在 moment 顶层；S2/S6 常在 moment.topic 下 —— 两处都取
        "title": (m.get("title") or topic.get("title") or "").strip(),
        "summary": (m.get("summary") or topic.get("summary") or "").strip(),
        "comments": _int(stat.get("comments")),
        "supports": _int(stat.get("supports")),
        "ups": _int(stat.get("ups")),
        "pv_total": _int(stat.get("pv_total")),
        "publish_time": m.get("publish_time") or m.get("created_time") or "",
        "hashtags_json": json.dumps(tag_titles, ensure_ascii=False),
        "hashtag_id": hashtag_id,
        "hashtag_title": hashtag_title,
        "source_type": source_type,
        "crawled_at": crawled_at,
    }


class DiscoveryCrawler(CommunityCrawler):
    """复用 CommunityCrawler 的会话 / 重试 / 限速，只增三个平台级接口。"""

    def fetch_hot_hashtags(self, from_off: int, limit: int = 10) -> list[dict[str, Any]]:
        d = self._get(HASHTAG_URL, {"from": from_off, "limit": limit}, f"{BASE}/discover")
        return (d or {}).get("data", {}).get("list") or []

    def fetch_feed_page(self, url: str, params: dict[str, Any], referer: str) -> tuple[list[dict[str, Any]], str]:
        """通用翻页：返回 (本页条目, next_page 路径)。next_page 为空串表示到尾。"""
        d = self._get(url, params, referer) or {}
        data = d.get("data") or {}
        nxt = data.get("next_page") or ""
        return data.get("list") or [], nxt


def collect_feed(
    cw: DiscoveryCrawler,
    url: str,
    first_params: dict[str, Any],
    referer: str,
    max_pages: int,
    empty_pages: int,
    store: dict[str, dict[str, Any]],
    parse_fn,
    stats: dict[str, int],
    label: str,
) -> None:
    """next_page 驱动翻页（next_page 是带 session_id 的完整路径，直接用它请求）。"""
    next_url: str | None = None
    params = first_params
    empty = 0
    page = 0
    while page < max_pages:
        if next_url:
            items, nxt = cw.fetch_feed_page(next_url, {}, referer)
        else:
            items, nxt = cw.fetch_feed_page(url, params, referer)
        page += 1
        stats["pages"] += 1
        if not items:
            print(f"[stop] {label} 第 {page} 页空")
            break
        crawled_at = now_cn_iso()
        page_added = 0
        for it in items:
            row = parse_fn(it, crawled_at)
            if not row:
                continue
            stats["fetched"] += 1
            if row["moment_id"] not in store:
                store[row["moment_id"]] = row
                stats["added"] += 1
                page_added += 1
        print(f"[page] {label} p{page} got={len(items)} added={page_added} total={len(store)}")
        if page_added == 0:
            empty += 1
            if empty >= empty_pages:
                print(f"[stop] {label} 连续 {empty} 页无新增")
                break
        else:
            empty = 0
        if not nxt:
            break
        next_url = BASE + nxt if nxt.startswith("/") else nxt
        cw.polite_sleep()


def run(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"[dir] {data_dir}")

    cw = DiscoveryCrawler(load_xua(), args.sleep_min, args.sleep_max)
    stats = {"pages": 0, "fetched": 0, "added": 0, "comment_calls": 0, "comments_added": 0}
    sources = [s.strip() for s in args.source.split(",") if s.strip()]
    if "all" in sources:
        sources = ["hot-hashtags", "discover", "hashtag-feed"]

    # ---------- S4 话题热榜 ----------
    tags: dict[str, dict[str, Any]] = {}
    if "hot-hashtags" in sources:
        tp = data_dir / "hot_hashtags.csv"
        tags = load_existing(tp, "hashtag_id")
        crawled_at = now_cn_iso()
        frm = 0
        for _ in range(args.max_pages):
            items = cw.fetch_hot_hashtags(frm, args.limit)
            if not items:
                break
            added = 0
            for it in items:
                row = parse_hashtag(it, crawled_at)
                if not row:
                    continue
                key = str(row["hashtag_id"] or f"t:{row['title']}")
                if key not in tags:
                    added += 1
                # 同一话题覆盖写：表里保留的是最近一次抓到的榜单状态
                tags[key] = row
            print(f"[S4] from={frm} got={len(items)} new={added} total={len(tags)}")
            if added == 0:
                break
            frm += args.limit
            cw.polite_sleep()
        save_csv(tp, tags, HASHTAG_FIELDS)
        print(f"[S4 done] 话题 {len(tags)} 条 -> {tp}")

    # ---------- S5 发现页跨游戏流 ----------
    posts_path = data_dir / "discovery_posts.csv"
    comments_path = data_dir / "discovery_comments.csv"
    posts = load_existing(posts_path, "moment_id")
    comments = load_existing(comments_path, "comment_id")

    if "discover" in sources:
        collect_feed(
            cw, DISCOVER_URL, {"category_id": args.category_id, "sort": "default"},
            f"{BASE}/discover", args.max_pages, args.empty_pages, posts,
            lambda it, ts: parse_moment(it, ts, "discover"), stats, "S5",
        )
        save_csv(posts_path, posts, POST_FIELDS)

    # ---------- S6 话题下帖子流（S4 → 深挖） ----------
    if "hashtag-feed" in sources:
        if not tags:
            tags = load_existing(data_dir / "hot_hashtags.csv", "hashtag_id")
        pool = [r for r in tags.values() if str(r.get("hashtag_id") or "").isdigit()]
        pool.sort(key=lambda r: _int(r.get("sort")) or 999)
        if args.hashtag_ids:
            wanted = [w.strip() for w in args.hashtag_ids.split(",") if w.strip()]
            pool = [r for r in pool if str(r["hashtag_id"]) in wanted]
        else:
            pool = pool[: args.from_hot]
        if not pool:
            print("[S6] 无话题可用：先跑 --source hot-hashtags，或用 --hashtag-ids 指定", file=sys.stderr)
        for t in pool:
            tid = str(t["hashtag_id"])
            tname = str(t.get("title") or "")
            print(f"[S6] 话题 {tid} {tname}")
            collect_feed(
                cw, HASHTAG_FEED_URL,
                {"hashtag_id": tid, "from": 0, "limit": args.limit, "sort": "default"},
                f"{BASE}/hashtag/{quote(tname)}", args.hashtag_pages, args.empty_pages, posts,
                lambda it, ts, _tid=tid, _tn=tname: parse_moment(it, ts, "hashtag", _tid, _tn),
                stats, f"S6:{tname[:12]}",
            )
            save_csv(posts_path, posts, POST_FIELDS)

    # ---------- 评论（可选） ----------
    if args.comment_limit > 0 and posts:
        todo = [p for p in posts.values() if _int(p.get("comments")) > 0 and p["moment_id"] not in comments]
        for p in todo[: args.max_comment_calls]:
            gid = _int(p.get("group_id"))
            if not gid:
                continue
            items = cw.fetch_comments(p["moment_id"], gid, args.comment_limit, f"{BASE}/discover")
            stats["comment_calls"] += 1
            crawled_at = now_cn_iso()
            for ci in items:
                c = parse_comment(ci, p["moment_id"], crawled_at)
                if not c:
                    continue
                c["source_type"] = p.get("source_type", "")
                if c["comment_id"] not in comments:
                    comments[c["comment_id"]] = c
                    stats["comments_added"] += 1
            cw.polite_sleep()
        save_csv(comments_path, comments, COMMENT_FIELDS)

    save_csv(posts_path, posts, POST_FIELDS)
    save_csv(comments_path, comments, COMMENT_FIELDS)
    print(
        f"[done] posts={len(posts)} comments={len(comments)} pages={stats['pages']} "
        f"comment_calls={stats['comment_calls']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TapTap 平台级发现通道爬虫（S4 热榜 / S5 发现流 / S6 话题流）")
    p.add_argument("--source", default="all", help="hot-hashtags / discover / hashtag-feed / all（逗号分隔）")
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help=f"输出目录（默认 {DEFAULT_DATA_DIR}）")
    p.add_argument("--limit", type=int, default=10, help="单页条数")
    p.add_argument("--max-pages", type=int, default=3, help="S4/S5 翻页上限")
    p.add_argument("--hashtag-pages", type=int, default=2, help="S6 每个话题翻页上限")
    p.add_argument("--empty-pages", type=int, default=2, help="连续 N 页无新增即停")
    p.add_argument("--category-id", type=int, default=0, help="S5 发现页分类 id（0=全部）")
    p.add_argument("--from-hot", type=int, default=5, help="S6：取热榜前 N 个话题深挖")
    p.add_argument("--hashtag-ids", default="", help="S6：指定话题 id（逗号分隔），优先于 --from-hot")
    p.add_argument("--comment-limit", type=int, default=0, help="每帖评论抓取上限；0 = 不抓")
    p.add_argument("--max-comment-calls", type=int, default=30, help="本轮评论接口调用上限")
    p.add_argument("--sleep-min", type=float, default=0.8)
    p.add_argument("--sleep-max", type=float, default=1.5)
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
