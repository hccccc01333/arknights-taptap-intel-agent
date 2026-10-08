import { isDemoMode, type Idea, type Planet, type Universe } from '../lib/api';

export type GameGroup = {
  id: string;
  name: string;
  topics: Planet[];
  contentCount: number;
  ideaCount: number;
  genre: string;
  genreSource: 'demo' | 'entities' | 'unknown';
};

export type GamePulse = GameGroup & {
  topicCount: number;
  /** Actual concrete ideas whose passed field is strictly true. */
  opportunityCount: number;
  /** Original 0..1 units; display code may multiply by 100. No samples means null. */
  avgHeat: number | null;
  avgVelocity: number | null;
  heatSamples: number;
  velocitySamples: number;
};

export type GenrePulse = {
  id: string;
  name: string;
  genre: string;
  /** Unknown also covers a category whose member games do not share one source. */
  genreSource: GameGroup['genreSource'];
  games: GamePulse[];
  topics: Planet[];
  gameCount: number;
  topicCount: number;
  contentCount: number;
  ideaCount: number;
  opportunityCount: number;
  /** Arithmetic topic-sample means in original 0..1 units, not means of game means. */
  avgHeat: number | null;
  avgVelocity: number | null;
  heatSamples: number;
  velocitySamples: number;
};

export type Material = {
  id: string;
  topicId: string;
  topicTitle: string;
  platform: string;
  title: string;
  excerpt: string;
  url?: string;
  sourceReference?: string;
  publishedAt?: string;
  usage?: string;
};

export type StrategyEntry = {
  id: string;
  key: string;
  idea: Idea;
  topic?: Planet;
  topicId: string;
};

const platformAliases: Record<string, string> = {
  taptap: 'taptap', 'tap tap': 'taptap',
  bilibili: 'bilibili', '哔哩哔哩': 'bilibili', 'b站': 'bilibili',
  weibo: 'weibo', '微博': 'weibo',
  baidu: 'baidu', baidu_hot: 'baidu', '百度': 'baidu',
  douyin: 'douyin', '抖音': 'douyin',
  steam: 'steam', zhihu: 'zhihu', '知乎': 'zhihu',
  xiaohongshu: 'xiaohongshu', '小红书': 'xiaohongshu',
};
const platformLabels: Record<string, string> = {
  taptap: 'TapTap', bilibili: '哔哩哔哩', weibo: '微博', baidu: '百度',
  douyin: '抖音', steam: 'Steam', zhihu: '知乎', xiaohongshu: '小红书',
};

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined;
}

function platformKey(value: unknown): string {
  return platformAliases[text(value).toLowerCase()] || 'unknown';
}

/** Unknown keys and evidence tiers never become user-facing platform names. */
export function platformLabel(value: unknown): string {
  return platformLabels[platformKey(value)] || '来源未标注';
}

// This is an explicit vocabulary for the synthetic demo.game keys, not a live classifier.
const demoGenres: Record<string, string> = {
  genshin: '动作角色扮演', 'star-rail': '回合制角色扮演',
  indie: '独立游戏', 'game-expo': '行业活动', 'steam-festival': '试玩发现',
  'wuthering-waves': '动作角色扮演', 'domestic-games': '国产游戏专题',
  community: '玩家社区', multiplayer: '多人协作', puzzle: '解谜',
  retro: '经典游戏', 'mobile-experience': '移动游戏体验',
};
const genreLabels: Record<string, string> = {
  rpg: '角色扮演', action_rpg: '动作角色扮演', arpg: '动作角色扮演',
  turn_based_rpg: '回合制角色扮演', action: '动作', adventure: '冒险',
  strategy: '策略', simulation: '模拟', puzzle: '解谜', casual: '休闲',
  tower_defense: '塔防', shooter: '射击', racing: '竞速', sports: '体育',
  rhythm: '音游', card: '卡牌', survival: '生存', roguelike: '肉鸽',
};

function explicitGenres(topic: Planet, demo: boolean): string[] {
  if (demo) {
    const label = demoGenres[text(topic.game)];
    return label ? [`${label}（示例品类）`] : [];
  }
  const entities = Array.isArray(topic.entities) ? topic.entities : [];
  return entities.flatMap(entity => {
    const match = /^genre[_:](.+)$/i.exec(text(entity));
    const value = match ? text(match[1]) : '';
    return value ? [genreLabels[value.toLowerCase()] || value] : [];
  });
}

function isIdea(value: unknown): value is Idea {
  const item = record(value);
  return !!item && !!text(item.id) && !!text(item.name) && typeof item.type === 'string'
    && typeof item.score === 'number' && Number.isFinite(item.score)
    && typeof item.passed === 'boolean';
}

