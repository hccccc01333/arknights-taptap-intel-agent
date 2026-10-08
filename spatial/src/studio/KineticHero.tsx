import type { Planet } from '../lib/api';
import { formatScore, platformName } from '../lib/presentation';
import { Icon } from '../ui/Icon';
type Mode = 'signal'|'network'|'strategy';
export function KineticHero({topics,mode,onMode,motion,onMotion,onExplore,onLibrary,onTopic}:{topics:Planet[];mode:Mode;onMode:(mode:Mode)=>void;motion:boolean;onMotion:()=>void;onExplore:()=>void;onLibrary:()=>void;onTopic:(id:string)=>void}) {
 const active=topics[mode==='network'?1:mode==='strategy'?2:0]||topics[0];
 return <section className="studio-hero kinetic-hero">
  <div className="kinetic-horizon" aria-hidden="true"><i/><i/><i/></div><div className="kinetic-wordmark" aria-hidden="true">pulse</div>
  <div className="kinetic-intro"><span>游戏的下一次心跳，<br/>从玩家的声音开始。</span><i/><span>探索热点，理解传播，<br/>把发现变成下一步。</span></div>
  <h1><span>让热爱，</span><span>有回响。</span></h1>
  {active&&<button className="kinetic-current" onClick={()=>onTopic(active.id)}><span><i/>这一刻的信号 / {active.gameName||'游戏话题'}</span><b>{active.title}</b><small>{active.platforms.map(platformName).join(' / ')}<em>热度 {formatScore(active.heat)}</em></small><Icon name="arrow" size={21}/></button>}
  <div className="kinetic-start"><button className="kinetic-circle" aria-label="进入热点宇宙" onClick={onExplore}><span>进入<br/>热点宇宙</span><Icon name="arrow" size={26}/><svg viewBox="0 0 120 120" aria-hidden="true"><circle cx="60" cy="60" r="57"/></svg></button><div><p>从全网话题中，<br/>找到属于你的新可能。</p><button onClick={onLibrary}>寻找素材与策略<Icon name="arrow" size={16}/></button></div></div>
  <div className="studio-form-selector kinetic-form-selector" aria-label="3D装置形态"><p>触碰信号，探索它的另一面。<small>拖动旋转 · 双击复位</small></p><div>{([{id:'signal',label:'信号'},{id:'network',label:'共振'},{id:'strategy',label:'生长'}] as const).map(item=><button key={item.id} aria-pressed={mode===item.id} onClick={()=>onMode(item.id)}><b>{item.label}</b><i/></button>)}</div></div>
  <div className="studio-hero-bottom kinetic-bottom"><a href="#home-signals" onClick={e=>{e.preventDefault();document.getElementById('home-signals')?.scrollIntoView({behavior:motion?'instant':'smooth'});}}>向下，听见更多<span>↓</span></a><p>{topics.length} 个话题相遇于此<small>来自玩家，通往增长</small></p><button aria-pressed={motion} onClick={onMotion}>{motion?'开启动态体验':'减少动画'}<Icon name={motion?'play':'pause'} size={14}/></button></div>
 </section>;
}
