import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { DragEvent, FormEvent } from 'react';
import type { Idea, Planet, Universe } from '../lib/api';
import { Icon } from './Icon';
import './opportunities.css';

type DraftStatus = 'pending' | 'researching' | 'ready' | 'archived';
type DraftPriority = 'low' | 'normal' | 'high';
type Draft = { status: DraftStatus; notes: string; priority: DraftPriority; targetDate: string; updatedAt: string };
type DraftMap = Record<string, Draft>;
type BoardEntry = { key: string; topicId: string; topic?: Planet; idea: Idea };

// 只写入此功能独立的草稿键；不读取帐号凭证、业务缓存或其他游戏的数据。
const STORAGE_KEY = 'taptap-growth-opportunity-drafts:v1';
const NOTE_LIMIT = 4000;
const columns: { id: DraftStatus; name: string; hint: string; icon: string }[] = [
  { id: 'pending', name: '待研判', hint: '整理线索，留下第一判断', icon: 'spark' },
  { id: 'researching', name: '研究中', hint: '补充证据与可行性备注', icon: 'search' },
  { id: 'ready', name: '准备执行', hint: '记录下一步，尚未提交执行', icon: 'arrow' },
  { id: 'archived', name: '已归档', hint: '保留思考，随时重新研究', icon: 'layers' },
];
const platformNames: Record<string, string> = { taptap: 'TapTap', bilibili: 'B站', weibo: '微博', baidu: '百度', douyin: '抖音' };
const isStatus = (value: unknown): value is DraftStatus => columns.some((column) => column.id === value);
const priorityNames: Record<DraftPriority, string> = { low: '低', normal: '普通', high: '高' };
const isPriority = (value: unknown): value is DraftPriority => value === 'low' || value === 'normal' || value === 'high';
const todayKey = () => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const validDate = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(new Date(value + 'T00:00:00Z').getTime()) && new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;
const defaultDraft: Draft = { status: 'pending', notes: '', priority: 'normal', targetDate: '', updatedAt: '' };

function readDrafts(): { drafts: DraftMap; failed: boolean } {
  if (typeof window === 'undefined') return { drafts: {}, failed: false };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { drafts: {}, failed: false };
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object') return { drafts: {}, failed: true };
    const record = value as { version?: unknown; drafts?: unknown };
    if (record.version !== 1 || !record.drafts || typeof record.drafts !== 'object' || Array.isArray(record.drafts)) {
      return { drafts: {}, failed: true };
    }
    const drafts: DraftMap = {};
    for (const [key, entry] of Object.entries(record.drafts)) {
      if (!entry || typeof entry !== 'object' || !key.startsWith('[')) continue;
      const draft = entry as Partial<Draft>;
      if (isStatus(draft.status) && typeof draft.notes === 'string') {
        drafts[key] = {
          status: draft.status, notes: draft.notes.slice(0, NOTE_LIMIT),
          priority: isPriority(draft.priority) ? draft.priority : 'normal', targetDate: validDate(draft.targetDate) ? draft.targetDate : '',
          updatedAt: typeof draft.updatedAt === 'string' ? draft.updatedAt : '',
        };
      }
    }
    return { drafts, failed: false };
  } catch {
    return { drafts: {}, failed: true };
  }
}

function getEntries(universe: Universe): BoardEntry[] {
  const topics = new Map((Array.isArray(universe?.planets) ? universe.planets : []).map((topic) => [topic.id, topic]));
  const ideas = universe?.ideas;
  if (!ideas || typeof ideas !== 'object' || Array.isArray(ideas)) return [];
  const entries: BoardEntry[] = [];
  const seen = new Set<string>();
  for (const [topicId, list] of Object.entries(ideas)) {
    if (!Array.isArray(list)) continue;
    const topic = topics.get(topicId);
    for (const idea of list) {
      if (!idea || typeof idea.id !== 'string' || !idea.id) continue;
      // game、话题和方案联合标识，避免不同游戏同名方案串用草稿。
      const key = JSON.stringify([topic?.game || '', topicId, idea.id]);
      if (seen.has(key)) continue;
      seen.add(key);
      entries.push({ key, topicId, topic, idea });
    }
  }
  return entries;
}

