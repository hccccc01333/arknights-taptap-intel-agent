"""Research tools: public browser, screenshots and local OCR with evidence provenance."""
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import uuid
from urllib.parse import urlparse
from datetime import datetime,timedelta,timezone

from agent_v2.ingest import normalize,timestamp
from agent_v2.store import ROOT,dump,now_iso

ARTIFACT_ROOT=ROOT/'data'/'v3'/'research_captures'
BLOCKED=('验证码','安全验证','人机验证','访问过于频繁','Access Denied','verify you are human')


def public_url(url):
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):raise ValueError('仅支持公开 HTTPS 网页')
    addresses=socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('网页地址不属于公网')
    return url


def node_path():
    explicit=os.environ.get('V3_BROWSER_NODE')
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    found=explicit or (str(bundled) if bundled.is_file() else shutil.which('node'))
    if not found:raise RuntimeError('浏览器工具缺少 Node.js')
    return found


def ocr(image_path):
    executable=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    if not executable.is_file():return {'status':'unavailable','text':'','lines':[],'error':'未安装 Windows OCR 运行环境'}
    try:
        result=subprocess.run([str(executable),'-NoProfile','-NonInteractive','-File',str(Path(__file__).with_name('windows_ocr.ps1')),
            '-ImagePath',str(Path(image_path).resolve())],capture_output=True,text=True,encoding='utf-8-sig',timeout=18)
        if result.returncode:raise RuntimeError('OCR 运行失败')
        data=json.loads(result.stdout.strip())
        data['raw_text']=data.get('text','')
        data['text']=re.sub(r'(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])','',data.get('text',''))
        for line in data.get('lines',[]):
            line['raw_text']=line['text'];line['text']=re.sub(r'(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])','',line['text'])
        return data
    except (OSError,ValueError,subprocess.SubprocessError):return {'status':'unavailable','text':'','lines':[],'error':'截图识别未完成'}


