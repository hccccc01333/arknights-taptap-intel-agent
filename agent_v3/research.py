"""Evidence gaps drive bounded research actions, with a model planner when available."""
import json,re
from datetime import datetime,timedelta,timezone

from jsonschema import Draft202012Validator
from agent_v2.ingest import normalize
from agent_v2.store import dump,now_iso,stable_id
from . import enrichment,discussion
from .public_sources import search_news
from .materials import capture

READABLE=('chinanews','tieba','bilibili','gamemedia')
PLAN_SCHEMA={'type':'object','properties':{'reason':{'type':'string','minLength':4,'maxLength':800},
  'actions':{'type':'array','maxItems':4,'items':{'type':'object','properties':{
    'tool':{'type':'string','enum':['read_detail','search_news','search_web','sample_discussion','browse_page','screenshot_ocr','read_comments_visual']},
    'source_ref':{'type':'integer','minimum':0,'maximum':11},'query':{'type':'string','maxLength':80}},
    'required':['tool','source_ref','query'],'additionalProperties':False}}},'required':['reason','actions'],'additionalProperties':False}


def inspect(store,topic):
    direct=topic['evidence'];samples=discussion.read_samples(store,topic['topic_id'],limit=40)
    informative=[e for e in samples if not e['sample']['flags']]
    recent_cutoff=(datetime.now(timezone.utc)-timedelta(hours=72)).isoformat(timespec='seconds')
    known=len({e['sample']['author_hash'] for e in informative if e['sample']['author_hash']})
    context=[e for e in direct if e['content_scope'] in ('article_excerpt','post_excerpt','video_description','browser_page_excerpt','browser_ocr_excerpt') and len(e['body'])>=50]
    background_sources=topic.get('research_sources',[]);linked=len(background_sources)
    return {'context':{'status':'present' if context else 'missing','evidence_ids':[e['evidence_id'] for e in context]},
      'discussion':{'status':'sampled' if informative else 'insufficient' if samples else 'missing',
        'raw_samples':len(samples),'informative_samples':len(informative),'known_authors':known,
        'recent_72h_samples':sum(bool(e['published_at'] and e['published_at']>=recent_cutoff) for e in samples),
        'publication_unknown':sum(not e['published_at'] for e in samples),
        'max_samples_inspected':40,'published_from':min((e['published_at'] for e in samples if e['published_at']),default=None),
        'published_to':max((e['published_at'] for e in samples if e['published_at']),default=None),
        'methods':sorted({m for e in samples for m in e['sample']['methods']}),
        'note':'有限一级评论样本，不代表总体；互赞、纯起哄、代码/链接、邀请推广与重复标记不直接用于需求推断'},
      'background':{'status':'retrieved_unverified' if linked else 'missing','count':linked,
                    'article_excerpts':sum(e['content_scope']=='article_excerpt' for e in background_sources)},
      'material':{'status':'references_only','note':'媒体引用未下载或转录，来源使用条件需核查'}}


