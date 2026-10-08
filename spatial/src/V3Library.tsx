import {useState} from 'react';
import {GameSignal} from './V3Hotspots';

export const intelligenceGroups:Record<string,string>={release_update:'新作与版本',player_need:'玩家需求',community_creation:'社区创作',market_movement:'市场动向',risk_monitoring:'风险观察',unclassified:'待完善分类'};
export const materialGroups:Record<string,string>={copy:'传播文案',script:'视频脚本',game_adaptation:'游戏改编',expression_pattern:'表达模板',source_quote:'来源表达',visual_brief:'视觉方案',production_checklist:'制作清单'};
export const growthGroups:Record<string,string>={game_discovery:'发现好游戏',community_participation:'社区参与',return_visit:'回访与活跃',game_conversion:'游戏预约与体验',unclassified:'待完善分类'};
const platform=(p:string)=>({mobile:'移动端',pc:'PC',cross_platform:'跨平台',unknown:'平台待核对'} as Record<string,string>)[p]||'';

export function Library({items,groups,category,id,title,summary,children,empty}:{items:any[];groups:Record<string,string>;category:(i:any)=>string;id:(i:any)=>string;title:(i:any)=>string;summary:(i:any)=>string;children:(i:any)=>React.ReactNode;empty:string}){
 const [group,setGroup]=useState('all'),[query,setQuery]=useState(''),[selected,setSelected]=useState('');
 const counts=items.reduce((r:any,i:any)=>({...r,[category(i)]:(r[category(i)]||0)+1}),{});
 const visible=items.filter(i=>(group==='all'||category(i)===group)&&(!query||(title(i)+' '+summary(i)).toLowerCase().includes(query.toLowerCase())));
 const chosen=items.find(i=>id(i)===selected);
 return <div className="v3-library"><div className="v3-library-tools"><div className="v3-categories" aria-label="内容分类"><button aria-pressed={group==='all'} onClick={()=>setGroup('all')}>全部 · {items.length}</button>{Object.entries(groups).filter(([key])=>counts[key]).map(([key,label])=><button key={key} aria-pressed={group===key} onClick={()=>setGroup(key)}>{label} · {counts[key]}</button>)}</div><input aria-label="搜索已交付内容" placeholder="搜索已交付内容" value={query} onChange={e=>setQuery(e.target.value)}/></div>
  {!items.length?<p className="v2-empty">{empty}</p>:!visible.length?<p className="v2-empty">没有匹配的内容。</p>:<div className="v3-library-cards">{visible.map(i=><button className="v3-library-card" key={id(i)} aria-pressed={selected===id(i)} onClick={()=>setSelected(id(i))}><small>{groups[category(i)]||'待完善分类'}{i.payload?.platform&&` · ${platform(i.payload.platform)}`}</small><h3>{title(i)}</h3><p>{summary(i).slice(0,160)}</p><span>打开内容 →</span></button>)}</div>}
  {chosen&&<section className="v3-library-detail" aria-label="交付内容详情"><button className="v2-secondary" onClick={()=>setSelected('')}>收起内容</button>{children(chosen)}</section>}
 </div>;
}

export function IntelligenceLibrary({items,openTopic}:{items:any[];openTopic:(id:string)=>void}){
 return <Library items={items} groups={intelligenceGroups} category={i=>i.payload.category||(i.risk_assessment?.polarity==='negative'?'risk_monitoring':'unclassified')} id={i=>i.signal_id} title={i=>i.payload.title} summary={i=>i.payload.observed_change} empty="暂时没有完成的游戏情报。后台会继续研究并归档符合条件的变化线索。">{i=><GameSignal item={i} openTopic={openTopic}/>}</Library>;
}
