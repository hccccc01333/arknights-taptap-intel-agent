import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { api, apiBaseUrl, isDemoMode, type Planet, type Universe } from '../lib/api';
import { demoHistory } from '../lib/demo';
import { formatNumber, formatScore, lifecycleNames } from '../lib/presentation';
import { Icon } from '../ui/Icon';
import { gamePulses, genrePulses, groupGames, normalizeEvidence, platformLabel, strategyEntries, type GamePulse, type Material } from './catalog';
import './field-discovery.css';
import { intelligenceData } from '../research/data';

export type FieldDiscoveryProps = {
  page: 'games' | 'timeline' | 'library'; universe: Universe;
  initialTopicId?: string | null; initialTab?: 'materials' | 'strategies';
  initialGame?: string | null; initialPlatform?: string | null;
  onSelect: (topicId: string) => void;
  onWorkspace?: (topicId?: string, ideaId?: string) => void;
  onResearch?: (topicId: string) => void;
};
const topicsOf = (universe: Universe) => Array.isArray(universe?.planets) ? universe.planets : [];
const platformsOf = (topic: Planet) => Array.isArray(topic.platforms) ? topic.platforms : [];
const count = (value: number) => formatNumber(Number.isFinite(value) ? value : 0);
const includes = (value: string, query: string) => !query.trim() || value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
const storageKey = (name: string) => 'taptap-public-' + name + ':v1:' + (isDemoMode ? 'demo' : 'live:' + encodeURIComponent(apiBaseUrl || 'same-origin'));
function gameId(value: string | null | undefined, groups: { id: string; name: string; topics: Planet[] }[]) {
  return !value || value === 'all' ? 'all' : groups.find(group => group.id === value || group.name === value || group.topics.some(topic => topic.game === value))?.id || 'all';
}
function Heading({ number, title, description, children }: { number: string; title: string; description: string; children?: ReactNode }) {
  return <header className="fd-heading" data-section={number}><div><h1>{title}</h1><p>{description}</p></div>{children}</header>;
}
function Empty({ title, text, children }: { title: string; text: string; children?: ReactNode }) {
  return <div className="fd-empty"><Icon name="search" size={30} /><h2>{title}</h2><p>{text}</p>{children}</div>;
}
function readFollowed(): string[] {
  try { const data: unknown = JSON.parse(localStorage.getItem(storageKey('followed-games')) || '[]'); return Array.isArray(data) ? [...new Set(data.filter((value): value is string => typeof value === 'string'))] : []; }
  catch { return []; }
}
type Rank = 'heat' | 'opportunities' | 'content' | 'velocity';
const ranks: Record<Rank, string> = { heat: '热度', opportunities: '增长机会', content: '内容量', velocity: '传播势头' };
function Games({ universe, initialGame, onSelect }: Pick<FieldDiscoveryProps, 'universe' | 'initialGame' | 'onSelect'>) {
  const groups = useMemo(() => gamePulses(universe), [universe]);
  const genres = useMemo(() => genrePulses(universe), [universe]);
  const [query, setQuery] = useState(() => groups.find(group => group.id === gameId(initialGame, groups))?.name || '');
  const [genre, setGenre] = useState('all');
  const [rank, setRank] = useState<Rank>('heat');
  const [followed, setFollowed] = useState(readFollowed);
  const [onlyFollowed, setOnlyFollowed] = useState(false);
  const [compare, setCompare] = useState<string[]>([]);
  const [open, setOpen] = useState<Set<string>>(() => new Set(initialGame ? [gameId(initialGame, groups)] : []));
  const [limit, setLimit] = useState(8);
  const [notice, setNotice] = useState('');
  const accordionId = useId();
  const initialContext = useRef(initialGame);
  useEffect(() => {
    if (initialContext.current === initialGame) return;
    initialContext.current = initialGame;
    const id = gameId(initialGame, groups); setQuery(groups.find(group => group.id === id)?.name || ''); setGenre('all'); setOnlyFollowed(false); setLimit(8); setOpen(new Set(id === 'all' ? [] : [id]));
  }, [initialGame, groups]);
  const metric = (group: GamePulse) => rank === 'heat' ? group.avgHeat ?? -1 : rank === 'velocity' ? group.avgVelocity ?? -1 : rank === 'opportunities' ? group.opportunityCount : group.contentCount;
  const visible = groups.filter(group => (genre === 'all' || genre === group.genre) && (!onlyFollowed || followed.includes(group.id)) && includes(group.name + ' ' + group.topics.map(topic => topic.title).join(' '), query))
    .sort((a, b) => metric(b) - metric(a) || b.topicCount - a.topicCount || a.name.localeCompare(b.name, 'zh-CN'));
  const comparison = groups.filter(group => compare.includes(group.id));
  function follow(id: string) {
    const next = followed.includes(id) ? followed.filter(value => value !== id) : [...followed, id]; setFollowed(next);
    try { localStorage.setItem(storageKey('followed-games'), JSON.stringify(next)); setNotice('关注已保存在本浏览器。'); }
    catch { setNotice('关注暂存于本页，浏览器保存不可用。'); }
  }
  function compareGame(id: string) {
    if (compare.includes(id)) setCompare(compare.filter(value => value !== id));
    else if (compare.length < 3) setCompare([...compare, id]);
    else setNotice('同时比较最多三个档案，请先移除一个。');
  }
  function reset() { setQuery(''); setGenre('all'); setOnlyFollowed(false); setLimit(8); }
  return <div className="fd-page fd-games">
    <Heading number="01" title={'游戏的档案，\n不止于一个名字。'} description="把分散的讨论收进同一份档案。关注一种热爱，或者比较几个新的增长方向。"><div className="fd-heading-stamp"><strong>{groups.length}</strong><span>游戏 / 话题档案</span></div></Heading>
    <section className="fd-pulse" aria-label="品类热度剖面"><div className="fd-section-heading"><h2>现在，哪种热爱更响？</h2><p>当前话题热度剖面。每根线是一个有效话题，排列不代表时间。</p></div>
      <div className="fd-wave-strip">{genres.map(group => <button className={'fd-wave ' + (genre === group.genre ? 'fd-active' : '')} key={group.id} aria-pressed={genre === group.genre} onClick={() => { setGenre(value => value === group.genre ? 'all' : group.genre); setLimit(8); }}>
        <span>{group.name}</span><div className="fd-wave-bars" aria-hidden="true">{group.topics.filter(topic => typeof topic.heat === 'number' && Number.isFinite(topic.heat) && topic.heat >= 0 && topic.heat <= 1).map(topic => <i key={topic.id} style={{ height: topic.heat! * 100 + '%' }} />)}{!group.heatSamples && <em>暂无评分</em>}</div><strong>{formatScore(group.avgHeat)}<small>/ 100</small></strong><p>{group.topicCount} 个话题 · {group.opportunityCount} 个机会</p>
      </button>)}</div>{!genres.length && <p className="fd-boundary">收到可用话题后，品类剖面会在这里展开。</p>}
      <p className="fd-boundary">热度与势头按现有有效话题评分取算术平均，缺失不计入；原始 0–1 显示为 0–100。增长机会是实际通过分析门禁的方案数，仍需研究判断。{isDemoMode ? '此页品类、话题与指标均为合成示例。' : '品类仅来自明确实体标签，没有标签时显示「未分类」。'}</p>
    </section>
    <div className="fd-filter-row"><label className="fd-search"><Icon name="search" size={21} /><input aria-label="搜索游戏档案" type="search" value={query} onChange={event => { setQuery(event.target.value); setLimit(8); }} placeholder="游戏名，或一条讨论的关键词" /></label><button className={'fd-followed-toggle ' + (onlyFollowed ? 'fd-active' : '')} aria-pressed={onlyFollowed} onClick={() => { setOnlyFollowed(value => !value); setLimit(8); }}><Icon name="bookmark" size={16} />只看关注 · {groups.filter(group => followed.includes(group.id)).length}</button></div>
    <nav className="fd-genre-tabs" aria-label="游戏品类筛选"><button aria-pressed={genre === 'all'} className={genre === 'all' ? 'fd-active' : ''} onClick={() => { setGenre('all'); setLimit(8); }}>全部品类</button>{genres.map(group => <button key={group.id} className={genre === group.genre ? 'fd-active' : ''} aria-pressed={genre === group.genre} onClick={() => { setGenre(group.genre); setLimit(8); }}>{group.name}</button>)}</nav>
    <div className="fd-rank-tabs"><span>按当前快照排列</span><div role="group" aria-label="档案排行榜">{(Object.keys(ranks) as Rank[]).map(value => <button key={value} aria-pressed={rank === value} className={rank === value ? 'fd-active' : ''} onClick={() => { setRank(value); setLimit(8); }}>{ranks[value]}</button>)}</div></div>
    {notice && <p className="fd-feedback" role="status">{notice}</p>}
    {comparison.length > 0 && <section className="fd-compare" aria-label="增长机会比较"><div className="fd-section-heading"><h2>放在一起，再做判断。</h2><button onClick={() => setCompare([])}>清空比较<Icon name="close" size={16} /></button></div><div className="fd-table-scroll"><table><thead><tr><th scope="col">当前快照</th>{comparison.map(group => <th key={group.id} scope="col"><button onClick={() => compareGame(group.id)} aria-label={'移除比较：' + group.name}>{group.name}<Icon name="close" size={14} /></button></th>)}</tr></thead><tbody><tr><th scope="row">平均热度</th>{comparison.map(group => <td key={group.id}>{formatScore(group.avgHeat)}<small>{group.heatSamples}/{group.topicCount} 个有效样本</small></td>)}</tr><tr><th scope="row">传播势头</th>{comparison.map(group => <td key={group.id}>{formatScore(group.avgVelocity)}</td>)}</tr><tr><th scope="row">通过门禁的方案</th>{comparison.map(group => <td key={group.id}>{group.opportunityCount} / {group.ideaCount}</td>)}</tr><tr><th scope="row">关联内容</th>{comparison.map(group => <td key={group.id}>{count(group.contentCount)}</td>)}</tr></tbody></table></div></section>}
    <div className="fd-game-index">{visible.slice(0, limit).map((group, index) => <section className={'fd-game-record ' + (open.has(group.id) ? 'fd-record-open' : '')} key={group.id}>
      <button className="fd-game-tab" aria-expanded={open.has(group.id)} aria-controls={accordionId + '-' + index} onClick={() => setOpen(previous => { const next = new Set(previous); next.has(group.id) ? next.delete(group.id) : next.add(group.id); return next; })}>
        <span className="fd-record-num">{String(index + 1).padStart(2, '0')}</span><div className="fd-record-name"><span className="fd-genre">{group.genre}</span><h2>{group.name}</h2></div><div className="fd-game-numbers"><div><strong>{formatScore(group.avgHeat)}</strong><span>平均热度</span></div><div><strong>{group.topicCount}</strong><span>话题</span></div><div><strong>{group.opportunityCount}</strong><span>增长机会</span></div></div><span className="fd-tab-toggle"><Icon name={open.has(group.id) ? 'close' : 'plus'} size={29} /></span>
      </button>
      <div className="fd-game-tools"><span>{count(group.contentCount)} 条内容 / {group.ideaCount} 个方案</span><button aria-pressed={followed.includes(group.id)} onClick={() => follow(group.id)}><Icon name={followed.includes(group.id) ? 'check' : 'bookmark'} size={15} />{followed.includes(group.id) ? '已关注' : '关注档案'}</button><button aria-pressed={compare.includes(group.id)} onClick={() => compareGame(group.id)}>{compare.includes(group.id) ? '移出比较' : '加入比较'}<Icon name="layers" size={15} /></button>{group.topics[0] && <button onClick={() => onSelect(group.topics[0].id)}>打开热点档案<Icon name="arrow" size={16} /></button>}</div>
      {open.has(group.id) && <div className="fd-game-topics" id={accordionId + '-' + index}>{group.topics.map(topic => <button key={topic.id} onClick={() => onSelect(topic.id)}><span>{lifecycleNames[topic.lifecycle || ''] || '状态待确认'}</span><h3>{topic.title}</h3><p>{platformsOf(topic).map(platformLabel).join(' / ') || '平台未标注'}</p><strong>{formatScore(topic.heat)}<Icon name="arrow" size={18} /></strong></button>)}</div>}
    </section>)}</div>
    {!visible.length && <Empty title={groups.length ? '这份档案，暂时没有匹配。' : '第一份档案，还在路上。'} text={onlyFollowed ? '关注仅保存在本浏览器。可以关闭关注筛选，再寻找新的游戏。' : '放宽关键词与品类，或等待上游提供话题。'}><button className="fd-button" onClick={reset}>查看全部档案<Icon name="arrow" size={17} /></button></Empty>}
    {visible.length > limit && <button className="fd-more" onClick={() => setLimit(value => value + 8)}>再打开 {Math.min(8, visible.length - limit)} 份档案<Icon name="plus" size={17} /></button>}
    <p className="fd-boundary">关注保存在当前浏览器。这里的排列与比较不会修改后端，也不是对未来热度的预测。</p>
  </div>;
}