function IdeaCard({ entry, draft, editing, editorText, focused, dragging, onDragStart, onDragEnd, onSelect, onMove, onPlan, onEdit, onText, onSave, onCancel }: {
  entry: BoardEntry; draft: Draft; editing: boolean; editorText: string; focused: boolean; dragging: boolean;
  onSelect: (id: string) => void; onMove: (status: DraftStatus) => void; onPlan: (patch: Partial<Pick<Draft, 'priority' | 'targetDate'>>) => void;
  onDragStart: (event: DragEvent<HTMLElement>) => void; onDragEnd: () => void;
  onEdit: () => void; onText: (text: string) => void; onSave: () => void; onCancel: () => void;
}) {
  const fieldId = useId();
  const title = entry.idea.name || '未命名方案';
  const score = Number.isFinite(entry.idea.score) ? String(Math.round(entry.idea.score * 100)) : '—';
  const passed = entry.idea.passed === true ? '已通过分析门禁' : entry.idea.passed === false ? '未通过分析门禁' : '门禁结果未提供';
  const platforms = Array.isArray(entry.topic?.platforms) ? entry.topic.platforms : [];
  const handleSave = (event: FormEvent) => { event.preventDefault(); onSave(); };
  return <article className={'opp-card opp-card-' + draft.status + (focused ? ' opp-card-focused' : '') + (dragging ? ' opp-card-dragging' : '')}
    data-draft-key={entry.key} draggable={!editing} onDragStart={onDragStart} onDragEnd={onDragEnd} aria-label={'方案草稿：' + title}>
    {focused && <div className="opp-focus-label"><Icon name="link" size={12} />来自策略页的方案</div>}
    <div className="opp-card-top"><span className="opp-type"><Icon name="spark" size={12} />{entry.idea.type || '类型未提供'}</span><span className="opp-score" title="方案评分 / 100">评分 <b>{score}</b></span></div>
    <h3>{title}</h3>
    <button type="button" className="opp-topic-link" onClick={() => onSelect(entry.topicId)} aria-label={'查看关联话题：' + (entry.topic?.title || entry.topicId)}><Icon name="link" size={13} /><span>{entry.topic?.title || entry.topicId}</span><Icon name="chevron" size={12} /></button>
    <div className="opp-card-context"><span>{entry.topic?.gameName || '关联话题'}</span>{platforms.length > 0 && <span>{platforms.slice(0, 2).map(key => platformNames[key] || key).join(' · ')}{platforms.length > 2 ? ' +' + (platforms.length - 2) : ''}</span>}</div>
    <div className={'opp-upstream ' + (entry.idea.passed === true ? 'opp-upstream-positive' : '')}><span className="opp-status-dot" />{passed}</div>
    <div className="opp-planning"><label>优先级<select aria-label={'方案「' + title + '」的本地优先级'} value={draft.priority} onChange={event => { if (isPriority(event.target.value)) onPlan({ priority: event.target.value }); }}>{Object.entries(priorityNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label><label>目标日期<input type="date" aria-label={'方案「' + title + '」的本地目标日期'} value={draft.targetDate} onChange={event => onPlan({ targetDate: validDate(event.target.value) ? event.target.value : '' })} /></label></div>
    {draft.targetDate && draft.targetDate < todayKey() && draft.status !== 'archived' && <p className="opp-date-warning">目标日期已过，仅为本地规划提醒。</p>}
    {draft.notes.trim() && !editing && <p className="opp-note-preview">{draft.notes}</p>}
    {editing && <form className="opp-note-editor" onSubmit={handleSave}><label htmlFor={fieldId + '-note'}>本地研究备注</label><textarea id={fieldId + '-note'} autoFocus rows={5} maxLength={NOTE_LIMIT} value={editorText} placeholder="记录判断、待核实的问题，或下一步准备…" onChange={event => onText(event.target.value)} /><div className="opp-editor-footer"><span>{editorText.length} / {NOTE_LIMIT}</span><div><button type="button" className="opp-text-button" onClick={onCancel}>取消</button><button type="submit" className="opp-save-button"><Icon name="check" size={12} />保存备注</button></div></div></form>}
    <div className="opp-card-bottom"><label className="opp-status-select" title="只修改本地草稿状态，不触发后端动作"><span className="opp-sr-only">{'移动方案「' + title + '」到'}</span><select value={draft.status} onChange={event => { if (isStatus(event.target.value)) onMove(event.target.value); }}>{columns.map(column => <option key={column.id} value={column.id}>{column.name}</option>)}</select><Icon name="down" size={12} /></label><button type="button" className={'opp-note-button ' + (draft.notes.trim() ? 'opp-has-note' : '')} onClick={onEdit} aria-expanded={editing} aria-label={(draft.notes.trim() ? '编辑' : '添加') + '方案「' + title + '」的本地备注'}><Icon name="file" size={13} />{draft.notes.trim() ? '编辑备注' : '添加备注'}</button></div>
  </article>;
}

export function OpportunityBoard({ universe, onSelect, onToast, focusTopicId, focusIdeaId, onClearFocus }: {
  universe: Universe; onSelect: (id: string) => void; onToast: (message: string) => void;
  focusTopicId?: string | null; focusIdeaId?: string | null; onClearFocus?: () => void;
}) {
  const headingId = useId();
  const [initial] = useState(readDrafts);
  const [drafts, setDrafts] = useState<DraftMap>(initial.drafts);
  const [storageLimited, setStorageLimited] = useState(initial.failed);
  const initialErrorReported = useRef(false);
  const [query, setQuery] = useState('');
  const [type, setType] = useState('all');
  const [platform, setPlatform] = useState('all');
  const [topicFilter, setTopicFilter] = useState('all');
  const [priority, setPriority] = useState('all');
  const [due, setDue] = useState('all');
  const [sort, setSort] = useState('priority');
  const [focusDismissed, setFocusDismissed] = useState(false);
  const [draggingKey, setDraggingKey] = useState<string | null>(null);
  const [dropLane, setDropLane] = useState<DraftStatus | null>(null);
  const boardRef = useRef<HTMLElement | null>(null);
  const pendingFocus = useRef<string | null>(null);
  const dragRef = useRef<string | null>(null);
  const [editorKey, setEditorKey] = useState<string | null>(null);
  const [editorText, setEditorText] = useState('');
  const entries = useMemo(() => getEntries(universe), [universe]);
  const types = useMemo(() => [...new Set(entries.map((entry) => entry.idea.type).filter(Boolean))].sort(), [entries]);
  const platforms = useMemo(() => [...new Set(entries.flatMap((entry) => Array.isArray(entry.topic?.platforms) ? entry.topic.platforms : []))].sort(), [entries]);

  useEffect(() => {
    if (initial.failed && !initialErrorReported.current) {
      initialErrorReported.current = true;
      onToast('浏览器草稿暂时无法读取；仍可整理方案并导出 JSON。');
    }
  }, [initial.failed, onToast]);

  const topicChoices = useMemo(() => [...new Map(entries.map(entry => [entry.topicId, entry.topic?.title || entry.topicId])).entries()], [entries]);
  const focusEntry = entries.find(entry => entry.idea.id === focusIdeaId && (!focusTopicId || entry.topicId === focusTopicId));
  const contextTopicId = focusTopicId && entries.some(entry => entry.topicId === focusTopicId) ? focusTopicId : focusEntry?.topicId || null;
  const contextActive = !focusDismissed && Boolean(focusTopicId || focusIdeaId);
  const contextMissing = contextActive && (Boolean(focusTopicId && !entries.some(entry => entry.topicId === focusTopicId)) || Boolean(focusIdeaId && !focusEntry));
  const contextTitle = topicChoices.find(([id]) => id === contextTopicId)?.[1] || (Array.isArray(universe?.planets) ? universe.planets.find(topic => topic.id === focusTopicId)?.title : '') || focusTopicId || '来源方案';
  const today = todayKey();
  const nextWeekDate = new Date(today + 'T00:00:00Z'); nextWeekDate.setUTCDate(nextWeekDate.getUTCDate() + 6);
  const nextWeek = nextWeekDate.toISOString().slice(0, 10);
  useEffect(() => {
    setFocusDismissed(false); setTopicFilter(contextTopicId || 'all'); setQuery(''); setType('all'); setPlatform('all'); setPriority('all'); setDue('all');
  }, [focusTopicId, focusIdeaId, entries]);
  useEffect(() => {
    if (!contextActive || !focusEntry) return;
    const node = [...(boardRef.current?.querySelectorAll<HTMLElement>('[data-draft-key]') || [])].find(element => element.dataset.draftKey === focusEntry.key);
    node?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'auto' });
  }, [contextActive, focusEntry?.key, topicFilter]);
  useEffect(() => {
    const key = pendingFocus.current; if (!key) return;
    pendingFocus.current = null;
    const node = [...(boardRef.current?.querySelectorAll<HTMLElement>('[data-draft-key]') || [])].find(element => element.dataset.draftKey === key);
    node?.querySelector<HTMLSelectElement>('.opp-status-select select')?.focus({ preventScroll: true });
  }, [drafts]);

  const visibleEntries = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    const priorityValue = { high: 2, normal: 1, low: 0 };
    return entries.filter(entry => {
      const draft = drafts[entry.key] || defaultDraft;
      const searchText = (entry.idea.name || '') + ' ' + (entry.topic?.title || '') + ' ' + (entry.topic?.gameName || '');
      const dueMatch = due === 'all' || (due === 'unscheduled' && !draft.targetDate) || (due === 'overdue' && Boolean(draft.targetDate) && draft.targetDate < today && draft.status !== 'archived') || (due === 'upcoming' && Boolean(draft.targetDate) && draft.targetDate >= today && draft.targetDate <= nextWeek && draft.status !== 'archived');
      return (!term || searchText.toLocaleLowerCase().includes(term)) && (type === 'all' || entry.idea.type === type)
        && (platform === 'all' || entry.topic?.platforms?.includes(platform)) && (topicFilter === 'all' || entry.topicId === topicFilter)
        && (priority === 'all' || draft.priority === priority) && dueMatch;
    }).sort((a, b) => {
      const left = drafts[a.key] || defaultDraft, right = drafts[b.key] || defaultDraft;
      const order = sort === 'priority' ? priorityValue[right.priority] - priorityValue[left.priority]
        : sort === 'date' ? (left.targetDate || '9999-12-31').localeCompare(right.targetDate || '9999-12-31')
        : sort === 'updated' ? right.updatedAt.localeCompare(left.updatedAt)
        : (Number.isFinite(b.idea.score) ? b.idea.score : -Infinity) - (Number.isFinite(a.idea.score) ? a.idea.score : -Infinity);
      return order || (a.idea.name || '').localeCompare(b.idea.name || '', 'zh-CN');
    });
  }, [entries, drafts, query, type, platform, topicFilter, priority, due, sort, today, nextWeek]);
  const hasFilters = Boolean(query.trim()) || type !== 'all' || platform !== 'all' || topicFilter !== 'all' || priority !== 'all' || due !== 'all';
  const notesCount = entries.filter(entry => Boolean(drafts[entry.key]?.notes.trim())).length;

  function saveDraft(entry: BoardEntry, patch: Partial<Pick<Draft, 'status' | 'notes' | 'priority' | 'targetDate'>>) {
    const next = { ...drafts, [entry.key]: { ...(drafts[entry.key] || defaultDraft), ...patch, updatedAt: new Date().toISOString() } };
    setDrafts(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, drafts: next }));
      setStorageLimited(false);
      return true;
    } catch {
      setStorageLimited(true);
      return false;
    }
  }

  function move(entry: BoardEntry, status: DraftStatus) {
    const node = [...(boardRef.current?.querySelectorAll<HTMLElement>('[data-draft-key]') || [])].find(element => element.dataset.draftKey === entry.key);
    if (node?.contains(document.activeElement)) pendingFocus.current = entry.key;
    const saved = saveDraft(entry, { status });
    const name = columns.find((column) => column.id === status)?.name;
    onToast(saved ? `本地草稿已移至「${name}」，未提交后端。` : '状态已在本页更新；浏览器保存不可用，请导出 JSON 备份。');
  }

  function edit(entry: BoardEntry) {
    if (editorKey === entry.key) { setEditorKey(null); return; }
    setEditorKey(entry.key);
    setEditorText(drafts[entry.key]?.notes || '');
  }

  function saveNote(entry: BoardEntry) {
    const saved = saveDraft(entry, { notes: editorText.slice(0, NOTE_LIMIT) });
    setEditorKey(null);
    onToast(saved ? '备注已保存到当前浏览器。' : '备注已暂存于本页；浏览器保存不可用，请导出 JSON 备份。');
  }

  function exportDrafts() {
    try {
      const payload = {
        format: 'taptap-growth-opportunity-drafts', version: 1, exportedAt: new Date().toISOString(),
        notice: '状态、备注、优先级与目标日期为本地整理草稿，未提交后端，不代表审批或执行结果。',
        entries: entries.map((entry) => ({
          game: entry.topic?.game || null, topicId: entry.topicId, topicTitle: entry.topic?.title || '',
          ideaId: entry.idea.id, title: entry.idea.name, type: entry.idea.type,
          upstream: { score: Number.isFinite(entry.idea.score) ? entry.idea.score : null, passed: entry.idea.passed },
          ...(drafts[entry.key] || defaultDraft),
        })),
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `taptap-opportunity-drafts-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      onToast(`已导出当前看板的 ${entries.length} 个方案草稿。`);
    } catch {
      onToast('草稿导出未完成，请重试。');
    }
  }

  function updatePlan(entry: BoardEntry, patch: Partial<Pick<Draft, 'priority' | 'targetDate'>>) {
    const saved = saveDraft(entry, patch);
    onToast(saved ? '优先级与目标日期已更新到本地草稿。' : '规划已暂存本页；浏览器保存不可用，请导出草稿。');
  }
  function startDrag(entry: BoardEntry, event: DragEvent<HTMLElement>) {
    if ((event.target as HTMLElement).closest('input,select,textarea,button,a')) { event.preventDefault(); return; }
    dragRef.current = entry.key; setDraggingKey(entry.key);
    event.dataTransfer.setData('application/x-taptap-opportunity-key', entry.key);
    event.dataTransfer.effectAllowed = 'move';
  }
  function endDrag() { dragRef.current = null; setDraggingKey(null); setDropLane(null); }
  function dropOn(status: DraftStatus, event: DragEvent<HTMLElement>) {
    const key = dragRef.current; const entry = entries.find(item => item.key === key);
    if (!entry) return;
    event.preventDefault(); move(entry, status); endDrag();
  }
  function clearContext() { setFocusDismissed(true); setTopicFilter('all'); onClearFocus?.(); }
  function revealContext() { setTopicFilter(contextTopicId || 'all'); setQuery(''); setType('all'); setPlatform('all'); setPriority('all'); setDue('all'); }

  function clearFilters() { setQuery(''); setType('all'); setPlatform('all'); setTopicFilter('all'); setPriority('all'); setDue('all'); }

  return (
    <section className="opp-board" ref={boardRef} aria-labelledby={headingId}>
      <div className="opp-heading">
        <div><span className="opp-eyebrow">增长方案工作台</span><h2 id={headingId}>增长创意草稿</h2><p>从话题里的方案开始，整理判断与下一步。</p></div>
        <button type="button" className="opp-export" onClick={exportDrafts} disabled={!entries.length}>
          <Icon name="download" size={15} />导出草稿<span>JSON</span>
        </button>
      </div>
      <div className="opp-local-notice"><Icon name="shield" size={16} /><p><strong>本地草稿</strong>：状态、备注、优先级与目标日期仅保存在当前浏览器，未提交后端。</p><span>仅整理 · 不触发执行</span></div>
      {contextActive && <div className={'opp-context-notice ' + (contextMissing ? 'opp-context-missing' : '')}><div className="opp-context-label"><strong>来自策略页的研究上下文</strong><span>{contextTitle}{focusEntry ? ' · ' + focusEntry.idea.name : ''}</span>{contextMissing && <p>{contextTopicId ? '指定方案暂未出现在当前数据中，已显示此话题的可用草稿。' : '指定话题或方案暂未出现在当前数据中，已展示全部可用方案。'}</p>}</div>{contextTopicId && <button onClick={revealContext}>显示来源方案</button>}<button onClick={clearContext}><Icon name="close" size={13} />清除上下文</button></div>}
      {storageLimited && <p className="opp-storage-warning" role="status">浏览器存储不可用或已有草稿无法读取。当前修改暂存于本页，建议导出 JSON 保存。</p>}
      <div className="opp-toolbar">
        <label className="opp-search"><Icon name="search" size={16} /><span className="opp-sr-only">搜索方案或关联话题</span>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索方案、关联话题或游戏…" />
        </label>
        <label className="opp-filter"><span>类型</span><select aria-label="按方案类型筛选" value={type} onChange={(event) => setType(event.target.value)}>
          <option value="all">全部类型</option>{types.map((value) => <option key={value} value={value}>{value}</option>)}
        </select><Icon name="down" size={12} /></label>
        <label className="opp-filter"><span>平台</span><select aria-label="按关联话题平台筛选" value={platform} onChange={(event) => setPlatform(event.target.value)}>
          <option value="all">全部平台</option>{platforms.map((value) => <option key={value} value={value}>{platformNames[value] || value}</option>)}
        </select><Icon name="down" size={12} /></label>
        <label className="opp-filter"><span>话题</span><select aria-label="按关联话题筛选" value={topicFilter} onChange={event => setTopicFilter(event.target.value)}><option value="all">全部话题</option>{topicChoices.map(([id, title]) => <option key={id} value={id}>{title}</option>)}</select><Icon name="down" size={12} /></label>
        <label className="opp-filter"><span>优先级</span><select aria-label="按本地优先级筛选" value={priority} onChange={event => setPriority(event.target.value)}><option value="all">全部优先级</option>{Object.entries(priorityNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select><Icon name="down" size={12} /></label>
        <label className="opp-filter"><span>日期</span><select aria-label="按本地目标日期筛选" value={due} onChange={event => setDue(event.target.value)}><option value="all">全部目标日期</option><option value="overdue">目标日期已过</option><option value="upcoming">未来七天</option><option value="unscheduled">尚未排期</option></select><Icon name="down" size={12} /></label>
        <label className="opp-filter"><span>排序</span><select aria-label="草稿排序" value={sort} onChange={event => setSort(event.target.value)}><option value="priority">优先级优先</option><option value="date">目标日期优先</option><option value="score">方案评分优先</option><option value="updated">最近修改优先</option></select><Icon name="down" size={12} /></label>
        {hasFilters && <button type="button" className="opp-clear" onClick={clearFilters}>重置筛选</button>}
      </div>
      <div className="opp-summary" aria-live="polite"><span><b>{entries.length}</b> 个上游方案</span><span><b>{notesCount}</b> 个已有备注</span><span className="opp-visible-count">当前显示 <b>{visibleEntries.length}</b> 个</span></div>
      <p className="opp-drag-hint">桌面可拖动卡片到任一分类；键盘和手机可使用卡片底部的状态选择。所有操作仅更新本地草稿。</p>
      <p className="opp-mobile-hint"><Icon name="arrow" size={12} />左右滑动查看四个草稿分类</p>
      <div className="opp-board-scroll" tabIndex={0} aria-label="草稿看板，可横向滚动查看分类">
        <div className="opp-lanes">
          {columns.map((column, index) => {
            const items = visibleEntries.filter((entry) => (drafts[entry.key]?.status || 'pending') === column.id);
            const total = entries.filter((entry) => (drafts[entry.key]?.status || 'pending') === column.id).length;
            return <section key={column.id} className={'opp-lane opp-lane-' + column.id + (dropLane === column.id ? ' opp-lane-drop-active' : '')} aria-labelledby={headingId + '-' + column.id} onDragOver={event => { if (dragRef.current && entries.some(entry => entry.key === dragRef.current)) { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; setDropLane(column.id); } }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropLane(null); }} onDrop={event => dropOn(column.id, event)}>
              <div className="opp-lane-heading"><div><span className="opp-lane-step">0{index + 1}</span><h3 id={`${headingId}-${column.id}`}>{column.name}</h3>
                <span className="opp-lane-count" aria-label={`${items.length} 个显示方案，总共 ${total} 个`}>{items.length}{hasFilters && <small>/{total}</small>}</span></div><p>{column.hint}</p></div>
              <div className="opp-lane-cards">
                {items.map((entry) => <IdeaCard key={entry.key} entry={entry} draft={drafts[entry.key] || defaultDraft}
                  editing={editorKey === entry.key} editorText={editorText} onSelect={onSelect}
                  focused={contextActive && focusEntry?.key === entry.key} dragging={draggingKey === entry.key}
                  onDragStart={event => startDrag(entry, event)} onDragEnd={endDrag} onPlan={patch => updatePlan(entry, patch)}
                  onMove={(status) => move(entry, status)} onEdit={() => edit(entry)} onText={setEditorText}
                  onSave={() => saveNote(entry)} onCancel={() => setEditorKey(null)} />)}
                {!items.length && <div className="opp-empty"><span><Icon name={column.icon} size={21} /></span>
                  <b>{hasFilters ? '没有匹配的方案' : column.id === 'pending' && !entries.length ? '暂时没有上游方案' : '留一个位置给新思路'}</b>
                  <p>{hasFilters ? '试试调整搜索或筛选条件。' : !entries.length ? '上游分析产出方案后，会显示在这里。' : '用卡片底部的状态选择，将草稿移到这里。'}</p>
                </div>}
              </div>
            </section>;
          })}
        </div>
      </div>
      <p className="opp-footnote"><Icon name="help" size={13} />评分与分析门禁结果来自话题分析；看板状态是独立的本地整理记录，不代表上线或执行结果。</p>
    </section>
  );
}
