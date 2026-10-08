import { useEffect, useMemo, useRef, useState } from 'react';
import { api, isDemoMode, type Idea, type Planet, type Universe } from '../lib/api';
import { defaultFilters, downloadFile, filterTopics, formatNumber, formatScore, lifecycleNames, loadLocal, platformName, saveLocal } from '../lib/presentation';
import { normalizeEvidence, type Material } from './catalog';
import { Icon } from '../ui/Icon';
import { intelligenceData } from '../research/data';

export type IntelligenceGoal = 'intelligence' | 'materials' | 'strategy';
type Request = { question: string; game: string; platform: string; period: string; goal: IntelligenceGoal; rising: boolean };
type RecordDetail = { topic: Planet; materials: Material[]; summary: string; recommendation: string; detail: Record<string, any>; error?: string };
type Task = { id: string; request: Request; records: RecordDetail[]; createdAt: string; failures: number };
type HistoryEntry = { id: string; question: string; game: string; platform: string; period: string; goal: IntelligenceGoal; rising: boolean; createdAt: string; topicIds: string[] };
const HISTORY = 'taptap-pulse-intelligence-tasks:v1:' + (isDemoMode ? 'demo' : 'live');
const goalLabels: Record<IntelligenceGoal, string> = { intelligence: '找热点', materials: '找素材', strategy: '找增长创意' };
const generic = /^(现在|当前|今天|本周|最近|帮我|请|给我|我们|我|taptap|tap tap|pulse|全网|全平台|有哪些|有什么|哪些|怎么|如何|可以|值得|适合|推荐|发现|看看|看看有哪些|寻找|找到|追踪|热点|热门|游戏|话题|内容|情报|素材|材料|策略|增长|创意|机会|社区|运营|研究|分析|传播|正在|升温|的|和|与|相关|做|在|想|能|一下|做什么|什么|找|关注|成为|借势|近期|热度|在涨|方向|一个|方案|？|\?|。|\s|，|,)+$/i;
function getHistory(): HistoryEntry[] {
 const raw = loadLocal<unknown>(HISTORY, []);
 return Array.isArray(raw) ? raw.filter((h): h is HistoryEntry => !!h && typeof h.id === 'string' && typeof h.question === 'string' && typeof h.game === 'string' && typeof h.platform === 'string' && typeof h.period === 'string' && typeof h.createdAt==='string' && Number.isFinite(Date.parse(h.createdAt)) && ['intelligence','materials','strategy'].includes(h.goal) && Array.isArray(h.topicIds)).slice(0,8) : [];
}
function matchTopics(universe: Universe, request: Request) {
 const scoped = filterTopics(universe.planets, {...defaultFilters,game:request.game,platform:request.platform,lifecycle:request.rising?'rising':'all',sort:request.goal==='strategy'?'velocity':'heat'},[]);
 const term = request.question.toLocaleLowerCase().trim();
 const allNames = [...new Set(universe.planets.map(p=>p.gameName).filter((v):v is string=>!!v))].sort((a,b)=>b.length-a.length);
 const named = allNames.filter(name=>term.includes(name.toLocaleLowerCase()));
 const categories = [...new Set(universe.planets.map(p=>p.category).filter((v):v is string=>!!v))].filter(name=>term.includes(name.toLocaleLowerCase()));
 let relevant = scoped;
 if (named.length) relevant = scoped.filter(p=>named.includes(p.gameName||''));
 else if (categories.length) relevant = scoped.filter(p=>categories.includes(p.category||''));
 else if (term && !generic.test(term)) {
  const fragments = term.split(/[\s，,、？?。]/).filter(Boolean);
  const exact = scoped.filter(p=>fragments.some(word=>`${p.title} ${p.gameName||''} ${p.category||''}`.toLocaleLowerCase().includes(word)));
  relevant = exact;
 }
 if (request.period !== 'all') {
  const age = request.period==='day'?24:168; const now=Date.now();
  relevant = relevant.filter(p=>p.firstSeen&&Number.isFinite(Date.parse(p.firstSeen))&&now-Date.parse(p.firstSeen)<=age*3600000&&Date.parse(p.firstSeen)<=now);
 }
 return relevant.slice(0,6);
}
function dateLabel(date: string) { return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(date)); }
function text(v: unknown) { return typeof v==='string'?v:''; }
function detailRecord(topic: Planet, data: Record<string, any>): RecordDetail { return { topic,detail:data,materials:normalizeEvidence(data,topic),summary:text(data.summary)||text(data.what_happened?.summary)||text(data.trend_analysis?.trigger),recommendation:text(data.recommendation)||text(data.next_step?.recommendation) }; }
export function briefContent(task: Task, universe: Universe) {
 return `# TapTap Pulse · 情报档案\n\n需求：${task.request.question}\n\n${isDemoMode?'合成演示情境，未调用真实 AI 模型，不代表实时热点。':'读取已有 API 情报快照，本次检索没有重新生成模型结论。'}\n\n整理时间：${task.createdAt}\n范围：${task.request.game} / ${task.request.platform} / ${task.request.period}\n\n`+task.records.map(({topic,materials,summary,recommendation,error})=>`## ${topic.title}\n\n热度 ${formatScore(topic.heat)} / 传播势头 ${formatScore(topic.velocity)}；关联内容 ${formatNumber(topic.contentCount)} 条\n来源平台：${topic.platforms.map(platformName).join('、')}\n\n${summary||'摘要未提供。'}\n${error?'证据读取失败：'+error:''}\n\n### 素材\n${materials.map(m=>`- ${m.title}（${platformName(m.platform)}）\n  ${m.excerpt}\n  ${m.url?'来源：'+m.url:'原文链接未提供'}`).join('\n')}\n\n### TapTap 增长方向\n${recommendation||'当前记录没有提供完整增长建议。'}\n${(universe.ideas[topic.id]||[]).map(i=>`- ${i.name} · ${i.type} · 评分 ${formatScore(i.score)} · ${i.passed?'通过分析门禁':'尚未通过分析门禁'}`).join('\n')}\n`).join('\n---\n\n');
}
export type DossierProps = { universe: Universe; topic: Planet; record?: RecordDetail; onRecordUpdated?:(record:RecordDetail)=>void; initialTab?: IntelligenceGoal; onResearch:(id:string)=>void; onLibrary:(id:string,tab:'materials'|'strategies')=>void; onTimeline:(id:string)=>void; onToast:(message:string)=>void };
export function TopicDossier({universe,topic,record,onRecordUpdated,initialTab='intelligence',onResearch,onLibrary,onTimeline,onToast}:DossierProps) {
 const [tab,setTab]=useState(initialTab); const [detail,setDetail]=useState<RecordDetail|null>(record||null); const [busy,setBusy]=useState(!record); const [error,setError]=useState(''); const [retry,setRetry]=useState(0); const [ideaId,setIdeaId]=useState('');
 const updateRecord=useRef(onRecordUpdated);updateRecord.current=onRecordUpdated;
 useEffect(()=>{setTab(initialTab);},[initialTab,topic.id]);
 useEffect(()=>{setIdeaId('');if(record?.topic.id===topic.id&&retry===0){setDetail(record);setBusy(false);setError(record.error||'');return;}let current=true;const controller=new AbortController();setBusy(true);setDetail(null);setError('');intelligenceData.topic(topic.id,{signal:controller.signal,force:retry>0}).then(data=>{if(current){const next=detailRecord(topic,data);setDetail(next);setBusy(false);if(record){setRetry(0);updateRecord.current?.(next);}}}).catch(e=>{if(current){setError(e instanceof Error?e.message:'素材读取失败');setBusy(false);}});return()=>{current=false;controller.abort();};},[topic,record,retry]);
 const currentDetail=record?.topic.id===topic.id&&retry===0?record:detail?.topic.id===topic.id?detail:null;
 const materials = currentDetail?.materials||[]; const ideas=universe.ideas[topic.id]||[]; const chosen=ideas.find(i=>i.id===ideaId)||ideas[0];
 const fullCreatives=Array.isArray(currentDetail?.detail.creatives)?currentDetail!.detail.creatives:[];
 const full=fullCreatives.find((v:any)=>v.idea_id===chosen?.id);
 const recommendation=currentDetail?.recommendation||text(full?.strategy)||text(full?.execution?.where);
 const copyMaterial=async(m:Material)=>{try{await navigator.clipboard.writeText(`${m.title}\n${m.excerpt}\n${m.url||'原文链接未提供'}`);onToast('已复制素材与来源说明。');}catch{onToast('浏览器无法复制，请下载素材清单。');}};
 const pin=()=>{onResearch(topic.id);};
 return <article className="field-dossier" key={topic.id}>
  <header className="dossier-heading"><div><span>{topic.gameName||'未归属游戏'} / {lifecycleNames[topic.lifecycle||'']||'阶段未提供'}</span><h2>{topic.title.split(' · ').slice(1).join(' · ')||topic.title}</h2></div><button className="field-pin" onClick={pin} aria-label="把这个话题加入研究夹"><Icon name="bookmark" size={20}/><span>留作研究</span></button></header>
  <nav className="dossier-tabs" aria-label="情报档案内容">{Object.entries(goalLabels).map(([id,label])=><button key={id} aria-pressed={tab===id} onClick={()=>setTab(id as IntelligenceGoal)}>{id==='intelligence'?'热点判断':id==='materials'?'证据与素材':'TapTap 增长创意'}<span>{id==='materials'?materials.length:id==='strategy'?ideas.length:''}</span></button>)}</nav>
  {busy&&<p className="field-loading" role="status">正在读取档案中的证据…</p>}{error&&<div className="field-error" role="alert">{error}<button onClick={()=>setRetry(v=>v+1)}>重新读取</button></div>}
  {tab==='intelligence'&&<div className="dossier-intelligence"><div className="dossier-abstract"><p>{currentDetail?.summary||(!busy?'当前快照未提供文字摘要，可以从指标与素材继续研究。':'')}</p><div className="dossier-metrics"><span><b>{formatScore(topic.heat)}</b>热度 / 100</span><span><b>{formatScore(topic.velocity)}</b>传播势头 / 100</span><span><b>{formatNumber(topic.contentCount)}</b>关联内容</span></div></div><div className="dossier-source-map"><h3>从哪里听见？</h3>{topic.platforms.map((p,i)=><div key={p}><i style={{background:['var(--blue)','var(--orange)','var(--ink)','var(--lime)'][i%4]}}/><span>{platformName(p)}</span><Icon name="arrow" size={15}/></div>)}<button onClick={()=>onTimeline(topic.id)}>沿着时间线看下去 ↗</button><p>{topic.firstSeen?'首次记录 '+dateLabel(topic.firstSeen):'首次记录时间未提供'}<br/>平台覆盖来自记录，不推断传播先后。</p></div></div>}
  {tab==='materials'&&<div className="dossier-materials"><div className="dossier-section-intro"><h3>让每个判断，都有来处。</h3><button onClick={()=>downloadFile(`TapTap-${topic.gameName||'热点'}-素材.md`,materials.map(m=>`## ${m.title}\n\n${m.excerpt}\n\n来源：${m.url||'原文链接未提供'}`).join('\n\n'))} disabled={!materials.length}><Icon name="download" size={16}/>下载素材清单</button></div>{materials.map((m,i)=><article className="dossier-material" key={m.id}><span className="material-source">{platformName(m.platform)}<small>{m.url?'可查看原始来源':'原文未提供'}</small></span><div><h4>{m.title}</h4><p>{m.excerpt}</p>{m.url&&<a href={m.url} target="_blank" rel="noopener noreferrer">查看原文 ↗</a>}</div><button aria-label={'复制素材'+m.title} onClick={()=>void copyMaterial(m)}><Icon name="file" size={17}/></button></article>)}{!busy&&!materials.length&&<p className="field-empty">当前档案没有可引用的素材。</p>}<button className="field-text-link" onClick={()=>onLibrary(topic.id,'materials')}>进入素材库，筛选与收藏全部素材 <Icon name="arrow"/></button></div>}
  {tab==='strategy'&&<div className="dossier-strategies"><aside><h3>从这个热点，走向 TapTap。</h3>{ideas.map(i=><button key={i.id} aria-pressed={chosen?.id===i.id} onClick={()=>setIdeaId(i.id)}><small>{i.type} / {i.passed?'通过分析门禁':'待进一步研判'}</small><b>{i.name}</b><span>评分 {formatScore(i.score)} <Icon name="arrow" size={16}/></span></button>)}{!ideas.length&&<p>当前话题没有关联方案。可以先保存研究问题，补充证据。</p>}</aside><div className="strategy-sheet">{chosen&&<><div className="strategy-sheet-label">{isDemoMode?'策划示例':'已有分析方案'}<span>GROWTH NOTE</span></div><h3>{chosen.name}</h3><p>{recommendation||'目前仅提供方案标题与评分，完整执行建议尚未提供。'}</p><div className="strategy-steps"><h4>带着这些问题继续细化</h4><label><input type="checkbox"/>这个热点对应哪些玩家需求？</label><label><input type="checkbox"/>有哪些可引用、可授权的素材？</label><label><input type="checkbox"/>怎样在 TapTap 内帮助玩家发现或参与？</label></div>{full?.execution&&<div className="strategy-execution"><h4>已有执行建议</h4><p>{text(full.execution.where)}</p>{Array.isArray(full.execution.steps)&&<ol>{full.execution.steps.map((s:string,i:number)=><li key={i}>{s}</li>)}</ol>}</div>}<div className="strategy-footnote"><Icon name="spark" size={17}/><p>评分与门禁来自当前记录。核实来源、素材授权与热点窗口后，再决定实际执行。</p></div><button className="field-solid" onClick={pin}>把这个机会加入研究夹 <Icon name="plus" size={17}/></button></>}<button className="field-text-link" onClick={()=>onLibrary(topic.id,'strategies')}>查看全部关联策略 ↗</button></div></div>}
  <footer className="dossier-footnote">{isDemoMode?'合成演示档案 · 未调用真实 AI 模型':'已有情报快照 · 本次读取未重新调用模型'}<span>事实、来源与建议一起保留。</span></footer>
 </article>;
}
type Props = { universe: Universe | null; initialQuestion?: string; onSelect:(id:string)=>void; onResearch:(id:string)=>void; onLibrary:(id:string,tab:'materials'|'strategies')=>void; onTimeline:(id:string)=>void; onToast:(message:string)=>void; onTaskChange?:(active:boolean)=>void };
export function IntelligenceFlow({universe,initialQuestion='',onSelect,onResearch,onLibrary,onTimeline,onToast,onTaskChange}:Props) {
 const [question,setQuestion]=useState(initialQuestion); const [game,setGame]=useState('all'); const [platform,setPlatform]=useState('all');const [period,setPeriod]=useState('all'); const [goal,setGoal]=useState<IntelligenceGoal>('intelligence'); const [rising,setRising]=useState(false); const [advanced,setAdvanced]=useState(false);
 const [task,setTask]=useState<Task|null>(null); const [selectedId,setSelectedId]=useState(''); const [busy,setBusy]=useState(false); const [step,setStep]=useState(0); const [history,setHistory]=useState(getHistory); const [historyOpen,setHistoryOpen]=useState(false); const [error,setError]=useState('');
 const generation=useRef(0); const resultRef=useRef<HTMLDivElement>(null); const inputRef=useRef<HTMLTextAreaElement>(null); const runLock=useRef(false);
 useEffect(()=>()=>{generation.current++;},[]);
 const games=useMemo(()=>[...new Map((universe?.planets||[]).map(p=>[p.game||p.gameName||'unknown',p.gameName||'未归属游戏'])).entries()],[universe]);
 const platforms=useMemo(()=>[...new Set((universe?.planets||[]).flatMap(p=>p.platforms))],[universe]);
 async function run(overrides: Partial<Request> = {}) {
  if(runLock.current||!universe)return;
  const values={question:question.trim(),game,platform,period,goal,rising,...overrides};const request:Request={question:values.question.trim(),game:values.game,platform:values.platform,period:values.period,goal:values.goal,rising:values.rising}; if(!request.question){inputRef.current?.focus();return;}
  runLock.current=true;const token=++generation.current;setBusy(true);setError('');setStep(0);
  try {
   const candidates=matchTopics(universe,request);setStep(1);
   const results=await Promise.allSettled(candidates.map(async topic=>detailRecord(topic,await api.topic(topic.id))));
   if(token!==generation.current)return;setStep(2);
   let failures=0;const records=results.map((r,i)=>{if(r.status==='fulfilled')return r.value;failures++;return {topic:candidates[i],detail:{},materials:[],summary:'',recommendation:'',error:r.reason instanceof Error?r.reason.message:'证据未能读取'} as RecordDetail;});
   const next:Task={id:crypto.randomUUID(),request,records,createdAt:new Date().toISOString(),failures};onTaskChange?.(true);setTask(next);setSelectedId(records[0]?.topic.id||'');setGoal(request.goal);setQuestion(request.question);setGame(request.game);setPlatform(request.platform);setPeriod(request.period);setRising(request.rising);
   const entry:HistoryEntry={id:next.id,...request,createdAt:next.createdAt,topicIds:records.map(r=>r.topic.id)};
   const updated=[entry,...getHistory()].slice(0,8);setHistory(updated);if(!saveLocal(HISTORY,updated))onToast('浏览器无法保存任务记录，请下载情报档案。');setStep(3);
   requestAnimationFrame(()=>resultRef.current?.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth',block:'start'}));
  } catch(e){if(token===generation.current)setError(e instanceof Error?e.message:'未能整理这份档案，请重新尝试。');}
  finally{if(token===generation.current){setBusy(false);runLock.current=false;}}
 }
 const selected=task?.records.find(r=>r.topic.id===selectedId)||task?.records[0];
 const exportTask=()=>{if(task&&universe)downloadFile('TapTap-情报档案-'+task.createdAt.slice(0,10)+'.md',briefContent(task,universe));};
 return <div className={'intelligence-flow '+(task?'has-task':'')}>
  <form className="intelligence-composer" onSubmit={e=>{e.preventDefault();void run();}}>
   <div className="composer-top"><span><i/>你的需求，从这里开始。</span><button type="button" aria-expanded={historyOpen} onClick={()=>setHistoryOpen(v=>!v)}>最近探索 <span>{history.length}</span></button></div>
   <textarea ref={inputRef} value={question} maxLength={500} aria-label="你想研究什么游戏热点" onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void run();}}} placeholder="哪些游戏热点，能成为 TapTap 的增长机会？" rows={2}/>
   <div className="composer-bottom"><div className="composer-goals" aria-label="选择情报目标">{Object.entries(goalLabels).map(([id,label])=><button key={id} type="button" aria-pressed={goal===id} onClick={()=>setGoal(id as IntelligenceGoal)}>{label}</button>)}</div><button className="composer-submit" aria-label="开始探索" disabled={busy||!universe||!question.trim()}><span>{busy?'整理中':'开始探索'}</span><Icon name={busy?'refresh':'arrow'} size={22}/></button></div>
   <div className="composer-scope"><button type="button" onClick={()=>setAdvanced(v=>!v)} aria-expanded={advanced}><Icon name="settings" size={15}/>调整追踪范围 <Icon name="down" size={13}/></button><span>{game==='all'?'所有游戏':games.find(([id])=>id===game)?.[1]} · {platform==='all'?'全平台':platformName(platform)} · {period==='all'?'当前快照':period==='day'?'24小时内首次记录':'7天内首次记录'}</span></div>
   {advanced&&<div className="composer-options"><label>游戏<select aria-label="情报任务游戏" value={game} onChange={e=>setGame(e.target.value)}><option value="all">所有游戏</option>{games.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label><label>平台<select aria-label="情报任务平台" value={platform} onChange={e=>setPlatform(e.target.value)}><option value="all">全平台</option>{platforms.map(p=><option key={p} value={p}>{platformName(p)}</option>)}</select></label><label>首次记录<select aria-label="情报任务时间" value={period} onChange={e=>setPeriod(e.target.value)}><option value="all">当前快照</option><option value="day">过去24小时</option><option value="week">过去7天</option></select></label><label className="composer-rising"><input type="checkbox" checked={rising} onChange={e=>setRising(e.target.checked)}/>只看正在升温</label></div>}
  </form>
  {historyOpen&&<div className="intelligence-history"><div><b>最近的好奇心</b><button onClick={()=>setHistoryOpen(false)} aria-label="关闭最近探索"><Icon name="close" size={17}/></button></div>{history.map(h=><button key={h.id} disabled={busy} onClick={()=>{setHistoryOpen(false);void run({...h});}}><span>{h.question}</span><small>{goalLabels[h.goal]} · {dateLabel(h.createdAt)}</small><Icon name="arrow" size={15}/></button>)}{!history.length&&<p>开始一次探索，记录会保存在这个浏览器里。</p>}</div>}
  {!task&&<div className="intelligence-starters"><span>不妨从这些问题开始</span>{['帮我发现独立游戏的内容机会','原神有哪些值得关注的热点','有哪些正在升温的游戏话题'].map(q=><button key={q} disabled={busy||!universe} onClick={()=>void run({question:q,rising:q.includes('升温')})}>{q}<Icon name="arrow" size={16}/></button>)}</div>}
  {busy&&<div className="intelligence-progress" role="status" aria-label="整理情报进度">{['读取当前快照','检索相关热点','整理来源素材','装订情报档案'].map((label,i)=><span key={label} className={step===i?'current':step>i?'done':''}>{step>i?<Icon name="check" size={14}/>:<i/>}{label}</span>)}</div>}
  {error&&<p className="field-error" role="alert">{error}</p>}
  {task&&universe&&<div className="intelligence-result" ref={resultRef}>
   <header className="intelligence-result-heading"><div><span>情报档案 / {dateLabel(task.createdAt)}</span><h2>{task.records.length?'你的线索，已摊开。':'这条线索，还没被收录。'}</h2><p>{task.request.question}</p></div><button className="field-outline" disabled={!task.records.length} onClick={exportTask}><Icon name="download" size={18}/>下载情报档案</button></header>
   <div className="intelligence-result-scope"><span>{task.records.length} 个热点 · {task.records.reduce((n,r)=>n+r.materials.length,0)} 条素材 · {task.records.reduce((n,r)=>n+(universe.ideas[r.topic.id]?.length||0),0)} 个关联方案</span><small>{isDemoMode?'合成演示结果 · 未调用真实模型':'检索已有分析结果 · 未重新调用模型'}</small></div>
   {task.failures>0&&<p className="field-error">{task.failures} 个话题的证据读取失败，已保留热点记录；可以进入档案重试。</p>}
   {!task.records.length?<div className="intelligence-empty"><Icon name="search" size={50}/><h3>换一个游戏，或把问题说得更具体。</h3><p>这里只展示当前快照中实际存在的记录。试试游戏名称、话题标题，或扩大追踪范围。</p><button className="field-solid" onClick={()=>void run({question:'有哪些游戏热点',game:'all',platform:'all',period:'all',rising:false})}>看看当前已收录的热点 <Icon name="arrow"/></button></div>:<>
    {task.records.length>1&&<div className="intelligence-file-index">{task.records.map(r=><button key={r.topic.id} aria-pressed={selected?.topic.id===r.topic.id} onClick={()=>setSelectedId(r.topic.id)}><span>{r.topic.gameName}<b>{formatScore(r.topic.heat)}</b></span><strong>{r.topic.title.split(' · ').slice(1).join(' · ')||r.topic.title}</strong><small>{r.materials.length} 条素材 / {universe.ideas[r.topic.id]?.length||0} 个方案<Icon name="arrow" size={16}/></small></button>)}</div>}
    {selected&&<TopicDossier key={task.id+':'+selected.topic.id} universe={universe} topic={selected.topic} record={selected} onRecordUpdated={record=>setTask(current=>{if(!current||current.id!==task.id)return current;const records=current.records.map(r=>r.topic.id===record.topic.id?record:r);return {...current,records,failures:records.filter(r=>!!r.error).length};})} initialTab={goal} onResearch={onResearch} onLibrary={onLibrary} onTimeline={onTimeline} onToast={onToast}/>}
    <div className="intelligence-next"><span>顺着线索，继续问。</span><button onClick={()=>setGoal('materials')}>这个热点有哪些素材？ <Icon name="arrow" size={16}/></button><button onClick={()=>setGoal('strategy')}>可以做什么增长创意？ <Icon name="arrow" size={16}/></button><button onClick={()=>selected&&onSelect(selected.topic.id)}>打开完整热点档案 ↗</button></div>
   </>}
  </div>}
 </div>;
}
