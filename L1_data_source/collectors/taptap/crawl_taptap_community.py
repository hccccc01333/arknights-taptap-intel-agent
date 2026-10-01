#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/taptap/crawl_taptap_community.py — TapTap 社区通道爬虫（帖子流 + 热评 + 帖子评论）。

与评分爬虫（crawl_taptap_reviews.py）平行的第二数据通道：
  社区公司视角的数据面 = 游戏社区的话题流，不只评分区。

接口（2026-09-28 抓包验证）：
  group/v1/recommend            全平台社区地图（app_id → group_id 映射来源，from 翻页）
  feed/v7/by-group              社区帖子流（group_id + from 翻页；moment.topic.summary=正文、
                                stat.comments/supports/pv_total、hot_comment_list 内嵌热评）
  moment-comment/v1/by-moment   帖子评论流（moment_id）

用法：
  python crawl_taptap_community.py --game wuthering-waves --max-posts 300 --comment-limit 20
输出（数据隔离目录）：
  <data_dir>/community/posts.csv      帖子（含热评 JSON）
  <data_dir>/community/comments.csv   帖子评论
  <data_dir>/community/community_checkpoint.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))  # pii_hash 在跨层共享目录
from pii_hash import hash_user_id  # noqa: E402  用户标识加盐哈希（与评分区/发现流共用同一套）

ROOT = Path(__file__).resolve().parents[3]
CRAWLER_DIR = Path(__file__).resolve().parent
TZ_CN = timezone(timedelta(hours=8))
DEFAULT_GAME = "arknights"

BASE = "https://www.taptap.cn"
RECOMMEND_URL = f"{BASE}/webapiv2/group/v1/recommend"
FEED_URL = f"{BASE}/webapiv2/feed/v7/by-group"
COMMENT_URL = f"{BASE}/webapiv2/moment-comment/v1/by-moment"

POST_FIELDS = [
    "moment_id", "group_id", "app_id", "author_name", "author_id_hash", "title", "summary",
    "comments", "supports", "ups", "pv_total", "publish_time",
    "hot_comment_count", "hot_comments_json", "crawled_at", "source_type",
]
COMMENT_FIELDS = [
    "moment_id", "comment_id", "author_name", "author_id_hash", "content", "supports",
    "publish_time", "crawled_at",
]


def now_cn_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def load_xua() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(CRAWLER_DIR / ".env")
    except ImportError:
        for line in (CRAWLER_DIR / ".env").read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "TAPTAP_X_UA":
                os.environ.setdefault("TAPTAP_X_UA", v.strip())
    xua = os.environ.get("TAPTAP_X_UA", "").strip()
    if not xua:
        print("缺少 TAPTAP_X_UA（L1_data_source/collectors/taptap/.env）", file=sys.stderr)
        raise SystemExit(2)
    return xua


