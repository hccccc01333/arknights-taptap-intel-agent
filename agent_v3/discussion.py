"""Traceable, bounded public discussion sampling; no population sentiment claims."""
import hashlib,json,re
from datetime import datetime,timedelta,timezone
from urllib.parse import urlencode

from agent_v2.connectors import legacy,UA
from agent_v2.ingest import normalize,timestamp,clean
from agent_v2.store import dump,now_iso,stable_id
from .public_sources import read_public
from .materials import capture


class DiscussionConfigurationError(ValueError):pass


def capability(store,key):
    row=store.conn.execute('SELECT * FROM research_capability WHERE capability=?',(key,)).fetchone()
    return dict(row) if row else None


def provider_blocked(store,platform):
    row=capability(store,'discussion:'+platform)
    # A 400 on one sort mode does not mean every public comment mode failed.
    # Keep the failure, but apply its backoff to that mode rather than all posts.
    return row if row and row['status']=='failed' and row['retry_at']>now_iso() and 'HTTP 400' not in (row['error'] or '') else None


def needs_sampling(store,evidence_id,platform):
    if provider_blocked(store,platform):return False
    for method in ('hot','recent'):
        mode=capability(store,'discussion:'+platform+':'+method)
        cached=capability(store,'discussion:'+evidence_id+':'+method)
        if mode and mode['status']=='failed' and mode['retry_at']>now_iso():continue
        if not cached or cached['retry_at']<=now_iso():return True
    return False


def needs_visual(store,evidence_id):
    cached=capability(store,'comments_visual:'+evidence_id)
    if cached and cached['retry_at']>now_iso():return False
    return not store.conn.execute('SELECT 1 FROM discussion_sample WHERE parent_evidence_id=? LIMIT 1',(evidence_id,)).fetchone()


def quality_flags(text):
    flags=[]
    if re.search(r'互赞|回赞|有赞必回|求赞',text):flags.append('engagement_exchange')
    # A short concrete request can be useful; laughter and emoji add no claim.
    stripped=re.sub(r'\[[^\]]{1,30}\]|\s|[\W_]', '', text)
    if len(stripped)<2 or re.fullmatch(r'(哈|呵|嘿|啊|哦|嗯|好|顶|赞|支持|加油|666|233|笑死|哈哈)+',stripped):
        flags.append('low_information')
    if re.fullmatch(r'[A-Za-z0-9_-]{6,64}',text.strip()) or re.fullmatch(r'https?://\S+',text.strip()):flags.append('code_or_link_only')
    if re.search(r'邀请码|邀请帮|帮我助力|求助力|填.{0,4}码|输入.{0,4}邀请码',text):flags.append('referral_solicitation')
    return flags


def record_capability(store,key,status,*,error=None,payload=None):
    old=capability(store,key);stamp=now_iso()
    retry=(datetime.now(timezone.utc)+timedelta(minutes=60 if status=='ok' else 30)).isoformat(timespec='seconds')
    success=stamp if status=='ok' else old['succeeded_at'] if old else None
    with store.conn:store.conn.execute('''INSERT INTO research_capability VALUES(?,?,?,?,?,?,?) ON CONFLICT(capability)
      DO UPDATE SET status=excluded.status,attempted_at=excluded.attempted_at,succeeded_at=excluded.succeeded_at,
      retry_at=excluded.retry_at,error=excluded.error,payload=excluded.payload''',(key,status,stamp,success,retry,error,dump(payload or {})))


