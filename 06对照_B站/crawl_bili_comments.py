#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B 站轻量评论采集（对照样本，非全网中台）

目标：搜索相关视频 + 拉取一级评论，写出 comments_sample.csv。
策略：
1) 优先 WBI 搜索视频；失败则用 UP 稿件列表降级
2) 评论走公开 reply API（type=1, oid=aid）
3) 限速 + 可配置 UA/Cookie；不写入真实 Cookie 到仓库
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from functools import reduce
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
MOD_DIR = Path(__file__).resolve().parent
RAW_DIR = MOD_DIR / "raw"
REPORT_DIR = MOD_DIR / "reports"
RUN_LOG_DIR = MOD_DIR / "run_logs"
OUT_CSV = MOD_DIR / "comments_sample.csv"
VIDEOS_CSV = MOD_DIR / "videos_sample.csv"
CHECKPOINT = MOD_DIR / "checkpoint_crawl.json"

TZ_CN = timezone(timedelta(hours=8))
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# WBI mixin table (public bilibili client convention)
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

COMMENT_FIELDS = [
    "comment_id",
    "rpid",
    "aid",
    "bvid",
    "video_title",
    "video_url",
    "up_mid",
    "up_name",
    "user_mid_hash",
    "text",
    "like_count",
    "reply_count",
    "publish_time",
    "publish_time_cn",
    "keyword",
    "source",
    "crawled_at",
    "raw_json_path",
]

