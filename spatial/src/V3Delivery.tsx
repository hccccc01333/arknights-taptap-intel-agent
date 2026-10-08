import {useEffect,useState} from 'react';
import {v3Api} from './lib/api';

export const deliveryTime=(v?:string)=>v&&!Number.isNaN(Date.parse(v))?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(v)):'时间未确认';
const names:Record<string,string>={bilibili:'B站',taptap:'TapTap',weibo:'微博',gamemedia:'游戏媒体',baidu:'百度',web:'网页'};

export function SourceReferences({ids,children}:{ids:string[];children?:React.ReactNode}){
 const [opened,setOpened]=useState(false),[sources,setSources]=useState<any[]|null>(null),[error,setError]=useState('');
 const key=[...new Set(ids||[])].join(',');
 useEffect(()=>{if(!opened||!key)return;let live=true;setSources(null);setError('');
  const values=key.split(',');void Promise.all(Array.from({length:Math.ceil(values.length/20)},(_,i)=>v3Api.sources(values.slice(i*20,i*20+20))))
   .then(results=>live&&setSources(results.flatMap(r=>r.sources))).catch(e=>live&&setError(e.message));return()=>{live=false;};
 },[opened,key]);
 return <details className="v3-source-references" onToggle={e=>setOpened(e.currentTarget.open)}><summary>核查来源与引用{key?` · ${key.split(',').length} 条`:''}</summary>
  {children}{error&&<p role="alert">{error}</p>}{opened&&key&&!sources&&!error&&<p role="status">正在读取来源档案…</p>}
  {sources?.map(s=><article key={s.evidence_id}><b>{s.title||'来源记录'}</b><small>{names[s.platform]||s.platform||'网页'} · 发布 {deliveryTime(s.published_at)}</small>{/^https?:\/\//i.test(s.url||'')&&<a href={s.url} target="_blank" rel="noreferrer">打开原始来源 ↗</a>}</article>)}
  {sources&&key.split(',').some(id=>!sources.some(s=>s.evidence_id===id))&&<p className="v2-muted">部分来源未收入当前公开档案，请在本地核查；不能据此补写未读内容。</p>}
  {!key&&<p className="v2-muted">没有独立来源引用。原创草案的事件依据在方案溯源中查看。</p>}
 </details>;
}

function materialText(m:any){
 const d=m.application?.delivery;
 return [`### ${m.title||'制作内容'}`,d?`使用者：${d.audience}\n使用位置：${d.placement}`:'',d?.body||m.content||'',
  d?.adaptation_steps?.length?`使用方法：\n${d.adaptation_steps.map((s:string,i:number)=>`${i+1}. ${s}`).join('\n')}`:'',
  `使用边界：${d?.usage_boundary||m.rights_status||'使用前核查游戏能力、来源授权与发布位置。'}`,m.application?.game_application?`玩家场景：${m.application.game_application}`:'',
  m.application?.usage_example?`使用示例：${m.application.usage_example}`:'',m.rights_status?`素材条件：${m.rights_status}`:''].filter(Boolean).join('\n\n');
}
function creativeText(item:any){
 const c=item.payload;
 return [`# ${c.title}`, '增长草案：效果尚未验证，上线前核查入口、游戏能力与素材条件。',
  ...[['audience','目标玩家'],['growth_goal','用户动作目标'],['hook','创意机制'],['distribution','传播位置'],['placement','TapTap 承接'],['user_action','用户动作']].map(([k,l])=>`${l}：${c[k]||'待确认'}`),
  `用户路径：${c.journey?.join(' → ')||'待确认'}`,`## 可编辑文案\n\n${c.copy||''}`,
  ...(c.deliverables||[]).filter((m:any)=>m.kind!=='copy').map(materialText),
  `## 执行方法\n\n${(c.steps||[]).map((s:string,i:number)=>`${i+1}. ${s}`).join('\n')}`,
  `时机：${c.timing||'待确认'}`,`资源条件：${c.resources||'待确认'}`,`待验证假设：${c.growth_hypothesis||'待确认'}`,
  `上线前核查：${c.prerequisites?.join('；')||'待确认'}`,`建议验证：${c.validation_plan||c.measurement||'待确认'}`,`风险：${c.risks?.join('；')||'待确认'}`].join('\n\n');
}
export function DeliveryActions({item,type}:{item:any;type:'material'|'creative'}){
 const [message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 async function draft(){
  const base=type==='creative'?creativeText(item):materialText(item);
  const ids=[...new Set<string>(type==='creative'?(item.payload.deliverables||[]).flatMap((m:any)=>m.evidence_ids||[]):item.evidence_ids||[])];
  let appendix='';if(ids.length){try{const result=await Promise.all(Array.from({length:Math.ceil(ids.length/20)},(_,i)=>v3Api.sources(ids.slice(i*20,i*20+20))));
   const sources=result.flatMap(r=>r.sources);appendix='\n\n## 来源\n\n'+ids.map(id=>{const s=sources.find(s=>s.evidence_id===id);return s?`- ${s.title||'来源'} · ${names[s.platform]||s.platform||'网页'} · ${s.url||'链接未确认'}`:`- ${id}：在本地档案中核查`;}).join('\n');
  }catch{appendix='\n\n来源链接暂时未取得，请回到系统核查事件依据。';}}
  return base+`\n\n整理时间：${deliveryTime(item.delivery_meta?.created_at||item.created_at)}\n来源事件：${item.delivery_meta?.event_title||'见系统事件解读'}`+appendix;
 }
 async function deliver(mode:'copy'|'download'){
  setBusy(true);setMessage('');try{const text=await draft();if(mode==='copy'){await navigator.clipboard.writeText(text);setMessage('已复制，可继续编辑。');}
   else{const url=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=((item.payload?.title||item.title||'交付内容').replace(/[\\/:*?"<>|]/g,'_').slice(0,70))+'.md';a.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000);setMessage('已下载 Markdown 稿，包含使用边界和来源。');}
  }catch{setMessage('复制失败，请选择下载稿件，或选中文字复制。');}finally{setBusy(false);}
 }
 return <div className="v3-delivery-actions"><button disabled={busy} onClick={()=>void deliver('copy')}>{type==='creative'?'复制完整方案':'复制可编辑稿'}</button><button className="v2-secondary" disabled={busy} onClick={()=>void deliver('download')}>下载 Markdown</button><small role="status">{message}</small></div>;
}