def fetch_page(store,parent,method):
    platform=parent['platform'];locator=store.conn.execute('SELECT payload FROM source_locator WHERE evidence_id=?',(parent['evidence_id'],)).fetchone()
    locator=json.loads(locator[0]) if locator else {}
    if platform=='taptap':
        module=legacy('discussion_tap','taptap/crawl_taptap_community.py')
        try:xua=module.load_xua()
        except SystemExit:raise DiscussionConfigurationError('TapTap 公开客户端参数未配置') from None
        params={'X-UA':xua,'moment_id':parent['external_id'],'sort':'rank' if method=='hot' else 'time',
                'order':'desc','regulate_all':'false','limit':20,'from':0}
        if locator.get('group_id'):params['group_id']=locator['group_id']
        content,_=read_public(module.COMMENT_URL+'?'+urlencode(params),{'www.taptap.cn'},
           headers={**UA,'Accept':'application/json, text/plain, */*','Origin':'https://www.taptap.cn','Referer':parent['url']})
        value=json.loads(content)
        if value.get('success') is not True or not isinstance(value.get('data'),dict):raise ValueError('评论接口未返回有效数据')
        data=value['data'];parsed=[]
        for raw in (data.get('list') or [])[:20]:
            row=module.parse_comment(raw,parent['external_id'],now_iso())
            author=module._author_obj(raw) or module._author_obj(raw.get('comment') or {})
            author_id=str(author.get('id') or author.get('user_id') or '')
            if row:parsed.append({'id':row['comment_id'],'text':row['content'],
                                 'author_hash':hashlib.sha256(('taptap:'+author_id).encode()).hexdigest()[:24] if author_id else None,
                                 'published_at':timestamp(row['publish_time']),'likes':row['supports']})
    elif platform=='bilibili':
        if not locator.get('aid'):raise ValueError('缺少已验证视频编号，未请求评论')
        url='https://api.bilibili.com/x/v2/reply?'+urlencode({'type':1,'oid':locator['aid'],'pn':1,'ps':20,'sort':2 if method=='hot' else 0})
        content,_=read_public(url,{'api.bilibili.com'},headers=UA)
        value=json.loads(content)
        if value.get('code')!=0 or not isinstance(value.get('data'),dict):raise ValueError('评论接口未返回有效数据')
        data=value['data'];parsed=[]
        for raw in (data.get('replies') or [])[:20]:
            mid=str((raw.get('member') or {}).get('mid') or '')
            parsed.append({'id':str(raw.get('rpid') or ''),'text':(raw.get('content') or {}).get('message') or '',
              'author_hash':hashlib.sha256(('bilibili:'+mid).encode()).hexdigest()[:24] if mid else None,
              'published_at':timestamp(raw.get('ctime')),'likes':raw.get('like',0)})
    else:raise ValueError('此来源尚无公开讨论连接器')
    return parsed,data.get('total',(data.get('page') or {}).get('count'))


def save_samples(store,topic_id,parent,rows,method):
    accepted=[];stamp=now_iso()
    for row in rows[:20]:
        sample_method=row.get('method',method)
        text=clean(row.get('text'),2400);cid=str(row.get('id') or '')
        if not cid or not text:continue
        item=normalize({'title':parent['title']+' · 评论样本','item_id':parent['external_id']+':'+cid,
          'description':text,'url':parent['url'],'published_at':row.get('published_at'),'observed_at':stamp,
          'like':row.get('likes',0)},parent['platform'],'v3:discussion')
        item['kind']='comment';store.upsert_evidence(item)
        if row.get('provenance'):
            scope='comment_ocr_sample' if sample_method=='screenshot_ocr' else 'comment_sample'
            store.conn.execute('INSERT INTO source_read_meta VALUES(?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET source_version=excluded.source_version,payload=excluded.payload',
                (item['evidence_id'],store.snapshot(item['evidence_id']),dump({**row['provenance'],'scope':scope,'requested_url':parent['url'],'method':sample_method,'comments_read':True})))
        digest=stable_id('comment_text_',re.sub(r'\s+','',text))
        flags=quality_flags(text)
        canonical=store.conn.execute('SELECT evidence_id FROM discussion_sample WHERE text_hash=? AND parent_evidence_id=? ORDER BY observed_at,evidence_id LIMIT 1',(digest,parent['evidence_id'])).fetchone()
        if canonical and canonical[0]!=item['evidence_id']:flags.append('duplicate_text')
        store.conn.execute('''INSERT INTO discussion_sample VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(evidence_id)
          DO UPDATE SET text_hash=excluded.text_hash,flags=excluded.flags,author_hash=excluded.author_hash,
          published_at=excluded.published_at''',
          (item['evidence_id'],parent['evidence_id'],cid,parent['platform'],row.get('author_hash'),digest,dump(flags),item['published_at'],stamp))
        store.conn.execute('INSERT OR IGNORE INTO discussion_observation VALUES(?,?,?)',(item['evidence_id'],sample_method,stamp))
        store.conn.execute('''INSERT INTO research_link VALUES(?,?,'discussion','direct',?,?) ON CONFLICT(topic_id,evidence_id) DO NOTHING''',
          (topic_id,item['evidence_id'],'来自已保存帖子的一级评论，有限抽样',stamp))
        capture(store,item['evidence_id'],channel='discussion:'+parent['platform']+':'+method,observed_at=stamp)
        accepted.append(item['evidence_id'])
    store.conn.commit();return accepted