def plan(store,topic,model=None,mission=None,feedback=None):
    gaps=inspect(store,topic);sources=(topic['evidence'][:9]+topic.get('research_sources',[])[:3])[:12];actions=[]
    for i,e in enumerate(sources):
        if e['platform'] in READABLE and e['content_scope'] not in ('article_excerpt','video_description','topic_description'):
            actions.append({'tool':'read_detail','source_ref':i,'query':''})
    if gaps['context']['status']=='missing' and gaps['background']['status']=='missing':
        query=re.sub(r'[【】#\[\]，。！？!?,]',' ',topic['title']).strip()[:60]
        if len(query)>=2:actions.append({'tool':'search_news','source_ref':0,'query':query})
    for i,e in enumerate(sources):
        if e['platform'] in ('taptap','bilibili') and discussion.needs_sampling(store,e['evidence_id'],e['platform']):
            actions.append({'tool':'sample_discussion','source_ref':i,'query':''})
        elif e['platform'] in ('taptap','bilibili') and discussion.provider_blocked(store,e['platform']) and discussion.needs_visual(store,e['evidence_id']):
            actions.append({'tool':'read_comments_visual','source_ref':i,'query':''})
    actions.sort(key=lambda a:0 if a['tool']=='sample_discussion' else 1 if a['tool']=='read_detail' else 2)
    value={'reason':'按正文语境、背景与讨论缺口补证据；素材与机会判断继续独立积累','actions':actions[:4]}
    if mission and mission.get('query'):
        preferred=mission.get('preferred_tool','search_web')
        index=next((i for i,e in enumerate(sources) if e['platform'] in ('taptap','bilibili')),None)
        if preferred in ('sample_discussion','read_comments_visual') and index is None:preferred='search_web'
        value['actions']=[{'tool':preferred,'source_ref':index or 0,'query':mission['query'] if preferred=='search_web' else ''},*value['actions']][:4]
    planner='policy_fallback'
    if model is not None and getattr(model,'supports_tasks',False):
        packet={'title':topic['title'],'gaps':gaps,'mission':mission or {},'feedback':feedback or [],'sources':[{k:e[k] for k in ('title','platform','url','content_scope')} for e in sources],
                'tools':{'read_detail':list(READABLE),'sample_discussion':['taptap','bilibili'],
                         'search_news':'官方公开新闻搜索，结果是待核对背景，不是升温证据',
                         'search_web':'公开网页搜索，按预算补读正文；日期与事件归属待核对',
                         'browse_page':'用隔离浏览器加载输入来源，读取动态可见文字并留截图，不登录',
                         'read_comments_visual':'评论接口失败时先定位、滑动到公开评论区，最多三屏截图识别；没有评论边界或要求登录则记录缺口。OCR日期/作者未知，不能证明翻红',
                         'screenshot_ocr':'浏览器有限滚动截图，本地中文 OCR 识别图片/画面文字，保留原图和位置；不能穿透验证码或代表完整评论'}}
        response=model.run_task('research_plan',packet,PLAN_SCHEMA,
           '你是研究子 Agent，根据主 Agent 问题、缺口与上轮工具结果选择最多四个有必要的动作，使用当前sources数组的source_ref。优先用爬虫read_detail获取正文与sample_discussion真实讨论；静态正文不足时browse_page，动态图片文字用screenshot_ocr。评论接口失败或没有样本时可read_comments_visual，先滑动定位评论区再截图识别。搜索命中可在下一轮读取；失败不要反复重试相同动作，改查询或来源。查询只写主题词。不需要则空列表。不要预设热点必须与游戏相关，不把平台简介或 OCR 广告当评论。',timeout_seconds=60)
        Draft202012Validator(PLAN_SCHEMA).validate(response['result']);value=response['result'];planner='model'
    for a in value['actions']:
        if a['source_ref']>=len(sources):raise ValueError('研究计划引用未知来源')
        e=sources[a['source_ref']]
        if a['tool']=='read_detail' and e['platform'] not in READABLE:raise ValueError('研究计划选择未实现正文连接器')
        if a['tool'] in ('sample_discussion','read_comments_visual') and e['platform'] not in ('taptap','bilibili'):raise ValueError('研究计划选择未实现讨论连接器')
    return {**value,'planner':planner,'gaps_before':gaps,'topic_id':topic['topic_id'],'fingerprint':topic['fingerprint'],
            'source_ids':[e['evidence_id'] for e in sources],'model_metadata':response if planner=='model' else None}


def background(store,topic_id,query):
    key='news_search:'+stable_id('',query);previous=discussion.capability(store,key)
    if previous and previous['retry_at']>now_iso():
        saved=json.loads(previous['payload']).get('evidence_ids',[])
        with store.conn:
            for eid in saved:store.conn.execute("INSERT OR IGNORE INTO research_link VALUES(?,?,'background','unverified',?,?)",
                (topic_id,eid,'复用搜索命中，是否同一事件仍待核对',now_iso()))
        return {'calls':0,'status':'cached' if previous['status']=='ok' else 'deferred','evidence_ids':saved,'reason':previous['error'] or '查询结果仍在有效期'}
    try:
        rows=search_news(query);ids=[]
        with store.conn:
            for raw in rows:
                item=normalize({**raw,'observed_at':now_iso()},'chinanews','v3:research:search')
                if not item:continue
                item['kind']='search';store.upsert_evidence(item)
                store.conn.execute("INSERT INTO research_link VALUES(?,?,'background','unverified',?,?) ON CONFLICT(topic_id,evidence_id) DO NOTHING",
                        (topic_id,item['evidence_id'],'搜索命中，是否同一事件仍待核对',now_iso()))
                capture(store,item['evidence_id'],channel='research:news_search');ids.append(item['evidence_id'])
        result={'calls':1,'status':'ok','evidence_ids':ids,'query':query,'note':'真实搜索命中，仅作为待核对背景'}
        discussion.record_capability(store,key,'ok',payload=result);return result
    except Exception as error:
        reason='背景检索失败：'+type(error).__name__;discussion.record_capability(store,key,'failed',error=reason)
        return {'calls':1,'status':'failed','error':reason}