/** List only concrete ideas; topic.nIdeas is not used as a substitute for them. */
export function strategyEntries(universe: Universe): StrategyEntry[] {
  const topics = new Map((Array.isArray(universe.planets) ? universe.planets : []).map(topic => [topic.id, topic]));
  const ideas = record(universe.ideas);
  if (!ideas) return [];
  return Object.entries(ideas).flatMap(([topicId, value]) => {
    if (!Array.isArray(value)) return [];
    return value.filter(isIdea).map(idea => ({
      id: idea.id,
      key: `${encodeURIComponent(topicId)}:${encodeURIComponent(idea.id)}`,
      idea,
      topic: topics.get(topicId),
      topicId,
    }));
  });
}

/** Aggregate actual game identity and counts. Genre comes only from explicit labels. */
export function groupGames(universe: Universe, demo = isDemoMode): GameGroup[] {
  const topics = Array.isArray(universe.planets) ? universe.planets : [];
  const gameNames = new Map<string, string>();
  for (const topic of topics) {
    if (text(topic.game) && text(topic.gameName)) gameNames.set(text(topic.game), text(topic.gameName));
  }
  const counts = new Map<string, number>();
  for (const entry of strategyEntries(universe)) counts.set(entry.topicId, (counts.get(entry.topicId) || 0) + 1);
  const groups = new Map<string, GameGroup>();
  const genres = new Map<string, Set<string>>();
  for (const topic of topics) {
    const name = text(topic.gameName) || gameNames.get(text(topic.game)) || text(topic.game) || '未归属游戏';
    const id = name === '未归属游戏' && !text(topic.gameName) && !text(topic.game)
      ? 'game:unknown' : `game:${name}`;
    let group = groups.get(id);
    if (!group) {
      group = { id, name, topics: [], contentCount: 0, ideaCount: 0, genre: '未分类', genreSource: 'unknown' };
      groups.set(id, group);
      genres.set(id, new Set());
    }
    group.topics.push(topic);
    group.contentCount += Number.isFinite(topic.contentCount) ? Math.max(0, topic.contentCount) : 0;
    group.ideaCount += counts.get(topic.id) || 0;
    for (const label of explicitGenres(topic, demo)) genres.get(id)!.add(label);
  }
  for (const group of groups.values()) {
    const values = [...genres.get(group.id)!];
    if (values.length) {
      group.genre = values.join(' / ');
      group.genreSource = demo ? 'demo' : 'entities';
    }
  }
  return [...groups.values()].sort((a, b) => b.contentCount - a.contentCount || a.name.localeCompare(b.name, 'zh-CN'));
}

function metricMean(topics: Planet[], key: 'heat' | 'velocity'): { average: number | null; samples: number } {
  let sum = 0;
  let samples = 0;
  for (const topic of topics) {
    const value = topic[key];
    // Scores use 0..1 in the existing API. Null, non-finite and out-of-range values
    // are missing samples rather than zeros; a real score of 0 remains valid.
    if (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1) {
      sum += value;
      samples += 1;
    }
  }
  return { average: samples ? sum / samples : null, samples };
}

function pulseMetrics(topics: Planet[]): Pick<GamePulse, 'avgHeat' | 'avgVelocity' | 'heatSamples' | 'velocitySamples'> {
  const heat = metricMean(topics, 'heat');
  const velocity = metricMean(topics, 'velocity');
  return {
    avgHeat: heat.average,
    avgVelocity: velocity.average,
    heatSamples: heat.samples,
    velocitySamples: velocity.samples,
  };
}

function pulseOrder(a: Pick<GamePulse, 'avgHeat' | 'contentCount' | 'name'>, b: Pick<GamePulse, 'avgHeat' | 'contentCount' | 'name'>): number {
  if (a.avgHeat === null && b.avgHeat !== null) return 1;
  if (a.avgHeat !== null && b.avgHeat === null) return -1;
  return (b.avgHeat ?? 0) - (a.avgHeat ?? 0)
    || b.contentCount - a.contentCount || a.name.localeCompare(b.name, 'zh-CN');
}

/** Current snapshot only: unweighted valid-topic means and concrete passed ideas.
 * Ranked by mean heat, with missing heat last; no history or inferred genre is added.
 */
export function gamePulses(universe: Universe, demo = isDemoMode): GamePulse[] {
  const opportunities = new Map<string, number>();
  for (const entry of strategyEntries(universe)) {
    if (entry.idea.passed === true) opportunities.set(entry.topicId, (opportunities.get(entry.topicId) || 0) + 1);
  }
  return groupGames(universe, demo).map(group => ({
    ...group,
    ...pulseMetrics(group.topics),
    topicCount: group.topics.length,
    opportunityCount: group.topics.reduce((count, topic) => count + (opportunities.get(topic.id) || 0), 0),
  })).sort(pulseOrder);
}

/** A game's complete genre string forms one category, including composite labels.
 * Each game enters exactly one category, preventing multi-label total duplication.
 * Means use all valid topic samples directly, giving every topic equal weight.
 */