class CommunityCrawler:
    def __init__(self, x_ua: str, sleep_min: float = 0.8, sleep_max: float = 1.5):
        self.x_ua = x_ua
        self.sleep_min, self.sleep_max = sleep_min, sleep_max
        self.s = requests.Session()
        self.s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Origin": BASE,
            }
        )

    def polite_sleep(self) -> None:
        time.sleep(random.uniform(self.sleep_min, self.sleep_max))

    def _get(self, url: str, params: dict[str, Any], referer: str, retries: int = 3) -> dict[str, Any] | None:
        last_err = ""
        for attempt in range(retries):
            try:
                r = self.s.get(
                    url,
                    params={"X-UA": self.x_ua, **params},
                    headers={"Referer": referer},
                    timeout=20,
                )
                if r.status_code != 200:
                    print(f"[warn] {url.rsplit('/', 1)[-1]} HTTP {r.status_code} (attempt {attempt + 1})", file=sys.stderr)
                    last_err = f"HTTP {r.status_code}"
                else:
                    return r.json()
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {str(e)[:60]}"
                print(f"[warn] {url.rsplit('/', 1)[-1]} {last_err} (attempt {attempt + 1})", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
        print(f"[error] 重试 {retries} 次仍失败：{last_err}", file=sys.stderr)
        return None

    # ---- group 映射：app_id -> group_id（recommend 翻页，带缓存） ----
    def resolve_group_id(self, app_id: int, cache_path: Path, preset: int | None = None, max_pages: int = 150) -> int | None:
        if preset:
            return int(preset)
        if cache_path.exists():
            mp = json.loads(cache_path.read_text(encoding="utf-8"))
            if str(app_id) in mp:
                return int(mp[str(app_id)])
        else:
            mp = {}
        # HTML 自寻（快，先试）
        if not preset:
            g = self.discover_group_id(app_id, f"{BASE}/app/{app_id}/topic")
            if g:
                mp[str(app_id)] = g
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(mp, ensure_ascii=False, indent=2), encoding="utf-8")
                return g
        frm = 0
        pages = 0
        while pages < max_pages:
            d = self._get(RECOMMEND_URL, {"from": frm}, f"{BASE}/forum") or {}
            lst = (d.get("data") or {}).get("list") or []
            if not lst:
                break
            pages += 1
            for g in lst:
                aid = str(g.get("app_id"))
                gid = g.get("id")
                if aid and gid:
                    mp[aid] = int(gid)
            if str(app_id) in mp:
                break
            frm += 20
            time.sleep(0.3)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(mp, ensure_ascii=False, indent=2), encoding="utf-8")
        gid = mp.get(str(app_id))
        if not gid:
            print(
                f"[error] recommend 清单 {pages} 页内未找到 app_id={app_id} 的 group_id；"
                f"请在浏览器打开该游戏社区页抓一次 feed 请求，把 group_id 写入游戏档案的 group_id 字段",
                file=sys.stderr,
            )
        return gid

    # ---- group_id 自寻：游戏社区页 HTML 链接挖掘 + feed 验证 ----
    def discover_group_id(self, app_id: int, referer: str) -> int | None:
        """从 /app/<id>/topic 页 HTML 挖 /group/<id> 链接，用 feed 接口验证归属。"""
        try:
            r = self.s.get(f"{BASE}/app/{app_id}/topic", headers={"User-Agent": self.s.headers["User-Agent"], "Referer": f"{BASE}/"}, timeout=20)
        except requests.RequestException:
            return None
        candidates = []
        for gid in re.findall(r"/group/(\d{3,})", r.text):
            if gid not in candidates:
                candidates.append(gid)
        for gid in candidates[:5]:
            items = self.fetch_feed_page(int(gid), 0, f"{BASE}/app/{app_id}/topic")
            for it in items[:1]:
                if ((it.get("moment") or {}).get("app") or {}).get("id") == app_id:
                    print(f"[group] HTML 自寻命中：app_id={app_id} -> group_id={gid}")
                    return int(gid)
            self.polite_sleep()
        return None

    # ---- 帖子流 ----
    def fetch_feed_page(self, group_id: int, from_off: int, referer: str, feed_type: str = "feed") -> list[dict[str, Any]]:
        d = self._get(
            FEED_URL,
            {"from": from_off, "group_id": group_id, "limit": 10, "sort": "default",
             "status": 0, "type": feed_type, "with_hot_comment": "true"},
            referer,
        )
        return (d or {}).get("data", {}).get("list") or []

    # ---- 帖子评论 ----
    def fetch_comments(self, moment_id: str, group_id: int, limit: int, referer: str) -> list[dict[str, Any]]:
        d = self._get(
            COMMENT_URL,
            {"moment_id": moment_id, "sort": "rank", "order": "desc",
             "regulate_all": "false", "group_id": group_id, "limit": min(limit, 50)},
            referer,
        )
        return (d or {}).get("data", {}).get("list") or []


def parse_post(item: dict[str, Any], group_id: int, crawled_at: str, source_type: str = "feed") -> dict[str, Any] | None:
    m = item.get("moment") or {}
    mid = m.get("id_str")
    if not mid:
        return None
    topic = m.get("topic") or {}
    stat = m.get("stat") or {}
    author_obj = (m.get("author") or {}).get("user") or m.get("author") or {}
    author = author_obj.get("name", "")
    hot = m.get("hot_comment_list") or []
    pub = m.get("publish_time") or m.get("created_time")
    return {
        "moment_id": str(mid),
        "group_id": group_id,
        "app_id": (m.get("app") or {}).get("id", ""),
        "author_name": author,
        "author_id_hash": hash_user_id(author_obj.get("id")),
        "title": (topic.get("title") or "").strip(),
        "summary": (topic.get("summary") or "").strip(),
        "comments": int(stat.get("comments") or 0),
        "supports": int(stat.get("supports") or 0),
        "ups": int(stat.get("ups") or 0),
        "pv_total": int(stat.get("pv_total") or 0),
        "publish_time": pub or "",
        "hot_comment_count": len(hot),
        "hot_comments_json": json.dumps(hot, ensure_ascii=False),
        "crawled_at": crawled_at,
        "source_type": source_type,
    }