VIDEO_FIELDS = [
    "aid",
    "bvid",
    "title",
    "url",
    "up_mid",
    "up_name",
    "pubdate_cn",
    "play",
    "review",
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


def ts_to_cn_iso(ts: int | float | None) -> str:
    if ts is None or ts == "" or int(ts) <= 0:
        return ""
    return datetime.fromtimestamp(int(ts), TZ_CN).isoformat(timespec="seconds")


def hash_mid(mid: Any) -> str:
    return hashlib.sha256(str(mid).encode("utf-8")).hexdigest()[:16]


def sleep_jitter(lo: float, hi: float) -> None:
    time.sleep(random.uniform(lo, hi))


class BiliClient:
    def __init__(self, ua: str, cookie: str, sleep_min: float, sleep_max: float) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": ua,
                "Referer": "https://www.bilibili.com",
                "Origin": "https://www.bilibili.com",
                "Accept": "application/json, text/plain, */*",
            }
        )
        if cookie:
            self.session.headers["Cookie"] = cookie
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self._wbi_keys: tuple[str, str] | None = None
        self.stats = {
            "requests": 0,
            "http_errors": 0,
            "api_errors": 0,
            "wbi_ok": False,
            "search_mode": "",
            "degraded": False,
            "notes": [],
        }

    def _get(self, url: str, params: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
        self.stats["requests"] += 1
        sleep_jitter(self.sleep_min, self.sleep_max)
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            self.stats["http_errors"] += 1
            return {"code": -1, "message": f"request_error:{exc}", "data": None}
        if resp.status_code != 200:
            self.stats["http_errors"] += 1
            return {"code": -1, "message": f"http_{resp.status_code}", "data": None}
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            self.stats["api_errors"] += 1
            return {"code": -1, "message": "invalid_json", "data": None}
        if int(data.get("code", -1)) != 0:
            self.stats["api_errors"] += 1
        return data

    def _get_wbi_keys(self) -> tuple[str, str] | None:
        if self._wbi_keys:
            return self._wbi_keys
        data = self._get("https://api.bilibili.com/x/web-interface/nav")
        try:
            wbi = data["data"]["wbi_img"]
            img_url = wbi["img_url"]
            sub_url = wbi["sub_url"]
            img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
            sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
            self._wbi_keys = (img_key, sub_key)
            self.stats["wbi_ok"] = True
            return self._wbi_keys
        except Exception:  # noqa: BLE001
            self.stats["notes"].append("WBI key fetch failed")
            return None

    def _encode_wbi(self, params: dict[str, Any]) -> dict[str, Any]:
        keys = self._get_wbi_keys()
        if not keys:
            return params
        img_key, sub_key = keys
        mixin = reduce(lambda s, i: s + (img_key + sub_key)[i], MIXIN_KEY_ENC_TAB, "")[:32]
        params = dict(params)
        params["wts"] = int(time.time())
        # filter special chars as bilibili does
        filtered = {
            k: "".join(ch for ch in str(v) if ch not in "!'()*")
            for k, v in params.items()
        }
        query = urllib.parse.urlencode(sorted(filtered.items()))
        filtered["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
        return filtered

    def search_videos(self, keyword: str, page: int = 1, page_size: int = 20) -> list[dict[str, Any]]:
        # Try WBI search first
        params = {
            "search_type": "video",
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
            "order": "totalrank",
        }
        signed = self._encode_wbi(params)
        data = self._get("https://api.bilibili.com/x/web-interface/wbi/search/type", signed)
        if int(data.get("code", -1)) == 0:
            result = (data.get("data") or {}).get("result") or []
            self.stats["search_mode"] = "wbi_search"
            return [x for x in result if isinstance(x, dict)]

        # Fallback: legacy search
        data2 = self._get(
            "https://api.bilibili.com/x/web-interface/search/type",
            {
                "search_type": "video",
                "keyword": keyword,
                "page": page,
                "page_size": page_size,
            },
        )
        if int(data2.get("code", -1)) == 0:
            result = (data2.get("data") or {}).get("result") or []
            self.stats["search_mode"] = "legacy_search"
            self.stats["degraded"] = True
            self.stats["notes"].append("WBI search failed; used legacy search")
            return [x for x in result if isinstance(x, dict)]

        self.stats["notes"].append(
            f"search failed keyword={keyword} wbi={data.get('message')} legacy={data2.get('message')}"
        )
        return []

    def list_up_videos(self, mid: int, pn: int = 1, ps: int = 30) -> list[dict[str, Any]]:
        # Prefer WBI space search
        params = {
            "mid": mid,
            "ps": ps,
            "pn": pn,
            "order": "pubdate",
            "platform": "web",
        }
        signed = self._encode_wbi(params)
        data = self._get("https://api.bilibili.com/x/space/wbi/arc/search", signed)
        if int(data.get("code", -1)) != 0:
            # older endpoint
            data = self._get(
                "https://api.bilibili.com/x/space/arc/search",
                {"mid": mid, "ps": ps, "pn": pn, "order": "pubdate"},
            )
            self.stats["degraded"] = True
        if int(data.get("code", -1)) != 0:
            self.stats["notes"].append(f"up list failed mid={mid}: {data.get('message')}")
            return []
        vlist = (((data.get("data") or {}).get("list") or {}).get("vlist")) or []
        if not self.stats.get("search_mode"):
            self.stats["search_mode"] = "up_arc_list"
        return [x for x in vlist if isinstance(x, dict)]

    def fetch_replies(self, aid: int, next_cursor: int = 0, mode: int = 3, ps: int = 20) -> dict[str, Any]:
        """优先 reply/main 的 next 游标；WBI 易 -403，作次选。"""
        data2 = self._get(
            "https://api.bilibili.com/x/v2/reply/main",
            {"type": 1, "oid": aid, "mode": mode, "next": int(next_cursor), "ps": ps},
        )
        if int(data2.get("code", -1)) == 0:
            return data2.get("data") or {}

        params: dict[str, Any] = {
            "type": 1,
            "oid": aid,
            "mode": mode,
            "pagination_str": json.dumps({"offset": ""}, ensure_ascii=False),
            "plat": 1,
            "web_location": "1315875",
        }
        data = self._get("https://api.bilibili.com/x/v2/reply/wbi/main", self._encode_wbi(params))
        if int(data.get("code", -1)) == 0:
            self.stats["degraded"] = True
            return data.get("data") or {}

        data3 = self._get(
            "https://api.bilibili.com/x/v2/reply",
            {"type": 1, "oid": aid, "sort": 2, "pn": 1, "ps": ps},
        )
        if int(data3.get("code", -1)) == 0:
            self.stats["degraded"] = True
            d = data3.get("data") or {}
            return {"replies": d.get("replies") or [], "cursor": {"is_end": True, "next": 0}}
        self.stats["notes"].append(
            f"reply failed aid={aid}: main={data2.get('message')} wbi={data.get('message')} classic={data3.get('message')}"
        )
        return {}

    def fetch_child_replies(self, aid: int, root_rpid: int, pn: int = 1, ps: int = 10) -> list[dict[str, Any]]:
        data = self._get(
            "https://api.bilibili.com/x/v2/reply/reply",
            {"type": 1, "oid": aid, "root": root_rpid, "ps": ps, "pn": pn},
        )
        if int(data.get("code", -1)) != 0:
            return []
        return [r for r in ((data.get("data") or {}).get("replies") or []) if isinstance(r, dict)]


def clean_html(text: str) -> str:
    text = text or ""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def normalize_search_video(item: dict[str, Any], keyword: str, source: str) -> dict[str, Any] | None:
    aid = item.get("aid") or item.get("id")
    bvid = item.get("bvid") or ""
    title = clean_html(str(item.get("title") or ""))
    if not aid:
        return None
    author = item.get("author") or item.get("owner", {}).get("name") if isinstance(item.get("owner"), dict) else item.get("author")
    mid = item.get("mid") or (item.get("owner", {}) or {}).get("mid")
    pubdate = item.get("pubdate") or item.get("created") or 0
    url = f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}"
    return {
        "aid": int(aid),
        "bvid": bvid,
        "title": title,
        "url": url,
        "up_mid": mid or "",
        "up_name": author or "",
        "pubdate_cn": ts_to_cn_iso(pubdate),
        "play": item.get("play") or item.get("stat", {}).get("view") if isinstance(item.get("stat"), dict) else item.get("play") or "",
        "review": item.get("review") or item.get("video_review") or "",
        "keyword": keyword,
        "source": source,
        "crawled_at": now_cn_iso(),
    }


def normalize_up_video(item: dict[str, Any], keyword: str) -> dict[str, Any] | None:
    aid = item.get("aid")
    if not aid:
        return None
    bvid = item.get("bvid") or ""
    return {
        "aid": int(aid),
        "bvid": bvid,
        "title": clean_html(str(item.get("title") or "")),
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}",
        "up_mid": item.get("mid") or "",
        "up_name": item.get("author") or "",
        "pubdate_cn": ts_to_cn_iso(item.get("created")),
        "play": item.get("play") or "",
        "review": item.get("video_review") or item.get("comment") or "",
        "keyword": keyword,
        "source": "up_arc_list",
        "crawled_at": now_cn_iso(),
    }