export function genrePulses(universe: Universe, demo = isDemoMode): GenrePulse[] {
  const categories = new Map<string, GamePulse[]>();
  for (const game of gamePulses(universe, demo)) {
    const members = categories.get(game.genre);
    if (members) members.push(game);
    else categories.set(game.genre, [game]);
  }
  return [...categories.entries()].map(([genre, games]) => {
    const topics = games.flatMap(game => game.topics);
    const sources = new Set(games.map(game => game.genreSource));
    const genreSource: GameGroup['genreSource'] = sources.size === 1 ? games[0].genreSource : 'unknown';
    return {
      id: `genre:${encodeURIComponent(genre)}`,
      name: genre,
      genre,
      genreSource,
      games,
      topics,
      gameCount: games.length,
      topicCount: topics.length,
      contentCount: games.reduce((count, game) => count + game.contentCount, 0),
      ideaCount: games.reduce((count, game) => count + game.ideaCount, 0),
      opportunityCount: games.reduce((count, game) => count + game.opportunityCount, 0),
      ...pulseMetrics(topics),
    };
  }).sort(pulseOrder);
}

function sourceUrl(value: unknown): string | undefined {
  const raw = text(value);
  if (!/^https?:\/\/[^/\s]/i.test(raw) || /[\u0000-\u001f\u007f]/.test(raw)) return undefined;
  try {
    const url = new URL(raw);
    return ['http:', 'https:'].includes(url.protocol) && url.hostname && !url.username && !url.password
      ? url.href : undefined;
  } catch { return undefined; }
}

function firstText(item: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = text(item[key]);
    if (value) return value;
  }
  return '';
}

function comparable(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

/** Merge all supported evidence lists, including empty-evidence + populated-panel payloads. */
export function normalizeEvidence(detail: unknown, topic: Planet): Material[] {
  const root = record(detail);
  if (!root) return [];
  const workspace = record(root.workspace);
  const sources: { path: string; value: unknown }[] = [];
  for (const [prefix, container] of [['', root], ['workspace.', workspace]] as const) {
    if (!container) continue;
    sources.push({ path: `${prefix}evidence`, value: container.evidence });
    const panel = record(container.evidence_panel);
    if (panel) {
      sources.push({ path: `${prefix}evidence_panel.facts`, value: panel.facts });
      sources.push({ path: `${prefix}evidence_panel.community`, value: panel.community });
    }
  }
  const materials: Material[] = [];
  for (const source of sources) {
    if (!Array.isArray(source.value)) continue;
    source.value.forEach((value: unknown, index: number) => {
      const item = record(value);
      const excerpt = item ? firstText(item, ['excerpt', 'text', 'snippet', 'summary', 'normalized_text']) : text(value);
      const actualTitle = item ? firstText(item, ['title', 'normalized_title', 'name']) : '';
      if (!actualTitle && !excerpt) return;
      const title = actualTitle || (excerpt.length > 60 ? `${excerpt.slice(0, 60)}…` : excerpt);
      const platform = platformKey(item?.platform);
      const rawUrl = item ? firstText(item, ['url', 'source_url', 'original_url', 'link', 'href', 'permalink']) : '';
      const url = sourceUrl(rawUrl);
      const sourceReference = item ? firstText(item, ['source_id', 'content_id', 'id']) : '';
      const publication = item ? firstText(item, ['published_at', 'publishedAt', 'publish_time']) : '';
      const publishedAt = publication && Number.isFinite(Date.parse(publication)) ? publication : '';
      const usage = item ? firstText(item, ['usage_conditions', 'usage', 'license', 'rights']) : '';
      const body = comparable(excerpt || actualTitle);
      const duplicate = materials.find(material => {
        if (url && material.url) return url === material.url;
        return comparable(material.excerpt || material.title) === body
          && (material.platform === platform || material.platform === 'unknown' || platform === 'unknown');
      });
      if (duplicate) {
        // A panel mirror may omit source metadata that is present in another list.
        if (duplicate.platform === 'unknown' && platform !== 'unknown') duplicate.platform = platform;
        if (!duplicate.url && url) duplicate.url = url;
        if (!duplicate.sourceReference && sourceReference) duplicate.sourceReference = sourceReference;
        if (!duplicate.publishedAt && publishedAt) duplicate.publishedAt = publishedAt;
        if (!duplicate.usage && usage) duplicate.usage = usage;
        return;
      }
      materials.push({
        id: `${encodeURIComponent(topic.id)}:${source.path}:${index}:${encodeURIComponent(title)}`,
        topicId: topic.id,
        topicTitle: topic.title,
        platform,
        title,
        excerpt,
        ...(url ? { url } : {}),
        ...(sourceReference ? { sourceReference } : {}),
        ...(publishedAt ? { publishedAt } : {}),
        ...(usage ? { usage } : {}),
      });
    });
  }
  return materials;
}
