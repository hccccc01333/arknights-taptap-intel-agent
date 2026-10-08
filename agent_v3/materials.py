"""Source inventory exists independently of AI analysis and creative delivery."""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from agent_v2.store import stable_id, now_iso


def public_url(value):
    value = str(value or "").strip()
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            return None
        if host == "localhost" or host.endswith((".localhost", ".local")) or "." not in host:
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return value[:2000]
    except ValueError:
        return None


def capture(store, evidence_id, *, raw=None, channel="", observed_at=None):
    """Save actual text and references, never imply a media file was downloaded."""
    source = store.evidence([evidence_id])[0]
    raw = raw or {}
    stamp = observed_at or source["last_seen_at"]
    version = store.snapshot(evidence_id)
    items = []
    if len(source["body"].strip()) >= 20:
        items.append(("source_text", source["body"], public_url(source["url"]), source["content_scope"]))
    url = public_url(source["url"])
    if url and source["platform"] == "bilibili" and source["external_id"]:
        items.append(("video_reference", "", url, "video_page_only"))
    elif url and source["kind"] == "news":
        items.append(("article_reference", "", url, "article_page_only"))
    image = public_url(raw.get("pic") or raw.get("thumbnail_url"))
    if image:
        items.append(("image_reference", "", image, "remote_image_reference"))
    ids = []
    for kind, content, asset_url, scope in items:
        key = stable_id("source_asset_", "|".join([evidence_id, version, kind, content, asset_url or ""]))
        store.conn.execute("""INSERT INTO source_asset
          (asset_id,evidence_id,source_version,kind,title,content,url,scope,rights_status,first_seen_at,last_seen_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET
          last_seen_at=MAX(source_asset.last_seen_at,excluded.last_seen_at)""",
          (key,evidence_id,version,kind,source["title"],content,asset_url,scope,
           "来源使用条件未核查；用于研究参考，引用或制作前核查",stamp,stamp))
        if channel:
            store.conn.execute("INSERT OR IGNORE INTO source_asset_channel VALUES(?,?)", (key,channel))
        ids.append(key)
    return ids


def index_recent(store, hours=48, limit=1200):
    cutoff = (datetime.now(timezone.utc)-timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = store.conn.execute("""SELECT DISTINCT e.evidence_id FROM evidence e
      WHERE e.last_seen_at>=? AND e.last_seen_at<=? AND
      (EXISTS(SELECT 1 FROM channel_observation o WHERE o.evidence_id=e.evidence_id AND o.observed_at>=?)
       OR (e.kind='news' AND e.published_at>=?))
      ORDER BY e.last_seen_at DESC LIMIT ?""", (cutoff,now_iso(),cutoff,cutoff,limit)).fetchall()
    before = store.conn.execute("SELECT COUNT(*) FROM source_asset").fetchone()[0]
    with store.conn:
        for row in rows:
            capture(store,row[0])
    return {"examined":len(rows),"new_assets":store.conn.execute("SELECT COUNT(*) FROM source_asset").fetchone()[0]-before,
            "limit":limit,"note":"真实正文/摘要与媒体引用入库；热搜词不自动当作制作素材。媒体引用尚未下载或转录。"}
