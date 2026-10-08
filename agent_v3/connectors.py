"""Explicit cross-domain channels; each board retains its own observations."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from agent_v2 import connectors as adapters
from agent_v2.ingest import normalize, clean
from agent_v2.store import now_iso, dump
from .materials import capture
from .public_sources import fetch_feed

CHANNELS = [
    {"id":"baidu:realtime","platform":"baidu","scope":"综合热搜","domain":"综合"},
    {"id":"baidu:movie","platform":"baidu","scope":"电影榜","domain":"娱乐"},
    {"id":"baidu:teleplay","platform":"baidu","scope":"电视剧榜","domain":"娱乐"},
    {"id":"baidu:game","platform":"baidu","scope":"游戏榜","domain":"游戏"},
    {"id":"bilibili:popular","platform":"bilibili","scope":"全站热门","domain":"综合"},
    {"id":"bilibili:game","platform":"bilibili","scope":"游戏排行","domain":"游戏"},
    {"id":"weibo:hot","platform":"weibo","scope":"综合热搜","domain":"综合"},
    {"id":"tieba:hot","platform":"tieba","scope":"热议话题","domain":"综合"},
    {"id":"gamemedia:news","platform":"gamemedia","scope":"游戏资讯","domain":"游戏"},
    {"id":"taptap:discover","platform":"taptap","scope":"发现页","domain":"游戏"},
    {"id":"chinanews:society","platform":"chinanews","scope":"社会新闻订阅","domain":"社会","signal_type":"news_feed"},
    {"id":"chinanews:culture","platform":"chinanews","scope":"文娱新闻订阅","domain":"文化","domains":["文化","娱乐"],"signal_type":"news_feed"},
    {"id":"chinanews:life","platform":"chinanews","scope":"生活新闻订阅","domain":"生活方式","signal_type":"news_feed"},
]


def fetch(channel):
    platform, scope=channel.split(":",1)
    if platform=="chinanews":return fetch_feed(scope)
    if platform=="baidu":
        module=adapters.legacy("baidu","baidu/crawl_baidu_hot.py")
        rows=module.BaiduHotSearch().fetch(scope)[:40]
        return [{"word":r.get("word"),"description":r.get("desc"),"hot_score":r.get("hotScore"),"rank":i+1,
                 "url":r.get("url") or "https://www.baidu.com/s?wd="+quote(r.get("word") or ""),"source":scope} for i,r in enumerate(rows)]
    if channel=="bilibili:popular":
        rows=adapters.bili_client().popular(ps=40)
        return [{"bvid":r.get("bvid"),"aid":r.get('aid'),"title":clean(r.get("title"),240),"description":clean(r.get("desc")),
                 "url":"https://www.bilibili.com/video/"+str(r.get("bvid") or ""),"pubdate":r.get("pubdate"),
                 "pic":r.get("pic"),"rank":i+1,"source":"popular",**{k:r.get("stat",{}).get(v) for k,v in (("play","view"),("like","like"),("reply","reply"),("share","share"))}} for i,r in enumerate(rows)]
    if channel=="bilibili:game":
        rows=adapters.bili_client().ranking_game()
        return [{"bvid":r.get("bvid"),"aid":r.get('aid'),"title":clean(r.get("title"),240),"description":clean(r.get("desc")),
                 "url":"https://www.bilibili.com/video/"+str(r.get("bvid") or ""),"pubdate":r.get("pubdate"),
                 "pic":r.get("pic"),"rank":i+1,"source":"ranking_game",
                 **{k:r.get("stat",{}).get(v) for k,v in (("play","view"),("like","like"),("reply","reply"),("share","share"))}} for i,r in enumerate(rows[:40])]
    return adapters.fetch(platform)


def collect(store, channel, *, force=False):
    config=next((c for c in CHANNELS if c["id"]==channel),None)
    if not config:
        raise ValueError("未知渠道")
    previous=store.conn.execute("SELECT * FROM channel_health WHERE channel_id=?",(channel,)).fetchone()
    stamp=now_iso()
    if previous and previous["retry_after"] and previous["retry_after"]>stamp and not force:
        return {"channel_id":channel,"status":"paused","retry_after":previous["retry_after"],"count":0}
    try:
        rows=fetch(channel)
        accepted=[]
        for position, raw in enumerate(rows,1):
            item=normalize({**raw,"observed_at":stamp},config["platform"],"v3:live:"+channel)
            if not item:continue
            if config.get('signal_type')=='news_feed':item['kind']='news'
            metrics=dict(item["metrics"])
            rank=metrics.pop("rank",raw.get("rank",position)) if config.get('signal_type')!='news_feed' else None
            # Board scores/positions belong to a channel, not to a platform-wide counter.
            metrics.pop("hot_score",None)
            store.upsert_evidence({**item,"metrics":metrics})
            locator={k:str(raw[k]) for k in ('aid','group_id') if raw.get(k) and str(raw[k]).isdigit()}
            if locator:store.conn.execute('INSERT INTO source_locator VALUES(?,?) ON CONFLICT(evidence_id) DO UPDATE SET payload=excluded.payload',(item['evidence_id'],dump(locator)))
            store.conn.execute("INSERT OR IGNORE INTO channel_observation VALUES(?,?,?,?,?)",(channel,item["evidence_id"],stamp,rank,dump(item["metrics"])))
            capture(store,item["evidence_id"],raw=raw,channel=channel,observed_at=stamp)
            accepted.append(item["evidence_id"])
        if not accepted:raise ValueError("空响应")
        store.conn.execute("INSERT INTO channel_health VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET attempted_at=excluded.attempted_at,succeeded_at=excluded.succeeded_at,status='ok',count=excluded.count,failures=0,error=NULL,retry_after=NULL",
                          (channel,stamp,stamp,"ok",len(accepted),0,None,None))
        store.conn.commit()
        return {"channel_id":channel,"status":"ok","count":len(accepted),"evidence_ids":accepted}
    except Exception as error:
        store.conn.rollback()
        failures=(previous["failures"] if previous else 0)+1
        retry=(datetime.now(timezone.utc)+timedelta(minutes=min(60,5*2**min(failures-1,4)))).isoformat(timespec="seconds")
        reason=type(error).__name__+": 未获得有效内容"
        store.conn.execute("INSERT INTO channel_health VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET attempted_at=excluded.attempted_at,status='failed',failures=excluded.failures,error=excluded.error,retry_after=excluded.retry_after",
                          (channel,stamp,None,"failed",0,failures,reason,retry))
        store.conn.commit()
        return {"channel_id":channel,"status":"failed","count":0,"error":reason,"retry_after":retry}


def refresh(store):
    rows=[collect(store,c["id"]) for c in CHANNELS]
    return {"channels":rows,"status":"ok" if all(r["status"]=="ok" for r in rows) else "partial"}


def coverage(store):
    result=[]
    for config in CHANNELS:
        row=store.conn.execute("SELECT * FROM channel_health WHERE channel_id=?",(config["id"],)).fetchone()
        capabilities=adapters.CAPABILITIES.get(config['platform'],{'discover':True,'search':False,'read':True,'snapshots':False,'comments':False})
        if config['platform']=='tieba':capabilities={**capabilities,'read':True,'read_scope':'topic_description'}
        if config['platform'] in ('taptap','bilibili'):capabilities={**capabilities,'comments':True,'comment_scope':'bounded_first_page_hot_recent'}
        result.append({**config,"health":dict(row) if row else None,"capabilities":capabilities})
    return result
