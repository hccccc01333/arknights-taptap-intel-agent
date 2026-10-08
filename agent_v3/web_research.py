"""Public web search and bounded article reads; search matches remain unverified."""
import re
from datetime import datetime,timedelta,timezone
import xml.etree.ElementTree as ET
from urllib.parse import urlencode,urlparse
from bs4 import BeautifulSoup

from agent_v2.ingest import clean,timestamp,normalize
from agent_v2.store import now_iso,dump,stable_id
from .public_sources import read_public,allowed_url
from .materials import capture

ARTICLE_HOSTS={'www.chinanews.com.cn','www.chinanews.com','www.news.cn','www.xinhuanet.com',
    'www.people.com.cn','society.people.com.cn','culture.people.com.cn','ent.people.com.cn',
    'news.sina.com.cn','finance.sina.com.cn','games.sina.com.cn','www.thepaper.cn',
    'www.gamersky.com','www.3dmgame.com','www.taptap.cn','www.bilibili.com',
    'www.mihoyo.com','www.hypergryph.com','www.bbc.com','www.reuters.com'}


def search(query):
    if not isinstance(query,str) or not 2<=len(query.strip())<=80:raise ValueError('检索词须为 2 至 80 字')
    raw,_=read_public('https://www.bing.com/search?'+urlencode({'q':query,'format':'rss'}),{'www.bing.com'},max_bytes=256000,timeout=18)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('搜索响应包含不支持的 XML 声明')
    root=ET.fromstring(raw)
    if root.tag!='rss':raise ValueError('公开搜索未返回可解析结果')
    result=[]
    for node in root.findall('./channel/item')[:6]:
        url=(node.findtext('link') or '').strip();host=urlparse(url).hostname
        if not host or not allowed_url(url,{host}):continue
        title=clean(node.findtext('title'),240)
        if title:result.append({'title':title,'url':url,'description':clean(node.findtext('description'),1000),
            'published_at':None,'search_date_unverified':node.findtext('pubDate'),'source':'search:bing'})
    return result


def article(url):
    if not allowed_url(url,ARTICLE_HOSTS):raise ValueError('该网页尚未接入正文读取，保留搜索摘要')
    raw,resolved=read_public(url,ARTICLE_HOSTS,max_bytes=1024*1024,timeout=18)
    soup=BeautifulSoup(raw,'html.parser')
    for tag in soup.select('script,style,nav,footer,header,form,aside'):tag.decompose()
    container=soup.select_one('article,#artibody,#articleContent,.article-content,.content-detail,main') or soup
    paragraphs=[p.get_text(' ',strip=True) for p in container.find_all('p')]
    paragraphs=[p for p in paragraphs if len(p)>=25 and not re.search(r'版权所有|ICP备|点击登录|隐私政策',p)]
    body='\n'.join(dict.fromkeys(paragraphs))
    if len(body)<80 or any(s in body[:300] for s in ('请输入验证码','访问过于频繁','Access Denied')):
        raise ValueError('网页未取得可靠正文，未计为已读')
    return {'body':body[:6000],'resolved_url':resolved,'content_truncated':len(body)>6000,
        'scope':'article_excerpt','comments_read':False}


def background(store,topic_id,query,*,max_calls=3):
    from .discussion import capability,record_capability
    key='web_search:'+stable_id('',query);old=capability(store,key)
    if old and old['retry_at']>now_iso():
        ids=json_ids(old['payload'])
        with store.conn:
            for eid in ids:store.conn.execute("INSERT OR IGNORE INTO research_link VALUES(?,?,'background','unverified',?,?)",
                (topic_id,eid,'复用公开搜索结果，是否同一事件仍须核对',now_iso()))
        return {'calls':0,'status':'cached' if old['status']=='ok' else 'deferred','evidence_ids':ids,'query':query}
    if max_calls<1:return {'calls':0,'status':'budget_exhausted','evidence_ids':[]}
    calls=1;ids=[];reads=[]
    try:
        rows=search(query)
        # Bing RSS can silently return unrelated fallback results. Do not feed them as evidence.
        tokens=set(re.findall(r'[a-zA-Z0-9]{3,}|[\u4e00-\u9fff]{2,}',query.lower()))
        def relevant(row):
            value=(row['title']+' '+row.get('description','')).lower()
            return not tokens or any(t in value or len(t)>3 and any(t[i:i+3] in value for i in range(len(t)-2)) for t in tokens)
        rows=[r for r in rows if relevant(r)]
        if not rows and calls<max_calls:
            from .browser_tools import search_browser
            calls+=1;rows=[r for r in search_browser(query) if relevant(r)]
        for raw in rows:
            host=urlparse(raw['url']).hostname
            item=normalize({**raw,'observed_at':now_iso()},'web:'+str(host),'v3:research:web_search')
            if not item:continue
            with store.conn:
                store.upsert_evidence(item)
                store.conn.execute("INSERT OR IGNORE INTO research_link VALUES(?,?,'background','unverified',?,?)",
                    (topic_id,item['evidence_id'],'公开搜索命中，不证明事件相同、热度或发布日期',now_iso()))
            ids.append(item['evidence_id'])
            if host in ARTICLE_HOSTS and calls<max_calls:
                calls+=1
                try:
                    detail=article(raw['url'])
                    with store.conn:
                        store.upsert_evidence({**item,'body':detail['body'],'source_path':'v3:read_source:web'})
                        store.conn.execute('INSERT INTO source_read_meta VALUES(?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET source_version=excluded.source_version,payload=excluded.payload',
                            (item['evidence_id'],store.snapshot(item['evidence_id']),dump({**detail,'requested_url':raw['url']})))
                        stamp=now_iso();retry=(datetime.now(timezone.utc)+timedelta(hours=6)).isoformat(timespec='seconds')
                        store.conn.execute('INSERT INTO source_read VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET status=excluded.status,attempted_at=excluded.attempted_at,succeeded_at=excluded.succeeded_at,retry_at=excluded.retry_at,error=NULL,source_version=excluded.source_version,scope=excluded.scope,characters=excluded.characters',
                            (item['evidence_id'],'ok',stamp,stamp,retry,None,store.snapshot(item['evidence_id']),'article_excerpt',len(detail['body'])))
                    reads.append({'evidence_id':item['evidence_id'],'status':'ok'})
                except Exception as error:reads.append({'evidence_id':item['evidence_id'],'status':'failed','error':type(error).__name__})
            with store.conn:capture(store,item['evidence_id'],channel='research:web_search')
        result={'calls':calls,'status':'ok' if ids else 'no_relevant_results','query':query,'evidence_ids':ids,'reads':reads,
            'note':'公开网页搜索；命中和未核实日期不代表热点或事件事实'}
        record_capability(store,key,'ok',payload=result)
        return result
    except Exception as error:
        result={'calls':calls,'status':'failed','query':query,'evidence_ids':ids,'error':type(error).__name__}
        record_capability(store,key,'failed',error='公开搜索未完成：'+type(error).__name__,payload=result)
        return result


def json_ids(payload):
    import json
    return json.loads(payload).get('evidence_ids',[])