function dateKey(value: string | null): string | null {
  if (!value || !Number.isFinite(new Date(value).getTime())) return null;
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
}
function Timeline({ universe, initialGame, initialPlatform, initialTopicId, onSelect, onResearch }: Pick<FieldDiscoveryProps, 'universe' | 'initialGame' | 'initialPlatform' | 'initialTopicId' | 'onSelect' | 'onResearch'>) {
  const topics = topicsOf(universe);
  const groups = useMemo(() => groupGames(universe), [universe]);
  const [game, setGame] = useState(() => gameId(initialGame, groups));
  const [platform, setPlatform] = useState(initialPlatform || 'all');
  const [scope, setScope] = useState(initialTopicId || 'all');
  const [start, setStart] = useState(''); const [end, setEnd] = useState('');
  const [date, setDate] = useState<string | null>(null);
  const [order, setOrder] = useState<'newest' | 'oldest'>('newest');
  const chosen = groups.find(group => group.id === game);
  const chosenIds = chosen ? new Set(chosen.topics.map(topic => topic.id)) : null;
  const selectedTopics = topics.filter(topic => (!chosenIds || chosenIds.has(topic.id)) && (platform === 'all' || platformsOf(topic).includes(platform)) && (scope === 'all' || scope === topic.id));
  const selectedIds = new Set(selectedTopics.map(topic => topic.id));
  // Demo dates are synthetic experience data; live renders only supplied firstSeen.
  const records = isDemoMode ? topics.map((topic, index) => ({ topic, date: demoHistory.daily[index % demoHistory.daily.length].date }))
    : topics.map(topic => ({ topic, date: dateKey(topic.firstSeen) })).filter((entry): entry is { topic: Planet; date: string } => Boolean(entry.date));
  const invalidRange = Boolean(start && end && start > end);
  const visible = records.filter(entry => selectedIds.has(entry.topic.id) && (!date || date === entry.date) && (!start || entry.date >= start) && (!end || entry.date <= end) && !invalidRange);
  const dates = [...new Set(visible.map(entry => entry.date))].sort((a, b) => order === 'newest' ? b.localeCompare(a) : a.localeCompare(b));
  const missing = selectedTopics.filter(topic => !dateKey(topic.firstSeen)).length;
  const platforms = [...new Set(topics.flatMap(platformsOf))];
  const allDates = [...new Set(records.map(entry => entry.date))].sort();
  const max = Math.max(1, ...demoHistory.daily.map(point => point.discussions));
  useEffect(() => { setGame(gameId(initialGame, groups)); setPlatform(initialPlatform || 'all'); setScope(initialTopicId || 'all'); setDate(null); }, [initialGame, initialPlatform, initialTopicId]);
  function reset() { setGame('all'); setPlatform('all'); setScope('all'); setDate(null); setStart(''); setEnd(''); }
  function recent() { const last = allDates.at(-1); if (!last) return; const first = new Date(last + 'T00:00:00Z'); first.setUTCDate(first.getUTCDate() - 6); setStart(first.toISOString().slice(0, 10)); setEnd(last); setDate(null); }
  return <div className="fd-page fd-timeline">
    <Heading number="02" title={'把第一次发现，\n留在时间的刻度里。'} description={isDemoMode ? '沿着一段合成观察，体验从信号到研究线索的过程。' : '重新找到话题第一次进入视野的时间，不把空白补成故事。'}><div className="fd-heading-stamp"><strong>{visible.length}</strong><span>{isDemoMode ? '合成观察' : '首次记录'}</span></div></Heading>
    <div className="fd-time-controls"><label className="fd-select">游戏<select aria-label="时间轴游戏筛选" value={game} onChange={event => { setGame(event.target.value); setDate(null); }}><option value="all">全部游戏</option>{groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label><label className="fd-select">平台<select aria-label="时间轴平台筛选" value={platform} onChange={event => { setPlatform(event.target.value); setDate(null); }}><option value="all">全部平台</option>{platforms.map(key => <option key={key} value={key}>{platformLabel(key)}</option>)}</select></label><label className="fd-select fd-topic-select">话题<select aria-label="时间轴话题筛选" value={scope} onChange={event => { setScope(event.target.value); setDate(null); }}><option value="all">全部话题</option>{topics.map(topic => <option key={topic.id} value={topic.id}>{topic.title}</option>)}</select></label></div>
    <div className="fd-date-tools"><label>从<input type="date" aria-label="时间轴开始日期" value={start} max={end || undefined} onChange={event => { setStart(event.target.value); setDate(null); }} /></label><span>—</span><label>到<input type="date" aria-label="时间轴结束日期" value={end} min={start || undefined} onChange={event => { setEnd(event.target.value); setDate(null); }} /></label><button onClick={recent} disabled={!allDates.length}>最近记录的七天</button><button onClick={reset}>重置筛选<Icon name="refresh" size={15} /></button></div>
    {invalidRange && <p className="fd-feedback" role="alert">结束日期需要晚于或等于开始日期。</p>}
    <p className="fd-boundary">{isDemoMode ? '合成体验时间线。日期、讨论量与观察均为演示；筛选只影响下方观察记录，图表保持全网合成样本。' : '首次发现记录；未接传播历史。仅使用上游已有的首次发现时间。平台筛选表示话题包含该平台，不代表当时在该平台传播；缺失日期不补造。'}</p>
    {isDemoMode && <section className="fd-demo-history" aria-label="全网合成七天讨论量"><div className="fd-section-heading"><h2>七天的示例切片。</h2><span>全网合成讨论量 / 条</span></div><div>{demoHistory.daily.map(point => <button key={point.date} aria-pressed={date === point.date} className={date === point.date ? 'fd-active' : ''} aria-label={'查看 ' + point.date + ' 的合成观察'} onClick={() => setDate(current => current === point.date ? null : point.date)}><span>{point.label}</span><i style={{ height: point.discussions / max * 85 + 'px' }} /><strong>{count(point.discussions)}</strong><small>{records.filter(entry => entry.date === point.date && selectedIds.has(entry.topic.id)).length} 个观察</small></button>)}</div></section>}
    <header className="fd-time-title"><h2>{date ? date.replaceAll('-', '.') : isDemoMode ? '观察的刻度' : '首次发现的刻度'}</h2><div><button onClick={() => setOrder(value => value === 'newest' ? 'oldest' : 'newest')}>{order === 'newest' ? '最新在前' : '最早在前'}<Icon name="down" size={14} /></button>{date && <button onClick={() => setDate(null)}>全部日期</button>}</div></header>
    <div className="fd-ruler">{dates.map(day => <section className="fd-ruler-day" key={day}><button className="fd-ruler-date" onClick={() => setDate(value => value === day ? null : day)} aria-label={'筛选 ' + day + ' 的记录'}><time dateTime={day}><span>{day.slice(0, 4)} / {day.slice(5, 7)}</span><strong>{day.slice(8, 10)}</strong></time></button><div className="fd-ruler-events">{visible.filter(entry => entry.date === day).map(({ topic }) => <article key={topic.id}><button className="fd-time-event" onClick={() => onSelect(topic.id)}><div><span>{isDemoMode ? '合成观察' : '首次发现'} / {topic.gameName || '跨游戏话题'}</span><h3>{topic.title}</h3><p>{isDemoMode ? '体验用研究线索，等待补充证据与判断。' : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(topic.firstSeen!)) + ' · 北京时间'}<br />{platformsOf(topic).map(platformLabel).join(' / ') || '平台未标注'}</p></div><Icon name="arrow" size={24} /></button>{onResearch && <div className="fd-event-actions"><button onClick={() => onResearch(topic.id)}>加入研究夹<Icon name="bookmark" size={14} /></button></div>}</article>)}</div></section>)}</div>
    {!visible.length && <Empty title="这段刻度，暂时留白。" text={isDemoMode ? '当前范围没有匹配的合成观察，可以放宽筛选。' : '当前范围没有有效首次发现记录，不生成额外历史。'}><button className="fd-button" onClick={reset}>查看全部记录<Icon name="arrow" size={17} /></button></Empty>}
    {!isDemoMode && missing > 0 && <p className="fd-boundary">{missing} 个话题没有有效首次发现时间，仍可从游戏档案访问。</p>}
  </div>;
}

function sourceUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const raw = value.trim();
  if (!/^https?:\/\/[^/\s]/i.test(raw) || /[\u0000-\u001f\u007f]/.test(raw)) return undefined;
  try { const url = new URL(raw); return url.hostname && !url.username && !url.password && ['https:', 'http:'].includes(url.protocol) ? url.href : undefined; } catch { return undefined; }
}
const favoritesKey = storageKey('materials');
function readMaterials(): { entries: Record<string, Material>; failed: boolean } {
  try {
    const data: unknown = JSON.parse(localStorage.getItem(favoritesKey) || '[]');
    if (!Array.isArray(data)) return { entries: {}, failed: true };
    const entries: Record<string, Material> = {};
    for (const value of data) {
      if (!value || typeof value !== 'object') continue;
      const item = value as Material;
      if ([item.id, item.topicId, item.topicTitle, item.title, item.excerpt, item.platform].every(value => typeof value === 'string')) entries[item.id] = { ...item, url: sourceUrl(item.url) };
    }
    return { entries, failed: false };
  } catch { return { entries: {}, failed: true }; }
}
function downloadMaterials(materials: Material[]) {
  const time = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date());
  const date = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const text = '# TapTap 研究素材包\n\n数据说明：' + (isDemoMode ? '全合成演示片段，未经真实采集验证' : '当前 API 已提供的证据片段') + '\n导出时间：' + time + '（北京时间）\n素材数量：' + materials.length + '\n\n' + materials.map((item, index) => '## ' + (index + 1) + '. ' + item.title + '\n\n关联话题：' + item.topicTitle + '\n来源平台：' + platformLabel(item.platform) + '\n原文链接：' + (sourceUrl(item.url) || '未提供') + '\n\n' + item.excerpt).join('\n\n---\n\n') + '\n';
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' })); const link = document.createElement('a'); link.href = url; link.download = 'TapTap-素材包-' + date + '.md'; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function Library({ universe, initialTopicId, initialTab, initialGame, initialPlatform, onSelect, onWorkspace, onResearch }: Omit<FieldDiscoveryProps, 'page'>) {
  const topics = topicsOf(universe); const groups = useMemo(() => groupGames(universe), [universe]);
  const [tab, setTab] = useState<'materials' | 'strategies'>(initialTab || 'materials');
  const [game, setGame] = useState(() => gameId(initialGame, groups));
  const [topicId, setTopicId] = useState(() => {
    const choices = groups.find(group => group.id === gameId(initialGame, groups))?.topics || topics;
    return choices.find(topic => topic.id === initialTopicId)?.id || choices[0]?.id || '';
  });
  const [scope, setScope] = useState(initialTopicId || 'all');
  const [platform, setPlatform] = useState(initialPlatform || 'all'); const [type, setType] = useState('all'); const [query, setQuery] = useState('');
  const [savedOnly, setSavedOnly] = useState(false); const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [initialSaved] = useState(readMaterials); const [saved, setSaved] = useState(initialSaved.entries);
  const [selectedIdea, setSelectedIdea] = useState(''); const [retry, setRetry] = useState(0);
  const [response, setResponse] = useState<{ id: string; data: any; materials: Material[]; loading: boolean; error: string }>({ id: '', data: null, materials: [], loading: false, error: '' });
  const [notice, setNotice] = useState(initialSaved.failed ? '收藏暂时无法读取，可以继续复制和下载素材。' : '');
  const noticeTimer = useRef(0);
  const signature = JSON.stringify([initialTopicId || null, initialTab || null, initialGame || null, initialPlatform || null]);
  const applied = useRef(signature); const skipWrite = useRef(false);
  const choices = useMemo(() => groups.find(group => group.id === game)?.topics || (game === 'all' ? topics : []), [groups, game, topics]);
  const choiceIds = useMemo(() => new Set(choices.map(topic => topic.id)), [choices]);
  const ideas = useMemo(() => strategyEntries(universe), [universe]);
  const visibleIdeas = useMemo(() => ideas.filter(entry => (game === 'all' || choiceIds.has(entry.topicId)) && (scope === 'all' || scope === entry.topicId) && (platform === 'all' || (entry.topic && platformsOf(entry.topic).includes(platform))) && (type === 'all' || type === entry.idea.type) && includes(entry.idea.name + ' ' + (entry.topic?.title || '') + ' ' + (entry.topic?.gameName || ''), query)), [ideas, game, choiceIds, scope, platform, type, query]);
  const idea = visibleIdeas.find(entry => entry.key === selectedIdea) || visibleIdeas[0];
  const activeId = tab === 'strategies' ? idea?.topicId || '' : topicId;
  const activeTopic = topics.find(topic => topic.id === activeId);
  const rawMaterials = savedOnly ? Object.values(saved) : response.id === topicId ? response.materials : [];
  const materials = rawMaterials.filter(item => (game === 'all' || choiceIds.has(item.topicId)) && (platform === 'all' || platform === item.platform) && includes(item.title + ' ' + item.excerpt + ' ' + item.topicTitle, query));
  const chosenMaterials = materials.filter(item => selected.has(item.id));
  const platforms = [...new Set([...topics.flatMap(platformsOf), ...Object.values(saved).map(item => item.platform), ...response.materials.map(item => item.platform), ...(initialPlatform ? [initialPlatform] : [])])].filter(Boolean);
  const types = [...new Set(ideas.map(entry => entry.idea.type))].filter(Boolean);
  const busy = tab === 'materials' && !savedOnly && Boolean(activeTopic) && (response.id !== topicId || response.loading);
  const detail = response.id === activeId ? response.data : null;
  const recommendation = typeof detail?.recommendation === 'string' ? detail.recommendation : '';
  const summary = typeof detail?.summary === 'string' ? detail.summary : typeof detail?.what_happened?.summary === 'string' ? detail.what_happened.summary : '';
  useEffect(() => () => clearTimeout(noticeTimer.current), []);
  useEffect(() => {
    if (applied.current === signature) return;
    applied.current = signature; skipWrite.current = true;
    const nextGame = gameId(initialGame, groups); const next = groups.find(group => group.id === nextGame)?.topics || topics;
    setGame(nextGame); setTopicId(next.find(topic => topic.id === initialTopicId)?.id || next[0]?.id || ''); setScope(initialTopicId || 'all'); setPlatform(initialPlatform || 'all'); setTab(initialTab || 'materials'); setType('all'); setQuery(''); setSavedOnly(false); setSelected(new Set()); setSelectedIdea('');
  }, [signature, universe]);
  useEffect(() => {
    if (!choices.some(topic => topic.id === topicId)) setTopicId(choices[0]?.id || '');
    if (scope !== 'all' && !choices.some(topic => topic.id === scope)) setScope('all');
  }, [choices, topicId, scope]);
  useEffect(() => {
    if (skipWrite.current) { skipWrite.current = false; return; }
    const [path, raw] = location.hash.split('?'); if (!/^#\/library\/?$/.test(path)) return;
    const params = new URLSearchParams(raw || ''); const routeTopic = tab === 'materials' ? topicId : scope === 'all' ? '' : scope;
    for (const [key, value] of [['topic', routeTopic], ['tab', tab], ['game', game === 'all' ? '' : game], ['platform', platform === 'all' ? '' : platform]]) { if (value) params.set(key, value); else params.delete(key); }
    const next = path + (params.size ? '?' + params.toString() : ''); if (next !== location.hash) history.replaceState(history.state, '', location.pathname + location.search + next);
  }, [tab, topicId, scope, game, platform, signature]);
  useEffect(() => {
    if (!activeTopic || (tab === 'materials' && savedOnly)) return;
    let current = true; const controller=new AbortController(); const id = activeTopic.id; setResponse({ id, data: null, materials: [], loading: true, error: '' });
    intelligenceData.topic(id,{signal:controller.signal,force:retry>0}).then(data => { if (current) setResponse({ id, data, materials: normalizeEvidence(data, activeTopic), loading: false, error: '' }); }).catch(error => { if (current) setResponse({ id, data: null, materials: [], loading: false, error: error instanceof Error ? error.message : '证据读取失败，请重试。' }); });
    return () => { current = false; controller.abort(); };
  }, [activeTopic, tab, savedOnly, retry]);
  function say(message: string) { clearTimeout(noticeTimer.current); setNotice(message); noticeTimer.current = window.setTimeout(() => setNotice(''), 5000); }
  function favorite(item: Material) {
    const next = { ...saved }; const remove = Boolean(next[item.id]); if (remove) delete next[item.id]; else next[item.id] = item; setSaved(next);
    try { localStorage.setItem(favoritesKey, JSON.stringify(Object.values(next))); say(remove ? '已取消本地收藏。' : '已收藏到当前浏览器。'); } catch { say('收藏暂存于本页，建议下载素材包保存。'); }
  }
  async function copy(item: Material) { try { await navigator.clipboard.writeText(item.title + '\n\n' + item.excerpt + (sourceUrl(item.url) ? '\n\n' + sourceUrl(item.url) : '')); say('片段已复制。'); } catch { say('无法自动复制，可以手动选择片段文字。'); } }
  function download() { const items = chosenMaterials.length ? chosenMaterials : materials; if (!items.length) return; try { downloadMaterials(items); say('已下载 ' + items.length + ' 条素材的实际内容。'); } catch { say('下载未完成，请重试。'); } }
  function changeTab(next: 'materials' | 'strategies') { if (next === 'strategies') setScope(topicId || 'all'); else if (activeId && choiceIds.has(activeId)) setTopicId(activeId); setTab(next); setQuery(''); setType('all'); setSelected(new Set()); }
  return <div className="fd-page fd-library">
    <Heading number="03" title={'把线索铺开。\n让下一步有据可依。'} description="一张编辑桌，两种工作方式：留下可追溯的片段，或把一个增长方案看清楚。" />
    <div className="fd-library-tabs" role="tablist" aria-label="编辑桌模式"><button role="tab" aria-selected={tab === 'materials'} className={tab === 'materials' ? 'fd-active' : ''} onClick={() => changeTab('materials')}><span>01</span><b>内容纸条</b><small>{Object.keys(saved).length} 条本地收藏</small></button><button role="tab" aria-selected={tab === 'strategies'} className={tab === 'strategies' ? 'fd-active' : ''} onClick={() => changeTab('strategies')}><span>02</span><b>策略工作页</b><small>{ideas.length} 个上游方案</small></button></div>
    <div className="fd-library-controls"><label className="fd-search"><Icon name="search" size={20} /><input aria-label={tab === 'materials' ? '搜索内容纸条' : '搜索策略方案'} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={tab === 'materials' ? '一句话，或一条值得留下的线索' : '搜索方案与关联话题'} /></label>
      <label className="fd-select">游戏<select aria-label="编辑桌游戏筛选" value={game} onChange={event => { setGame(event.target.value); setScope('all'); setSelected(new Set()); }}><option value="all">全部游戏</option>{groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label>
      {tab === 'materials' ? <label className="fd-select fd-topic-select">话题<select aria-label="内容纸条话题" value={topicId} disabled={savedOnly} onChange={event => { setTopicId(event.target.value); setSelected(new Set()); }}>{choices.map(topic => <option key={topic.id} value={topic.id}>{topic.title}</option>)}{!choices.length && <option value="">暂无话题</option>}</select></label> : <label className="fd-select">类型<select aria-label="策略类型筛选" value={type} onChange={event => setType(event.target.value)}><option value="all">全部类型</option>{types.map(value => <option key={value} value={value}>{value}</option>)}</select></label>}
      <label className="fd-select">平台<select aria-label="编辑桌平台筛选" value={platform} onChange={event => setPlatform(event.target.value)}><option value="all">全部平台</option>{platforms.map(key => <option key={key} value={key}>{platformLabel(key)}</option>)}</select></label>
    </div>
    {tab === 'strategies' && <div className="fd-strategy-scope"><label className="fd-select">研究话题<select aria-label="策略话题筛选" value={scope} onChange={event => { setScope(event.target.value); setSelectedIdea(''); }}><option value="all">全部话题的方案</option>{choices.map(topic => <option key={topic.id} value={topic.id}>{topic.title}</option>)}</select></label>{scope !== 'all' && <button onClick={() => setScope('all')}>看全部话题<Icon name="arrow" size={15} /></button>}</div>}
    {isDemoMode && <p className="fd-boundary">演示编辑桌：片段、情境、建议与评分均为合成样本，未经过真实采集验证。</p>}
    {notice && <p className="fd-feedback" role="status">{notice}</p>}
    {tab === 'materials' ? <section role="tabpanel" aria-label="内容纸条"><div className="fd-material-toolbar"><div><button aria-pressed={!savedOnly} className={!savedOnly ? 'fd-active' : ''} onClick={() => { setSavedOnly(false); setSelected(new Set()); }}>当前话题</button><button aria-pressed={savedOnly} className={savedOnly ? 'fd-active' : ''} onClick={() => { setSavedOnly(true); setSelected(new Set()); }}>收藏夹 · {Object.keys(saved).length}</button></div><button className="fd-button" disabled={!materials.length || busy} onClick={download}><Icon name="download" size={17} />{chosenMaterials.length ? '下载选中 · ' + chosenMaterials.length : '下载当前素材'}</button></div>
      <p className="fd-boundary">{savedOnly ? '收藏仅保存在当前浏览器；下载 Markdown 可在浏览器之外继续研究。' : '内容取自所选话题的证据结构。未提供原文地址时，不生成来源链接。'}</p>
      {busy && <div role="status"><Empty title="正在铺开纸条…" text="读取话题已有的证据片段。" /></div>}
      {!busy && !savedOnly && response.id === topicId && response.error && <div role="alert"><Empty title="这次没能打开素材。" text={response.error}><button className="fd-button" onClick={() => setRetry(value => value + 1)}>重新读取<Icon name="refresh" size={16} /></button></Empty></div>}
      {!busy && <div className="fd-paper-stack">{materials.map((item, index) => <article className="fd-paper" key={item.id}><div className="fd-paper-index"><span>{String(index + 1).padStart(2, '0')}</span><small>{platformLabel(item.platform)}</small><label><input type="checkbox" aria-label={'选择纸条：' + item.title} checked={selected.has(item.id)} onChange={() => setSelected(previous => { const next = new Set(previous); next.has(item.id) ? next.delete(item.id) : next.add(item.id); return next; })} />加入素材包</label></div><div className="fd-paper-body"><h2>{item.title}</h2><p>{item.excerpt}</p><button className="fd-paper-topic" onClick={() => onSelect(item.topicId)}>{item.topicTitle}<Icon name="arrow" size={17} /></button><div className="fd-paper-actions"><button onClick={() => void copy(item)}><Icon name="file" size={15} />复制片段</button><button aria-pressed={Boolean(saved[item.id])} onClick={() => favorite(item)}><Icon name={saved[item.id] ? 'check' : 'bookmark'} size={15} />{saved[item.id] ? '已收藏' : '收藏'}</button>{onResearch && <button onClick={() => onResearch(item.topicId)}>加入研究夹<Icon name="layers" size={15} /></button>}{sourceUrl(item.url) && <a href={sourceUrl(item.url)} target="_blank" rel="noopener noreferrer">原文来源<Icon name="link" size={15} /></a>}</div></div></article>)}</div>}
      {!busy && !materials.length && (savedOnly || !response.error) && <Empty title={savedOnly ? '值得留下的，慢慢收集。' : '这张桌面，暂时留白。'} text={savedOnly ? '在话题纸条里收藏片段，再回来继续研究。' : '当前话题或筛选没有可用证据片段。'}>{(query || platform !== 'all') && <button className="fd-button" onClick={() => { setQuery(''); setPlatform('all'); }}>放宽筛选<Icon name="arrow" size={17} /></button>}</Empty>}
    </section> : <section role="tabpanel" aria-label="策略工作页" className="fd-strategy-desk"><div className="fd-strategy-menu">{visibleIdeas.map((entry, index) => <button key={entry.key} className={idea?.key === entry.key ? 'fd-active' : ''} aria-pressed={idea?.key === entry.key} onClick={() => setSelectedIdea(entry.key)}><span>{String(index + 1).padStart(2, '0')}</span><div><small>{entry.idea.type || '类型未提供'}</small><h2>{entry.idea.name}</h2><p>{entry.topic?.title || entry.topicId}</p></div><Icon name="arrow" size={23} /></button>)}{!visibleIdeas.length && <Empty title="下一条好点子，还在酝酿。" text={ideas.length ? '调整话题、平台、类型与关键词，看看其他方案。' : '上游提供具体方案后，会在这里展开。'} />}</div>
      {idea && <aside className="fd-strategy-sheet"><span className="fd-sheet-label">先研究，再行动</span><h2>{idea.idea.name}</h2><div className="fd-strategy-mark"><div><span>方案评分</span><strong>{formatScore(idea.idea.scoreAvailable===false?null:idea.idea.score)}<small>/100</small></strong></div><p><Icon name={idea.idea.passed ? 'check' : 'eye'} size={17} />{idea.idea.passedAvailable===false?'未提供门禁结论':idea.idea.passed ? '通过分析门禁' : '未通过分析门禁'}</p></div><button className="fd-sheet-topic" onClick={() => onSelect(idea.topicId)}><span>关联话题</span>{idea.topic?.title || idea.topicId}<Icon name="arrow" size={18} /></button><div className="fd-sheet-body"><section><h3>话题建议</h3><p>{response.id === activeId && response.loading ? '正在读取已有建议…' : recommendation || '上游尚未提供具体建议，先留下需要核实的问题。'}</p>{response.id === activeId && response.error && <><p className="fd-feedback">{response.error}</p><button onClick={() => setRetry(value => value + 1)}>重试读取<Icon name="refresh" size={15} /></button></>}</section>{summary && <section><h3>相关情境</h3><p>{summary}</p></section>}</div><div className="fd-sheet-actions">{onResearch && <button onClick={() => onResearch(idea.topicId)}>加入研究夹<Icon name="bookmark" size={17} /></button>}{onWorkspace && <button className="fd-button" onClick={() => onWorkspace(idea.topicId, idea.idea.id)}>整理这份草稿<Icon name="arrow" size={19} /></button>}</div><p className="fd-boundary">评分与门禁来自上游，仍需研究判断。工作台仅整理本地草稿，此处不会执行或提交方案。</p></aside>}
    </section>}
  </div>;
}

export function FieldDiscovery(props: FieldDiscoveryProps) {
  if (props.page === 'games') return <Games {...props} />;
  if (props.page === 'timeline') return <Timeline {...props} />;
  return <Library {...props} />;
}
