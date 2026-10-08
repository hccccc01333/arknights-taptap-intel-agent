from __future__ import annotations

import csv
import html
import json
import re
from email.utils import parsedate_to_datetime
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .store import Store, ROOT, now_iso, stable_id

TZ = timezone(timedelta(hours=8))


def timestamp(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        text = str(value).strip()
        if re.fullmatch(r"\d{10}(?:\.\d+)?", text):
            parsed = datetime.fromtimestamp(float(text), timezone.utc)
        else:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                parsed = parsedate_to_datetime(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None


def clean(value: Any, limit=6000) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).strip()[:limit]


def normalize(row: dict[str, Any], platform: str, source_path: str) -> dict[str, Any] | None:
    title = clean(row.get("title") or row.get("word") or row.get("topic_name") or row.get("summary"), 240)
    observed = timestamp(row.get("observed_at") or row.get("last_seen_at") or row.get("crawled_at"))
    if not title or not observed:
        return None
    original_platform = str(row.get("platform") or platform) if platform == "agent" else platform
    original_platform = {"bili": "bilibili"}.get(original_platform, original_platform)
    external_id = str(row.get("bvid") or row.get("moment_id") or row.get("post_id") or row.get("item_id") or row.get("topic_id") or "")
    url = html.unescape(str(row.get("url") or ""))
    if not url and original_platform == "taptap" and external_id:
        url = f"https://www.taptap.cn/moment/{external_id}"
    if url and not re.match(r"^https?://", url):
        url = ""
    identity = external_id or url or title
    # Search hits and organic observations of the same original post share an ID.
    evidence_id = stable_id("evidence_", f"{original_platform}|{identity}")
    source = str(row.get("source") or row.get("source_type") or "")
    kind = ("search" if platform == "agent" or source.startswith("search:") else
            "ranking" if row.get("word") or row.get("topic_name") else
            "news" if platform == "gamemedia" else "content")
    metrics = {}
    for target, options in {"views": ("play", "pv_total"), "likes": ("like", "ups", "supports"),
                            "comments": ("reply", "comments", "reply_count"),
                            "shares": ("share",), "rank": ("rank",), "hot_score": ("hot_score",)}.items():
        for field in options:
            try:
                value = float(str(row.get(field, "")).replace(",", ""))
                if value >= 0 and value < float("inf"):
                    metrics[target] = value
                    break
            except (TypeError, ValueError):
                continue
    published = timestamp(row.get("published_at") or row.get("pubdate") or row.get("publish_time") or row.get("create_time"))
    return {"evidence_id": evidence_id, "platform": original_platform, "external_id": external_id,
            "kind": kind, "title": title, "body": clean(row.get("content") or row.get("summary") or row.get("description") or row.get("desc")),
            "url": url or None, "published_at": published, "first_seen_at": timestamp(row.get("first_seen_at")) or observed,
            "last_seen_at": observed, "metrics": metrics,
            "context": clean(row.get("app_title") or row.get("matched_game") or row.get("forum") or row.get("query") or source, 200),
            "source_path": source_path}


def source_files(root: Path):
    raw = root / "data" / "raw"
    fixed = (("baidu", "baidu_index/hot_search.csv"), ("baidu", "baidu/hot_search.csv"),
             ("weibo", "weibo/hot_search.csv"), ("bilibili", "bilibili/hot_videos.csv"),
             ("gamemedia", "gamemedia/news.csv"), ("agent", "agent/search_results.csv"),
             ("tieba", "tieba/hot_topics.csv"), ("taptap", "taptap/discovery_posts.csv"),
             ("taptap", "taptap/community/posts.csv"))
    for platform, name in fixed:
        yield platform, raw / name
    for name in ("taptap/communities/*/posts.csv", "tieba/forums/*/posts.csv"):
        for path in sorted(raw.glob(name)):
            yield name.split("/")[0], path


def ingest(store: Store, root: Path | None = None, max_rows_per_file=3000) -> dict[str, Any]:
    root = root or ROOT
    inserted = observations = skipped_files = 0
    sources, failures = [], []
    for platform, path in source_files(root):
        relative = path.relative_to(root).as_posix()
        if not path.is_file():
            continue
        stat = path.stat()
        signature = f"normalizer-2.1:{stat.st_size}:{stat.st_mtime_ns}"
        previous = store.conn.execute("SELECT file_signature FROM source WHERE source_path=?", (relative,)).fetchone()
        if previous and previous[0] == signature:
            skipped_files += 1
            continue
        latest, accepted = None, 0
        try:
            # CSV parsing preserves quoted multiline content. Bound imported history,
            # while keeping each source's latest rows rather than its first rows.
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = deque(csv.DictReader(stream), maxlen=max_rows_per_file)
            for row in rows:
                item = normalize(row, platform, relative)
                if not item:
                    continue
                crawled,seen=timestamp(row.get("crawled_at")),timestamp(row.get("last_seen_at"))
                if platform=="taptap" and crawled and seen and seen>crawled:
                    old=store.conn.execute("SELECT metrics FROM observation WHERE evidence_id=? AND observed_at=?",(item["evidence_id"],crawled)).fetchone()
                    if old:
                        old_metrics=json.loads(old[0]);old_metrics["quality"]="migration_time_ambiguous"
                        store.conn.execute("UPDATE observation SET metrics=? WHERE evidence_id=? AND observed_at=?",(json.dumps(old_metrics,ensure_ascii=False),item["evidence_id"],crawled))
                inserted += store.upsert_evidence(item)
                accepted += 1
                observations += 1
                latest = max(latest or item["last_seen_at"], item["last_seen_at"])
            store.conn.execute("INSERT INTO source VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_path) DO UPDATE SET file_signature=excluded.file_signature,last_imported_at=excluded.last_imported_at,latest_observed_at=excluded.latest_observed_at,records=excluded.records,status=excluded.status,error=NULL",
                               (relative, platform, signature, now_iso(), latest, accepted, "ok" if accepted else "empty", None))
            sources.append({"path": relative, "platform": platform, "observations": accepted, "latest_at": latest})
        except (OSError, csv.Error, UnicodeError) as error:
            failures.append({"path": relative, "error": type(error).__name__})
            store.conn.execute("INSERT INTO source VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_path) DO UPDATE SET status=excluded.status,error=excluded.error",
                               (relative, platform, None, now_iso(), None, 0, "failed", type(error).__name__))
        store.conn.commit()
    return {"inserted_evidence": inserted, "observations_processed": observations,
            "unchanged_files": skipped_files, "sources": sources, "failures": failures,
            "history_limit_per_file": max_rows_per_file}
