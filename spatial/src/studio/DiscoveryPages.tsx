import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { api, apiBaseUrl, isDemoMode, type Planet, type Universe } from '../lib/api';
import { demoHistory } from '../lib/demo';
import { formatNumber, formatScore, lifecycleNames } from '../lib/presentation';
import { Icon } from '../ui/Icon';
import { groupGames, gamePulses, genrePulses, normalizeEvidence, platformLabel, strategyEntries, type Material } from './catalog';
import './discovery.css';

type DiscoveryPage = 'games' | 'timeline' | 'library';
export type DiscoveryProps = { page: DiscoveryPage; universe: Universe; initialTopicId?: string | null; initialTab?: 'materials' | 'strategies'; initialGame?: string | null; initialPlatform?: string | null; onSelect: (id: string) => void; onWorkspace?: (topicId?: string, ideaId?: string) => void; onResearch?: (topicId: string) => void };
type Props = DiscoveryProps;
const count = (value: number) => formatNumber(Number.isFinite(value) ? value : 0);
const topicsOf = (universe: Universe) => Array.isArray(universe?.planets) ? universe.planets : [];

function PageTitle({ label, title, description, children }: { label: string; title: string; description: string; children?: ReactNode }) {
  return <header className="dis-page-title"><div><span className="dis-kicker">{label}</span><h1>{title}</h1><p>{description}</p></div>{children}</header>;
}

function modeStorageKey(name: string) { return 'taptap-public-' + name + ':v1:' + (isDemoMode ? 'demo' : 'live:' + encodeURIComponent(apiBaseUrl || 'same-origin')); }
function readFollowedGames(): string[] {
  try { const data: unknown = JSON.parse(localStorage.getItem(modeStorageKey('followed-games')) || '[]'); return Array.isArray(data) ? data.filter((value): value is string => typeof value === 'string') : []; }
  catch { return []; }
}
function resolveGame(value: string | null | undefined, groups: { id: string; name: string; topics: Planet[] }[]): string {
  if (!value || value === 'all') return 'all';
  return groups.find(group => group.id === value || group.name === value || group.topics.some(topic => topic.game === value))?.id || 'all';
}
const platformsOf = (topic: Planet) => Array.isArray(topic.platforms) ? topic.platforms : [];
const matchesQuery = (text: string, query: string) => !query.trim() || text.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
type Ranking = 'heat' | 'opportunities' | 'content' | 'velocity';
const rankingNames: Record<Ranking, string> = { heat: '平均热度', opportunities: '增长机会', content: '内容规模', velocity: '传播势头' };

