#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TapTap 明日方舟评价爬虫（MVP）

规格：01爬虫/爬取提示词构建.md
数据输出：02数据/
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
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import requests

ROOT = Path(__file__).resolve().parents[1]
CRAWLER_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT / "02数据"
RAW_DIR = DATA_DIR / "raw"
REPORT_DIR = DATA_DIR / "reports"
RUN_LOG_DIR = DATA_DIR / "run_logs"
REVIEWS_CSV = DATA_DIR / "reviews.csv"
CHECKPOINT_PATH = DATA_DIR / "checkpoint.json"

BASE_URL = "https://www.taptap.cn"
API_PATH = "/webapiv2/review/v2/list-by-app"
DEFAULT_GAME = "arknights"  # 游戏档案 key，见 games/<key>.json
APP_ID = 70253  # 兜底值；运行时以游戏档案为准（build_parser 的 --game）
LIMIT = 10
SCORE_MAX = 5
TZ_CN = timezone(timedelta(hours=8))


def load_game_profile(game_key: str) -> dict[str, Any]:
    """加载游戏档案（games/game_profile.py，唯一参数化入口）。"""
    sys.path.insert(0, str(ROOT / "games"))
    from game_profile import load as _load  # noqa: PLC0415

    return _load(game_key)

CSV_FIELDS = [
    "review_id",
    "moment_id",
    "app_id",
    "source_url",
    "request_from",
    "crawled_at",
    "raw_json_path",
    "user_name",
    "user_id",
    "user_id_hash",
    "avatar_url",
    "is_moderator",
    "text",
    "raw_text",
    "rating_tags",
    "stage",
    "stage_label",
    "source_code",
    "is_edited",
    "edited_time",
    "publish_time",
    "publish_time_cn",
    "score_raw",
    "score_max",
    "score_norm",
    "is_recommend",
    "played_spent_sec",
    "total_played_spent_sec",
    "played_hours",
    "hidden_spent",
    "image_count",
    "image_urls",
    "image_original_urls",
    "device_raw",
    "publish_platform",
    "support_count",
    "reply_preview_count",
    "first_seen_at",
    "last_seen_at",
]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())


