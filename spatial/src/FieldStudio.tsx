import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { isDemoMode, login, type Universe } from './lib/api';
import { formatScore, loadLocal, saveLocal } from './lib/presentation';
import { Icon } from './ui/Icon';
import { Modal } from './ui/Modal';
import { FieldDiscovery } from './studio/FieldDiscovery';
import { FieldSignals } from './studio/FieldSignals';
import { FieldResearch } from './studio/FieldResearch';
import { TopicDossier } from './studio/IntelligenceFlow';
import { SessionDesk } from './research/SessionDesk';
import { SessionIndex } from './research/SessionIndex';
import { useSessions } from './research/repository';
import { defaultRequest } from './research/domain';
import { startResearch } from './research/engine';
import { intelligenceData, useEnvironment } from './research/data';
import { GuideHub } from './research/GuideHub';
import { ResourceOrientation } from './research/PageInstructions';
import { navigate, navigation as nav, allNavigation, resourceNavigation, useRoute, type Page, type NavOptions } from './research/navigation';
import { groupGames, strategyEntries } from './studio/catalog';
import './studio/field.css';
import './research/research-v4.css';
import './research/research-v5.css';
import './research/research-v6.css';
import './research/research-v7.css';
import './research/research-v8.css';
import { PulseHome } from './research/PulseHome';
const LegacyWorkspace=lazy(()=>import('./studio/LegacyWorkspace'));
const EmployeeWorkbench=lazy(()=>import('./research/EmployeeWorkbench'));
export default function FieldStudio(){
 const [guideOpen,setGuideOpen]=useState(false);
 const [universe,setUniverse]=useState<Universe|null>(null);const [error,setError]=useState('');const [retry,setRetry]=useState(0);const [menu,setMenu]=useState(false);const [searchOpen,setSearchOpen]=useState(false);const [search,setSearch]=useState('');const [cursor,setCursor]=useState(0);const [quickId,setQuickId]=useState<string|null>(null);const [toast,setToast]=useState('');
 const [reduced,setReduced]=useState(()=>loadLocal<boolean>('taptap_v2_reduced_motion',matchMedia('(prefers-reduced-motion:reduce)').matches)===true);const toastTimer=useRef(0);
 const route=useRoute(reduced,()=>{setMenu(false);setSearchOpen(false);setQuickId(null);setGuideOpen(false);});const go=navigate;const sessions=useSessions();const scope=useEnvironment();
 const [loginOpen,setLoginOpen]=useState(false);const [actor,setActor]=useState('');const [password,setPassword]=useState('');const [loginBusy,setLoginBusy]=useState(false);const [loginError,setLoginError]=useState('');
 useEffect(()=>{let live=true;const controller=new AbortController();setUniverse(null);setError('');intelligenceData.universe({signal:controller.signal,force:retry>0}).then(value=>{if(live)setUniverse(value);}).catch(e=>{if(live)setError(e instanceof Error?e.message:'情报读取失败');});return()=>{live=false;controller.abort();};},[retry,scope]);
 useEffect(()=>{saveLocal('taptap_v2_reduced_motion',reduced);},[reduced]);
 useEffect(()=>{const key=(e:KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();setSearchOpen(v=>!v);}if(e.key==='Escape')setMenu(false);};addEventListener('keydown',key);return()=>removeEventListener('keydown',key);},[]);
 useEffect(()=>()=>clearTimeout(toastTimer.current),[]);
 useEffect(()=>{document.title=(route.page==='home'||route.page==='ai'?'做方案':allNavigation.find(n=>n.id===route.page)?.label||'我的项目')+' · TapTap Pulse';},[route.page]);
 const say=(message:string)=>{clearTimeout(toastTimer.current);setToast(message);toastTimer.current=window.setTimeout(()=>setToast(''),4800);};
 const remember=(id:string)=>{const prev=loadLocal<unknown>('taptap-pulse-recent-topics',[]);saveLocal('taptap-pulse-recent-topics',[id,...(Array.isArray(prev)?prev.filter(v=>typeof v==='string'&&v!==id):[])].slice(0,8));};
 const openTopic=(id:string)=>{remember(id);go('signals',{topic:id});};
 const openSession=(id:string)=>go('research',{session:id});
 const addResearch=(id:string)=>{const topic=universe?.planets.find(p=>p.id===id);if(!topic||!universe)return;void startResearch(scope,{...defaultRequest,question:topic.title,game:topic.game||'all'},universe).then(openSession).catch(e=>say(e instanceof Error?e.message:'研究创建失败'));};
 const handoff=(topic?:string,idea?:string)=>go('workbench',{topic,idea,page:idea?'opportunities':topic?'space':'overview'});
 const topics=universe?.planets||[];const quickTopic=topics.find(p=>p.id===quickId);
 const term=search.trim().toLocaleLowerCase();
 const searchEntries=[...topics.filter(p=>term&&`${p.title} ${p.gameName||''}`.toLocaleLowerCase().includes(term)).slice(0,6).map(p=>({id:p.id,title:p.title,detail:'热点档案 · 热度 '+formatScore(p.heat),page:'signals' as Page,options:{topic:p.id} as NavOptions})),...(universe?groupGames(universe).filter(g=>term&&`${g.name} ${g.genre}`.toLocaleLowerCase().includes(term)).slice(0,3).map(g=>({id:g.id,title:g.name,detail:'游戏档案 · '+g.genre,page:'games' as Page,options:{game:g.topics[0]?.game||g.name} as NavOptions})):[]),...(universe?strategyEntries(universe).filter(s=>term&&`${s.idea.name} ${s.idea.type}`.toLocaleLowerCase().includes(term)).slice(0,3).map(s=>({id:s.key,title:s.idea.name,detail:'增长策略 · '+s.topic?.gameName,page:'library' as Page,options:{topic:s.topicId,tab:'strategies'} as NavOptions})):[]),...allNavigation.filter(n=>!term||n.label.includes(term)).map(n=>({id:n.id,title:n.label,detail:'前往 '+n.label,page:n.id,options:{} as NavOptions})),...sessions.sessions.filter(s=>s.status!=='archived'&&term&&s.request.question.toLocaleLowerCase().includes(term)).slice(0,4).map(s=>({id:s.id,title:s.request.question,detail:'我的项目 · '+s.selectedEvidenceIds.length+' 条证据',page:'research' as Page,options:{session:s.id} as NavOptions}))];
 useEffect(()=>setCursor(0),[search]);
 async function doLogin(){if(loginBusy)return;setLoginBusy(true);setLoginError('');try{await login(actor,password);setPassword('');setLoginOpen(false);setRetry(v=>v+1);}catch(e){setLoginError(e instanceof Error?e.message:'登录失败');}finally{setLoginBusy(false);}}
 const dossierProps={onResearch:addResearch,onLibrary:(id:string,tab:'materials'|'strategies')=>go('library',{topic:id,tab}),onTimeline:(id:string)=>go('timeline',{topic:id}),onToast:say};
 if(route.page==='workbench'&&route.workspacePage==='legacy')return <Suspense fallback={<div className="field-loading">正在打开经典工作台…</div>}><LegacyWorkspace onLeave={()=>go('home')} initialTopicId={route.topic} initialIdeaId={route.idea} onExplore={(page,options)=>go(page,options)}/><button className="v7-legacy-return" onClick={()=>go('workbench')}>← 返回员工工作台</button></Suspense>;
 return <div data-modal-root className={'field field-v4 field-v5 field-v6 field-v7 field-v8 '+(reduced?'less-motion':'')}>
  <header className="field-header" data-app-content><a href="#/home" className="field-brand" aria-label="TapTap Pulse 首页"><span className="field-brand-symbol" aria-hidden="true">p</span><span>pulse<small>by TapTap</small></span></a><nav className={menu?'open':''} aria-label="网站导航">{nav.map(n=><a key={n.id} href={'#/'+n.id} aria-current={route.page===n.id||(n.id==='home'&&route.page==='ai')||(n.id==='signals'&&resourceNavigation.some(tool=>tool.id===route.page))?'page':undefined}><span>{n.label}</span></a>)}</nav><div className="field-header-tools"><button className="field-header-search" aria-label="搜索游戏、热点与策略" onClick={()=>setSearchOpen(true)}><Icon name="search" size={19}/><kbd>⌘ K</kbd></button><button className="v8-header-guide" onClick={()=>setGuideOpen(true)}><Icon name="help" size={18}/><span>使用指南</span></button><button className="field-menu" aria-label={menu?'关闭导航':'打开导航'} aria-expanded={menu} onClick={()=>setMenu(v=>!v)}><Icon name={menu?'close':'layers'} size={21}/></button></div></header>
  <main data-app-content>
   {error&&<div className="field-api-error" role="alert"><p>{error}</p><button onClick={()=>setRetry(v=>v+1)}>重新连接</button>{!isDemoMode&&<button onClick={()=>setLoginOpen(true)}>登录后读取情报</button>}</div>}
   {!isDemoMode&&universe?.pipelineHealth?.status==='degraded'&&<section className="field-data-health" role="status" aria-label="情报数据时效"><strong>部分情报缺少近期数据，请核查后使用。</strong><details><summary>查看各层数据时间</summary><ul>{universe.pipelineHealth.warnings.map(message=><li key={message}>{message}</li>)}</ul><p>{universe.pipelineHealth.note}</p></details><button onClick={()=>setRetry(v=>v+1)}>重新检查</button></section>}
   {(route.page==='home'||route.page==='ai')&&<PulseHome universe={universe} reducedMotion={reduced} onGuide={()=>setGuideOpen(true)} onSelect={id=>{remember(id);setQuickId(id);}} onOpen={openSession} onToast={say}/> }
   {['signals','games','timeline','library'].includes(route.page)&&<ResourceOrientation page={route.page}/>}
   {route.page==='signals'&&universe&&<div className="field-page"><FieldSignals universe={universe} topicId={route.topic} game={route.game} platform={route.platform} onSelect={openTopic} {...dossierProps}/></div>}
   {(['games','timeline','library'] as Page[]).includes(route.page)&&universe&&<div className="field-page"><FieldDiscovery page={route.page as 'games'|'timeline'|'library'} universe={universe} initialTopicId={route.topic} initialTab={route.tab} initialGame={route.game} initialPlatform={route.platform} onSelect={openTopic} onResearch={addResearch} onWorkspace={handoff}/></div>}
   {route.page==='research'&&<div className="v4-page">{route.session?<SessionDesk id={route.session} universe={universe} reducedMotion={reduced} onOpen={openSession} onIndex={()=>go('research')} onTimeline={id=>go('timeline',{topic:id})} onToast={say}/>:<SessionIndex onStartGuide={()=>setGuideOpen(true)} universe={universe} onOpen={openSession} onToast={say} onLegacy={()=>go('notes')}/>}</div>}
   {route.page==='workbench'&&<Suspense fallback={<div className="field-loading">正在展开员工工作台…</div>}><EmployeeWorkbench universe={universe} initialPage={route.workspacePage} initialActionId={route.action} initialSessionId={route.session} initialTopicId={route.topic} reducedMotion={reduced} onNavigate={(page,action,session)=>go('workbench',{page,action,session})} onResearch={id=>id?openSession(id):go('research')} onTopicResearch={addResearch} onLegacy={()=>go('workbench',{page:'legacy'})} onRefresh={()=>setRetry(v=>v+1)} onToast={say}/></Suspense>}
   {route.page==='notes'&&universe&&<div className="field-page"><FieldResearch universe={universe} topicId={route.topic} onSelect={openTopic} onLibrary={dossierProps.onLibrary} onToast={say}/></div>}
   {!universe&&!error&&!['home','ai','research','workbench'].includes(route.page)&&<div className="field-loading" role="status">正在读取情报档案…</div>}
  </main>
  <footer className="field-footer" data-app-content><a href="#/home" className="field-footer-brand">pulse<span>↗</span></a><div><h2>让热爱，有下一步。</h2><p>{isDemoMode?'合成演示体验 · 现有交互未调用真实 AI 模型。':'读取已有 API 情报 · 本次检索未重新调用模型。'}<br/>热点、素材来源与建议的边界会保留在档案中。</p></div><div className="field-footer-links"><a href="#/research">我的项目 ↗</a>{resourceNavigation.map(n=><a href={'#/'+n.id} key={n.id}>{n.label} ↗</a>)}<button onClick={()=>setReduced(v=>!v)} aria-pressed={reduced}>{reduced?'开启动画':'减少动画'}</button></div></footer>
  {guideOpen&&<GuideHub universe={universe} onClose={()=>setGuideOpen(false)} onOpen={openSession} onNavigate={page=>{setGuideOpen(false);go(page);}}/>}
  {quickTopic&&universe&&<Modal title="打开这份热点档案" onClose={()=>setQuickId(null)} className="field-quick-file"><TopicDossier universe={universe} topic={quickTopic} {...dossierProps}/></Modal>}
  {searchOpen&&<Modal title="顺着好奇心，直达线索。" onClose={()=>setSearchOpen(false)} className="field-search-modal"><div onKeyDown={e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setCursor(v=>(v+(e.key==='ArrowDown'?1:-1)+Math.max(1,searchEntries.length))%Math.max(1,searchEntries.length));}if(e.key==='Enter'&&e.target instanceof HTMLInputElement){e.preventDefault();const item=searchEntries[cursor];if(item)go(item.page,item.options);}}}><label className="field-global-search"><Icon name="search" size={24}/><input value={search} aria-label="全站搜索" placeholder="一个游戏、热点，或者增长想法…" onChange={e=>setSearch(e.target.value)}/></label><div className="field-search-results">{searchEntries.map((s,i)=><button key={s.id} className={cursor===i?'selected':''} onFocus={()=>setCursor(i)} onMouseEnter={()=>setCursor(i)} onClick={()=>go(s.page,s.options)}><span><b>{s.title}</b><small>{s.detail}</small></span><Icon name="arrow" size={20}/></button>)}{!searchEntries.length&&<p className="field-empty">没有匹配的线索，试试游戏名或更短的关键词。</p>}</div><p className="field-search-hint">↑ ↓ 选择 / Enter 打开 / Esc 关闭</p></div></Modal>}
  {loginOpen&&<Modal title="连接你的情报账户" onClose={()=>setLoginOpen(false)}><form className="field-login" onSubmit={e=>{e.preventDefault();void doLogin();}}><label>账号<input value={actor} autoComplete="username" onChange={e=>setActor(e.target.value)} required/></label><label>密码<input type="password" value={password} autoComplete="current-password" onChange={e=>setPassword(e.target.value)} required/></label>{loginError&&<p role="alert">{loginError}</p>}<button className="field-solid" disabled={loginBusy}>{loginBusy?'正在连接…':'登录并读取情报'}</button></form></Modal>}
  {toast&&<div className="field-toast" role="status"><Icon name="check" size={17}/>{toast}</div>}
 </div>;
}
