#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微博轻量博文+评论采集（对照样本，非全网中台）

目标：关键词检索相关微博 + 拉取热门/时间序评论，写出 comments_sample.csv。
策略：
1) m.weibo.cn 搜索 container（综合/实时）
2) 可选超话 containerid / 用户时间线降级
3) 评论走 hotflow / comments/show
4) 限速 + 可选 Cookie；风控失败时写出 best-effort 降级样本并记报告
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

MOD_DIR = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]   # 项目根（games/、.env、taptap .env 都从这里找）
DATA_DIR = ROOT / "data" / "raw" / "weibo"   # 数据落回 data/raw/，注册表 internal connector 从这里读

DEFAULT_GAME = "arknights"  # 游戏档案 key，见 games/<key>.json


def load_game_profile(game_key: str = DEFAULT_GAME) -> dict:
    """加载游戏档案（games/game_profile.py，唯一参数化入口）。"""
    import sys

    sys.path.insert(0, str(ROOT / "games"))
    from game_profile import load as _load  # noqa: PLC0415

    return _load(game_key)


GAME_PROFILE = load_game_profile()
_ALIASES = [a for a in (GAME_PROFILE.get("aliases") or []) if a]


def is_relevant(text: str) -> bool:
    """软相关过滤：任一等价名（aliases）命中即视为相关。"""
    low = (text or "").lower()
    return any(a.lower() in low for a in _ALIASES)
RAW_DIR = DATA_DIR / "json"
REPORT_DIR = DATA_DIR / "reports"
RUN_LOG_DIR = DATA_DIR / "run_logs"
OUT_CSV = DATA_DIR / "comments_sample.csv"
POSTS_CSV = DATA_DIR / "posts_sample.csv"
CHECKPOINT = DATA_DIR / "checkpoint_crawl.json"

TZ_CN = timezone(timedelta(hours=8))
DEFAULT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)

# 官方/相关账号 uid（搜索失败时降级拉时间线；失效可改 env 或游戏档案）
DEFAULT_UIDS = GAME_PROFILE.get("cross_channel", {}).get("weibo_uids", "")

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

COMMENT_FIELDS = [
    "comment_id",
    "cid",
    "mid",
    "post_mid",
    "post_text",
    "url",
    "user_id_hash",
    "text",
    "like_count",
    "reply_count",
    "publish_time",
    "publish_time_cn",
    "keyword",
    "item_type",
    "source",
    "crawled_at",
    "raw_json_path",
]

POST_FIELDS = [
    "mid",
    "id",
    "text",
    "url",
    "user_id_hash",
    "like_count",
    "reposts_count",
    "comments_count",
    "publish_time",
    "publish_time_cn",
    "keyword",
    "source",
    "crawled_at",
]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def now_cn_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def hash_uid(uid: Any) -> str:
    return hashlib.sha256(str(uid).encode("utf-8")).hexdigest()[:16]


def sleep_jitter(lo: float, hi: float) -> None:
    time.sleep(random.uniform(lo, hi))


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    t = html.unescape(str(raw))
    t = TAG_RE.sub(" ", t)
    t = WS_RE.sub(" ", t).strip()
    return t


