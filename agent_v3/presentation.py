"""Product views of research records; retain raw captures and source history."""
import json

def material_ready(item):
    if item['kind'] in ('copy','script','visual_brief','production_checklist'):
        return len(item.get('content','').strip())>=(60 if item['kind']=='script' else 20)
    delivery=item.get('application',{}).get('delivery',{})
    return bool(len(delivery.get('body','').strip())>=60 and delivery.get('adaptation_steps')
                and delivery.get('audience') and delivery.get('usage_boundary'))

def classify_outputs(data):
    """Older deliveries get conservative library labels, not new AI facts."""
    for item in data.get('game_signals',[]):
        p=item['payload']
        if p.get('category'):continue
        text=' '.join(p.get(k,'') for k in ('title','game_context','observed_change'))
        category='risk_monitoring' if item.get('risk_assessment',{}).get('polarity') in ('negative','mixed') else \
            'market_movement' if any(w in text for w in ('销量','销售','排行','商业','市场')) else \
            'release_update' if any(w in text for w in ('发售','上线','版本','更新','新作','新角色','爆料')) else \
            'community_creation' if any(w in text for w in ('创作','布局','装修','同人','分享','展示')) else 'player_need'
        p.update(category=category,classification_origin='legacy_library_rule')
    for item in data.get('creatives',[]):
        p=item['payload']
        if p.get('category'):continue
        text=' '.join(p.get(k,'') for k in ('growth_goal','user_action','placement'))
        p['category']='game_conversion' if any(w in text for w in ('预约','下载','测试报名')) else \
            'community_participation' if any(w in text for w in ('评论','发帖','回复','参与','分享','讨论')) else \
            'return_visit' if any(w in text for w in ('回访','留存','回流')) else 'game_discovery'
        p['classification_origin']='legacy_library_rule'
    return data


def game_coverage(store):
    from datetime import datetime,timedelta,timezone
    from .connectors import CHANNELS
    from .discovery import read_topic
    from .freshness import delivery_current
    now=datetime.now(timezone.utc);cutoff=(now-timedelta(days=7)).isoformat(timespec='seconds');ceiling=now.isoformat(timespec='seconds')
    channels=[c['id'] for c in CHANNELS if c['domain']=='游戏']
    slots=','.join('?' for _ in channels)
    rows=store.conn.execute(f'''SELECT e.platform,COUNT(DISTINCT e.evidence_id) AS count FROM evidence e
      JOIN channel_observation o USING(evidence_id) WHERE o.channel_id IN ({slots}) AND o.observed_at>=?
      AND e.published_at>=? AND e.published_at<=? AND e.kind NOT IN ('comment','search') GROUP BY e.platform''',(*channels,cutoff,cutoff,ceiling)).fetchall()
    ids=[r[0] for r in store.conn.execute(f'''SELECT DISTINCT m.topic_id FROM topic_member m JOIN channel_observation o USING(evidence_id)
      WHERE m.active=1 AND o.channel_id IN ({slots}) AND o.observed_at>=?''',(*channels,cutoff))]
    briefs=[]
    for tid in ids:
        topicrow=store.conn.execute('SELECT fingerprint FROM topic WHERE topic_id=?',(tid,)).fetchone()
        if not topicrow:continue
        interpretation=store.interpretation(tid,topicrow[0])
        if not interpretation or interpretation['payload']['status']!='ready':continue
        p=interpretation['payload'];topic=read_topic(store,tid,include_tracking=False)
        from agent_v2.store import stable_id,dump
        decisionrow=store.conn.execute('SELECT payload FROM main_decision WHERE topic_id=? AND fingerprint=? AND context_version=?',
            (tid,topicrow[0],stable_id('context_',dump(store.context())))).fetchone()
        decision=json.loads(decisionrow[0]) if decisionrow else {}
        signals=topic['business_outputs']['game_signals']
        # Game channels provide discovery slots; meaning still needs AI confirmation.
        if not signals and decision.get('route')!='game_direct':continue
        freshness=delivery_current(store,topic,p)
        if not freshness['business_eligible']:continue
        briefs.append({'topic_id':tid,'created_at':interpretation['created_at'],'payload':p,
            'kind':'hotspot' if freshness['heat_evidence'] else 'game_signal','signals':len(signals),
            'risk_assessment':topic['business_outputs']['risk_assessment']})
    briefs.sort(key=lambda x:x['created_at'],reverse=True)
    return {'window_days':7,'published_recent_sources':sum(r['count'] for r in rows),'by_platform':[dict(r) for r in rows],
        'game_channel_topics':len(ids),'interpreted':len(briefs),'briefs':briefs[:12],
        'note':'统计游戏渠道近七天有发布日期的来源；搜索命中、评论和日期未知的榜单词不计入。游戏资讯或玩家帖不自动等于热点。'}


