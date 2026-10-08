"""Bounded public connectors. V1 collection files are never rewritten here."""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import quote, urlparse

import requests

from .ingest import normalize, clean
from .store import Store, ROOT, now_iso

CAPABILITIES = {
    "bilibili": {"discover": True, "search": True, "read": True, "snapshots": True, "comments": False},
    "gamemedia": {"discover": True, "search": False, "read": True, "snapshots": False, "comments": False},
    "baidu": {"discover": True, "search": False, "read": False, "snapshots": True, "comments": False},
    "weibo": {"discover": True, "search": False, "read": False, "snapshots": True, "comments": False},
    "taptap": {"discover": True, "search": False, "read": False, "snapshots": True, "comments": False},
    "tieba": {"discover": True, "search": False, "read": False, "snapshots": True, "comments": False},
}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36"}


@lru_cache(maxsize=8)
def legacy(name, file):
    path = ROOT / "L1_data_source" / "collectors" / file
    # The existing standalone collectors use sibling imports.
    for folder in (path.parent, path.parent.parent):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location("v2_connector_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def bili_client():
    module = legacy("bili", "bilibili/crawl_bili_game_hot.py")
    return module.BiliHot(sleep=0.5)


def fetch(platform: str, query="") -> list[dict]:
    if platform == "bilibili":
        client = bili_client()
        if query and not client._crawler:
            raise ValueError("B站搜索签名不可用，请检查连接器配置")
        if query:
            rows = client._crawler.search_videos(query, page=1, page_size=20, order="totalrank")
        else:
            rows = client.ranking_game()
        result = []
        for raw in rows[:40]:
            stat = raw.get("stat") or raw
            # Strip highlighting BEFORE limiting text; keep the actual counters.
            item = {"title": clean(raw.get("title"), 240), "description": clean(raw.get("desc") or raw.get("description")),
                    "bvid": raw.get("bvid"), "url": "https://www.bilibili.com/video/" + str(raw.get("bvid") or ""),
                    "pubdate": raw.get("pubdate"), "source": "search:" + query if query else "ranking_game"}
            for target, source in (("play", "view"), ("like", "like"), ("reply", "reply"), ("share", "share")):
                value = stat.get(source)
                if value is None and target == "play":
                    value = stat.get("play")
                if value is not None:
                    item[target] = value
            result.append(item)
        return result
    if platform == "gamemedia":
        module = legacy("media", "gamemedia/crawl_gamemedia.py")
        return [*module.fetch_gcores()[:25], *module.fetch_3dm()[:25], *module.fetch_gamersky()[:40]]
    if platform == "baidu":
        module = legacy("baidu", "baidu/crawl_baidu_hot.py")
        return [{"word": r.get("word"), "hot_score": r.get("hotScore"), "rank": i+1,
                 "url": r.get("url") or "https://www.baidu.com/s?wd=" + quote(r.get("word") or ""), "source": "game"}
                for i, r in enumerate(module.BaiduHotSearch().fetch("game")[:40])]
    if platform == "weibo":
        module = legacy("weibo", "weibo/crawl_weibo_hot.py")
        return [{"word": r.get("word"), "hot_score": r.get("num"), "rank": i+1,
                 "url": "https://s.weibo.com/weibo?q=" + quote(r.get("word") or "")}
                for i, r in enumerate(module.WeiboHotSearch(module.load_cookie()).fetch()[:50])]
    if platform == "taptap":
        module = legacy("taptap", "taptap/crawl_taptap_discovery.py")
        crawler = module.DiscoveryCrawler(module.load_xua(),0.5,1)
        data = crawler._get(module.DISCOVER_URL,{"category_id":0,"sort":"default"},module.BASE+"/discover",retries=1)
        return [r for raw in ((data or {}).get("data") or {}).get("list",[])[:30] if (r:=module.parse_moment(raw,now_iso()))]
    if platform == "tieba":
        module = legacy("tieba", "tieba/crawl_tieba_hot.py")
        return module.fetch()[:40]
    raise ValueError("该渠道目前只接入历史采集记录，尚未实现 V2 实时连接器")


def collect(store: Store, platform: str, query="", *, force=False) -> dict:
    capability = CAPABILITIES.get(platform)
    if not capability or not capability["search" if query else "discover"]:
        raise ValueError("连接器未实现该能力")
    previous = store.conn.execute("SELECT * FROM connector_health WHERE platform=?", (platform,)).fetchone()
    if not force and previous and previous["retry_after"] and previous["retry_after"] > now_iso():
        return {"platform": platform, "status": "paused", "retry_after": previous["retry_after"], "evidence_ids": []}
    stamp = now_iso()
    try:
        rows = fetch(platform, query)
        ids, inserted = [], 0
        for row in rows:
            item = normalize({**row, "observed_at": stamp, "query": query}, platform, "v2:live:" + platform)
            if item:
                inserted += store.upsert_evidence(item)
                ids.append(item["evidence_id"])
        if not ids:
            raise ValueError("连接器返回空结果；无法区分无内容、接口变化和平台限制")
        store.conn.execute("INSERT INTO connector_health VALUES(?,?,?,?,?,?,?) ON CONFLICT(platform) DO UPDATE SET last_attempt=excluded.last_attempt,last_success=excluded.last_success,status='ok',failures=0,error=NULL,retry_after=NULL",
                           (platform, stamp, stamp, "ok", 0, None, None))
        store.conn.commit()
        return {"platform": platform, "query": query, "status": "ok", "evidence_ids": ids,
                "inserted_evidence": inserted, "observed_at": stamp,
                "note": "实际采样结果，不能代表全网讨论量。榜单只说明该榜单中的排名。"}
    except Exception as error:
        store.conn.rollback()
        failures = (previous["failures"] if previous else 0) + 1
        retry = (datetime.now(timezone.utc) + timedelta(minutes=min(30, failures*5))).isoformat(timespec="seconds")
        # Do not persist cookies, headers, request URLs or credentials from errors.
        reason = f"{type(error).__name__}: 连接器读取失败或返回空结果"
        if isinstance(error, requests.HTTPError) and error.response is not None:
            reason = f"HTTP {error.response.status_code}: 平台拒绝或接口不可用"
        store.conn.execute("INSERT INTO connector_health VALUES(?,?,?,?,?,?,?) ON CONFLICT(platform) DO UPDATE SET last_attempt=excluded.last_attempt,status=excluded.status,failures=excluded.failures,error=excluded.error,retry_after=excluded.retry_after",
                           (platform, stamp, None, "failed", failures, reason, retry))
        store.conn.commit()
        return {"platform": platform, "status": "failed", "error": reason, "retry_after": retry, "evidence_ids": []}


def refresh_sources(store: Store) -> dict:
    results = [collect(store, platform) for platform in CAPABILITIES]
    return {"channels": results, "status": "ok" if all(r["status"] == "ok" for r in results) else "partial"}


def read_source(store: Store, evidence_id: str) -> dict:
    rows = store.evidence([evidence_id])
    if not rows:
        raise ValueError("证据不存在")
    original = rows[0]
    if original["platform"] == "bilibili" and original["external_id"]:
        response = requests.get("https://api.bilibili.com/x/web-interface/view",
                                params={"bvid": original["external_id"]}, headers=UA, timeout=15)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise ValueError("B站内容详情受限或不可用")
        row = payload["data"]
        stat = row.get("stat") or {}
        item = normalize({"bvid": original["external_id"], "title": row.get("title"), "description": row.get("desc"),
                          "url": original["url"], "pubdate": row.get("pubdate"), "observed_at": now_iso(),
                          **{k:stat[v] for k,v in (("play","view"),("like","like"),("reply","reply"),("share","share")) if v in stat}},
                         "bilibili", "v2:read_source")
    elif original["platform"] == "gamemedia":
        url = original["url"] or ""
        allowed = {"www.gamersky.com", "www.3dmgame.com", "www.gcores.com"}
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.port not in (None,443):
            raise ValueError("此来源尚未配置正文连接器")
        # Redirects are checked before following; evidence text cannot choose a URL.
        for _ in range(3):
            with requests.get(url, headers=UA, timeout=15, allow_redirects=False, stream=True) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers.get("Location", ""))
                    target = urlparse(url)
                    if target.scheme != "https" or target.hostname not in allowed or target.port not in (None,443):
                        raise ValueError("来源重定向超出已实现连接器")
                    continue
                response.raise_for_status()
                content = b""
                for chunk in response.iter_content(16384):
                    content += chunk
                    if len(content) > 1024*1024:
                        raise ValueError("正文页面超过读取预算")
                break
        else:
            raise ValueError("重定向超过预算")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content.decode("utf-8", errors="replace"), "html.parser")
        article = soup.select_one(".Mid2L_con, .news_warp_center, article")
        if article is None:
            return {"status": "unavailable", "note": "页面未找到可靠正文区域，保留原标题与摘要。"}
        for noise in article.select("script, style, nav, footer, aside"):
            noise.decompose()
        body = "\n".join(p.get_text(" ", strip=True) for p in article.select("p"))[:10000]
        if len(body) < 50:
            return {"status": "unavailable", "note": "正文不足或页面需要渲染，不能宣称已读全文。"}
        item = normalize({"title": original["title"], "description": body, "url": original["url"],
                          "published_at": original["published_at"], "observed_at": now_iso()}, "gamemedia", "v2:read_source")
    else:
        return {"status": "unavailable", "note": "当前没有此渠道的正文读取能力，请保留摘要或标题限制。"}
    if item:
        store.upsert_evidence(item)
        store.conn.commit()
    return {"status": "ok", "evidence": store.evidence([evidence_id]), "note": "返回实际详情或正文，不包含未读取的评论。"}