def flatten_contents(node: Any) -> str:
    """评论正文富文本拍平。

    实测两种形态：
      (a) 评分区/社区早期：contents = {"text": "..."} 或纯字符串
      (b) 发现流/S6：contents = {"json": [{"type":"paragraph","children":[{"text":"..."},
                                           {"type":"tap_emoji","children":[{"text":"[表情_斜眼笑]"}]}]}]}
    递归收集所有 text 节点；表情占位保留（对情绪判断有信息量）。
    """
    out: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, str):
            if n:
                out.append(n)
        elif isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict):
            t = n.get("text")
            if isinstance(t, str) and t:
                out.append(t)
            for k in ("json", "children", "content", "contents"):
                v = n.get(k)
                if isinstance(v, (list, dict, str)):
                    walk(v)

    walk(node)
    return "".join(out).strip()


def _author_obj(obj: dict[str, Any]) -> dict[str, Any]:
    """作者对象：author（发现流）→ author.user（评分区/社区）。"""
    a = obj.get("author")
    if isinstance(a, dict):
        if a.get("name") or a.get("id"):
            return a
        u = a.get("user")
        if isinstance(u, dict):
            return u
    return {}


def _author_name(obj: dict[str, Any]) -> str:
    """作者名：author.user.name（评分区/社区）→ author.name（发现流帖子评论）。"""
    for src in (obj.get("author"), (obj.get("author") or {}).get("user")):
        if isinstance(src, dict) and src.get("name"):
            return str(src["name"])
    return ""


def parse_comment(item: dict[str, Any], moment_id: str, crawled_at: str) -> dict[str, Any] | None:
    # 实测：评论对象直接在 item 顶层（无 comment 包裹）；正文在 contents（dict 或 str）
    c = item.get("comment") or item
    cid = c.get("id_str") or c.get("id")
    if not cid:
        return None
    author = _author_name(item) or _author_name(c)
    aobj = _author_obj(item) or _author_obj(c)
    rawc = c.get("contents")
    content = flatten_contents(rawc) if rawc is not None else ""
    if not content:
        content = flatten_contents(c.get("content") or c.get("summary") or "")
    stat = c.get("stat") or {}
    supports = stat.get("supports") or stat.get("likes") or c.get("ups") or 0
    return {
        "moment_id": moment_id,
        "comment_id": str(cid),
        "author_name": author,
        "author_id_hash": hash_user_id(aobj.get("id")),
        "content": str(content).strip(),
        "supports": int(supports or 0),
        "publish_time": c.get("publish_time") or c.get("created_time") or c.get("updated_time") or "",
        "crawled_at": crawled_at,
    }


def load_existing(path: Path, key_field: str) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return {str(r.get(key_field)): r for r in csv.DictReader(f) if r.get(key_field)}