function GamesPage({ universe, initialGame, onSelect }: Pick<Props, 'universe' | 'initialGame' | 'onSelect'>) {
  const [query, setQuery] = useState('');
  const [genre, setGenre] = useState('all');
  const [ranking, setRanking] = useState<Ranking>('heat');
  const [followedOnly, setFollowedOnly] = useState(false);
  const [followed, setFollowed] = useState(readFollowedGames);
  const [compare, setCompare] = useState<string[]>([]);
  const [feedback, setFeedback] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [limit, setLimit] = useState(8);
  const groupId = useId();
  const groups = useMemo(() => gamePulses(universe), [universe]);
  const pulses = useMemo(() => genrePulses(universe), [universe]);
  const genres = useMemo(() => [...new Set(groups.map(group => group.genre))], [groups]);
  useEffect(() => { const id = resolveGame(initialGame, groups); if (id !== 'all') { setQuery(groups.find(group => group.id === id)?.name || ''); setExpanded(new Set([id])); } }, [initialGame, universe]);
  const valueOf = (group: typeof groups[number]) => ranking === 'heat' ? group.avgHeat ?? -1 : ranking === 'velocity' ? group.avgVelocity ?? -1 : ranking === 'opportunities' ? group.opportunityCount : group.contentCount;
  const visible = groups.filter(group => (genre === 'all' || group.genre === genre) && (!followedOnly || followed.includes(group.id))
    && matchesQuery(group.name + ' ' + group.topics.map(topic => topic.title).join(' '), query)).sort((a, b) => valueOf(b) - valueOf(a) || b.topicCount - a.topicCount || a.name.localeCompare(b.name, 'zh-CN'));
  const comparing = groups.filter(group => compare.includes(group.id));
  const toggle = (id: string) => setExpanded(previous => { const next = new Set(previous); next.has(id) ? next.delete(id) : next.add(id); return next; });
  function follow(id: string) {
    const next = followed.includes(id) ? followed.filter(value => value !== id) : [...followed, id]; setFollowed(next);
    try { localStorage.setItem(modeStorageKey('followed-games'), JSON.stringify(next)); setFeedback('关注已保存在当前浏览器。'); }
    catch { setFeedback('关注已暂存本页，浏览器保存不可用。'); }
  }
  function compareGame(id: string) {
    setCompare(previous => previous.includes(id) ? previous.filter(value => value !== id) : previous.length < 3 ? [...previous, id] : previous);
    if (!compare.includes(id) && compare.length >= 3) setFeedback('最多同时比较三个游戏，请先移除一个。');
  }
  return <div className="dis-page dis-games">
    <PageTitle label="游戏与品类" title={'每一种热爱，\n都有话题。'} description="看看哪里正在被讨论，哪里还有值得研究的机会。从一组话题，走进一个游戏的世界。">
      <div className="dis-total-art" aria-label={groups.length + ' 个游戏或话题群组'}><span>{String(groups.length).padStart(2, '0')}</span><p>个游戏 / 话题群组</p><i aria-hidden="true" /><b aria-hidden="true">✳</b></div>
    </PageTitle>
    <section className="dis-genre-pulse" aria-label="品类脉冲">
      <div className="dis-section-intro"><h2>先听见，品类的脉冲。</h2><p>当前快照，不是历史曲线。点击品类，缩小探索范围。</p></div>
      <div className="dis-pulse-flow">{[...pulses].sort((a, b) => (b.avgHeat ?? -1) - (a.avgHeat ?? -1)).map((pulse, index) => <button key={pulse.id} className={'dis-pulse ' + (genre === pulse.genre ? 'dis-pulse-active' : '')} aria-pressed={genre === pulse.genre} onClick={() => { setGenre(genre === pulse.genre ? 'all' : pulse.genre); setLimit(8); }}>
        <span className="dis-pulse-name">{pulse.name}</span><div className="dis-pulse-column"><i style={{ height: (pulse.avgHeat === null ? 0 : Math.max(3, pulse.avgHeat * 100)) + '%', background: index % 2 ? '#aaa6f8' : '#fb9279' }} /></div><strong>{formatScore(pulse.avgHeat)}<small>/100</small></strong><span>{pulse.topicCount} 个话题 · {pulse.opportunityCount} 个机会</span>
      </button>)}</div>
      {!pulses.length && <p className="dis-data-note">有可用话题后，会在这里汇总品类脉冲。</p>}
      <p className="dis-metric-note">热度 / 势头为现有话题有效评分的算术平均（原始值 0–1，显示为 0–100）；缺失值不计入。增长机会是已通过上游分析门禁的实际方案数量，仍需研究判断。</p>
    </section>
    <div className="dis-find-line"><label className="dis-search"><Icon name="search" size={20} /><input type="search" value={query} onChange={event => { setQuery(event.target.value); setLimit(8); }} aria-label="搜索游戏或关联话题" placeholder="游戏名，或让你好奇的话题…" /></label><span>{count(topicsOf(universe).length)} 个热点，{count(groups.reduce((sum, group) => sum + group.ideaCount, 0))} 个方案</span></div>
    <nav className="dis-genre-list" aria-label="游戏品类筛选"><button className={genre === 'all' ? 'dis-selected' : ''} aria-pressed={genre === 'all'} onClick={() => { setGenre('all'); setLimit(8); }}>全部品类</button>{genres.map(value => <button key={value} className={genre === value ? 'dis-selected' : ''} aria-pressed={genre === value} onClick={() => { setGenre(value); setLimit(8); }}>{value}</button>)}</nav>
    <div className="dis-ranking-controls"><div role="group" aria-label="游戏排行榜切换">{(Object.keys(rankingNames) as Ranking[]).map(value => <button key={value} aria-pressed={ranking === value} onClick={() => { setRanking(value); setLimit(8); }}>{rankingNames[value]}</button>)}</div><button className="dis-followed-filter" aria-pressed={followedOnly} onClick={() => { setFollowedOnly(value => !value); setLimit(8); }}><Icon name="bookmark" size={15} />只看关注（{groups.filter(group => followed.includes(group.id)).length}）</button></div>
    <p className="dis-data-note"><Icon name="shield" size={14} />{isDemoMode ? '示例品类、话题和指标均为合成样本。关注仅保存在当前浏览器。' : '品类只采用明确的实体标签；缺失时显示「未分类」。关注仅保存在当前浏览器。'}</p>
    {feedback && <p className="dis-feedback" role="status">{feedback}</p>}
    {comparing.length > 0 && <section className="dis-comparison" aria-label="游戏增长机会比较"><div className="dis-section-intro"><h2>把机会，放在一起看。</h2><button className="dis-quiet" onClick={() => setCompare([])}>清空比较<Icon name="close" size={14} /></button></div><div className="dis-comparison-scroll"><table><thead><tr><th scope="col">当前快照</th>{comparing.map(group => <th scope="col" key={group.id}><button onClick={() => compareGame(group.id)} aria-label={'移除比较：' + group.name}>{group.name}<Icon name="close" size={13} /></button></th>)}</tr></thead><tbody><tr><th scope="row">平均热度</th>{comparing.map(group => <td key={group.id}>{formatScore(group.avgHeat)}<small>{group.heatSamples}/{group.topicCount} 个有效话题</small></td>)}</tr><tr><th scope="row">传播势头</th>{comparing.map(group => <td key={group.id}>{formatScore(group.avgVelocity)}</td>)}</tr><tr><th scope="row">通过门禁的机会</th>{comparing.map(group => <td key={group.id}>{group.opportunityCount} / {group.ideaCount}</td>)}</tr><tr><th scope="row">关联内容</th>{comparing.map(group => <td key={group.id}>{count(group.contentCount)}</td>)}</tr></tbody></table></div></section>}
    <div className="dis-game-index">{visible.slice(0, limit).map((group, index) => {
      const open = expanded.has(group.id);
      return <section className={'dis-game-section ' + (open ? 'dis-game-open' : '')} key={group.id}>
        <button className="dis-game-row" aria-expanded={open} aria-controls={groupId + '-' + index} onClick={() => toggle(group.id)}>
          <span className="dis-row-number">{String(index + 1).padStart(2, '0')}</span><div className="dis-game-name"><span>{group.genre}<small>{group.genreSource === 'demo' ? '示例品类' : group.genreSource === 'entities' ? '实体标签' : '品类未提供'}</small></span><h2>{group.name}</h2></div>
          <div className="dis-game-counts"><span><b>{formatScore(group.avgHeat)}</b>平均热度</span><span><b>{group.topicCount}</b>热点</span><span><b>{group.opportunityCount}</b>增长机会</span></div><span className="dis-expand-mark"><Icon name={open ? 'close' : 'plus'} size={23} /></span>
        </button>
        <div className="dis-game-actions"><span>{count(group.contentCount)} 条关联内容 · {group.ideaCount} 个方案</span><button aria-pressed={followed.includes(group.id)} onClick={() => follow(group.id)}><Icon name={followed.includes(group.id) ? 'check' : 'bookmark'} size={14} />{followed.includes(group.id) ? '已关注' : '关注游戏'}</button><button aria-pressed={compare.includes(group.id)} onClick={() => compareGame(group.id)}><Icon name="layers" size={14} />{compare.includes(group.id) ? '已加入比较' : '加入比较'}</button>{group.topics[0] && <button onClick={() => onSelect(group.topics[0].id)}>探索星图<Icon name="arrow" size={14} /></button>}</div>
        {open && <div className="dis-game-topics" id={groupId + '-' + index}><p>从这里进入讨论现场</p>{group.topics.map(topic => <button key={topic.id} onClick={() => onSelect(topic.id)}><div><span>{lifecycleNames[topic.lifecycle || ''] || '状态待确认'}</span><h3>{topic.title}</h3><small>{platformsOf(topic).map(platformLabel).join(' / ') || '平台未标注'}</small></div><span>热度 {formatScore(topic.heat)}<Icon name="arrow" size={20} /></span></button>)}</div>}
      </section>;
    })}
    {!visible.length && <div className="dis-empty"><Icon name="search" size={32} /><h2>{followedOnly ? '下一次好奇，从关注开始。' : groups.length ? '换个词，再找找。' : '新的讨论，还在路上。'}</h2><p>{followedOnly ? '当前筛选没有已关注的游戏。关注保存在本浏览器，可以随时更改。' : groups.length ? '当前品类和关键词没有匹配的游戏或话题。' : '上游提供热点后，游戏索引会在这里展开。'}</p>{groups.length > 0 && <button className="dis-action" onClick={() => { setQuery(''); setGenre('all'); setFollowedOnly(false); }}>查看全部游戏<Icon name="arrow" size={16} /></button>}</div>}</div>
    {visible.length > limit && <button className="dis-load-more" onClick={() => setLimit(value => value + 8)}>再发现 {Math.min(8, visible.length - limit)} 个世界<Icon name="plus" size={17} /></button>}
  </div>;
}