def sample(store,topic_id,evidence_id,*,max_calls=2):
    parent=store.evidence([evidence_id])[0];platform=parent['platform'];results=[];calls=0
    if platform not in ('bilibili','taptap'):return {'calls':0,'status':'unsupported','results':[]}
    provider=provider_blocked(store,platform)
    if provider:return {'calls':0,'status':'deferred','results':[],'reason':provider['error']}
    for method in ('hot','recent'):
        if calls>=max(0,min(2,max_calls)):break
        mode=capability(store,'discussion:'+platform+':'+method)
        if mode and mode['status']=='failed' and mode['retry_at']>now_iso():continue
        key='discussion:'+evidence_id+':'+method;previous=capability(store,key)
        if previous and previous['retry_at']>now_iso():
            for eid in json.loads(previous['payload']).get('evidence_ids',[]):
                with store.conn:store.conn.execute("INSERT OR IGNORE INTO research_link VALUES(?,?,'discussion','direct',?,?)",
                    (topic_id,eid,'复用此帖真实评论样本，仍按原采样范围解释',now_iso()))
            continue
        calls+=1
        try:
            rows,total=fetch_page(store,parent,method)
            ids=save_samples(store,topic_id,parent,rows,method)
            result={'parent_evidence_id':evidence_id,'method':method,'status':'ok','returned':len(rows),
                    'saved':len(ids),'evidence_ids':ids,'provider_total':total,
                    'sampling_note':'一级评论第一页，按热门/时间抽样；不代表总体，不包含全部楼中楼'}
            record_capability(store,key,'ok',payload=result)
            record_capability(store,'discussion:'+platform,'ok',payload={'methods':[method]})
        except Exception as failure:
            code=getattr(getattr(failure,'response',None),'status_code',None)
            error='评论读取失败：'+type(failure).__name__+((' HTTP '+str(code)) if code else '')
            if isinstance(failure,DiscussionConfigurationError):error='TapTap 公开客户端参数未配置，讨论任务等待配置恢复'
            result={'parent_evidence_id':evidence_id,'method':method,'status':'failed','error':error}
            record_capability(store,key,'failed',error=error)
            record_capability(store,'discussion:'+platform+(':'+method if code==400 else ''),'failed',error=error)
            results.append(result);break
        results.append(result)
    return {'calls':calls,'status':'partial' if any(r['status']=='failed' for r in results) else 'ok' if results else 'cached','results':results}


def read_samples(store,topic_id,limit=20):
    rows=store.conn.execute('''SELECT d.* FROM discussion_sample d JOIN research_link r USING(evidence_id)
      WHERE r.topic_id=? AND r.role='discussion' ORDER BY d.observed_at DESC LIMIT ?''',(topic_id,min(40,limit))).fetchall()
    result=[]
    for row in rows:
        e=store.evidence([row['evidence_id']])[0]
        flags=list(dict.fromkeys([f for f in json.loads(row['flags']) if f not in ('low_information','duplicate_text')]+quality_flags(e['body'])))
        canonical=store.conn.execute('SELECT evidence_id FROM discussion_sample WHERE text_hash=? AND parent_evidence_id=? ORDER BY observed_at,evidence_id LIMIT 1',
                                    (row['text_hash'],row['parent_evidence_id'])).fetchone()
        if canonical and canonical[0]!=row['evidence_id']:flags.append('duplicate_text')
        result.append({**e,'sample':{**dict(row),'flags':flags,'classification_version':'discussion-quality-v3.11',
            'methods':[r[0] for r in store.conn.execute('SELECT DISTINCT method FROM discussion_observation WHERE evidence_id=?',(row['evidence_id'],))]}})
    return result
