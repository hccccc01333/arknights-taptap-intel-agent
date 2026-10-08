import type { Planet } from './api';

export const platformNames: Record<string, string> = { taptap: 'TapTap', bilibili: '哔哩哔哩', weibo: '微博', baidu: '百度', douyin: '抖音', steam: 'Steam', tieba: '贴吧', media: '游戏媒体', agent: '搜索' };
export const lifecycleNames: Record<string, string> = { EMERGING: '正在形成', GROWING: '持续升温', PEAKING: '热度高峰', REACTIVATED: '再次活跃', DECLINING: '逐渐降温', DORMANT: '趋于平静' };
export const platformColors: Record<string, string> = { taptap: '#46d9b6', bilibili: '#79b6f8', weibo: '#eca286', baidu: '#af9fe8', douyin: '#93c4db', steam: '#a0b8d6' };
export const formatNumber = (n: number) => new Intl.NumberFormat('zh-CN').format(n);
export const formatScore = (n: number | null | undefined) => n == null || !Number.isFinite(n) ? '—' : `${Math.round(n * 100)}`;
export const platformName = (key: string) => platformNames[key] || key;
export const lifecycleColor = (life: string | null) => ['DECLINING', 'DORMANT'].includes(life || '') ? '#aa9aea' : life === 'PEAKING' ? '#e8b477' : '#49d5b4';
export const beijingTime = () => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date());

export function loadLocal<T>(key: string, fallback: T): T {
  try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; } catch { return fallback; }
}
export function saveLocal(key: string, value: unknown): boolean {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}
export function downloadFile(name: string, content: string, type = 'text/markdown;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export type TopicFilters = { query: string; platform: string; lifecycle: string; game: string; scope: 'all' | 'saved' | 'ideas' | 'multi'; sort: 'heat' | 'relevance' | 'velocity' | 'contentCount' };
export const defaultFilters: TopicFilters = { query: '', platform: 'all', lifecycle: 'all', game: 'all', scope: 'all', sort: 'heat' };
export function filterTopics(topics: Planet[], filters: TopicFilters, saved: string[]) {
  const query = filters.query.trim().toLowerCase();
  return topics.filter(p =>
    (!query || `${p.title} ${p.gameName || ''} ${p.category || ''} ${p.platforms.map(platformName).join(' ')} ${p.platforms.join(' ')}`.toLowerCase().includes(query)) &&
    (filters.platform === 'all' || p.platforms.includes(filters.platform)) &&
    (filters.lifecycle === 'all' || (filters.lifecycle === 'rising' ? ['EMERGING', 'GROWING', 'REACTIVATED'].includes(p.lifecycle || '') : p.lifecycle === filters.lifecycle)) &&
    (filters.game === 'all' || (p.game || p.gameName) === filters.game) &&
    (filters.scope === 'all' || (filters.scope === 'saved' && saved.includes(p.id)) || (filters.scope === 'ideas' && p.nIdeas > 0) || (filters.scope === 'multi' && p.platformCount > 1))
  ).sort((a, b) => (b[filters.sort] ?? -Infinity) - (a[filters.sort] ?? -Infinity) || a.title.localeCompare(b.title, 'zh-CN'));
}