def run(store,*,topic_id=None,max_calls=6,model=None,run_id=None,delegations=None):
    from .discovery import queue,read_topic
    if delegations is not None:topics=[read_topic(store,d['topic_id']) for d in delegations[:3]]
    elif topic_id:topics=[read_topic(store,topic_id)]
    else:
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=48)).isoformat(timespec='seconds')
        ids=[t['topic_id'] for t in queue(store,100,include_reviewed=True)]
        # Public discussion and article sources have reserved discovery slots;
        # they are not displaced by the higher-volume board queues.
        ids.extend(r[0] for r in store.conn.execute('''SELECT DISTINCT t.topic_id FROM topic t JOIN topic_member m USING(topic_id)
          JOIN evidence e USING(evidence_id) WHERE t.eligible=1 AND t.last_seen_at>=? AND m.active=1
          AND e.platform IN ('taptap','bilibili','chinanews') ORDER BY t.last_seen_at DESC LIMIT 80''',(cutoff,)))
        topics=[read_topic(store,tid,include_tracking=False) for tid in dict.fromkeys(ids)]
    # Evidence-gap actions are durable. Source rotation breaks ties within a gap,
    # and previously failed capabilities prevent repeated futile requests.
    plans=[]
    for topic in topics:
        mission=next((d for d in delegations or [] if d['topic_id']==topic['topic_id']),None)
        try:p=plan(store,topic,mission=mission)
        except Exception as error:
            p=plan(store,topic);p['planner_error']=type(error).__name__
        if not p['actions']:continue
        key=stable_id('research_',dump([topic['topic_id'],topic['fingerprint'],'research-v1']))
        old=store.conn.execute('SELECT updated_at FROM research_task WHERE task_id=?',(key,)).fetchone()
        p['task_id']=key;p['previous_at']=old[0] if old else '';plans.append(p)
    plans.sort(key=lambda p:(p['previous_at'],p['gaps_before']['context']['status']!='missing',p['task_id']))
    calls=0;results=[];completed=[]
    # At most three topics per cycle; do not starve a public discussion source
    # just because boards with titles occupy the front of the topic queue.
    selected=[]
    discussion_plans=[p for p in plans if any(a['tool']=='sample_discussion' for a in p['actions'])]
    def discussion_value(p):
        sources=store.evidence(p['source_ids'])
        count=max((o['metrics'].get('comments',0) for e in sources for o in e['observations']),default=0)
        context=max((len(e['body']) for e in sources),default=0)
        return (p['previous_at'],count<=0,context<40,-count,p['task_id'])
    discussion_plans.sort(key=discussion_value)
    if discussion_plans:selected.append(discussion_plans[0])
    for p in plans:
        if p not in selected:selected.append(p)
        if len(selected)>=3:break
    for p in selected:
        if calls>=max_calls:break
        mission=next((d for d in delegations or [] if d['topic_id']==p['topic_id']),None)
        if model is not None and getattr(model,'supports_tasks',False) and not completed:
            try:
                mission=next((d for d in delegations or [] if d['topic_id']==p['topic_id']),None)
                fresh=plan(store,read_topic(store,p['topic_id'],include_tracking=False),model,mission=mission)
                p.update(fresh)
                if run_id and fresh.get('model_metadata'):
                    from .model import task_metadata
                    store.step(run_id,'research_plan_model',task_metadata(fresh['model_metadata']))
            except Exception as error:
                p['planner_error']=type(error).__name__
                from .opencode_zen import StructuredDeliveryError
                if run_id and isinstance(error,StructuredDeliveryError):
                    from .model import task_metadata
                    store.step(run_id,'research_plan_model',{**task_metadata(error.response),'delivery_status':'invalid'})
                if run_id:store.step(run_id,'research_plan_deferred',{'error':type(error).__name__,'note':'AI 计划未通过，沿用按缺口补证据的工具计划'})
                # Invalid JSON does not disable the research role for the rest of the cycle.
                if not isinstance(error,StructuredDeliveryError):model=None
        actions=[]
        pending=list(p['actions']);rounds=1;attempted=set()
        while pending or rounds<2 and actions and p['planner']=='model' and calls<max_calls:
            if not pending:
                try:
                    fresh=plan(store,read_topic(store,p['topic_id'],include_tracking=False),model,mission=mission,feedback=actions)
                    if run_id and fresh.get('model_metadata'):
                        from .model import task_metadata
                        store.step(run_id,'research_plan_model',task_metadata(fresh['model_metadata']))
                    p['source_ids']=fresh['source_ids'];pending=list(fresh['actions']);rounds+=1
                    if not pending:break
                except Exception as error:
                    from .opencode_zen import StructuredDeliveryError
                    if run_id and isinstance(error,StructuredDeliveryError):
                        from .model import task_metadata
                        store.step(run_id,'research_plan_model',{**task_metadata(error.response),'delivery_status':'invalid'})
                    if run_id:store.step(run_id,'research_replan_deferred',{'error':type(error).__name__})
                    break
            a=pending.pop(0)
            if calls>=max_calls:break
            eid=p['source_ids'][a['source_ref']]
            identity=(a['tool'],eid,a['query'])
            if identity in attempted:continue
            attempted.add(identity)
            if a['tool']=='search_news' and p['planner']=='policy_fallback' and inspect(store,read_topic(store,p['topic_id'],include_tracking=False))['context']['status']=='present':
                actions.append({**a,'evidence_id':eid,'result':{'calls':0,'status':'not_needed','reason':'补读已取得原始来源语境'}})
                continue
            if a['tool']=='read_detail':
                result=enrichment.prepare(store,topic_id=p['topic_id'],evidence_id=eid,max_calls=1)
                results.extend(result['results'])
                # An unsuccessful HTTP connector automatically escalates to rendered reading.
                if not any(r.get('status')=='ok' for r in result['results']) and result['calls']<max_calls-calls:
                    pending.insert(0,{'tool':'browse_page','source_ref':a['source_ref'],'query':''})
            elif a['tool']=='search_news':result=background(store,p['topic_id'],a['query'])
            elif a['tool']=='search_web':
                from .web_research import background as web_background
                result=web_background(store,p['topic_id'],a['query'],max_calls=min(3,max_calls-calls))
            elif a['tool'] in ('browse_page','screenshot_ocr'):
                from .browser_tools import read as browser_read
                result=browser_read(store,p['topic_id'],eid,visual=a['tool']=='screenshot_ocr')
                if a['tool']=='browse_page' and result.get('status')=='insufficient' and result['calls']<max_calls-calls:
                    pending.insert(0,{'tool':'screenshot_ocr','source_ref':a['source_ref'],'query':''})
            elif a['tool']=='read_comments_visual':
                from .browser_tools import read_comments
                result=read_comments(store,p['topic_id'],eid)
            else:
                result=discussion.sample(store,p['topic_id'],eid,max_calls=min(2,max_calls-calls))
                if discussion.needs_visual(store,eid) and result['calls']<max_calls-calls:
                    pending.insert(0,{'tool':'read_comments_visual','source_ref':a['source_ref'],'query':''})
            calls+=result['calls'];actions.append({**a,'evidence_id':eid,'result':result})
        topic=read_topic(store,p['topic_id'],include_tracking=False);after=inspect(store,topic)
        status='evidence_gaps_remaining' if after['context']['status']=='missing' or after['discussion']['status']!='sampled' else 'samples_ready_for_analysis'
        payload={k:v for k,v in p.items() if k not in ('model_metadata','previous_at')}
        payload.update({'actions_executed':actions,'gaps_after':after,'network_calls':sum(a['result']['calls'] for a in actions),'research_rounds':rounds})
        stamp=now_iso()
        with store.conn:store.conn.execute('''INSERT INTO research_task VALUES(?,?,?,?,?,?,?) ON CONFLICT(task_id)
           DO UPDATE SET updated_at=excluded.updated_at,status=excluded.status,payload=excluded.payload''',
           (p['task_id'],p['topic_id'],p['fingerprint'],stamp,stamp,status,dump(payload)))
        completed.append({'task_id':p['task_id'],'topic_id':p['topic_id'],'status':status,'gaps':after,'planner':p['planner']})
    # Spend remaining source budget fairly on real details, including feeds that
    # do not need background search. This remains independent of AI readiness.
    if calls<max_calls and not topic_id and delegations is None:
        extra=enrichment.prepare(store,max_calls=max_calls-calls);calls+=extra['calls'];results.extend(extra['results'])
    return {'calls':calls,'results':results,'limit':max_calls,'research_tasks':completed,'selection':'按证据缺口研究，剩余详情预算跨来源轮换'}


def overview(store):
    return {'tasks':[{**dict(r),'payload':json.loads(r['payload'])} for r in store.conn.execute('''SELECT r.*,t.title FROM research_task r
      JOIN topic t USING(topic_id) ORDER BY r.updated_at DESC LIMIT 20''')],
      'capabilities':[{**dict(r),'payload':json.loads(r['payload'])} for r in store.conn.execute('SELECT * FROM research_capability ORDER BY attempted_at DESC LIMIT 30')]}