def now_cn_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def ts_to_cn_iso(ts: int | float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(int(ts), TZ_CN).isoformat(timespec="seconds")


def hash_user_id(user_id: Any) -> str:
    raw = str(user_id).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def map_platform(device_raw: str) -> str:
    s = (device_raw or "").lower()
    if not s:
        return "未知"
    if "pc" in s or "windows" in s or "客户端" in device_raw:
        if "iphone" in s or "ipad" in s or "ios" in s:
            return "iOS"
        if any(x in s for x in ("android", "huawei", "honor", "xiaomi", "oppo", "vivo", "iqoo", "redmi", "samsung", "oneplus", "realme", "meizu")):
            return "Android"
        return "PC"
    if any(x in s for x in ("iphone", "ipad", "ios")):
        return "iOS"
    if "web" in s:
        return "Web"
    if any(
        x in s
        for x in (
            "huawei",
            "honor",
            "xiaomi",
            "oppo",
            "vivo",
            "iqoo",
            "redmi",
            "samsung",
            "oneplus",
            "realme",
            "meizu",
            "pixel",
            "android",
        )
    ):
        return "Android"
    # 型号形如 小米25080RABDC / P600
    if re.search(r"[\u4e00-\u9fff].*\d|[A-Z]{1,4}\d", device_raw):
        return "Android"
    return "未知"


def parse_item(item: dict[str, Any], request_from: int, crawled_at: str, raw_json_path: str) -> dict[str, Any] | None:
    moment = item.get("moment") or {}
    review = moment.get("review") or {}
    if not review.get("id"):
        return None

    author = ((moment.get("author") or {}).get("user")) or {}
    contents = review.get("contents") or {}
    images = review.get("images") or []
    ratings = review.get("ratings") or []
    comments = review.get("review_comments") or []
    stat = moment.get("stat") or {}

    created = moment.get("created_time")
    edited = moment.get("edited_time")
    publish = moment.get("publish_time") or created
    is_edited_flag = moment.get("edited")
    if is_edited_flag is None and created is not None and edited is not None:
        is_edited_flag = int(edited) != int(created)

    score = review.get("score")
    played = review.get("played_spent")
    total_played = review.get("total_played_spent")
    device_raw = moment.get("device") or ""

    review_id = review["id"]
    return {
        "review_id": review_id,
        "moment_id": moment.get("id_str") or "",
        "app_id": ((moment.get("app") or {}).get("id")) or APP_ID,
        "source_url": f"https://www.taptap.cn/review/{review_id}",
        "request_from": request_from,
        "crawled_at": crawled_at,
        "raw_json_path": raw_json_path,
        "user_name": author.get("name") or "",
        "user_id": author.get("id") or "",
        "user_id_hash": hash_user_id(author.get("id") or ""),
        "avatar_url": author.get("avatar") or "",
        "is_moderator": bool(author.get("is_moderator")),
        "text": contents.get("text") or "",
        "raw_text": contents.get("raw_text") or "",
        "rating_tags": json.dumps(ratings, ensure_ascii=False),
        "stage": review.get("stage"),
        "stage_label": review.get("stage_label") or "",
        "source_code": review.get("source"),
        "is_edited": bool(is_edited_flag),
        "edited_time": edited if edited is not None else "",
        "publish_time": publish if publish is not None else "",
        "publish_time_cn": ts_to_cn_iso(publish),
        "score_raw": score,
        "score_max": SCORE_MAX,
        "score_norm": round(float(score) / SCORE_MAX, 4) if score is not None else "",
        "is_recommend": (int(score) >= 4) if score is not None else "",
        "played_spent_sec": played if played is not None else "",
        "total_played_spent_sec": total_played if total_played is not None else "",
        "played_hours": round(float(played) / 3600, 4) if played is not None else "",
        "hidden_spent": review.get("hidden_spent") if "hidden_spent" in review else "",
        "image_count": len(images),
        "image_urls": json.dumps([img.get("url") for img in images if img.get("url")], ensure_ascii=False),
        "image_original_urls": json.dumps(
            [img.get("original_url") for img in images if img.get("original_url")],
            ensure_ascii=False,
        ),
        "device_raw": device_raw,
        "publish_platform": map_platform(device_raw),
        "support_count": stat.get("supports") if stat.get("supports") is not None else 0,
        "reply_preview_count": len(comments) if isinstance(comments, list) else 0,
        "first_seen_at": crawled_at,
        "last_seen_at": crawled_at,
    }


def load_checkpoint() -> dict[str, Any]:
    if not CHECKPOINT_PATH.exists():
        return {
            "last_from": 0,
            "watermark_review_id": None,
            "watermark_publish_time": None,
            "seen_review_ids": [],
        }
    return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))


def save_checkpoint(cp: dict[str, Any]) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")


def load_existing_index() -> dict[str, dict[str, Any]]:
    if not REVIEWS_CSV.exists():
        return {}
    index: dict[str, dict[str, Any]] = {}
    with REVIEWS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rid = str(row.get("review_id") or "")
            if rid:
                index[rid] = row
    return index


def write_reviews_csv(rows_by_id: dict[str, dict[str, Any]]) -> None:
    REVIEWS_CSV.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        rows_by_id.values(),
        key=lambda r: int(r["publish_time"]) if str(r.get("publish_time", "")).isdigit() else 0,
        reverse=True,
    )
    with REVIEWS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def changed(old: dict[str, Any], new: dict[str, Any]) -> bool:
    keys = ["text", "score_raw", "edited_time", "support_count", "image_count"]
    return any(str(old.get(k, "")) != str(new.get(k, "")) for k in keys)


