"""Bounded reads from verified public sources, independent of AI availability."""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

from agent_v2.connectors import read_source as legacy_read
from agent_v2.ingest import clean, timestamp
from agent_v2.store import now_iso

FEEDS = {name: f"https://www.chinanews.com.cn/rss/{name}.xml"
         for name in ("society", "culture", "life")}
DETAIL_PATHS = {"chinanews": "v3:read_source:chinanews", "tieba": "v3:read_source:tieba"}
SCOPES = {DETAIL_PATHS["chinanews"]: "article_excerpt", DETAIL_PATHS["tieba"]: "topic_description"}


def allowed_url(url, hosts):
    try:
        p = urlparse(url)
        return (p.scheme == "https" and p.hostname in hosts and p.port in (None, 443)
                and not p.username and not p.password)
    except ValueError:
        return False


def read_public(url, hosts, *, max_bytes=1024*1024, timeout=18, method='GET', data=None, headers=None):
    """No cookies or authentication; validate every redirect before requesting it."""
    deadline = time.monotonic() + timeout
    for _ in range(3):
        if not allowed_url(url, hosts):
            raise ValueError("来源地址超出公开连接器范围")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("公开来源读取超过时间预算")
        request=requests.get if method=='GET' else requests.post
        options={'timeout':min(12,remaining),'stream':True,'allow_redirects':False}
        if data is not None and method=='POST':options['data']=data
        if headers:options['headers']=headers
        with request(url, **options) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("Location", ""))
                if getattr(response,'status_code',302) in (301,302,303):method='GET';data=None
                continue
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(16384):
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise ValueError("公开页面超过大小预算")
                if time.monotonic() > deadline:
                    raise TimeoutError("公开来源读取超过时间预算")
            return bytes(content), url
    raise ValueError("公开来源重定向超过预算")


def parse_feed(content):
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("不支持带实体声明的订阅内容")
    root = ET.fromstring(content)
    if root.tag != "rss":
        raise ValueError("未取得 RSS 新闻订阅")
    result = []
    for item in root.findall("./channel/item")[:40]:
        url = (item.findtext("link") or "").strip()
        title = clean(item.findtext("title"), 240)
        if not title or not allowed_url(url, {"www.chinanews.com.cn"}):
            continue
        # A feed's ordering is publication ordering, never popularity or rank.
        result.append({"title": title, "url": url,
                       "description": clean(item.findtext("description")),
                       "published_at": timestamp(item.findtext("pubDate")), "source": "official_rss"})
    return result


def fetch_feed(scope):
    if scope not in FEEDS:
        raise ValueError("未知新闻订阅")
    content, _ = read_public(FEEDS[scope], {"www.chinanews.com.cn"})
    return parse_feed(content)


def parse_detail(content, platform):
    soup = BeautifulSoup(content, "html.parser")
    selector = ".left_zw" if platform == "chinanews" else ".topic-desc"
    region = soup.select_one(selector)
    if region is None:
        return None
    for noise in region.select("script,style,nav,footer,aside"):
        noise.decompose()
    paragraphs = region.select("p") if platform == "chinanews" else []
    body = "\n".join(p.get_text(" ", strip=True) for p in paragraphs) if paragraphs else region.get_text(" ", strip=True)
    minimum = 50 if platform == "chinanews" else 20
    if len(body) < minimum:
        return None
    return {"body": body[:6000], "content_truncated": len(body) > 6000,
            "scope": "article_excerpt" if platform == "chinanews" else "topic_description",
            "parser_version": "public-detail-v1", "comments_read": False}


def read_source(store, evidence_id):
    original = store.evidence([evidence_id])[0]
    platform = original["platform"]
    if platform not in DETAIL_PATHS:
        return legacy_read(store, evidence_id)
    url = original["url"] or ""
    hosts = {"www.chinanews.com.cn"} if platform == "chinanews" else {"tieba.baidu.com"}
    if platform == "tieba" and not re.fullmatch(r"/hottopic/browse/hottopic", urlparse(url).path):
        raise ValueError("贴吧仅支持公开话题简介")
    content, resolved_url = read_public(url, hosts)
    detail = parse_detail(content, platform)
    if not detail:
        return {"status": "unavailable", "note": "页面没有可靠正文区域，保留标题/摘要。"}
    store.upsert_evidence({**original, "body": detail["body"], "last_seen_at": now_iso(),
                           "metrics": {}, "source_path": DETAIL_PATHS[platform]})
    store.conn.commit()
    return {"status": "ok", "metadata": {k: v for k, v in detail.items() if k != "body"} |
            {"resolved_url": resolved_url}, "note": "实际读取来源原文片段或平台话题简介，未读取评论。"}


def search_news(query):
    """Official public search form, retrieved as background rather than hotness."""
    import json
    from datetime import datetime,timedelta,timezone
    from agent_v2.ingest import timestamp
    if not isinstance(query,str) or not 2<=len(query.strip())<=80:raise ValueError('搜索词需为 2 至 80 字')
    content,_=read_public('https://sou.chinanews.com.cn/search/news',{'sou.chinanews.com.cn'},method='POST',
       data={'q':query.strip(),'sortType':'time','searchField':'all','dateType':'3day','pageNum':1,'channel':'all'})
    soup=BeautifulSoup(content,'html.parser');rows=None
    for script in soup.select('script:not([src])'):
        match=re.search(r'var\s+docArr\s*=\s*(\[.*?\]);',script.get_text(),re.S)
        if match:rows=json.loads(match.group(1));break
    if not isinstance(rows,list):raise ValueError('搜索未返回可核查结果')
    result=[]
    cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat(timespec='seconds')
    ceiling=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec='seconds')
    for row in rows[:5]:
        def field(key):
            value=row.get(key)
            return ' '.join(str(v) for v in value) if isinstance(value,list) else value or ''
        published=timestamp(field('pubtime'))
        if not published or not cutoff<=published<=ceiling:continue
        url=str(field('url')).replace('http://www.chinanews.com.cn/','https://www.chinanews.com.cn/',1)
        if not allowed_url(url,{'www.chinanews.com.cn'}):continue
        title=clean(field('title'),240)
        if title:result.append({'title':title,'description':clean(field('content_without_tag')),'url':url,
            'published_at':published,'source':'search:'+query})
    return result