def browse(url,*,visual=False,region='article'):
    if region not in ('article','comments'):raise ValueError('未知浏览器阅读区域')
    public_url(url);capture_id='capture_'+uuid.uuid4().hex
    folder=ARTIFACT_ROOT/capture_id;folder.mkdir(parents=True,exist_ok=False)
    try:
        result=subprocess.run([node_path(),str(Path(__file__).with_name('browser_worker.cjs'))],
            input=dump({'url':url,'visual':visual,'region':region,'output_dir':str(folder)}),capture_output=True,text=True,encoding='utf-8',timeout=55)
        data=json.loads(result.stdout);data.update(capture_id=capture_id,captured_at=now_iso(),tool='browser_comments' if region=='comments' else 'browser_visual' if visual else 'browser_read')
        public_url(data.get('resolved_url',url));frames=[]
        for frame in data.get('captures',[])[:3]:
            file=Path(frame['file']).resolve()
            if file.parent!=folder.resolve() or file.suffix!='.png':raise ValueError('截图路径不合法')
            recognized=ocr(file) if visual and data['status']=='ok' else {'status':'not_requested','text':'','lines':[]}
            frames.append({**frame,'file':file.name,'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'ocr':recognized})
        data['captures']=frames
        data['comment_cards']=transcribe_cards(data.get('comment_cards',[]),frames)
        for i,frame in enumerate(frames):
            frame['comment_transcript']='\n'.join(c['text'] for c in data['comment_cards'] if c['frame_index']==i)
        # OCR remains an imperfect transcription, never a substitute for blocked access.
        text='\n'.join(dict.fromkeys(line['text'] for f in frames for line in f['ocr'].get('lines',[])))
        data['ocr_text']=text[:10000];data['ocr_status']='ok' if text else 'unavailable' if visual else 'not_requested'
        (folder/'capture.json').write_text(dump(data),encoding='utf-8')
        return data
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        data={'status':'failed','capture_id':capture_id,'captured_at':now_iso(),'error':type(error).__name__,'captures':[]}
        (folder/'capture.json').write_text(dump(data),encoding='utf-8');return data


def transcribe_cards(cards,frames):
    """OCR only inside a located comment body. No header/date/author inference."""
    result=[];seen=set()
    for card in cards[:36]:
        index=card.get('frame_index');box=card.get('bbox')
        if not isinstance(index,int) or not 0<=index<len(frames) or not isinstance(box,dict):continue
        text=card.get('text','').strip();method='browser_visible'
        if not text:
            lines=[]
            for line in frames[index].get('ocr',{}).get('lines',[]):
                words=[w['text'] for w in line.get('words',[]) if
                    box['x']<=w['x']+w['width']/2<=box['x']+box['width'] and box['y']<=w['y']+w['height']/2<=box['y']+box['height']]
                if words:lines.append(' '.join(words))
            text=re.sub(r'(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])','', '\n'.join(lines));method='screenshot_ocr'
        key=re.sub(r'\s+','',text)
        if not key or key in seen:continue
        seen.add(key);result.append({**card,'text':text[:2400],'method':method})
        if len(result)>=12:break
    return result


def read_comments(store,topic_id,evidence_id):
    from .discussion import capability,record_capability,save_samples
    parent=store.evidence([evidence_id])[0];key='comments_visual:'+evidence_id
    old=capability(store,key)
    if old and old['retry_at']>now_iso():
        saved=json.loads(old['payload']).get('evidence_ids',[])
        with store.conn:
            for eid in saved:
                store.conn.execute("INSERT OR IGNORE INTO research_link VALUES(?,?,'discussion','direct',?,?)",(topic_id,eid,'复用已定位评论区的有限样本，保留原截图与未知日期',now_iso()))
        return {'calls':0,'status':'cached' if old['status']=='ok' else 'deferred','evidence_ids':saved}
    try:data=browse(parent['url'],visual=True,region='comments')
    except Exception as error:return {'calls':0,'status':'failed','error':type(error).__name__}
    rows=[]
    if data['status']=='ok' and data.get('comment_region',{}).get('status')=='found':
        for card in data.get('comment_cards',[])[:12]:
            if not card.get('text','').strip():continue
            rows.append({'id':card.get('id') or 'visible_'+hashlib.sha256(re.sub(r'\s+','',card['text']).encode()).hexdigest()[:24],
                'text':card['text'],'published_at':timestamp(card.get('published_at')),'author_hash':None,'likes':0,
                'method':card['method'],'provenance':{'capture_id':data['capture_id'],'frame_index':card['frame_index'],
                    'bbox':card['bbox'],'date_text':card.get('date_text',''),'date_unverified':True,
                    'note':'公开页面有限可见样本；未知日期和作者不用于证明旧内容翻红；OCR 转录需对照截图'}})
    with store.conn:
        store.conn.execute('INSERT INTO research_capture VALUES(?,?,?,?,?,?)',
            (data['capture_id'],topic_id,evidence_id,now_iso(),data['status'],dump(data)))
        ids=save_samples(store,topic_id,parent,rows,'browser_visible') if rows else []
    status='ok' if ids else data.get('comment_region',{}).get('status',data['status'])
    result={'calls':1,'status':status,'capture_id':data['capture_id'],'evidence_ids':ids,'saved':len(ids),
        'note':'先定位评论区再滚动截图；无可靠评论边界时只保留缺口，不生成评论'}
    record_capability(store,key,'ok' if ids else 'failed',payload=result,error=None if ids else status)
    return result


def read(store,topic_id,evidence_id,*,visual=False):
    from .discussion import capability,record_capability
    source=store.evidence([evidence_id])[0];key=('browser_visual:' if visual else 'browser_read:')+evidence_id
    old=capability(store,key)
    if old and old['retry_at']>now_iso():return {'calls':0,'status':'cached' if old['status']=='ok' else 'deferred','evidence_ids':[evidence_id]}
    try:data=browse(source['url'],visual=visual)
    except Exception as error:return {'calls':0,'status':'failed','error':type(error).__name__}
    status=data['status'];body=data.get('body','').strip();transcript=data.get('ocr_text','').strip()
    if status=='ok' and len(body)<80 and len(transcript)<40:status='insufficient'
    with store.conn:
        store.conn.execute('INSERT INTO research_capture VALUES(?,?,?,?,?,?)',
            (data['capture_id'],topic_id,evidence_id,now_iso(),status,dump(data)))
        if status=='ok':
            combined=(body[:2000]+'\n[截图 OCR 转录，需对照原图核查]\n'+transcript[:8000]) if transcript else body[:10000]
            item={**source,'body':combined,'last_seen_at':now_iso(),'source_path':'v3:read_source:browser','metrics':{}}
            # A browser's meta date is evidence metadata, not silently an event date.
            store.upsert_evidence(item)
            version=store.snapshot(evidence_id);meta={k:v for k,v in data.items() if k not in ('body','ocr_text','links')}
            meta.update(scope='browser_ocr_excerpt' if transcript else 'browser_page_excerpt',comments_read=False,
                ocr_warning='OCR 可能错字；引用可通过截图与坐标复核',publication_meta_unverified=data.get('published_at'))
            store.conn.execute('INSERT INTO source_read_meta VALUES(?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET source_version=excluded.source_version,payload=excluded.payload',
                (evidence_id,version,dump(meta)))
            retry=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat(timespec='seconds')
            store.conn.execute('INSERT INTO source_read VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET status=excluded.status,attempted_at=excluded.attempted_at,succeeded_at=excluded.succeeded_at,retry_at=excluded.retry_at,error=NULL,source_version=excluded.source_version,scope=excluded.scope,characters=excluded.characters',
                (evidence_id,'ok',now_iso(),now_iso(),retry,None,version,meta['scope'],len(combined)))
    result={'calls':1,'status':status,'capture_id':data['capture_id'],'evidence_ids':[evidence_id],
        'dom_characters':len(body),'ocr_characters':len(transcript),'ocr_status':data.get('ocr_status'),
        'note':'有限可见区域，OCR 转录需核查；未取得完整评论','captures':len(data.get('captures',[]))}
    record_capability(store,key,'ok' if status=='ok' else 'failed',payload=result,error=None if status=='ok' else status)
    return result


def search_browser(query):
    from urllib.parse import urlencode
    data=browse('https://www.bing.com/search?'+urlencode({'q':query}),visual=False)
    if data['status']!='ok':raise ValueError('浏览器检索未取得结果')
    return [{'title':r['title'],'url':r['url'],'description':'','published_at':None,'source':'search:browser',
        'search_capture_id':data['capture_id']} for r in data.get('links',[]) if r.get('title')][:6]