def parse_weibo_time(s: str | None) -> tuple[str, str]:
    """Return (unix_str, cn_iso). Weibo uses 'Thu Jul 10 12:00:00 +0800 2025' etc."""
    if not s:
        return "", ""
    s = str(s).strip()
    if s.isdigit():
        ts = int(s)
        if ts > 10_000_000_000:
            ts //= 1000
        return str(ts), datetime.fromtimestamp(ts, TZ_CN).isoformat(timespec="seconds")
    for fmt in (
        "%a %b %d %H:%M:%S %z %Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ_CN)
            dt = dt.astimezone(TZ_CN)
            return str(int(dt.timestamp())), dt.isoformat(timespec="seconds")
        except ValueError:
            continue
    # relative like "刚刚" / "5分钟前" / "昨天 12:00" — keep empty unix, store raw as cn note
    return "", s


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


class WeiboClient:
    def __init__(self, ua: str, cookie: str, sleep_min: float, sleep_max: float) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://m.weibo.cn/",
                "MWeibo-Pwa": "1",
            }
        )
        if cookie:
            self.session.headers["Cookie"] = cookie
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self.stats: dict[str, Any] = {
            "requests": 0,
            "http_errors": 0,
            "api_errors": 0,
            "blocked": False,
            "degraded": False,
            "search_mode": "",
            "notes": [],
        }

    def warm_up(self) -> None:
        """Visit homepage to obtain guest cookies when possible."""
        self.stats["requests"] += 1
        sleep_jitter(self.sleep_min, self.sleep_max)
        try:
            self.session.get("https://m.weibo.cn/", timeout=20)
        except requests.RequestException as exc:
            self.stats["notes"].append(f"warmup_failed:{exc}")

    def _get(self, url: str, params: dict[str, Any] | None = None, timeout: int = 25) -> dict[str, Any]:
        self.stats["requests"] += 1
        sleep_jitter(self.sleep_min, self.sleep_max)
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            self.stats["http_errors"] += 1
            return {"ok": 0, "msg": f"request_error:{exc}", "data": None}
        if resp.status_code in (403, 418, 432):
            self.stats["blocked"] = True
            self.stats["http_errors"] += 1
            return {"ok": 0, "msg": f"http_{resp.status_code}", "data": None}
        if resp.status_code != 200:
            self.stats["http_errors"] += 1
            return {"ok": 0, "msg": f"http_{resp.status_code}", "data": None}
        text = resp.text or ""
        if "passport" in resp.url or "login" in text[:500].lower():
            self.stats["blocked"] = True
            self.stats["api_errors"] += 1
            return {"ok": 0, "msg": "login_redirect", "data": None}
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            self.stats["api_errors"] += 1
            if "验证" in text or "验证码" in text:
                self.stats["blocked"] = True
                return {"ok": 0, "msg": "captcha_or_html", "data": None}
            return {"ok": 0, "msg": "invalid_json", "data": None}
        ok = data.get("ok")
        if ok is not None and int(ok) != 1:
            self.stats["api_errors"] += 1
            msg = str(data.get("msg") or data.get("message") or "api_error")
            # -100 = passport redirect / login required（当前 m.weibo.cn 常见）
            if int(ok) == -100 or any(x in msg for x in ("登录", "login", "forbidden", "频")):
                self.stats["blocked"] = True
                if int(ok) == -100 and not any("login_required_ok=-100" in n for n in self.stats["notes"]):
                    self.stats["notes"].append("login_required_ok=-100 (配置 WEIBO_COOKIE 后可重试真实抓取)")
            return data
        return data

    def search_posts(self, keyword: str, page: int = 1, realtime: bool = False) -> list[dict[str, Any]]:
        # type=61 实时；type=1 综合
        typ = "61" if realtime else "1"
        containerid = f"100103type={typ}&q={keyword}"
        params = {
            "containerid": containerid,
            "page_type": "searchall",
            "page": page,
        }
        data = self._get("https://m.weibo.cn/api/container/getIndex", params=params)
        cards = ((data.get("data") or {}).get("cards")) or []
        posts: list[dict[str, Any]] = []
        for card in cards:
            posts.extend(self._extract_mblogs(card))
        if posts and not self.stats.get("search_mode"):
            self.stats["search_mode"] = "realtime" if realtime else "comprehensive"
        return posts

    def topic_posts(self, containerid: str, page: int = 1) -> list[dict[str, Any]]:
        data = self._get(
            "https://m.weibo.cn/api/container/getIndex",
            params={"containerid": containerid, "page": page},
        )
        cards = ((data.get("data") or {}).get("cards")) or []
        posts: list[dict[str, Any]] = []
        for card in cards:
            posts.extend(self._extract_mblogs(card))
        if posts:
            self.stats["search_mode"] = self.stats.get("search_mode") or "topic_container"
        return posts

    def user_timeline(self, uid: str, page: int = 1) -> list[dict[str, Any]]:
        containerid = f"107603{uid}"
        data = self._get(
            "https://m.weibo.cn/api/container/getIndex",
            params={"type": "uid", "value": uid, "containerid": containerid, "page": page},
        )
        cards = ((data.get("data") or {}).get("cards")) or []
        posts: list[dict[str, Any]] = []
        for card in cards:
            posts.extend(self._extract_mblogs(card))
        if posts:
            self.stats["search_mode"] = self.stats.get("search_mode") or f"uid:{uid}"
        return posts

    def _extract_mblogs(self, card: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(card, dict):
            return out
        mblog = card.get("mblog")
        if isinstance(mblog, dict):
            out.append(mblog)
        for group in card.get("card_group") or []:
            if isinstance(group, dict) and isinstance(group.get("mblog"), dict):
                out.append(group["mblog"])
        # nested cards
        for nested in card.get("cards") or []:
            out.extend(self._extract_mblogs(nested))
        return out

    def fetch_comments_hotflow(self, mid: str, max_id: str = "0", max_id_type: int = 0) -> dict[str, Any]:
        return self._get(
            "https://m.weibo.cn/comments/hotflow",
            params={"id": mid, "mid": mid, "max_id": max_id, "max_id_type": max_id_type},
        )

    def fetch_comments_show(self, mid: str, page: int = 1) -> dict[str, Any]:
        return self._get(
            "https://m.weibo.cn/api/comments/show",
            params={"id": mid, "page": page},
        )


def normalize_post(mblog: dict[str, Any], keyword: str, source: str) -> dict[str, Any] | None:
    mid = str(mblog.get("mid") or mblog.get("id") or "").strip()
    if not mid:
        return None
    text = clean_text(mblog.get("text") or mblog.get("raw_text") or "")
    if not text:
        return None
    user = mblog.get("user") or {}
    uid = user.get("id") or user.get("idstr") or ""
    unix, cn = parse_weibo_time(mblog.get("created_at"))
    url = f"https://m.weibo.cn/detail/{mid}"
    return {
        "mid": mid,
        "id": str(mblog.get("id") or mid),
        "text": text,
        "url": url,
        "user_id_hash": hash_uid(uid) if uid else "",
        "like_count": int(mblog.get("attitudes_count") or 0),
        "reposts_count": int(mblog.get("reposts_count") or 0),
        "comments_count": int(mblog.get("comments_count") or 0),
        "publish_time": unix,
        "publish_time_cn": cn,
        "keyword": keyword,
        "source": source,
        "crawled_at": now_cn_iso(),
        "_raw": mblog,
    }


def post_to_row(post: dict[str, Any]) -> dict[str, Any]:
    """Include the post itself as a sample row (item_type=post)."""
    mid = post["mid"]
    raw_path = RAW_DIR / f"post_{mid}.json"
    if post.get("_raw") is not None and not raw_path.exists():
        raw_path.write_text(json.dumps(post["_raw"], ensure_ascii=False), encoding="utf-8")
    return {
        "comment_id": f"weibo_post_{mid}",
        "cid": mid,
        "mid": mid,
        "post_mid": mid,
        "post_text": (post.get("text") or "")[:200],
        "url": post.get("url") or "",
        "user_id_hash": post.get("user_id_hash") or "",
        "text": post.get("text") or "",
        "like_count": post.get("like_count") or 0,
        "reply_count": post.get("comments_count") or 0,
        "publish_time": post.get("publish_time") or "",
        "publish_time_cn": post.get("publish_time_cn") or "",
        "keyword": post.get("keyword") or "",
        "item_type": "post",
        "source": post.get("source") or "weibo",
        "crawled_at": now_cn_iso(),
        "raw_json_path": str(raw_path.relative_to(DATA_DIR)).replace("\\", "/") if raw_path.exists() else "",
    }


def comment_to_row(c: dict[str, Any], post: dict[str, Any], source: str) -> dict[str, Any] | None:
    cid = str(c.get("id") or c.get("idstr") or c.get("mid") or "").strip()
    if not cid:
        return None
    text = clean_text(c.get("text") or c.get("reply_text") or "")
    if not text:
        return None
    user = c.get("user") or {}
    uid = user.get("id") or user.get("idstr") or ""
    unix, cn = parse_weibo_time(c.get("created_at"))
    mid = post["mid"]
    raw_path = RAW_DIR / f"comment_{mid}_{cid}.json"
    raw_path.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    return {
        "comment_id": f"weibo_{mid}_{cid}",
        "cid": cid,
        "mid": cid,
        "post_mid": mid,
        "post_text": (post.get("text") or "")[:200],
        "url": post.get("url") or f"https://m.weibo.cn/detail/{mid}",
        "user_id_hash": hash_uid(uid) if uid else "",
        "text": text,
        "like_count": int(c.get("like_count") or c.get("like_counts") or 0),
        "reply_count": int(c.get("total_number") or 0),
        "publish_time": unix,
        "publish_time_cn": cn,
        "keyword": post.get("keyword") or "",
        "item_type": "comment",
        "source": source,
        "crawled_at": now_cn_iso(),
        "raw_json_path": str(raw_path.relative_to(DATA_DIR)).replace("\\", "/"),
    }


def collect_posts(
    client: WeiboClient,
    keywords: list[str],
    topic_ids: list[str],
    uids: list[str],
    max_posts: int,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    posts: list[dict[str, Any]] = []

    def add_raw(mblog: dict[str, Any], keyword: str, source: str) -> None:
        nv = normalize_post(mblog, keyword, source)
        if not nv or nv["mid"] in seen:
            return
        # soft relevance（等价名来自游戏档案 aliases）
        blob = (nv["text"] + " " + keyword).lower()
        if not is_relevant(nv["text"]) and not is_relevant(blob):
            if not is_relevant(keyword):
                return
        seen.add(nv["mid"])
        posts.append(nv)

    for kw in keywords:
        if len(posts) >= max_posts:
            break
        for realtime in (False, True):
            if len(posts) >= max_posts:
                break
            for page in range(1, 5):
                if len(posts) >= max_posts:
                    break
                batch = client.search_posts(kw, page=page, realtime=realtime)
                if not batch:
                    break
                for mblog in batch:
                    add_raw(mblog, kw, "m_search")
                    if len(posts) >= max_posts:
                        break
                print(f"[search] kw={kw} realtime={realtime} page={page} batch={len(batch)} posts={len(posts)}")

    if len(posts) < max(8, max_posts // 8) and topic_ids:
        client.stats["degraded"] = True
        client.stats["notes"].append("search yield low; trying topic containers")
        for cid in topic_ids:
            if len(posts) >= max_posts:
                break
            for page in range(1, 4):
                batch = client.topic_posts(cid, page=page)
                if not batch:
                    break
                for mblog in batch:
                    add_raw(mblog, f"topic:{cid}", "topic_container")
                    if len(posts) >= max_posts:
                        break

    if len(posts) < max(8, max_posts // 8) and uids:
        client.stats["degraded"] = True
        client.stats["notes"].append("falling back to user timelines")
        for uid in uids:
            if len(posts) >= max_posts:
                break
            for page in range(1, 4):
                batch = client.user_timeline(uid, page=page)
                if not batch:
                    break
                for mblog in batch:
                    add_raw(mblog, f"uid:{uid}", "user_timeline")
                    if len(posts) >= max_posts:
                        break
                print(f"[uid] {uid} page={page} batch={len(batch)} posts={len(posts)}")

    return posts


def extract_comments_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None, int]:
    data = payload.get("data") or {}
    if isinstance(data, list):
        return data, None, 0
    comments = data.get("data") or data.get("comments") or []
    if not isinstance(comments, list):
        comments = []
    max_id = data.get("max_id")
    max_id_type = int(data.get("max_id_type") or 0)
    return comments, (str(max_id) if max_id not in (None, "") else None), max_id_type


def collect_items(
    client: WeiboClient,
    posts: list[dict[str, Any]],
    target: int,
    max_comment_pages: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_row(row: dict[str, Any] | None) -> None:
        if not row:
            return
        key = str(row["comment_id"])
        if key in seen:
            return
        seen.add(key)
        rows.append(row)

    # include posts themselves as part of the slice
    for post in posts:
        if len(rows) >= target:
            break
        add_row(post_to_row(post))

    for pi, post in enumerate(posts, 1):
        if len(rows) >= target:
            break
        mid = post["mid"]
        # prefer hotflow then show
        max_id = "0"
        max_id_type = 0
        got_any = False
        for page in range(max_comment_pages):
            if len(rows) >= target:
                break
            payload = client.fetch_comments_hotflow(mid, max_id=max_id, max_id_type=max_id_type)
            comments, next_max, next_type = extract_comments_payload(payload)
            if not comments:
                break
            got_any = True
            for c in comments:
                add_row(comment_to_row(c, post, source="hotflow"))
                if len(rows) >= target:
                    break
            print(f"[comments] post {pi}/{len(posts)} mid={mid} page={page+1} batch={len(comments)} total={len(rows)}")
            if not next_max or str(next_max) in ("0", max_id):
                break
            max_id = str(next_max)
            max_id_type = next_type

        if not got_any:
            for page in range(1, max_comment_pages + 1):
                if len(rows) >= target:
                    break
                payload = client.fetch_comments_show(mid, page=page)
                comments, _, _ = extract_comments_payload(payload)
                if not comments:
                    break
                for c in comments:
                    add_row(comment_to_row(c, post, source="comments_show"))
                    if len(rows) >= target:
                        break
                print(f"[comments-show] post {pi}/{len(posts)} mid={mid} page={page} batch={len(comments)} total={len(rows)}")

        CHECKPOINT.write_text(
            json.dumps(
                {
                    "updated_at": now_cn_iso(),
                    "posts_done": pi,
                    "items": len(rows),
                    "stats": {k: v for k, v in client.stats.items() if k != "notes"} | {"notes": client.stats.get("notes")},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return rows


def build_degraded_samples(n: int = 240) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Best-effort demo corpus when live crawl is blocked. Marked source=degraded_sample."""
    comment_themes = [
        "抽卡十连全蓝，非酋实录，保底什么时候才到啊",
        "新活动肝度有点猛，但我还是肝完了，鹰角加油",
        "这章剧情刀子太狠了，我哭得像个傻子",
        "客户端又闪退，更新后进不去，有没有一样的",
        "今天强度环境真的红温，这干员是不是太强了",
        "二创图太好看了，画师老师辛苦了",
        "礼包性价比一般，劝大家理性氪",
        "集成战略这期种子不错，通关很爽",
        "官方维护补偿到账了吗？我这边还没看到",
        "寻访出货了！六星终于来了 YYDS",
        "剧情文本质量在线，但节奏偏慢",
        "闪退三次了，客服能给个说法吗",
        "削弱之后还能打吗？感觉环境变了",
        "活动商店兑换优先级求带，萌新懵了",
        "高难关卡卡关中，求攻略干员推荐",
    ]
    post_themes = [
        "【明日方舟】本期卡池分析：性价比与保底提醒",
        "【明日方舟】活动前瞻：肝度、奖励与开荒建议",
        "【明日方舟】主线感想：刀子与群像",
        "【明日方舟】更新后问题汇总：闪退/登录",
        "【明日方舟】环境讨论：强度与平衡吐槽",
    ]
    now = datetime.now(TZ_CN)
    posts: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    rng = random.Random(42)
    n_posts = max(40, n // 8)
    suffixes = ["", "。", "…", "（个人观点）", " 有没有懂的", " 🐶"]

    for i in range(n_posts):
        text = post_themes[i % len(post_themes)] if i % 3 == 0 else f"【明日方舟讨论楼】{comment_themes[i % len(comment_themes)][:18]}"
        mid = f"deg{100000 + i}"
        ts = int((now - timedelta(hours=rng.randint(1, 720))).timestamp())
        cn = datetime.fromtimestamp(ts, TZ_CN).isoformat(timespec="seconds")
        post = {
            "mid": mid,
            "id": mid,
            "text": text,
            "url": f"https://m.weibo.cn/detail/{mid}",
            "user_id_hash": hash_uid(f"u{i % 17}"),
            "like_count": rng.randint(0, 500),
            "reposts_count": rng.randint(0, 80),
            "comments_count": rng.randint(0, 120),
            "publish_time": str(ts),
            "publish_time_cn": cn,
            "keyword": "明日方舟",
            "source": "degraded_sample",
            "crawled_at": now_cn_iso(),
        }
        posts.append(post)
        rows.append(
            {
                "comment_id": f"weibo_post_{mid}",
                "cid": mid,
                "mid": mid,
                "post_mid": mid,
                "post_text": text[:200],
                "url": post["url"],
                "user_id_hash": post["user_id_hash"],
                "text": text,
                "like_count": post["like_count"],
                "reply_count": post["comments_count"],
                "publish_time": str(ts),
                "publish_time_cn": cn,
                "keyword": "明日方舟",
                "item_type": "post",
                "source": "degraded_sample",
                "crawled_at": now_cn_iso(),
                "raw_json_path": "",
            }
        )

    i = 0
    while len(rows) < n:
        base = comment_themes[i % len(comment_themes)]
        body = f"{base}{suffixes[i % len(suffixes)]}"
        if i >= len(comment_themes):
            body = f"{body} #{i // len(comment_themes)}"
        mid = posts[i % len(posts)]["mid"]
        cid = f"degc{200000 + i}"
        ts = int((now - timedelta(hours=rng.randint(1, 720))).timestamp())
        cn = datetime.fromtimestamp(ts, TZ_CN).isoformat(timespec="seconds")
        rows.append(
            {
                "comment_id": f"weibo_{mid}_{cid}",
                "cid": cid,
                "mid": cid,
                "post_mid": mid,
                "post_text": (posts[i % len(posts)]["text"] or "")[:200],
                "url": f"https://m.weibo.cn/detail/{mid}",
                "user_id_hash": hash_uid(f"c{i % 29}"),
                "text": body,
                "like_count": rng.randint(0, 200),
                "reply_count": rng.randint(0, 20),
                "publish_time": str(ts),
                "publish_time_cn": cn,
                "keyword": "明日方舟",
                "item_type": "comment",
                "source": "degraded_sample",
                "crawled_at": now_cn_iso(),
                "raw_json_path": "",
            }
        )
        i += 1
    return posts, rows[:n]


def main(args: argparse.Namespace) -> int:
    load_dotenv(MOD_DIR / ".env")
    load_dotenv(MOD_DIR / "config.example.env")
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "L1_data_source/collectors/taptap" / ".env")

    ua = os.environ.get("WEIBO_UA", DEFAULT_UA).strip() or DEFAULT_UA
    cookie = os.environ.get("WEIBO_COOKIE", "").strip()
    sleep_min = float(os.environ.get("WEIBO_SLEEP_MIN", args.sleep_min))
    sleep_max = float(os.environ.get("WEIBO_SLEEP_MAX", args.sleep_max))
    if sleep_max < sleep_min:
        sleep_max = sleep_min

    keywords = [
        k.strip()
        for k in (args.keywords or os.environ.get("WEIBO_KEYWORDS", "明日方舟")).split(",")
        if k.strip()
    ]
    topic_raw = args.topic_ids or os.environ.get("WEIBO_TOPIC_CONTAINERIDS", "")
    topic_ids = [x.strip() for x in topic_raw.split(",") if x.strip()]
    uid_raw = args.uids or os.environ.get("WEIBO_UIDS", DEFAULT_UIDS)
    uids = [x.strip() for x in uid_raw.split(",") if x.strip()]

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)

    client = WeiboClient(ua=ua, cookie=cookie, sleep_min=sleep_min, sleep_max=sleep_max)
    started = now_cn_iso()
    print(
        f"[start] keywords={keywords} target={args.target} max_posts={args.max_posts} "
        f"cookie={'yes' if cookie else 'no'}"
    )

    client.warm_up()
    posts = collect_posts(client, keywords, topic_ids, uids, args.max_posts)
    print(f"[posts] n={len(posts)} mode={client.stats.get('search_mode')} blocked={client.stats.get('blocked')}")

    items = collect_items(client, posts, args.target, args.max_comment_pages) if posts else []
    degrade_used = False

    # ★ 合成降级样本默认关闭（项目纪律：没有的数据不编）。
    #   真要演示链路时显式 WEIBO_ALLOW_DEGRADED_SAMPLE=1 打开。
    allow_degraded = os.environ.get("WEIBO_ALLOW_DEGRADED_SAMPLE", "0") in {"1", "true", "True"}
    if len(items) < 200 and allow_degraded:
        client.stats["degraded"] = True
        degrade_used = True
        need = max(args.target, 240)
        client.stats["notes"].append(
            f"live items={len(items)} < 200; merging degraded_sample to reach demo size (~{need})"
        )
        deg_posts, deg_rows = build_degraded_samples(need)
        # keep live first
        seen = {r["comment_id"] for r in items}
        for r in deg_rows:
            if r["comment_id"] not in seen:
                items.append(r)
                seen.add(r["comment_id"])
            if len(items) >= need:
                break
        live_mids = {p["mid"] for p in posts}
        for p in deg_posts:
            if p["mid"] not in live_mids:
                posts.append(p)
                live_mids.add(p["mid"])

    # strip _raw before writing posts csv
    posts_out = [{k: v for k, v in p.items() if k != "_raw"} for p in posts]
    write_csv(POSTS_CSV, POST_FIELDS, posts_out)
    write_csv(OUT_CSV, COMMENT_FIELDS, items)

    ended = now_cn_iso()
    report = REPORT_DIR / f"crawl_weibo_{datetime.now(TZ_CN).strftime('%Y%m%d_%H%M%S')}.md"
    report.write_text(
        "\n".join(
            [
                "# 微博博文/评论采集报告",
                "",
                f"- 开始：{started}",
                f"- 结束：{ended}",
                f"- 关键词：{', '.join(keywords)}",
                f"- 博文数：{len(posts_out)}",
                f"- 样本条数（博文+评论）：{len(items)}",
                f"- 目标：{args.target}",
                f"- 搜索模式：{client.stats.get('search_mode') or 'n/a'}",
                f"- 是否降级：{client.stats.get('degraded')}",
                f"- 是否疑似风控：{client.stats.get('blocked')}",
                f"- 使用降级样本：{degrade_used}",
                f"- 请求次数：{client.stats.get('requests')}",
                f"- HTTP 错误：{client.stats.get('http_errors')}",
                f"- API 错误：{client.stats.get('api_errors')}",
                f"- Cookie：{'已配置（未入库）' if cookie else '未配置'}",
                f"- 输出样本：`{OUT_CSV.as_posix()}`",
                f"- 输出博文：`{POSTS_CSV.as_posix()}`",
                "- 备注：",
                *[f"  - {n}" for n in (client.stats.get("notes") or ["无"])],
            ]
        ),
        encoding="utf-8",
    )
    (RUN_LOG_DIR / "last_crawl.json").write_text(
        json.dumps(
            {
                "started": started,
                "ended": ended,
                "n_posts": len(posts_out),
                "n_items": len(items),
                "degrade_used": degrade_used,
                "stats": client.stats,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[done] items={len(items)} -> {OUT_CSV}")
    print(f"[report] {report}")
    if len(items) == 0:
        print("[warn] 0 items; check network/cookie", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Crawl Weibo posts/comments for Arknights contrast sample")
    p.add_argument("--target", type=int, default=350, help="目标样本条数（博文+评论，默认 350）")
    p.add_argument("--max-posts", type=int, default=40)
    p.add_argument("--max-comment-pages", type=int, default=3)
    p.add_argument("--keywords", default="", help="逗号分隔；默认读 env/example")
    p.add_argument("--topic-ids", default="", help="超话 containerid，逗号分隔")
    p.add_argument("--uids", default="", help="用户 uid，逗号分隔；搜索失败降级")
    p.add_argument("--sleep-min", type=float, default=1.2)
    p.add_argument("--sleep-max", type=float, default=2.4)
    return p


if __name__ == "__main__":
    raise SystemExit(main(build_parser().parse_args()))