function dateKey(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
}

function TimelinePage({ universe, initialGame, initialPlatform, initialTopicId, onSelect, onResearch }: Pick<Props, 'universe' | 'initialGame' | 'initialPlatform' | 'initialTopicId' | 'onSelect' | 'onResearch'>) {
  const groups = useMemo(() => groupGames(universe), [universe]);
  const topics = topicsOf(universe);
  const [game, setGame] = useState(() => resolveGame(initialGame, groups));
  const [platform, setPlatform] = useState(initialPlatform || 'all');
  const [topicScope, setTopicScope] = useState(initialTopicId || 'all');
  const [pickedDate, setPickedDate] = useState<string | null>(null);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [order, setOrder] = useState<'newest' | 'oldest'>('newest');
  const chosenGroup = groups.find(group => group.id === game);
  const chosenIds = chosenGroup ? new Set(chosenGroup.topics.map(topic => topic.id)) : null;
  const filteredTopics = topics.filter(topic => (!chosenIds || chosenIds.has(topic.id)) && (platform === 'all' || platformsOf(topic).includes(platform)) && (topicScope === 'all' || topic.id === topicScope));
  // 合成观察日期仅用于 demo；live 只映射现有 firstSeen，不补造后续传播事件。
  const observations = isDemoMode ? topics.map((topic, index) => ({ topic, date: demoHistory.daily[index % demoHistory.daily.length].date }))
    : topics.map(topic => ({ topic, date: dateKey(topic.firstSeen) })).filter((entry): entry is { topic: Planet; date: string } => Boolean(entry.date));
  const rangeError = Boolean(start && end && start > end);
  const filteredIds = new Set(filteredTopics.map(topic => topic.id));
  const matching = observations.filter(entry => filteredIds.has(entry.topic.id) && (!pickedDate || entry.date === pickedDate) && (!start || entry.date >= start) && (!end || entry.date <= end) && !rangeError);
  const dates = [...new Set(matching.map(entry => entry.date))].sort((a, b) => order === 'newest' ? b.localeCompare(a) : a.localeCompare(b));
  const allDates = [...new Set(observations.map(entry => entry.date))].sort();
  const missingDates = filteredTopics.filter(topic => !dateKey(topic.firstSeen)).length;
  const platforms = [...new Set(topics.flatMap(platformsOf))];
  const max = Math.max(1, ...demoHistory.daily.map(point => point.discussions));
  useEffect(() => { setGame(resolveGame(initialGame, groups)); setPlatform(initialPlatform || 'all'); setTopicScope(initialTopicId || 'all'); setPickedDate(null); }, [initialGame, initialPlatform, initialTopicId]);
  function reset() { setGame('all'); setPlatform('all'); setTopicScope('all'); setPickedDate(null); setStart(''); setEnd(''); }
  function recent() { const latest = allDates.at(-1); if (!latest) return; const earliest = new Date(latest + 'T00:00:00Z'); earliest.setUTCDate(earliest.getUTCDate() - 6); setStart(earliest.toISOString().slice(0, 10)); setEnd(latest); setPickedDate(null); }
  return <div className="dis-page dis-timeline">
    <PageTitle label="热点时间线" title={'把讨论的脚步，\n留在时间里。'} description={isDemoMode ? '沿着一段合成的观察旅程，体验话题如何成为值得研究的线索。' : '按游戏、平台与时间范围，找到已有话题第一次进入视野的时刻。'}>
      <div className="dis-time-art" aria-hidden="true"><i /><span>过去</span><b>此刻</b><em>→</em></div>
    </PageTitle>
    <div className="dis-timeline-filters"><label>游戏<select value={game} onChange={event => { setGame(event.target.value); setPickedDate(null); }} aria-label="时间线游戏筛选"><option value="all">全部游戏与话题</option>{groups.map(group => <option value={group.id} key={group.id}>{group.name}</option>)}</select></label><label>平台<select aria-label="时间线平台筛选" value={platform} onChange={event => { setPlatform(event.target.value); setPickedDate(null); }}><option value="all">全部平台</option>{platforms.map(value => <option value={value} key={value}>{platformLabel(value)}</option>)}</select></label><label className="dis-topic-filter">话题<select aria-label="时间线话题筛选" value={topicScope} onChange={event => { setTopicScope(event.target.value); setPickedDate(null); }}><option value="all">全部话题</option>{topics.map(topic => <option key={topic.id} value={topic.id}>{topic.title}</option>)}</select></label></div>
    <div className="dis-date-range"><label>从<input type="date" value={start} max={end || undefined} onChange={event => { setStart(event.target.value); setPickedDate(null); }} aria-label="时间线开始日期" /></label><span aria-hidden="true">—</span><label>到<input type="date" min={start || undefined} value={end} onChange={event => { setEnd(event.target.value); setPickedDate(null); }} aria-label="时间线结束日期" /></label><button className="dis-quiet" onClick={recent} disabled={!allDates.length}>最近记录的七天</button><button className="dis-quiet" onClick={reset}>重置筛选<Icon name="refresh" size={14} /></button></div>
    {rangeError && <p className="dis-feedback" role="alert">结束日期需要晚于或等于开始日期。</p>}
    <div className="dis-timeline-disclosure"><Icon name={isDemoMode ? 'eye' : 'calendar'} size={20} /><div><b>{isDemoMode ? '合成示例时间线 · 供体验' : '首次发现记录；未接传播历史'}</b><p>{isDemoMode ? '日期、讨论量与观察事件均为演示。游戏、平台、话题和范围只筛选事件列表，图表始终是全网合成样本。' : '只使用上游已有首次发现时间。按平台筛选代表该话题包含该平台，不代表当时在该平台发生传播。缺失日期不补造，空档不推断。'}</p></div></div>
    {isDemoMode && <section className="dis-week-journey" aria-label="七天的全网合成讨论量"><div className="dis-journey-heading"><h2>七天，一段观察。</h2><span>全网合成讨论量 / 条</span></div><div className="dis-date-steps">{demoHistory.daily.map(point => <button key={point.date} className={pickedDate === point.date ? 'dis-date-active' : ''} aria-pressed={pickedDate === point.date} onClick={() => setPickedDate(value => value === point.date ? null : point.date)} aria-label={'查看 ' + point.date + ' 的示例事件，合成讨论量 ' + point.discussions + ' 条'}><span>{point.label}</span><div className="dis-day-volume"><i style={{ height: point.discussions / max * 100 + '%' }} /></div><strong>{count(point.discussions)}</strong><small>{observations.filter(entry => entry.date === point.date && filteredIds.has(entry.topic.id)).length} 个示例观察</small></button>)}</div></section>}
    <div className="dis-timeline-heading"><h2>{pickedDate ? pickedDate.replaceAll('-', '.') + ' 的观察' : isDemoMode ? '沿途遇见的话题' : '从首次发现开始'}</h2><div><span>{matching.length} 条{isDemoMode ? '示例观察' : '已有记录'}</span><button className="dis-quiet" onClick={() => setOrder(value => value === 'newest' ? 'oldest' : 'newest')}>{order === 'newest' ? '最新在前' : '最早在前'}<Icon name="down" size={14} /></button>{pickedDate && <button className="dis-quiet" onClick={() => setPickedDate(null)}>全部日期</button>}</div></div>
    <div className="dis-event-flow">{dates.map(date => <section key={date} className="dis-event-day"><button className="dis-event-date" onClick={() => setPickedDate(value => value === date ? null : date)} aria-label={'筛选 ' + date + ' 的记录'}><time dateTime={date}><span>{date.slice(5, 7)}月</span><strong>{date.slice(8, 10)}</strong><small>{date.slice(0, 4)}</small></time></button><div className="dis-event-items">{matching.filter(entry => entry.date === date).map(({ topic }) => <div className="dis-event-record" key={topic.id}><button className="dis-event" onClick={() => onSelect(topic.id)}><span className="dis-event-line" aria-hidden="true" /><div><span className="dis-event-label">{isDemoMode ? '体验观察 · 合成' : '首次发现'}<i />{topic.gameName || '跨游戏话题'}</span><h3>{topic.title}</h3><p>{isDemoMode ? '一条话题进入视野，等待补充证据与判断。' : '上游记录时间：' + new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(topic.firstSeen!)) + '（北京时间）'}</p><span className="dis-event-platforms">{platformsOf(topic).map(platformLabel).join(' · ') || '平台未标注'}</span></div><Icon name="arrow" size={22} /></button>{onResearch && <button className="dis-event-research" onClick={() => onResearch(topic.id)}><Icon name="bookmark" size={13} />加入研究夹</button>}</div>)}</div></section>)}</div>
    {!matching.length && <div className="dis-empty"><Icon name="calendar" size={34} /><h2>{filteredTopics.length && observations.length ? '这一页时间，暂时留白。' : '等待第一条可用记录。'}</h2><p>{isDemoMode ? '当前筛选下没有示例观察，可以放宽范围。' : '当前筛选下没有有效首次发现记录，暂不生成时间线。'}</p><button className="dis-action" onClick={reset}>查看全部记录<Icon name="arrow" size={16} /></button></div>}
    {!isDemoMode && missingDates > 0 && <p className="dis-data-note">{missingDates} 个话题尚无有效首次发现时间，仍可从游戏索引访问。</p>}
  </div>;
}

