"""Publication, observation and renewed circulation are separate clocks."""
from datetime import datetime,timedelta,timezone
from agent_v2.ingest import timestamp
import re
from zoneinfo import ZoneInfo

HEAT_CHANNELS={'baidu:realtime','baidu:movie','baidu:teleplay','baidu:game','weibo:hot','tieba:hot','bilibili:popular','bilibili:game'}
WINDOW_HOURS=168


def validate_event_date(recency,sources):
    """Validate the calendar date against quoted text, not the collection clock."""
    target=timestamp(recency.get('date_iso'))
    if not target:return False
    expected=datetime.fromisoformat(target).astimezone(ZoneInfo('Asia/Shanghai')).date()
    text=recency.get('time_text','')
    explicit=re.search(r'(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})',text)
    short=re.search(r'(\d{1,2})月(\d{1,2})日',text)
    for fact in recency.get('facts',[]):
        source=sources.get(fact['evidence_id'])
        if not source or not text or text not in fact['quote']:continue
        if explicit:parts=tuple(map(int,explicit.groups()))
        elif short and timestamp(source.get('published_at')):
            parts=(datetime.fromisoformat(timestamp(source['published_at'])).astimezone(ZoneInfo('Asia/Shanghai')).year,*map(int,short.groups()))
        else:continue
        try:
            if datetime(*parts).date()==expected:return True
        except ValueError:continue
    return False


def publication_time_matches(recency,sources):
    """A model may cite the provided publication metadata; label it as such."""
    raw=recency.get('time_text','');normalized=timestamp(raw)
    iso=re.search(r'20\d{2}-\d{2}-\d{2}',raw)
    if not normalized and iso:normalized=timestamp(iso.group())
    m=re.search(r'(20\d{2})年(\d{1,2})月(\d{1,2})日',raw)
    if m:
        try:normalized=timestamp(datetime(*map(int,m.groups())).isoformat())
        except ValueError:return False
    if not normalized:return False
    proposed=datetime.fromisoformat(normalized).astimezone(ZoneInfo('Asia/Shanghai')).date()
    for fact in recency.get('facts',[]):
        published=timestamp((sources.get(fact['evidence_id']) or {}).get('published_at'))
        if published and datetime.fromisoformat(published).astimezone(ZoneInfo('Asia/Shanghai')).date()==proposed:return True
    return False


def assess(store,topic,*,at=None):
    at=at or datetime.now(timezone.utc);cutoff=(at-timedelta(hours=WINDOW_HOURS)).isoformat(timespec='seconds')
    ceiling=(at+timedelta(minutes=5)).isoformat(timespec='seconds');heat=[];revival=[]
    for s in topic['signals']:
        if s['kind']=='board_rank_rise' and s.get('channel_id') in HEAT_CHANNELS and cutoff<=s.get('to_at','')<=ceiling:
            heat.append(s);revival.append(s)
    for row in store.conn.execute('''SELECT o.channel_id,o.evidence_id,MAX(o.observed_at) AS observed_at FROM channel_observation o
      JOIN topic_member m USING(evidence_id) WHERE m.topic_id=? AND m.active=1 AND o.observed_at>=? AND o.observed_at<=?
      GROUP BY o.channel_id,o.evidence_id''',(topic['topic_id'],cutoff,ceiling)):
        if row['channel_id'] in HEAT_CHANNELS:heat.append({'kind':'board_presence',**dict(row)})
    recent_comments=[e for e in topic.get('discussion_samples',[]) if cutoff<=str(e.get('published_at') or '')<=ceiling
        and not e.get('sample',{}).get('flags') and e.get('sample',{}).get('author_hash')]
    if len({e['sample']['author_hash'] for e in recent_comments})>=2:
        revival.append({'kind':'recent_discussion','evidence_ids':[e['evidence_id'] for e in recent_comments[:8]],
            'published_from':min(e['published_at'] for e in recent_comments),'published_to':max(e['published_at'] for e in recent_comments),
            'note':'近期有限评论样本，证明近期讨论，不能外推全网爆火'})
    dated=[timestamp(e.get('published_at')) for e in topic['evidence'] if timestamp(e.get('published_at'))]
    recent=[d for d in dated if cutoff<=d<=ceiling]
    status='recent_publication' if recent else 'verified_revival' if revival else 'historical_only' if dated and max(dated)<cutoff else 'date_unknown'
    # Undated imported "ranking" is a candidate, not a dated heat observation.
    return {'status':status,'window_hours':WINDOW_HOURS,'cutoff':cutoff,'checked_at':at.isoformat(timespec='seconds'),
        'published_to':max(dated,default=None),'heat_evidence':heat,'revival_evidence':revival,
        'business_eligible':status in ('recent_publication','verified_revival'),
        'reason':{'recent_publication':'来源近一周发布，事件实际时间仍须解读核对','verified_revival':'旧内容有近期升温或讨论依据',
          'historical_only':'旧内容只有重新采集或榜单收录，没有近期翻红证据','date_unknown':'仅观测时间，尚未确认事件或内容时间'}[status]}


def delivery_current(store,topic,payload):
    verdict=assess(store,topic)
    recency=payload.get('recency',{});date=timestamp(recency.get('date_iso'))
    if recency.get('kind') in ('recent_event','revival') and recency.get('time_verified') and date and recency.get('facts') and verdict['cutoff']<=date<=verdict['checked_at']:
        allowed_ids={e['evidence_id'] for e in topic['evidence']}|set(recency.get('verified_source_ids',[]))
        if any(f['evidence_id'] in allowed_ids for f in recency['facts']):
            verdict.update(business_eligible=True,status='verified_event_time',event_time=date)
    # An explicit research conclusion that this is historical is stronger than a new article timestamp.
    if payload.get('recency',{}).get('kind')=='historical':verdict.update(business_eligible=False,status='historical_only')
    return verdict