def save_csv(path: Path, rows: dict[str, dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        rows.values(),
        key=lambda r: int(r.get("publish_time", 0)) if str(r.get("publish_time", "")).isdigit() else 0,
        reverse=True,
    )
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in ordered:
            w.writerow({k: r.get(k, "") for k in fields})


def run(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "games"))
    from game_profile import load as load_profile  # noqa: PLC0415

    prof = load_profile(args.game)
    data_dir = Path(args.data_dir or prof.get("data_dir") or "")
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    community_dir = data_dir / "community"
    community_dir.mkdir(parents=True, exist_ok=True)
    app_id = int(prof.get("app_id") or 0)
    referer = f"{BASE}/app/{app_id}/topic"
    print(f"[game] {prof.get('name')} app_id={app_id} data_dir={data_dir}")

    cw = CommunityCrawler(load_xua(), args.sleep_min, args.sleep_max)

    group_id = cw.resolve_group_id(app_id, community_dir / "group_map.json", preset=int(prof["group_id"]) if prof.get("group_id") else None)
    if not group_id:
        print(f"[stop] 未在 recommend 全平台清单中找到 app_id={app_id} 的社区", file=sys.stderr)
        return 3
    print(f"[group] group_id={group_id}")

    posts_path = community_dir / "posts.csv"
    comments_path = community_dir / "comments.csv"
    posts = load_existing(posts_path, "moment_id")
    comments = load_existing(comments_path, "comment_id")
    print(f"[existing] posts={len(posts)} comments={len(comments)}")

    stats = {"pages": 0, "fetched": 0, "added": 0, "comment_calls": 0, "comments_added": 0}
    cp_path = community_dir / "community_checkpoint.json"
    frm = 0
    if cp_path.exists() and args.resume:
        frm = int(json.loads(cp_path.read_text(encoding="utf-8")).get("last_from") or 0)
        print(f"[resume] last_from={frm}")
    empty_pages = 0
    feed_types = [t.strip() for t in args.types.split(",") if t.strip()]
    cur_type_idx = 0
    while len(posts) < args.max_posts and stats["pages"] < args.max_pages and cur_type_idx < len(feed_types):
        cur_type = feed_types[cur_type_idx]
        if stats["pages"] > 0 and frm > 0 and empty_pages >= args.empty_pages:
            # 切换下一种流
            cur_type_idx += 1
            if cur_type_idx >= len(feed_types):
                break
            cur_type = feed_types[cur_type_idx]
            frm = 0
            empty_pages = 0
            print(f"[switch] 切换到流类型：{cur_type}")
        items = cw.fetch_feed_page(group_id, frm, referer, cur_type)
        stats["pages"] += 1
        if not items:
            print(f"[stop] {cur_type} from={frm} 空页")
            cur_type_idx += 1
            if cur_type_idx >= len(feed_types):
                break
            frm = 0
            empty_pages = 0
            continue
        crawled_at = now_cn_iso()
        page_added = 0
        for it in items:
            row = parse_post(it, group_id, crawled_at, cur_type)
            if not row:
                continue
            stats["fetched"] += 1
            if row["moment_id"] not in posts:
                posts[row["moment_id"]] = row
                stats["added"] += 1
                page_added += 1
            # 帖子评论（只抓有评论且未抓过评论的帖子，受 --comment-limit 控制）
            if args.comment_limit > 0 and int(row["comments"]) > 0 and row["moment_id"] not in comments:
                if args.comment_limit >= 0 and stats["comment_calls"] >= args.max_comment_calls:
                    continue
                citems = cw.fetch_comments(row["moment_id"], group_id, args.comment_limit, referer)
                stats["comment_calls"] += 1
                for ci in citems:
                    c = parse_comment(ci, row["moment_id"], crawled_at)
                    if c and c["comment_id"] not in comments:
                        comments[c["comment_id"]] = c
                        stats["comments_added"] += 1
                cw.polite_sleep()
        save_csv(posts_path, posts, POST_FIELDS)
        cp_path.write_text(json.dumps({"last_from": frm + 10, "updated_at": now_cn_iso()}, ensure_ascii=False), encoding="utf-8")
        print(f"[page] from={frm} got={len(items)} added={page_added} total={len(posts)}")
        if page_added == 0:
            empty_pages += 1
        else:
            empty_pages = 0
        frm += 10
        cw.polite_sleep()

    save_csv(posts_path, posts, POST_FIELDS)
    save_csv(comments_path, comments, COMMENT_FIELDS)
    print(f"[done] posts={len(posts)} comments={len(comments)} pages={stats['pages']} comment_calls={stats['comment_calls']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TapTap 社区通道爬虫（帖子流 + 热评 + 帖子评论）")
    p.add_argument("--game", default=DEFAULT_GAME, help="游戏档案 key（games/<key>.json）")
    p.add_argument("--data-dir", default="", help="数据目录（默认取档案 data_dir）")
    p.add_argument("--max-posts", type=int, default=300)
    p.add_argument("--max-pages", type=int, default=60, help="翻页上限（推荐流每页约 50%% 重叠，需多翻）")
    p.add_argument("--types", default="feed,elite,top_feed", help="流类型（逗号分隔）：feed 推荐流 / elite 精华流 / top_feed 置顶")
    p.add_argument("--empty-pages", type=int, default=3, help="连续 N 页无新增才停")
    p.add_argument("--resume", action="store_true", help="从 checkpoint 续爬")
    p.add_argument("--comment-limit", type=int, default=20, help="每帖抓取的评论条数上限；0 = 不抓评论")
    p.add_argument("--max-comment-calls", type=int, default=50, help="本轮最多调用的评论接口次数（控请求量）")
    p.add_argument("--sleep-min", type=float, default=0.8)
    p.add_argument("--sleep-max", type=float, default=1.5)
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
