"""Publish curated V3 results, never the runtime database or model traces."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from agent_v2.store import ROOT, dump, now_iso

PRIVATE = {'actor','user_name','user_id','author','author_id','avatar_url','source_path',
    'reading_metadata','content_hash','reasoning_content','reasoning_details','reasoning',
    'context','context_version','api_key','password','token','access_token','authorization',
    'cookie','credentials','raw_response','raw_content','trace','error','body_excerpt','body',
    'raw','headers','reasoning_details','session_id','external_id'}

def clean(value):
    if isinstance(value,dict):
        return {k:clean(v) for k,v in value.items() if k.lower() not in PRIVATE and
                not any(part in k.lower() for part in ('secret','credential','api_key','token','cookie','password','reasoning_content'))}
    if isinstance(value,list):return [clean(v) for v in value]
    if isinstance(value,str):
        value=re.sub(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/](?![\\/])[^\s\n]*','[本地路径]',value)
        value=re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,})\b','[已隐藏]',value)
        value=re.sub(r'\b1[3-9]\d{9}\b','[联系方式]',value)
        value=re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}','[联系方式]',value)
        return value
    return value

def project(value,fields):
    return clean({k:value[k] for k in fields.split() if k in value})

def source(value):
    result=project(value,'evidence_id title platform published_at content_scope scope url')
    try:parts=urlsplit(value.get('url') or '')
    except ValueError:
        result.pop('url',None);return result
    if parts.scheme not in ('https','http') or not parts.netloc or parts.username:
        result.pop('url',None)
    else:
        from urllib.parse import parse_qsl,urlencode
        query=urlencode([(k,v) for k,v in parse_qsl(parts.query) if k.lower() in ('id','aid','bvid','article_id','p')])
        result['url']=urlunsplit((parts.scheme,parts.netloc,parts.path,query,''))
    result['public_projection']=True
    return result

def quotes(value):
    """Only short verification excerpts; complete source text stays local."""
    if isinstance(value,dict):
        return {k:(v[:120] if k=='quote' and isinstance(v,str) else quotes(v)) for k,v in value.items()}
    if isinstance(value,list):return [quotes(v) for v in value]
    return value

def signal(value):
    result=project(value,'signal_id topic_id fingerprint current_fingerprint created_at risk_assessment')
    result['payload']=quotes(project(value.get('payload',{}),
        'title category platform game_context observed_change why_it_matters hypothesis next_watch facts validation'))
    return result

def material(value):
    result=project(value,'material_id kind origin title content rights_status evidence_ids source_versions application')
    # Delivery body is generated, editable product content, not source text.
    delivery=value.get('application',{}).get('delivery') if value.get('application') else None
    if delivery:result['application']['delivery']['body']=clean(delivery['body'])
    if result.get('origin')=='source':result['content']=result.get('content','')[:180]
    return quotes(result)

def creative(value):
    result=project(value,'creative_id event_id created_at risk_assessment')
    result['payload']=project(value.get('payload',{}),
        'title category platform audience growth_goal hook distribution placement user_action journey copy steps '
        'timing resources growth_hypothesis prerequisites validation_plan measurement risks deliverables')
    result['payload']['deliverables']=[material(m) for m in result['payload'].get('deliverables',[])]
    return quotes(result)

def interpretation(value):
    result=project(value,'topic_id fingerprint created_at updated_at')
    result['payload']=quotes(project(value.get('payload',{}),
        'status headline one_line background core timeline views controversies unknowns heat_evidence '
        'freshness_assessment risk_assessment discussion_review'))
    from .connectors import CHANNELS
    domains={c['id']:c.get('domains',[c['domain']]) for c in CHANNELS}
    for entry in result['payload'].get('heat_evidence',[]):
        entry['domains']=domains.get(entry.get('channel_id'),[])
    return result

def topic(store,tid):
    from .discovery import read_topic
    value=read_topic(store,tid);outputs=value.get('business_outputs',{})
    result=project(value,'topic_id fingerprint title')
    result.update(public_projection=True,interpretation=interpretation(value['interpretation']) if value.get('interpretation') else None,
        evidence=[source(s) for s in value.get('evidence',[])],research_sources=[],discussion_samples=[],research_captures=[],
        followups=[project(f,'followup_id question status attempts retry_at updated_at') for f in value.get('followups',[]) if f['status']!='superseded'],
        tracked_events=[{**project(e,'tracked_id title note'), 'timeline':[
            {**project(n,'change_id kind observed_at'),'payload':project(n.get('payload',{}),'note title published_at')}
            for n in e.get('timeline',[])]} for e in value.get('tracked_events',[])],
        business_outputs={**quotes(project(outputs,'risk_assessment assessments opportunity judged_at jobs')),
            'game_signals':[signal(s) for s in outputs.get('game_signals',[])],
            'materials':[material(m) for m in outputs.get('materials',[])],
            'creatives':[creative(c) for c in outputs.get('creatives',[])]})
    return result

def snapshot(store):
    from .presentation import classify_outputs,material_ready
    # Read a coherent SQLite snapshot while the scheduler may be writing.
    store.conn.execute('BEGIN')
    try:
        raw=classify_outputs(store.overview())
        materials=[material(m) for m in store.usable_materials(limit=100) if material_ready(m)]
        hotspots=[interpretation(h) for h in raw['hotspots']]
        signals=[signal(s) for s in raw['game_signals']]
        creatives=[creative(c) for c in raw['creatives']]
        game=project(raw['game_coverage'],'published_recent_sources interpreted by_platform note')
        game['briefs']=[{**interpretation(b),**project(b,'kind risk_assessment')} for b in raw['game_coverage'].get('briefs',[])]
        stamp=now_iso()
        latest=store.conn.execute('SELECT MAX(stamp) FROM (SELECT created_at AS stamp FROM topic_intelligence UNION ALL SELECT created_at FROM creative)').fetchone()[0]
        overview={'version':raw['version'],'counts':{'hotspots':len(hotspots),'game_signal':len(signals),'usable_materials':len(materials)},
            'hotspots':hotspots,'game_signals':signals,'materials':materials,'creatives':creatives,'game_coverage':game,
            'latest_delivery_at':latest,'publication':{'generated_at':stamp,'mode':'public_results','worker':'local',
                'automatic_enabled':json.loads(store.conn.execute("SELECT value FROM settings WHERE key='schedule'").fetchone()[0]).get('enabled',False) if store.conn.execute("SELECT value FROM settings WHERE key='schedule'").fetchone() else False,
                'note':'真实产出的公开整理版；完整原文、评论、截图和运行记录保存在本地。'}}
        ids={h['topic_id'] for h in hotspots+game['briefs']}|{s['topic_id'] for s in signals}
        events={}
        for c in raw['creatives']:
            e=store.get_event(c['event_id'])
            if not e:continue
            result=project(e,'event_id title risk_assessment')
            basis=store.creative_basis(c)
            result['assessment']=quotes(project(basis,'topic_id topic_fingerprint summary unknowns'))
            result['source_snapshots']=[source(s) for s in e.get('source_snapshots',[])]
            result['versions']=[{'created_at':v.get('created_at'),'assessment':project(v.get('assessment',{}),'summary')} for v in e.get('versions',[])]
            events[c['event_id']]=result
            if basis.get('topic_id'):ids.add(basis['topic_id'])
        return {'schema':'v3-public-results-1','generated_at':stamp,'overview':overview,
            'topics':{tid:topic(store,tid) for tid in sorted(ids)},'events':events}
    finally:store.conn.rollback()

def gh(*args,body=None):
    result=subprocess.run(['gh',*args],input=dump(body) if body is not None else None,
        capture_output=True,text=True,encoding='utf-8',timeout=60)
    if result.returncode:raise RuntimeError('GitHub 同步失败（退出码 '+str(result.returncode)+'）')
    return json.loads(result.stdout) if result.stdout.strip() else {}

def publish(store,config):
    repo=config['repo'];branch=config.get('branch','site-data')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or branch!='site-data':
        raise ValueError('公开发布目标配置无效')
    data=snapshot(store)
    # Timestamp is a heartbeat. Compare actual business content to avoid
    # committing an unchanged copy every five minutes.
    content=json.loads(dump(data));content.pop('generated_at')
    content['overview']['publication'].pop('generated_at')
    digest=hashlib.sha256(dump(content).encode()).hexdigest()
    previous=store.conn.execute("SELECT value FROM settings WHERE key='public_site_state'").fetchone()
    state=json.loads(previous[0]) if previous else {}
    heartbeat=state.get('published_at','')<(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(timespec='seconds')
    if state.get('digest')==digest and not heartbeat:return {'status':'unchanged'}
    path='public-results.json';endpoint=f'repos/{repo}/contents/{path}'
    old=gh('api',endpoint+'?ref='+branch)
    body={'branch':branch,'message':'chore(site): sync curated V3 results '+now_iso(),
        'content':base64.b64encode(dump(data).encode('utf-8')).decode('ascii'),'sha':old['sha']}
    result=gh('api','--method','PUT',endpoint,'--input','-',body=body)
    state={'status':'published','published_at':data['generated_at'],'digest':digest,'commit':result['commit']['sha']}
    with store.conn:store.conn.execute("INSERT OR REPLACE INTO settings VALUES('public_site_state',?)",(dump(state),))
    return state

_guard=threading.Lock()
def tick(store):
    row=store.conn.execute("SELECT value FROM settings WHERE key='public_site'").fetchone()
    if not row:return
    config=json.loads(row[0])
    if not config.get('enabled') or config.get('next_run_at','')>now_iso() or not _guard.acquire(blocking=False):return
    config['next_run_at']=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec='seconds')
    with store.conn:store.conn.execute("UPDATE settings SET value=? WHERE key='public_site'",(dump(config),))
    path=store.conn.execute('PRAGMA database_list').fetchone()[2]
    def worker():
        from .store import Store
        current=None
        try:
            current=Store(path)
            publish(current,config)
        except Exception as error:
            # Never persist command output: it may include credential details.
            if current:
                with current.conn:current.conn.execute("INSERT OR REPLACE INTO settings VALUES('public_site_error',?)",
                    (dump({'at':now_iso(),'type':type(error).__name__}),))
        finally:
            if current:current.close()
            _guard.release()
    threading.Thread(target=worker,name='v3-public-site',daemon=True).start()