def extract_replies(payload: dict[str, Any]) -> list[dict[str, Any]]:
    replies = payload.get("replies") or []
    # some endpoints nest top replies separately
    tops = payload.get("top_replies") or []
    out = []
    for r in list(tops) + list(replies):
        if isinstance(r, dict):
            out.append(r)
    return out


def reply_to_row(reply: dict[str, Any], video: dict[str, Any]) -> dict[str, Any] | None:
    rpid = reply.get("rpid")
    content = (reply.get("content") or {}).get("message") if isinstance(reply.get("content"), dict) else ""
    text = clean_html(str(content or ""))
    if not rpid or not text:
        return None
    mid = (reply.get("member") or {}).get("mid") if isinstance(reply.get("member"), dict) else ""
    ctime = reply.get("ctime")
    aid = video["aid"]
    raw_name = f"reply_{aid}_{rpid}.json"
    raw_path = RAW_DIR / raw_name
    raw_path.write_text(json.dumps(reply, ensure_ascii=False), encoding="utf-8")
    return {
        "comment_id": f"bili_{aid}_{rpid}",
        "rpid": rpid,
        "aid": aid,
        "bvid": video.get("bvid", ""),
        "video_title": video.get("title", ""),
        "video_url": video.get("url", ""),
        "up_mid": video.get("up_mid", ""),
        "up_name": video.get("up_name", ""),
        "user_mid_hash": hash_mid(mid) if mid else "",
        "text": text,
        "like_count": reply.get("like") or 0,
        "reply_count": reply.get("rcount") or 0,
        "publish_time": ctime or "",
        "publish_time_cn": ts_to_cn_iso(ctime),
        "keyword": video.get("keyword", ""),
        "source": video.get("source", ""),
        "crawled_at": now_cn_iso(),
        "raw_json_path": str(raw_path.relative_to(MOD_DIR)).replace("\\", "/"),
    }


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def collect_videos(client: BiliClient, keywords: list[str], up_mids: list[int], max_videos: int) -> list[dict[str, Any]]:
    seen: set[int] = set()
    videos: list[dict[str, Any]] = []

    for kw in keywords:
        if len(videos) >= max_videos:
            break
        for page in range(1, 4):
            items = client.search_videos(kw, page=page, page_size=20)
            for item in items:
                nv = normalize_search_video(item, kw, client.stats.get("search_mode") or "search")
                if not nv or nv["aid"] in seen:
                    continue
                # soft relevance filter
                title = nv["title"]
                if "明日方舟" not in title and "方舟" not in title and "arknights" not in title.lower():
                    # keep if keyword was exact; still allow a few
                    if kw.replace(" ", "") not in title.replace(" ", ""):
                        continue
                seen.add(nv["aid"])
                videos.append(nv)
                if len(videos) >= max_videos:
                    break
            if len(videos) >= max_videos or not items:
                break

    if len(videos) < max(5, max_videos // 5):
        client.stats["degraded"] = True
        client.stats["notes"].append("search yield low; falling back to UP mid lists")
        for mid in up_mids:
            if len(videos) >= max_videos:
                break
            items = client.list_up_videos(mid, pn=1, ps=min(50, max_videos))
            for item in items:
                nv = normalize_up_video(item, keyword=f"up:{mid}")
                if not nv or nv["aid"] in seen:
                    continue
                seen.add(nv["aid"])
                videos.append(nv)
                if len(videos) >= max_videos:
                    break

    return videos


def collect_comments(
    client: BiliClient,
    videos: list[dict[str, Any]],
    target: int,
    max_pages_per_video: int,
    fetch_children: bool = True,
) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    seen_rpid: set[str] = set()

    def add_reply(reply: dict[str, Any], video: dict[str, Any]) -> None:
        nonlocal comments
        row = reply_to_row(reply, video)
        if not row:
            return
        key = str(row["rpid"])
        if key in seen_rpid:
            return
        seen_rpid.add(key)
        comments.append(row)

    for vi, video in enumerate(videos, 1):
        if len(comments) >= target:
            break
        # mode 3=热门, mode 2=时间；各拉若干页以扩覆盖
        for mode in (3, 2):
            if len(comments) >= target:
                break
            next_cursor = 0
            for page in range(max_pages_per_video):
                if len(comments) >= target:
                    break
                before = len(comments)
                payload = client.fetch_replies(int(video["aid"]), next_cursor=next_cursor, mode=mode)
                replies = extract_replies(payload)
                if not replies:
                    break
                for reply in replies:
                    add_reply(reply, video)
                    if fetch_children and len(comments) < target:
                        rcount = int(reply.get("rcount") or 0)
                        if rcount > 0:
                            for child in client.fetch_child_replies(int(video["aid"]), int(reply["rpid"]), pn=1, ps=8):
                                add_reply(child, video)
                                if len(comments) >= target:
                                    break
                cursor = payload.get("cursor") or {}
                nxt = cursor.get("next")
                is_end = bool(cursor.get("is_end"))
                added = len(comments) - before
                print(
                    f"[video {vi}/{len(videos)}] aid={video['aid']} mode={mode} page={page+1} "
                    f"batch={len(replies)} +new≈{added} total={len(comments)}"
                )
                if is_end or nxt is None:
                    break
                # bilibili: first page next_cursor=0, subsequent use cursor.next
                if int(nxt) == int(next_cursor):
                    break
                next_cursor = int(nxt)

        CHECKPOINT.write_text(
            json.dumps(
                {
                    "updated_at": now_cn_iso(),
                    "videos_done": vi,
                    "comments": len(comments),
                    "stats": client.stats,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return comments


def main(args: argparse.Namespace) -> int:
    load_dotenv(MOD_DIR / ".env")
    load_dotenv(MOD_DIR / "config.example.env")

    ua = os.environ.get("BILI_UA", DEFAULT_UA).strip() or DEFAULT_UA
    cookie = os.environ.get("BILI_COOKIE", "").strip()
    sleep_min = float(os.environ.get("BILI_SLEEP_MIN", args.sleep_min))
    sleep_max = float(os.environ.get("BILI_SLEEP_MAX", args.sleep_max))
    if sleep_max < sleep_min:
        sleep_max = sleep_min

    keywords = [k.strip() for k in (args.keywords or os.environ.get("BILI_KEYWORDS", "明日方舟")).split(",") if k.strip()]
    up_raw = args.up_mids or os.environ.get("BILI_UP_MIDS", "161775300")
    up_mids = [int(x.strip()) for x in up_raw.split(",") if x.strip().isdigit()]

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)

    client = BiliClient(ua=ua, cookie=cookie, sleep_min=sleep_min, sleep_max=sleep_max)
    started = now_cn_iso()
    print(f"[start] keywords={keywords} target={args.target} max_videos={args.max_videos} cookie={'yes' if cookie else 'no'}")

    videos = collect_videos(client, keywords, up_mids, args.max_videos)
    write_csv(VIDEOS_CSV, VIDEO_FIELDS, videos)
    print(f"[videos] n={len(videos)} mode={client.stats.get('search_mode')} degraded={client.stats.get('degraded')}")

    comments = collect_comments(client, videos, args.target, args.max_pages_per_video)
    write_csv(OUT_CSV, COMMENT_FIELDS, comments)

    ended = now_cn_iso()
    report = REPORT_DIR / f"crawl_bili_{datetime.now(TZ_CN).strftime('%Y%m%d_%H%M%S')}.md"
    report.write_text(
        "\n".join(
            [
                "# B 站评论采集报告",
                "",
                f"- 开始：{started}",
                f"- 结束：{ended}",
                f"- 关键词：{', '.join(keywords)}",
                f"- 视频数：{len(videos)}",
                f"- 评论数：{len(comments)}",
                f"- 目标：{args.target}",
                f"- 搜索模式：{client.stats.get('search_mode') or 'n/a'}",
                f"- 是否降级：{client.stats.get('degraded')}",
                f"- 请求次数：{client.stats.get('requests')}",
                f"- HTTP 错误：{client.stats.get('http_errors')}",
                f"- API 错误：{client.stats.get('api_errors')}",
                f"- Cookie：{'已配置（未入库）' if cookie else '未配置'}",
                f"- 输出评论：`{OUT_CSV.as_posix()}`",
                f"- 输出视频：`{VIDEOS_CSV.as_posix()}`",
                "- 备注：",
                *[f"  - {n}" for n in (client.stats.get('notes') or ['无'])],
            ]
        ),
        encoding="utf-8",
    )
    (RUN_LOG_DIR / "last_crawl.json").write_text(
        json.dumps({"started": started, "ended": ended, "n_videos": len(videos), "n_comments": len(comments), "stats": client.stats}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[done] comments={len(comments)} -> {OUT_CSV}")
    print(f"[report] {report}")
    if len(comments) == 0:
        print("[warn] 0 comments crawled; check network/WBI/cookie", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Crawl Bilibili comments for Arknights contrast sample")
    p.add_argument("--target", type=int, default=350, help="目标评论条数（默认 350）")
    p.add_argument("--max-videos", type=int, default=25)
    p.add_argument("--max-pages-per-video", type=int, default=4)
    p.add_argument("--keywords", default="", help="逗号分隔；默认读 env/example")
    p.add_argument("--up-mids", default="", help="逗号分隔 UP mid，搜索失败时降级")
    p.add_argument("--sleep-min", type=float, default=0.8)
    p.add_argument("--sleep-max", type=float, default=1.6)
    return p


if __name__ == "__main__":
    raise SystemExit(main(build_parser().parse_args()))
