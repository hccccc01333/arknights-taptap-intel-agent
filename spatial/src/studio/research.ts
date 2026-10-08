import { useEffect, useState } from 'react';
import { apiBaseUrl, isDemoMode } from '../lib/api';
import { loadLocal, saveLocal } from '../lib/presentation';

export const researchStatuses = { inbox: '待研究', investigating: '研究中', drafted: '已有结论' } as const;
export type ResearchStatus = keyof typeof researchStatuses;
export type ResearchDraft = { topicId: string; status: ResearchStatus; question: string; audience: string; conclusion: string; nextAction: string; evidenceIds: string[]; ideaId: string; checks: string[]; updatedAt: string };
const KEY = `taptap-pulse-research:v3:${isDemoMode ? 'demo' : apiBaseUrl || 'same-origin'}`;
const EVENT = 'pulse-research-change';
let unsaved: ResearchDraft[] | null = null;
const strings = (v: unknown) => Array.isArray(v) ? [...new Set(v.filter((s): s is string => typeof s === 'string'))].slice(0, 100) : [];
export function readResearch(): ResearchDraft[] {
 if (unsaved) return unsaved;
 const raw = loadLocal<unknown>(KEY, []);
 if (!Array.isArray(raw)) return [];
 const seen = new Set<string>();
 return raw.flatMap(item => {
  if (!item || typeof item.topicId !== 'string' || seen.has(item.topicId)) return [];
  seen.add(item.topicId);
  const value = (key: string) => typeof item[key] === 'string' ? item[key].slice(0, 4000) : '';
  const status: ResearchStatus = Object.keys(researchStatuses).includes(item.status) ? item.status as ResearchStatus : 'inbox';
  return [{ topicId: item.topicId, status, question: value('question'), audience: value('audience'), conclusion: value('conclusion'), nextAction: value('nextAction'), evidenceIds: strings(item.evidenceIds), ideaId: value('ideaId'), checks: strings(item.checks), updatedAt: value('updatedAt') }];
 }).slice(0, 60);
}
export function useResearch() {
 const [drafts, setDrafts] = useState(readResearch);
 useEffect(() => { const sync = () => setDrafts(readResearch()); addEventListener(EVENT, sync); addEventListener('storage', sync); return () => { removeEventListener(EVENT, sync); removeEventListener('storage', sync); }; }, []);
 const write = (next: ResearchDraft[]) => { setDrafts(next); const ok = saveLocal(KEY, next); unsaved = ok ? null : next; dispatchEvent(new Event(EVENT)); return ok; };
 const add = (topicId: string) => {
  const current = readResearch(); if (current.some(d => d.topicId === topicId)) return true;
  return write([...current, { topicId, status: 'inbox' as const, question: '', audience: '', conclusion: '', nextAction: '', evidenceIds: [], ideaId: '', checks: [], updatedAt: new Date().toISOString() }].slice(-60));
 };
 const update = (id: string, patch: Partial<ResearchDraft>) => write(drafts.map(d => d.topicId === id ? { ...d, ...patch, topicId: id, updatedAt: new Date().toISOString() } : d));
 const remove = (id: string) => write(drafts.filter(d => d.topicId !== id));
 return { drafts, add, update, remove };
}