class TapTapCrawler:
    def __init__(self, x_ua: str, sleep_min: float = 0.8, sleep_max: float = 1.8):
        self.x_ua = x_ua
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Referer": f"https://www.taptap.cn/app/{APP_ID}/review",
                "Origin": "https://www.taptap.cn",
            }
        )

    def build_url(self, from_offset: int) -> str:
        params = {
            "app_id": APP_ID,
            "filter_platform": "",
            "from": from_offset,
            "label": "",
            "limit": LIMIT,
            "mapping": "",
            "sort": "new",
            "source_type": "",
            "stage_type": "",
            "X-UA": self.x_ua,
        }
        return f"{BASE_URL}{API_PATH}?{urlencode(params)}"

    def fetch_page(self, from_offset: int) -> tuple[dict[str, Any] | None, str | None, int | None]:
        url = self.build_url(from_offset)
        last_status = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, timeout=20)
                last_status = resp.status_code
                if resp.status_code in (403, 429):
                    return None, f"blocked_http_{resp.status_code}", resp.status_code
                if resp.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                try:
                    return resp.json(), None, resp.status_code
                except json.JSONDecodeError:
                    bad = RAW_DIR / f"bad_response_from_{from_offset}.txt"
                    bad.write_text(resp.text[:5000], encoding="utf-8")
                    return None, "invalid_json", resp.status_code
            except requests.RequestException as exc:
                if attempt == 2:
                    return None, f"request_error:{exc}", last_status
                time.sleep(2 ** attempt)
        return None, "unknown_error", last_status

    def polite_sleep(self) -> None:
        time.sleep(random.uniform(self.sleep_min, self.sleep_max))


