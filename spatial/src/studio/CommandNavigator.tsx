import { useEffect, useRef, useState } from 'react';
import type { Universe } from '../lib/api';
import { formatScore, loadLocal } from '../lib/presentation';
import { Modal } from '../ui/Modal';
import { Icon } from '../ui/Icon';
import { groupGames, strategyEntries } from './catalog';
import './command.css';
export type StudioPage = 'home'|'signals'|'games'|'timeline'|'library'|'research'|'workbench';
export type RouteOptions = { topic?:string; game?:string; platform?:string; tab?:'materials'|'strategies'; idea?:string; page?:string };
type Entry = { key:string; title:string; hint:string; icon:string; page:StudioPage; options?:RouteOptions };
export function CommandNavigator({universe,onClose,onNavigate}:{universe:Universe|null;onClose:()=>void;onNavigate:(page:StudioPage,options?:RouteOptions)=>void}) {
 const [query,setQuery]=useState(''); const [cursor,setCursor]=useState(0); const resultsRef=useRef<HTMLDivElement>(null);
 const sections:Entry[]=[{key:'signals',title:'热点宇宙',hint:'探索3D星图',icon:'orbit',page:'signals'},{key:'games',title:'游戏与品类',hint:'品类脉冲与游戏榜单',icon:'globe',page:'games'},{key:'timeline',title:'传播时间线',hint:'追踪话题的出现',icon:'calendar',page:'timeline'},{key:'materials',title:'研究素材',hint:'证据、收藏与下载',icon:'file',page:'library'},{key:'strategies',title:'增长策略',hint:'从话题寻找机会',icon:'spark',page:'library',options:{tab:'strategies'}},{key:'research',title:'我的研究夹',hint:'整理自己的判断',icon:'bookmark',page:'research'},{key:'workbench',title:'情报工作台',hint:'继续研究与分析',icon:'terminal',page:'workbench'}];
 const term=query.trim().toLocaleLowerCase();
 const recent=loadLocal<unknown>('taptap-pulse-recent-topics',[]); const ids=Array.isArray(recent)?recent.filter((s):s is string=>typeof s==='string'):[];
 const topics=universe?.planets || [];
 const topicResults=(term?topics.filter(p=>`${p.title} ${p.gameName||''} ${p.platforms.join(' ')}`.toLocaleLowerCase().includes(term)):ids.flatMap(id=>topics.find(p=>p.id===id)||[])).slice(0,8).map(p=>({key:'topic:'+p.id,title:p.title,hint:(p.gameName||'游戏话题')+' · 热度 '+formatScore(p.heat),icon:'pulse',page:'signals' as const,options:{topic:p.id}}));
 const games=term&&universe?groupGames(universe).filter(g=>`${g.name} ${g.genre}`.toLocaleLowerCase().includes(term)).slice(0,4).map(g=>({key:g.id,title:g.name,hint:`${g.topics.length} 个话题 · ${g.genre}`,icon:'globe',page:'games' as const,options:{game:g.topics[0]?.game||g.name}})):[];
 const ideas=term&&universe?strategyEntries(universe).filter(s=>`${s.idea.name} ${s.idea.type}`.toLocaleLowerCase().includes(term)).slice(0,5).map(s=>({key:'idea:'+s.key,title:s.idea.name,hint:(s.topic?.gameName||'增长方案')+' · '+s.idea.type,icon:'spark',page:'library' as const,options:{topic:s.topicId,tab:'strategies' as const,idea:s.idea.id}})):[];
 const entries:Entry[]=[...topicResults,...games,...ideas,...sections.filter(s=>!term||`${s.title} ${s.hint}`.includes(term))];
 const signature=entries.map(e=>e.key).join('|');
 useEffect(()=>setCursor(0),[query]);
 useEffect(()=>{setCursor(v=>Math.min(v,Math.max(0,entries.length-1)));},[signature]);
 useEffect(()=>{resultsRef.current?.querySelector(`[data-result-index="${cursor}"]`)?.scrollIntoView({block:'nearest'});},[cursor]);
 const activate=(entry?:Entry)=>{if(!entry)return;onClose();onNavigate(entry.page,entry.options);};
 return <Modal title="好奇心，直达下一站。" eyebrow="PULSE / FIND ANYTHING" className="pulse-command-modal" onClose={onClose}><div onKeyDown={e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setCursor(i=>(i+(e.key==='ArrowDown'?1:-1)+Math.max(1,entries.length))%Math.max(1,entries.length));}if(e.key==='Enter'&&e.target instanceof HTMLInputElement){e.preventDefault();activate(entries[cursor]);}}}><label className="pulse-command-search"><Icon name="search" size={23}/><input value={query} aria-label="全站搜索" placeholder="输入游戏、热点、策略或功能…" onChange={e=>setQuery(e.target.value)}/><kbd>ESC</kbd></label><div className="pulse-command-results" ref={resultsRef}><p>{term?'搜索结果':'最近探索与快捷入口'}</p>{entries.map((entry,i)=><button key={entry.key} data-result-index={i} className={cursor===i?'selected':''} onMouseEnter={()=>setCursor(i)} onFocus={()=>setCursor(i)} onClick={()=>activate(entry)}><span><Icon name={entry.icon} size={19}/></span><div><b>{entry.title}</b><small>{entry.hint}</small></div><Icon name="arrow" size={16}/></button>)}{!entries.length&&<div className="pulse-command-empty">暂时没有匹配项，试试游戏名或更短的关键词。</div>}</div><footer><span>↑ ↓ 选择 · Enter 前往 · Esc 关闭</span><b>TapTap pulse.</b></footer></div></Modal>;
}