function safeLink(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const raw = value.trim();
  if (!/^https?:\/\/[^/\s]/i.test(raw) || /[\u0000-\u001f\u007f]/.test(raw)) return undefined;
  try {
    const url = new URL(raw);
    return ['https:', 'http:'].includes(url.protocol) && url.hostname && !url.username && !url.password ? url.href : undefined;
  } catch { return undefined; }
}
const favoritesKey = `taptap-public-materials:v1:${isDemoMode ? 'demo' : `live:${encodeURIComponent(apiBaseUrl || 'same-origin')}`}`;
function readFavorites(): { materials: Record<string, Material>; failed: boolean } {
  try {
    const raw = localStorage.getItem(favoritesKey);
    if (!raw) return { materials: {}, failed: false };
    const stored: unknown = JSON.parse(raw);
    if (!Array.isArray(stored)) return { materials: {}, failed: true };
    const materials: Record<string, Material> = {};
    for (const item of stored) {
      if (!item || typeof item !== 'object') continue;
      const entry = item as Material;
      if (typeof entry.id === 'string' && typeof entry.topicId === 'string' && typeof entry.topicTitle === 'string' && typeof entry.platform === 'string' && typeof entry.title === 'string' && typeof entry.excerpt === 'string') materials[entry.id] = { ...entry, url: safeLink(entry.url) };
    }
    return { materials, failed: false };
  } catch { return { materials: {}, failed: true }; }
}
function downloadMaterials(items: Material[]) {
  const mode = isDemoMode ? '合成演示素材，未经真实采集验证' : '当前 API 证据片段';
  const text = `# TapTap 研究素材包\n\n数据说明：${mode}\n导出时间：${new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date())}（北京时间）\n素材数量：${items.length}\n\n${items.map((item, index) => `## ${index + 1}. ${item.title}\n\n关联话题：${item.topicTitle}\n来源平台：${platformLabel(item.platform)}\n${safeLink(item.url) ? `原文链接：${safeLink(item.url)}\n` : '原文链接：未提供\n'}\n${item.excerpt}`).join('\n\n---\n\n')}\n`;
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url;
  const date = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  link.download = `TapTap-素材包-${date}.md`; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function LibraryPage({ universe, initialTopicId, initialTab, initialGame, initialPlatform, onSelect, onWorkspace, onResearch }: Pick<Props, 'universe' | 'initialTopicId' | 'initialTab' | 'initialGame' | 'initialPlatform' | 'onSelect' | 'onWorkspace' | 'onResearch'>) {
  const topics = topicsOf(universe);
  const groups = useMemo(() => groupGames(universe), [universe]);
  const [tab, setTab] = useState<'materials' | 'strategies'>(initialTab || 'materials');
  const [game, setGame] = useState(() => resolveGame(initialGame, groups));
  const [topicId, setTopicId] = useState(() => {
    const chosen = groups.find(group => group.id === resolveGame(initialGame, groups));
    const available = chosen ? chosen.topics : topics;
    return available.find(topic => topic.id === initialTopicId)?.id || available[0]?.id || '';
  });
  const [topicScope, setTopicScope] = useState(initialTopicId || 'all');
  const [query, setQuery] = useState(''); const [platform, setPlatform] = useState(initialPlatform || 'all'); const [type, setType] = useState('all');
  const [savedOnly, setSavedOnly] = useState(false);
  const [initialFavorites] = useState(readFavorites);
  const [favorites, setFavorites] = useState(initialFavorites.materials);
  const [selectedMaterials, setSelectedMaterials] = useState<Set<string>>(() => new Set());
  const [selectedStrategy, setSelectedStrategy] = useState('');
  const [retry, setRetry] = useState(0);
  const [response, setResponse] = useState<{ id: string; data: any; materials: Material[]; loading: boolean; error: string }>({ id: '', data: null, materials: [], loading: false, error: '' });
  const [feedback, setFeedback] = useState(initialFavorites.failed ? '浏览器收藏暂时无法读取，仍可查看、复制和下载素材。' : '');
  const feedbackTimer = useRef(0);
  const incomingContext = JSON.stringify([initialTopicId || null, initialTab || null, initialGame || null, initialPlatform || null]);
  const appliedContext = useRef(incomingContext);
  const skipRouteWrite = useRef(false);
  const availableTopics = useMemo(() => groups.find(group => group.id === game)?.topics || (game === 'all' ? topics : []), [groups, topics, game]);
  const gameTopicIds = useMemo(() => new Set(availableTopics.map(topic => topic.id)), [availableTopics]);
  const strategies = useMemo(() => strategyEntries(universe), [universe]);
  const visibleStrategies = useMemo(() => strategies.filter(entry => (type === 'all' || entry.idea.type === type) && (platform === 'all' || (entry.topic && platformsOf(entry.topic).includes(platform)))
    && (game === 'all' || gameTopicIds.has(entry.topicId)) && (topicScope === 'all' || entry.topicId === topicScope)
    && matchesQuery(entry.idea.name + ' ' + (entry.topic?.title || '') + ' ' + (entry.topic?.gameName || ''), query)), [strategies, query, type, platform, game, gameTopicIds, topicScope]);
  const strategy = visibleStrategies.find(entry => entry.key === selectedStrategy) || visibleStrategies[0];
  const activeTopicId = tab === 'strategies' ? strategy?.topicId || '' : topicId;
  const activeTopic = topics.find(topic => topic.id === activeTopicId);
  const sourceMaterials = savedOnly ? Object.values(favorites) : response.id === topicId ? response.materials : [];
  const materials = sourceMaterials.filter(item => (platform === 'all' || item.platform === platform) && (game === 'all' || gameTopicIds.has(item.topicId))
    && matchesQuery(item.title + ' ' + item.excerpt + ' ' + item.topicTitle, query));
  const platformOptions = [...new Set([...topics.flatMap(platformsOf), ...Object.values(favorites).map(item => item.platform), ...response.materials.map(item => item.platform), ...(initialPlatform ? [initialPlatform] : [])])].filter(Boolean);
  const typeOptions = [...new Set(strategies.map(entry => entry.idea.type).filter(Boolean))];
  const chosenMaterials = materials.filter(item => selectedMaterials.has(item.id));
  const busy = tab === 'materials' && !savedOnly && Boolean(activeTopic) && (response.id !== topicId || response.loading);
  const detail = response.id === activeTopicId ? response.data : null;
  const recommendation = typeof detail?.recommendation === 'string' ? detail.recommendation : '';
  const summary = typeof detail?.summary === 'string' ? detail.summary : typeof detail?.what_happened?.summary === 'string' ? detail.what_happened.summary : '';

  useEffect(() => () => window.clearTimeout(feedbackTimer.current), []);
  useEffect(() => {
    if (appliedContext.current === incomingContext) return;
    appliedContext.current = incomingContext; skipRouteWrite.current = true;
    const nextGame = resolveGame(initialGame, groups);
    const available = groups.find(group => group.id === nextGame)?.topics || topics;
    setGame(nextGame); setPlatform(initialPlatform || 'all'); setTab(initialTab || 'materials');
    setTopicId(available.find(topic => topic.id === initialTopicId)?.id || available[0]?.id || '');
    setTopicScope(initialTopicId || 'all'); setQuery(''); setType('all'); setSelectedStrategy(''); setSavedOnly(false); setSelectedMaterials(new Set());
  }, [incomingContext, universe]);
  useEffect(() => {
    if (!availableTopics.some(topic => topic.id === topicId)) setTopicId(availableTopics[0]?.id || '');
    if (topicScope !== 'all' && !availableTopics.some(topic => topic.id === topicScope)) setTopicScope('all');
  }, [availableTopics, topicId, topicScope]);
  useEffect(() => {
    if (skipRouteWrite.current) { skipRouteWrite.current = false; return; }
    // Preserve the library context without firing hashchange or issuing an API command.
    const [path, raw] = location.hash.split('?');
    if (!/^#\/library\/?$/.test(path)) return;
    const params = new URLSearchParams(raw || '');
    const routeTopic = tab === 'strategies' ? topicScope === 'all' ? '' : topicScope : topicId;
    for (const [key, value] of [['topic', routeTopic], ['tab', tab], ['game', game === 'all' ? '' : game], ['platform', platform === 'all' ? '' : platform]]) {
      if (value) params.set(key, value); else params.delete(key);
    }
    const next = path + (params.size ? '?' + params.toString() : '');
    if (next !== location.hash) history.replaceState(history.state, '', location.pathname + location.search + next);
  }, [tab, topicId, topicScope, game, platform, incomingContext]);
  useEffect(() => {
    if (!activeTopic || (tab === 'materials' && savedOnly)) return;
    let current = true;
    const id = activeTopic.id;
    setResponse({ id, data: null, materials: [], loading: true, error: '' });
    api.topic(id).then(data => { if (current) setResponse({ id, data, materials: normalizeEvidence(data, activeTopic), loading: false, error: '' }); })
      .catch(error => { if (current) setResponse({ id, data: null, materials: [], loading: false, error: error instanceof Error ? error.message : '素材读取失败，请稍后重试。' }); });
    return () => { current = false; };
  }, [activeTopic, retry, tab, savedOnly]);

  function say(message: string) { window.clearTimeout(feedbackTimer.current); setFeedback(message); feedbackTimer.current = window.setTimeout(() => setFeedback(''), 5000); }
  function toggleFavorite(item: Material) {
    const next = { ...favorites }; const removing = Boolean(next[item.id]);
    if (removing) delete next[item.id]; else next[item.id] = item;
    setFavorites(next);
    try { localStorage.setItem(favoritesKey, JSON.stringify(Object.values(next))); say(removing ? '已取消本地收藏。' : '素材已收藏到当前浏览器。'); }
    catch { say('收藏已暂存于本页；浏览器保存不可用，请下载素材包保留内容。'); }
  }
  async function copyMaterial(item: Material) {
    try { await navigator.clipboard.writeText(`${item.title}\n\n${item.excerpt}${safeLink(item.url) ? `\n\n${safeLink(item.url)}` : ''}`); say('片段已复制。'); }
    catch { say('当前浏览器无法自动复制，可以手动选择片段文字。'); }
  }
  function download() {
    const items = chosenMaterials.length ? chosenMaterials : materials;
    if (!items.length) return;
    try { downloadMaterials(items); say(`已下载 ${items.length} 条实际素材内容。`); } catch { say('素材包下载未完成，请重试。'); }
  }
  const toggleMaterial = (id: string) => setSelectedMaterials(previous => { const next = new Set(previous); next.has(id) ? next.delete(id) : next.add(id); return next; });
  function changeTab(next: 'materials' | 'strategies') { if (next === 'strategies') setTopicScope(topicId || 'all'); else if (activeTopicId && gameTopicIds.has(activeTopicId)) setTopicId(activeTopicId); setTab(next); setQuery(''); setType('all'); setSelectedMaterials(new Set()); }

  return <div className="dis-page dis-library">
    <PageTitle label="素材与策略" title={'灵感有来处。\n好点子，也有下一步。'} description="把证据片段留下来，把增长方案看清楚。用可追溯的内容，开始自己的研究。">
      <div className="dis-library-art" aria-hidden="true"><span>灵感</span><i>→</i><b>行动</b><em>＋</em></div>
    </PageTitle>
    <div className="dis-library-tabs" role="tablist" aria-label="素材与策略切换"><button role="tab" aria-selected={tab === 'materials'} className={tab === 'materials' ? 'dis-tab-active' : ''} onClick={() => changeTab('materials')}>研究素材<span>{Object.keys(favorites).length ? `收藏 ${Object.keys(favorites).length}` : '留下有用的片段'}</span></button><button role="tab" aria-selected={tab === 'strategies'} className={tab === 'strategies' ? 'dis-tab-active' : ''} onClick={() => changeTab('strategies')}>增长策略<span>{strategies.length} 个上游方案</span></button></div>
    <div className="dis-library-filters"><label className="dis-search"><Icon name="search" size={19} /><input type="search" value={query} onChange={event => setQuery(event.target.value)} aria-label={tab === 'materials' ? '搜索素材片段' : '搜索增长方案'} placeholder={tab === 'materials' ? '找到一句话，或一条线索…' : '搜索方案或关联话题…'} /></label>
      <label className="dis-select-field">游戏<select value={game} onChange={event => { setGame(event.target.value); setTopicScope('all'); setSelectedMaterials(new Set()); }} aria-label="素材或策略游戏筛选"><option value="all">全部游戏</option>{groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label>
      {tab === 'materials' ? <label className="dis-select-field">话题<select value={topicId} disabled={savedOnly} onChange={event => { setTopicId(event.target.value); setSelectedMaterials(new Set()); }} aria-label="素材话题选择">{availableTopics.map(topic => <option key={topic.id} value={topic.id}>{topic.title}</option>)}{!availableTopics.length && <option value="">暂无话题</option>}</select></label> : <label className="dis-select-field">方案类型<select value={type} onChange={event => setType(event.target.value)} aria-label="策略类型筛选"><option value="all">全部类型</option>{typeOptions.map(value => <option key={value} value={value}>{value}</option>)}</select></label>}
      <label className="dis-select-field">平台<select value={platform} onChange={event => setPlatform(event.target.value)} aria-label="素材或策略平台筛选"><option value="all">全部平台</option>{platformOptions.map(value => <option key={value} value={value}>{platformLabel(value)}</option>)}</select></label>
    </div>
    {tab === 'strategies' && <div className="dis-strategy-context"><label>研究话题<select aria-label="策略话题筛选" value={topicScope} onChange={event => { setTopicScope(event.target.value); setSelectedStrategy(''); }}><option value="all">全部话题的方案</option>{availableTopics.map(topic => <option key={topic.id} value={topic.id}>{topic.title}</option>)}</select></label>{topicScope !== 'all' && <button className="dis-quiet" onClick={() => setTopicScope('all')}>查看全部话题<Icon name="arrow" size={14} /></button>}</div>}
    {isDemoMode && <p className="dis-data-note"><Icon name="eye" size={14} />当前素材、建议与评分均为合成示例，不是实际采集记录。</p>}
    {feedback && <p className="dis-feedback" role="status"><Icon name="check" size={15} />{feedback}</p>}
    {tab === 'materials' ? <section className="dis-materials-view" role="tabpanel" aria-label="研究素材">
      <div className="dis-material-toolbar"><div><button className={!savedOnly ? 'dis-view-active' : ''} aria-pressed={!savedOnly} onClick={() => { setSavedOnly(false); setSelectedMaterials(new Set()); }}>话题素材</button><button className={savedOnly ? 'dis-view-active' : ''} aria-pressed={savedOnly} onClick={() => { setSavedOnly(true); setSelectedMaterials(new Set()); }}>我的收藏 <span>{Object.keys(favorites).length}</span></button></div><button className="dis-action" disabled={!materials.length || busy} onClick={download}><Icon name="download" size={16} />{chosenMaterials.length ? `下载选中片段（${chosenMaterials.length}）` : '下载当前素材'}</button></div>
      <p className="dis-material-note">{savedOnly ? '收藏保存在当前浏览器。下载素材包可以在浏览器之外继续研究。' : '片段来自所选话题的证据结构。来源未提供原文地址时，不生成链接。'}</p>
      {busy && <div className="dis-empty" role="status"><Icon name="refresh" size={32} /><h2>正在打开素材夹…</h2><p>读取这个话题已有的证据片段。</p></div>}
      {!busy && !savedOnly && response.id === topicId && response.error && <div className="dis-empty" role="alert"><Icon name="pulse" size={32} /><h2>这一次，没能读到素材。</h2><p>{response.error}</p><button className="dis-action" onClick={() => setRetry(value => value + 1)}>再试一次<Icon name="refresh" size={16} /></button></div>}
      {!busy && materials.map((item, index) => <article className="dis-material" key={item.id}><div className="dis-material-margin"><span>{String(index + 1).padStart(2, '0')}</span><small>{platformLabel(item.platform)}</small><label><input type="checkbox" checked={selectedMaterials.has(item.id)} onChange={() => toggleMaterial(item.id)} aria-label={`选择素材：${item.title}`} />加入素材包</label></div><div className="dis-material-paper"><span className="dis-quote-mark" aria-hidden="true">“</span><h2>{item.title}</h2><p>{item.excerpt}</p><button className="dis-material-topic" onClick={() => onSelect(item.topicId)}>{item.topicTitle}<Icon name="arrow" size={15} /></button><div className="dis-material-actions"><button onClick={() => void copyMaterial(item)}><Icon name="file" size={15} />复制片段</button>{onResearch && <button onClick={() => onResearch(item.topicId)}><Icon name="layers" size={15} />加入研究夹</button>}<button aria-pressed={Boolean(favorites[item.id])} onClick={() => toggleFavorite(item)}><Icon name={favorites[item.id] ? 'check' : 'bookmark'} size={15} />{favorites[item.id] ? '已收藏' : '收藏'}</button>{safeLink(item.url) && <a href={safeLink(item.url)} target="_blank" rel="noopener noreferrer">查看原文<Icon name="link" size={15} /></a>}</div></div></article>)}
      {!busy && !materials.length && (savedOnly || !response.error) && <div className="dis-empty"><Icon name="bookmark" size={32} /><h2>{savedOnly ? '给灵感，留一个位置。' : '这里暂时没有证据片段。'}</h2><p>{savedOnly ? '在话题素材中收藏有用的内容，再回来慢慢研究。' : query || platform !== 'all' ? '可以放宽关键词与平台筛选。' : '换一个话题看看，或等待上游补充可追溯的证据。'}</p>{(query || platform !== 'all') && <button className="dis-action" onClick={() => { setQuery(''); setPlatform('all'); }}>清除筛选<Icon name="arrow" size={16} /></button>}</div>}
    </section> : <section className="dis-strategy-view" role="tabpanel" aria-label="增长策略"><div className="dis-strategy-list">{visibleStrategies.map((entry, index) => <button className={strategy?.key === entry.key ? 'dis-strategy-selected' : ''} key={entry.key} onClick={() => setSelectedStrategy(entry.key)} aria-pressed={strategy?.key === entry.key}><span className="dis-strategy-index">{String(index + 1).padStart(2, '0')}</span><div><span>{entry.idea.type || '类型未提供'}</span><h2>{entry.idea.name}</h2><p>{entry.topic?.title || entry.topicId}</p></div><Icon name="arrow" size={22} /></button>)}{!visibleStrategies.length && <div className="dis-empty"><Icon name="spark" size={32} /><h2>下一条好点子，还在酝酿。</h2><p>{strategies.length ? '调整平台、类型或关键词，看看其他方案。' : '上游分析产出方案后，会显示在这里。'}</p></div>}</div>
      {strategy && <aside className="dis-strategy-detail"><span className="dis-kicker">先研究，再行动</span><h2>{strategy.idea.name}</h2><div className="dis-strategy-score"><div><span>方案评分</span><strong>{formatScore(strategy.idea.score)}<small>/ 100</small></strong></div><p><Icon name={strategy.idea.passed === true ? 'check' : 'eye'} size={16} />{strategy.idea.passed === true ? '通过分析门禁' : strategy.idea.passed === false ? '未通过分析门禁' : '暂无门禁结论'}</p></div><button className="dis-strategy-topic" onClick={() => onSelect(strategy.topicId)}><span>关联话题</span><b>{strategy.topic?.title || strategy.topicId}</b><Icon name="arrow" size={18} /></button>
        <section><h3>话题建议</h3>{response.id === activeTopicId && response.loading ? <p>正在读取已有建议…</p> : recommendation ? <p>{recommendation}</p> : <p>上游尚未提供具体建议。先记录待核实的问题，再决定是否推进。</p>}{response.id === activeTopicId && response.error && <><p className="dis-inline-error">{response.error}</p><button className="dis-quiet" onClick={() => setRetry(value => value + 1)}>重新读取建议<Icon name="refresh" size={14} /></button></>}</section>{summary && <section><h3>相关情境</h3><p>{summary}</p></section>}{onResearch && <button className="dis-research-cta" onClick={() => onResearch(strategy.topicId)}><Icon name="bookmark" size={16} />加入研究夹</button>}{onWorkspace && <button className="dis-action dis-action-light" onClick={() => onWorkspace(strategy.topicId, strategy.idea.id)}>进入工作台整理<Icon name="arrow" size={19} /></button>}<p className="dis-strategy-boundary">评分与门禁结果来自上游。进入工作台可记录本地草稿，此处不会执行或提交方案。</p>
      </aside>}
    </section>}
  </div>;
}

export function DiscoveryPages(props: Props) {
  if (props.page === 'games') return <GamesPage {...props} />;
  if (props.page === 'timeline') return <TimelinePage {...props} />;
  return <LibraryPage {...props} />;
}