def write_qc_report(
    rows: list[dict[str, Any]],
    run_stats: dict[str, Any],
    path: Path,
) -> None:
    n = len(rows)
    empty_text = sum(1 for r in rows if not str(r.get("text") or "").strip())
    bad_score = [
        r
        for r in rows
        if r.get("score_raw") not in ("", None)
        and (not str(r.get("score_raw")).lstrip("-").isdigit() or not (1 <= int(r["score_raw"]) <= 5))
    ]
    neg_play = [
        r
        for r in rows
        if str(r.get("played_spent_sec", "")).lstrip("-").isdigit() and int(r["played_spent_sec"]) < 0
    ]
    ids = [str(r["review_id"]) for r in rows]
    dup = len(ids) - len(set(ids))

    times = [int(r["publish_time"]) for r in rows if str(r.get("publish_time", "")).isdigit()]
    sorted_desc = times == sorted(times, reverse=True) if times else True

    platforms = {}
    for r in rows:
        p = r.get("publish_platform") or "未知"
        platforms[p] = platforms.get(p, 0) + 1

    lines = [
        f"# 爬取质检报告",
        "",
        f"- 生成时间：{now_cn_iso()}",
        f"- 结构化条数：{n}",
        f"- 请求页数：{run_stats.get('pages')}",
        f"- 获取条数：{run_stats.get('fetched')}",
        f"- 新增：{run_stats.get('added')}｜更新：{run_stats.get('updated')}｜重复：{run_stats.get('duplicates')}",
        f"- 失败请求：{run_stats.get('failed_requests')}",
        f"- 停止原因：{run_stats.get('stop_reason')}",
        f"- 最后 from：{run_stats.get('last_from')}",
        "",
        "## 质量检查",
        f"- review_id 重复数：{dup}",
        f"- 空正文：{empty_text}（{empty_text / n * 100:.2f}%）" if n else "- 空正文：0",
        f"- 评分越界条数：{len(bad_score)}",
        f"- 游玩时长为负：{len(neg_play)}",
        f"- 发布时间整体从新到旧：{'是' if sorted_desc else '否（CSV 已按时间重排，属正常）'}",
        "",
        "## 平台分布",
    ]
    for k, v in sorted(platforms.items(), key=lambda x: -x[1]):
        lines.append(f"- {k}: {v}")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    global APP_ID
    prof = load_game_profile(getattr(args, "game", DEFAULT_GAME))
    APP_ID = prof.get("app_id") or APP_ID
    print(f"[game] {prof.get('name')} (app_id={APP_ID}) from games/{getattr(args, 'game', DEFAULT_GAME)}.json")

    load_dotenv(CRAWLER_DIR / ".env")
    x_ua = os.environ.get("TAPTAP_X_UA", "").strip()
    if not x_ua:
        print("缺少环境变量 TAPTAP_X_UA。请复制 config.example.env 为 .env 并填入浏览器抓到的 X-UA。", file=sys.stderr)
        return 2

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)

    start_ts = None
    if args.since:
        start_ts = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=TZ_CN).timestamp())

    cp = load_checkpoint()
    start_from = args.from_offset
    if args.resume:
        start_from = int(cp.get("last_from") or 0)

    existing = load_existing_index()
    crawler = TapTapCrawler(x_ua=x_ua, sleep_min=args.sleep_min, sleep_max=args.sleep_max)

    stats = {
        "started_at": now_cn_iso(),
        "mode": args.mode,
        "pages": 0,
        "fetched": 0,
        "added": 0,
        "updated": 0,
        "duplicates": 0,
        "failed_requests": 0,
        "last_from": start_from,
        "stop_reason": "",
        "newest_review_id": None,
        "newest_publish_time": None,
    }

    empty_new_pages = 0
    from_offset = start_from
    stop = False

    print(f"[start] mode={args.mode} from={from_offset} max_records={args.max_records} out={DATA_DIR}")

    while not stop:
        if args.max_pages and stats["pages"] >= args.max_pages:
            stats["stop_reason"] = "max_pages"
            break

        payload, err, status = crawler.fetch_page(from_offset)
        stats["pages"] += 1
        stats["last_from"] = from_offset

        if err:
            stats["failed_requests"] += 1
            stats["stop_reason"] = err
            print(f"[stop] from={from_offset} status={status} err={err}")
            break

        assert payload is not None
        data = payload.get("data") or {}
        items = data.get("list") or []

        raw_name = f"from_{from_offset:06d}.json"
        raw_path = RAW_DIR / raw_name
        raw_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        crawled_at = now_cn_iso()
        page_added = 0

        if not items:
            stats["stop_reason"] = "empty_list"
            stop = True
        elif len(items) < LIMIT:
            # 仍解析本页，然后停
            pass

        all_older = True if items and start_ts is not None else False
        hit_watermark = False

        for item in items:
            row = parse_item(item, from_offset, crawled_at, str(raw_path.relative_to(ROOT)).replace("\\", "/"))
            if not row:
                continue
            stats["fetched"] += 1
            rid = str(row["review_id"])
            pub = row.get("publish_time")
            pub_i = int(pub) if str(pub).isdigit() else None

            if stats["newest_review_id"] is None and pub_i is not None:
                stats["newest_review_id"] = rid
                stats["newest_publish_time"] = pub_i

            if start_ts is not None and pub_i is not None and pub_i >= start_ts:
                all_older = False

            if args.mode == "incremental" and cp.get("watermark_publish_time") and pub_i is not None:
                if pub_i <= int(cp["watermark_publish_time"]) and rid in existing:
                    hit_watermark = True

            if rid in existing:
                old = existing[rid]
                if changed(old, row):
                    row["first_seen_at"] = old.get("first_seen_at") or crawled_at
                    row["last_seen_at"] = crawled_at
                    existing[rid] = row
                    stats["updated"] += 1
                    page_added += 1
                else:
                    old["last_seen_at"] = crawled_at
                    existing[rid] = old
                    stats["duplicates"] += 1
            else:
                existing[rid] = row
                stats["added"] += 1
                page_added += 1

            if args.max_records and (stats["added"] + stats["updated"]) >= args.max_records and args.mode == "full":
                # 以库内目标规模控制：当新增+更新累计达到上限附近时，用总条数控制更直观
                pass

            if args.max_records and len(existing) >= args.max_records and args.mode == "full" and start_from == 0:
                # 若已有库超过上限，仍继续本页；整页后检查
                pass

        if start_ts is not None and items and all_older:
            stats["stop_reason"] = "before_since_date"
            stop = True
        elif len(items) < LIMIT:
            stats["stop_reason"] = stats["stop_reason"] or "page_lt_limit"
            stop = True
        elif args.mode == "incremental" and hit_watermark:
            stats["stop_reason"] = "hit_watermark"
            stop = True
        elif args.max_records and len(existing) >= args.max_records:
            stats["stop_reason"] = "max_records"
            stop = True
        elif page_added == 0:
            empty_new_pages += 1
            if empty_new_pages >= args.empty_pages:
                stats["stop_reason"] = "consecutive_empty_new_pages"
                stop = True
        else:
            empty_new_pages = 0

        cp["last_from"] = from_offset + LIMIT
        if stats["newest_review_id"] and not cp.get("watermark_review_id"):
            cp["watermark_review_id"] = stats["newest_review_id"]
            cp["watermark_publish_time"] = stats["newest_publish_time"]
        save_checkpoint(cp)
        write_reviews_csv(existing)

        print(
            f"[page] from={from_offset} got={len(items)} added={stats['added']} "
            f"updated={stats['updated']} total={len(existing)}"
        )

        if stop:
            break

        from_offset += LIMIT
        crawler.polite_sleep()

    # 增量成功后抬高水位到本 run 最新
    if args.mode == "incremental" and stats["newest_publish_time"]:
        cp["watermark_review_id"] = stats["newest_review_id"]
        cp["watermark_publish_time"] = stats["newest_publish_time"]
    cp["last_from"] = from_offset
    cp["updated_at"] = now_cn_iso()
    save_checkpoint(cp)
    write_reviews_csv(existing)

    stats["ended_at"] = now_cn_iso()
    stats["total_rows"] = len(existing)
    if not stats["stop_reason"]:
        stats["stop_reason"] = "completed"

    run_path = RUN_LOG_DIR / f"run_{datetime.now(TZ_CN).strftime('%Y%m%d_%H%M%S')}.json"
    run_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    qc_path = REPORT_DIR / f"crawl_qc_{datetime.now(TZ_CN).strftime('%Y%m%d_%H%M%S')}.md"
    write_qc_report(list(existing.values()), stats, qc_path)

    print(f"[done] total={len(existing)} stop={stats['stop_reason']}")
    print(f"[out] {REVIEWS_CSV}")
    print(f"[qc] {qc_path}")
    return 0 if not str(stats["stop_reason"]).startswith("blocked") else 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Crawl TapTap reviews by game profile (games/<key>.json)"
    )
    p.add_argument("--game", default=DEFAULT_GAME, help="游戏档案 key（games/<key>.json）")
    p.add_argument("--mode", choices=["full", "incremental"], default="full")
    p.add_argument("--max-records", type=int, default=3000, help="Stop when local unique reviews reach this size")
    p.add_argument("--max-pages", type=int, default=0, help="0 means unlimited")
    p.add_argument("--from-offset", type=int, default=0)
    p.add_argument("--resume", action="store_true", help="Resume from checkpoint last_from")
    p.add_argument("--since", type=str, default="", help="YYYY-MM-DD; stop when page is all older")
    p.add_argument("--empty-pages", type=int, default=3, help="Stop after N consecutive pages with no new/updated rows")
    p.add_argument("--sleep-min", type=float, default=0.8)
    p.add_argument("--sleep-max", type=float, default=1.8)
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
