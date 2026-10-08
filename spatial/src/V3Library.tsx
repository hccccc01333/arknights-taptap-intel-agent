import {useEffect,useRef,useState} from 'react';
import {GameSignal} from './V3Hotspots';
import {deliveryTime} from './V3Delivery';

export const intelligenceGroups:Record<string,string>={release_update:'新作与版本',player_need:'玩家需求',community_creation:'社区创作',market_movement:'市场动向',risk_monitoring:'风险观察',unclassified:'待完善分类'};
export const materialGroups:Record<string,string>={copy:'传播文案',script:'视频脚本',game_adaptation:'游戏改编',expression_pattern:'表达模板',source_quote:'来源表达',visual_brief:'视觉方案',production_checklist:'制作清单'};
export const growthGroups:Record<string,string>={game_discovery:'发现好游戏',community_participation:'社区参与',return_visit:'回访与活跃',game_conversion:'游戏预约与体验',unclassified:'待完善分类'};
const platform=(p:string)=>({mobile:'移动端',pc:'PC',cross_platform:'跨平台',unknown:'平台待核对'} as Record<string,string>)[p]||'';

export function Library({items,groups,category,id,title,summary,children,empty}:{items:any[];groups:Record<string,string>;category:(i:any)=>string;id:(i:any)=>string;title:(i:any)=>string;summary:(i:any)=>string;children:(i:any)=>React.ReactNode;empty:string}){
 const [group,setGroup]=useState('all'),[query,setQuery]=useState(''),[selected,setSelected]=useState(''),[sort,setSort]=useState('recent'),[device,setDevice]=useState('all');
 const opener=useRef<HTMLElement|null>(null);
 const counts=items.reduce((r:any,i:any)=>({...r,[category(i)]:(r[category(i)]||0)+1}),{});
 const visible=items.filter(i=>(group==='all'||category(i)===group)&&(device==='all'||i.payload?.platform===device)&&(!query.trim()||JSON.stringify([title(i),summary(i),i.payload,i.application,i.delivery_meta?.event_title]).toLowerCase().includes(query.trim().toLowerCase()))).sort((a,b)=>sort==='title'?title(a).localeCompare(title(b),'zh-CN'):(Date.parse(b.delivery_meta?.created_at||b.created_at)||0)-(Date.parse(a.delivery_meta?.created_at||a.created_at)||0));
 const chosen=items.find(i=>id(i)===selected);
 return <div className="v3-library"><div className="v3-library-tools"><div className="v3-categories" aria-label="内容分类"><button aria-pressed={group==='all'} onClick={()=>setGroup('all')}>全部 · {items.length}</button>{Object.entries(groups).filter(([key])=>counts[key]).map(([key,label])=><button key={key} aria-pressed={group===key} onClick={()=>setGroup(key)}>{label} · {counts[key]}</button>)}</div><div className="v3-library-filters"><input aria-label="搜索已交付内容" placeholder="搜索标题、内容或来源事件" value={query} onChange={e=>setQuery(e.target.value)}/>{items.some(i=>i.payload?.platform)&&<select aria-label="游戏平台筛选" value={device} onChange={e=>setDevice(e.target.value)}><option value="all">全部游戏平台</option>{['mobile','pc','cross_platform','unknown'].map(p=><option key={p} value={p}>{platform(p)}</option>)}</select>}<select aria-label="内容排序" value={sort} onChange={e=>setSort(e.target.value)}><option value="recent">最新整理优先</option><option value="title">按标题排序</option></select></div></div>
  {!!items.length&&<p className="v2-muted" role="status">显示 {visible.length} / {items.length} 项 · 当前可用成果</p>}
  {!items.length?<p className="v2-empty">{empty}</p>:!visible.length?<p className="v2-empty">没有匹配的内容。可以调整分类、平台或搜索词。</p>:<div className="v3-library-cards">{visible.map(i=><button className="v3-library-card" key={id(i)} aria-pressed={selected===id(i)} onClick={e=>{opener.current=e.currentTarget;setSelected(id(i));}}><small>{groups[category(i)]||'待完善分类'}{i.payload?.platform&&` · ${platform(i.payload.platform)}`}</small><h3>{title(i)}</h3><p>{summary(i).slice(0,160)}</p>{i.delivery_meta?.event_title&&<small className="v3-card-context">来源事件 · {i.delivery_meta.event_title}</small>}<span>整理 {deliveryTime(i.delivery_meta?.created_at||i.created_at)} · 打开内容 →</span></button>)}</div>}
  {chosen&&<ReadingDialog close={()=>setSelected('')} title={title(chosen)} returnFocus={opener.current}>{children(chosen)}</ReadingDialog>}
 </div>;
}

function ReadingDialog({close,title,children,returnFocus}:{close:()=>void;title:string;children:React.ReactNode;returnFocus:HTMLElement|null}){
 const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{const node=dialog.current;if(node&&!node.open)node.showModal();const previous=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{node?.close();document.body.style.overflow=previous;if(returnFocus?.isConnected)returnFocus.focus();};},[]);
 return <dialog ref={dialog} className="v3-reading-dialog" aria-label="交付内容详情" onCancel={e=>{e.preventDefault();close();}} onClick={e=>{if(e.target===e.currentTarget)close();}}><div className="v3-reading-sheet"><header><b>{title}</b><button className="v2-secondary" autoFocus onClick={close}>关闭详情</button></header><div className="v3-reading-body">{children}</div></div></dialog>;
}

export function IntelligenceLibrary({items,openTopic}:{items:any[];openTopic:(id:string)=>void}){
 return <Library items={items} groups={intelligenceGroups} category={i=>i.payload.category||(i.risk_assessment?.polarity==='negative'?'risk_monitoring':'unclassified')} id={i=>i.signal_id} title={i=>i.payload.title} summary={i=>i.payload.observed_change} empty="暂时没有完成的游戏情报。后台会继续研究并归档符合条件的变化线索。">{i=><GameSignal item={i} openTopic={openTopic}/>}</Library>;
}
