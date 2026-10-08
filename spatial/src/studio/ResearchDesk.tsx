import { useEffect, useRef, useState } from 'react';
import { api, isDemoMode, type Universe } from '../lib/api';
import { downloadFile, formatNumber, formatScore, platformName } from '../lib/presentation';
import { Icon } from '../ui/Icon';
import { normalizeEvidence, type Material } from './catalog';
import { researchStatuses, useResearch, type ResearchStatus } from './research';
import './research.css';

type Props = { universe: Universe; initialTopicId?: string | null; embedded?: boolean; onSelect: (id: string) => void; onLibrary: (id: string, tab: 'materials' | 'strategies') => void; onTimeline: (id: string) => void; onWorkspace?: (id: string, ideaId?: string) => void };
const checks = [['source', '核实素材来源'], ['audience', '确认目标玩家'], ['risk', '检查表达与执行风险']] as const;
export function ResearchDesk({ universe, initialTopicId, embedded = false, onSelect, onLibrary, onTimeline, onWorkspace }: Props) {
 const { drafts, add, update, remove } = useResearch();
 const [activeId, setActiveId] = useState(initialTopicId || drafts[0]?.topicId || '');
 const [scope, setScope] = useState('all'); const [query, setQuery] = useState('');
 const [response, setResponse] = useState<{ id: string; materials: Material[]; summary: string; error: string; loading: boolean }>({id:'',materials:[],summary:'',error:'',loading:false});
 const [retry, setRetry] = useState(0); const [feedback, setFeedback] = useState(''); const [exporting, setExporting] = useState(false); const [removeId, setRemoveId] = useState('');
 const feedbackTimer = useRef<number>(0); const exportGeneration = useRef(0);
 const say = (message: string) => { clearTimeout(feedbackTimer.current); setFeedback(message); feedbackTimer.current = window.setTimeout(() => setFeedback(''), 5500); };
 useEffect(() => () => { clearTimeout(feedbackTimer.current); exportGeneration.current++; }, []);
 useEffect(() => { if (initialTopicId && universe.planets.some(p => p.id === initialTopicId)) { add(initialTopicId); setActiveId(initialTopicId); } }, [initialTopicId]);
 useEffect(() => { if (drafts.length && !drafts.some(d => d.topicId === activeId)) setActiveId(drafts[0].topicId); }, [drafts, activeId]);
 const active = drafts.find(d => d.topicId === activeId);
 const topic = universe.planets.find(p => p.id === activeId);
 const visible = drafts.filter(d => (scope === 'all' || d.status === scope) && (!query.trim() || `${universe.planets.find(p => p.id === d.topicId)?.title || d.topicId} ${d.question}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
 useEffect(() => {
  if (!topic || !active) return;
  let current = true; const id = topic.id;
  setResponse({id,materials:[],summary:'',error:'',loading:true});
  api.topic(id).then(data => { if (current) setResponse({id,materials:normalizeEvidence(data, topic),summary:typeof data?.summary === 'string' ? data.summary : typeof data?.what_happened?.summary === 'string' ? data.what_happened.summary : '',error:'',loading:false}); })
   .catch(e => { if (current) setResponse({id,materials:[],summary:'',error:e instanceof Error ? e.message : '证据读取失败',loading:false}); });
  return () => { current = false; };
 }, [topic, Boolean(active), retry]);
 const patch = (value: Parameters<typeof update>[1]) => { if (active && !update(active.topicId, value)) say('浏览器保存不可用，内容已暂存于本页；请导出研究简报。'); };
 const items = response.id === activeId ? response.materials : [];
 async function exportBrief() {
  if (exporting || !drafts.length) return;
  const generation = ++exportGeneration.current; setExporting(true);
  const snapshot = [...drafts];
  const results = await Promise.allSettled(snapshot.map(async draft => {
   const planet = universe.planets.find(p => p.id === draft.topicId);
   if (!planet) return { draft, planet, materials: [] as Material[], warning: '此话题不在当前数据快照中。' };
   try { return { draft, planet, materials: normalizeEvidence(await api.topic(planet.id), planet), warning: '' }; }
   catch { return { draft, planet, materials: [] as Material[], warning: '此次导出未能重新读取证据，需核实后补充。' }; }
  }));
  if (generation !== exportGeneration.current) return;
  let failures = 0;
  const sections = results.map(result => {
   if (result.status !== 'fulfilled') { failures++; return '## 未能完成的研究条目\n请重新读取后导出。'; }
   const { draft, planet, materials, warning } = result.value; if (warning) failures++;
   const chosen = materials.filter(m => draft.evidenceIds.includes(m.id));
   const idea = (universe.ideas[draft.topicId] || []).find(i => i.id === draft.ideaId);
   const missing = draft.evidenceIds.length - chosen.length;
   return [`## ${planet?.title || draft.topicId}`, `- 状态：${researchStatuses[draft.status]}`, `- 游戏：${planet?.gameName || '未提供'}`, `- 热度 / 势头：${formatScore(planet?.heat)} / ${formatScore(planet?.velocity)}`, `- 来源平台：${planet?.platforms.map(platformName).join('、') || '未提供'}`, '', `### 研究问题\n${draft.question || '待补充'}`, `### 目标玩家\n${draft.audience || '待补充'}`, '### 选用证据', chosen.length ? chosen.map(m => `- ${m.title}（${platformName(m.platform)}）\n  ${m.excerpt}\n  ${m.url ? `来源：${m.url}` : '来源链接未提供'} `).join('\n') : '尚未选用证据。', missing > 0 ? `有 ${missing} 条此前选用的证据不在当前响应中，需复核。` : '', warning, `### 我的判断\n${draft.conclusion || '待补充'}`, `### 关联方案\n${idea ? `${idea.name} · ${idea.type} · 评分 ${formatScore(idea.score)} / 100` : '未选用当前方案'}`, `### 下一步\n${draft.nextAction || '待补充'}`, `### 核实清单\n${checks.map(([id,label]) => `- [${draft.checks.includes(id) ? 'x' : ' '}] ${label}`).join('\n')}`].filter(Boolean).join('\n\n');
  });
  downloadFile(`TapTap-研究简报-${new Date().toISOString().slice(0,10)}.md`, `# TapTap Pulse · 研究简报\n\n${isDemoMode ? '合成演示情境，不代表实时热点。' : '基于当前 API 快照及来源记录。'}\n\n浏览器本地研究草稿，结论由用户填写。清单勾选为个人记录，不代表已核实或已审批。\n\n导出时间：${new Date().toISOString()}\n\n${sections.join('\n\n---\n\n')}`);
  setExporting(false); say(failures ? `已导出 ${snapshot.length} 项研究，其中 ${failures} 项有缺失，已在简报中标明。` : `已导出 ${snapshot.length} 项研究与选用证据。`);
 }
 return <section className={'research-desk '+(embedded?'research-embedded':'')}>
  <header className="research-heading"><div><span>MY RESEARCH / 03</span><h1>让好奇心，<br/>长出自己的判断。</h1><p>把热点、证据和策略放在一起，整理成你的下一步。</p></div><button className="research-export" disabled={!drafts.length || exporting} onClick={() => void exportBrief()}><Icon name="download" size={19}/>{exporting?'读取证据中…':'导出研究简报'}<small>MARKDOWN</small></button></header>
  <div className="research-overview"><span><b>{drafts.length.toString().padStart(2,'0')}</b>个研究主题</span>{Object.entries(researchStatuses).map(([id,label]) => <button key={id} aria-pressed={scope===id} onClick={()=>setScope(scope===id?'all':id)}><i className={'research-status-'+id}/>{label}<b>{drafts.filter(d=>d.status===id).length}</b></button>)}<small>保存在当前浏览器</small></div>
  {!drafts.length ? <div className="research-empty"><div className="research-paper-art" aria-hidden="true">✳</div><h2>第一条线索，就从这里开始。</h2><p>在热点或素材中点击「加入研究夹」，也可以从下面选一个话题。</p><div>{universe.planets.slice(0,3).map(p=><button key={p.id} onClick={()=>{add(p.id);setActiveId(p.id);}}>{p.title}<Icon name="plus" size={17}/></button>)}</div></div> : <div className="research-layout">
   <aside className="research-index"><label><Icon name="search" size={17}/><input aria-label="搜索研究主题" value={query} onChange={e=>setQuery(e.target.value)} placeholder="查找你的研究…"/></label><div className="research-index-list">{visible.map((d,i)=>{const p=universe.planets.find(p=>p.id===d.topicId);return <button key={d.topicId} aria-pressed={activeId===d.topicId} onClick={()=>{setActiveId(d.topicId);setRemoveId('');}}><small>{String(i+1).padStart(2,'0')} / {p?.gameName || '游戏话题'}</small><b>{p?.title || '不在当前快照中的话题'}</b><span><i className={'research-status-'+d.status}/>{researchStatuses[d.status]}<em>{d.evidenceIds.length} 条选用证据</em></span></button>;})}{!visible.length&&<p className="research-empty-inline">没有匹配的研究主题。</p>}</div></aside>
   {active && topic ? <article className="research-sheet" key={active.topicId}>
    <div className="research-sheet-top"><span>RESEARCH FILE / {topic.gameName || '未分类'}</span><select aria-label="研究主题状态" value={active.status} onChange={e=>patch({status:e.target.value as ResearchStatus})}>{Object.entries(researchStatuses).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></div>
    <h2>{topic.title}</h2><div className="research-topic-metrics"><span>热度 <b>{formatScore(topic.heat)}</b></span><span>关联内容 <b>{formatNumber(topic.contentCount)}</b></span><span>{topic.platforms.map(platformName).join(' / ')}</span></div>
    <nav className="research-context-nav" aria-label="继续研究此话题"><button onClick={()=>onSelect(topic.id)}>热点星图<Icon name="arrow" size={13}/></button><button onClick={()=>onTimeline(topic.id)}>传播时间线<Icon name="arrow" size={13}/></button><button onClick={()=>onLibrary(topic.id,'materials')}>完整素材<Icon name="arrow" size={13}/></button><button onClick={()=>onLibrary(topic.id,'strategies')}>增长策略<Icon name="arrow" size={13}/></button></nav>
    <div className="research-field-grid"><label>01 / 我想弄清楚什么？<textarea aria-label="研究问题" maxLength={4000} value={active.question} onChange={e=>patch({question:e.target.value})} placeholder="比如：玩家为何关注这个话题？"/></label><label>02 / 为哪些玩家做？<textarea aria-label="目标玩家" maxLength={4000} value={active.audience} onChange={e=>patch({audience:e.target.value})} placeholder="记录目标玩家、社区或内容受众…"/></label></div>
    <section className="research-evidence"><div className="research-subheading"><h3>03 / 让证据进入判断。</h3><span>{active.evidenceIds.length} 条已选用</span></div><p>选用需要引用的材料。示例内容与未提供原文的记录会保留来源边界。</p>{response.id===activeId&&response.loading&&<p role="status">正在读取这个话题的证据…</p>}{response.id===activeId&&response.error&&<div role="alert">{response.error}<button onClick={()=>setRetry(v=>v+1)}>重新读取</button></div>}{response.id===activeId&&!response.loading&&!response.error&&!items.length&&<p>当前没有可用素材，可先记录问题与待验证的判断。</p>}{items.map(m=><label className="research-evidence-item" key={m.id}><input type="checkbox" aria-label={'选用证据'+m.title} checked={active.evidenceIds.includes(m.id)} onChange={()=>patch({evidenceIds:active.evidenceIds.includes(m.id)?active.evidenceIds.filter(id=>id!==m.id):[...active.evidenceIds,m.id]})}/><span><small>{platformName(m.platform)} · {m.url?'有来源链接':'原文链接未提供'}</small><b>{m.title}</b><em>{m.excerpt}</em>{m.url&&<a href={m.url} target="_blank" rel="noopener noreferrer" onClick={e=>e.stopPropagation()}>查看原始来源 ↗</a>}</span></label>)}</section>
    <label className="research-long-field">04 / 我的判断<textarea aria-label="我的研究判断" value={active.conclusion} maxLength={4000} onChange={e=>patch({conclusion:e.target.value})} placeholder="结合素材写下自己的判断；区分事实、推测和待核实之处…"/></label>
    <label className="research-idea-field">05 / 关联一个增长方案<select aria-label="研究关联方案" value={active.ideaId} onChange={e=>patch({ideaId:e.target.value})}><option value="">暂不选用方案</option>{(universe.ideas[topic.id]||[]).map(i=><option key={i.id} value={i.id}>{i.name} · 评分 {formatScore(i.score)}</option>)}</select></label>
    <label className="research-long-field">06 / 准备采取的下一步<textarea aria-label="研究下一步" maxLength={4000} value={active.nextAction} onChange={e=>patch({nextAction:e.target.value})} placeholder="明确下一步、验证方式和完成标准…"/></label>
    <div className="research-checks"><b>把判断再向前推一步。</b>{checks.map(([id,label])=><label key={id}><input type="checkbox" checked={active.checks.includes(id)} onChange={()=>patch({checks:active.checks.includes(id)?active.checks.filter(c=>c!==id):[...active.checks,id]})}/>{label}</label>)}<small>个人核实清单 · 勾选不代表审批或执行</small></div>
    <div className="research-sheet-footer">{onWorkspace&&<button className="research-handoff" onClick={()=>onWorkspace(topic.id,active.ideaId||undefined)}>带到增长工作台<Icon name="arrow" size={18}/></button>}{removeId===topic.id?<div className="research-remove"><span>移出将清除本地研究记录。</span><button onClick={()=>{remove(topic.id);setRemoveId('');}}>确认移出</button><button onClick={()=>setRemoveId('')}>取消</button></div>:<button className="research-remove-start" onClick={()=>setRemoveId(topic.id)}>移出研究夹</button>}</div>
   </article> : <div className="research-sheet"><h2>此话题暂不在当前数据快照中。</h2><p>已保存的研究记录仍保留，可以导出简报。</p></div>}
  </div>}
  {feedback&&<div className="research-feedback" role="status">{feedback}</div>}
 </section>;
}