def recognized_captures(rows):
    result=[]
    for row in rows:
        payload=json.loads(row['payload']) if isinstance(row['payload'],str) else row['payload']
        if row['status']!='ok':continue
        raw_frames=payload.get('captures',[])
        if payload.get('tool')=='browser_comments':
            raw_frames=[{**f,'ocr':{**f.get('ocr',{}),'text':f.get('comment_transcript','')}} for f in raw_frames]
        frames=[{**frame,'frame_index':i} for i,frame in enumerate(raw_frames)
                if frame.get('ocr',{}).get('status')=='ok'
                and any(c.isalpha() for c in frame.get('ocr',{}).get('text',''))]
        if frames:result.append({**dict(row),'payload':{**payload,'captures':frames}})
    return result


def business_outputs(store,topic_id,fingerprint,record):
    """Current business revision only; older artifacts stay in their audit tables."""
    signals=[];materials=[];creatives=[]
    if record:
        for row in store.conn.execute('SELECT * FROM game_signal WHERE topic_id=? AND fingerprint=? AND run_id=?',
                (topic_id,fingerprint,record['run_id'])):
            p=json.loads(row['payload'])
            if p.get('validation',{}).get('status','accepted')=='accepted':signals.append({**dict(row),'payload':p})
        for row in store.conn.execute('''SELECT m.*,a.payload AS application FROM material_application_run a JOIN material m USING(material_id)
                WHERE a.topic_id=? AND a.fingerprint=? AND a.run_id=?''',(topic_id,fingerprint,record['run_id'])):
            materials.append({**dict(row),'application':json.loads(row['application'])})
        materials=[m for m in materials if material_ready(m)]
        for row in store.conn.execute('''SELECT c.* FROM creative c JOIN event e USING(event_id)
                WHERE json_extract(e.assessment,'$.topic_id')=? AND json_extract(e.assessment,'$.topic_fingerprint')=?
                ORDER BY c.created_at DESC''',(topic_id,fingerprint)):
            basis=store.creative_basis(row)
            if basis.get('topic_id')!=topic_id or basis.get('topic_fingerprint')!=fingerprint:continue
            creatives.append({**dict(row),'payload':json.loads(row['payload'])})
    jobs=[dict(r) for r in store.conn.execute('''SELECT stage,status,error FROM work_item WHERE topic_id=? AND fingerprint=?
            ORDER BY created_at DESC,rowid DESC LIMIT 8''',(topic_id,fingerprint))]
    p=record['payload'] if record else {}
    from .risk import policy
    safety=store.event_risk({'topic_id':topic_id,'topic_fingerprint':fingerprint})
    held_creatives=[{'creative_id':c['creative_id'],'title':c['payload']['title']} for c in creatives] if not safety['growth_allowed'] else []
    if not safety['growth_allowed']:creatives=[];materials=[]
    return {'game_signals':signals,'materials':materials,'creatives':creatives,
            'risk_assessment':safety,'held_creatives':held_creatives,
            'assessments':p.get('output_assessments',{}),'opportunity':p.get('opportunity'),
            'judged_at':record['created_at'] if record else None,'jobs':jobs}
