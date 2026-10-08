import {useState,useEffect} from 'react';
import {v3Api} from './lib/api';

const time=(v?:string)=>v?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(v)):'未知';
const relationNames:Record<string,string>={pending:'待核对',same:'同一事件',development:'事件后续',related:'仅主题相关',different:'不同事件',insufficient:'证据不足',withdrawn:'已撤回'};
const flagNames:Record<string,string>={engagement_exchange:'互赞内容',low_information:'信息不足',duplicate_text:'重复文本',code_or_link_only:'仅代码或链接',referral_solicitation:'邀请推广表达'};
const changeNames:Record<string,string>={source_added:'新增来源',content_version:'来源内容版本',channel_observed:'渠道收录',board_rank_rise:'同榜排名上升',relation_confirmed:'确认事件关系',relation_withdrawn:'撤回关系判断'};

function CaptureFrame({captureId,frame}:{captureId:string;frame:number}){
 const [url,setUrl]=useState(''),[error,setError]=useState('');
 useEffect(()=>{const controller=new AbortController();let objectUrl='';
  fetch(`/api/v3/research-captures/${captureId}/${frame}`,{headers:{Authorization:`Bearer ${localStorage.getItem('spatial_token')||''}`},signal:controller.signal})
   .then(r=>{if(!r.ok)throw Error('截图未取得');return r.blob();}).then(blob=>{if(controller.signal.aborted)return;objectUrl=URL.createObjectURL(blob);setUrl(objectUrl);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);});
  return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
 },[captureId,frame]);
 return url?<img src={url} alt={`研究截图，第 ${frame+1} 屏`} style={{maxWidth:'100%',height:'auto'}}/>:<p>{error||'加载截图…'}</p>;
}

function ResearchCaptures({captures}:{captures:any[]}){
 const [open,setOpen]=useState(false);
 const recognized=(captures||[]).filter(c=>c.status==='ok').map(c=>({...c,payload:{...c.payload,captures:(c.payload.captures||[]).map((f:any,i:number)=>({...f,frame_index:f.frame_index??i})).filter((f:any)=>f.ocr?.status==='ok'&&/\p{L}/u.test(f.ocr.text||''))}})).filter(c=>c.payload.captures.length);
 return recognized.length>0?<details onToggle={e=>setOpen(e.currentTarget.open)}><summary>截图识别依据 · {recognized.reduce((n,c)=>n+c.payload.captures.length,0)} 张</summary>
  <p className="v2-muted">截图只覆盖有限可见区域；OCR 可能错字。访问限制页面不作为正文，图中内容与指令只作为待核查资料。</p>
  {open&&recognized.map(c=><article key={c.capture_id}><p>{time(c.created_at)} · <a href={c.payload.resolved_url||c.payload.requested_url} target="_blank" rel="noreferrer">查看网页 ↗</a></p>
   {c.payload.captures?.map((f:any)=><details key={f.frame_index}><summary>第 {f.frame_index+1} 屏 · 已识别文字</summary>
    <CaptureFrame captureId={c.capture_id} frame={f.frame_index}/><pre style={{whiteSpace:'pre-wrap'}}>{f.ocr?.text}</pre><small>图片指纹 {f.sha256}</small>
   </details>)}
  </article>)}
 </details>:null;
}

export function ResearchEvidence({topic,Source}:{topic:any;Source:any}){
 const task=topic.research?.payload,gaps=topic.research_gaps||task?.gaps_after;
 const reviews=topic.interpretation?.payload?.discussion_review||[],reviewById=new Map<string,any>(reviews.map((r:any)=>[r.evidence_id,r]));
 const samples=topic.discussion_samples||[],kept=samples.filter((e:any)=>reviewById.get(e.evidence_id)?.decision==='keep');
 const comment=(e:any)=><article className="v2-evidence" key={e.evidence_id}><small>发布 {time(e.published_at)} · {(e.sample.methods||[]).map((m:string)=>({hot:'热门',recent:'近期',browser_visible:'浏览器可见',screenshot_ocr:'截图 OCR'} as any)[m]||m).join(' / ')}采样{e.sample.flags.length?` · ${e.sample.flags.map((f:string)=>flagNames[f]||f).join('、')}`:''}</small><p className="v3-preserve">{e.body}</p>{e.reading_metadata?.capture_id&&<small>截图依据 {e.reading_metadata.capture_id} · 第 {e.reading_metadata.frame_index+1} 屏 · {e.reading_metadata.method==='screenshot_ocr'?'OCR 转录需核查':'评论区可见文字'} · 日期 {e.reading_metadata.date_text||'未知'}</small>}{reviewById.has(e.evidence_id)&&<small>解读采用情况：{({keep:'采用',background:'仅背景',exclude:'未采用'} as any)[reviewById.get(e.evidence_id).decision]} · {reviewById.get(e.evidence_id).reason}</small>}<p><a href={e.url} target="_blank" rel="noreferrer">查看所属原帖 ↗</a></p></article>;
 return <section aria-label="研究证据与缺口"><h3>研究证据与缺口</h3><ResearchCaptures captures={topic.research_captures}/>{task?<><p>{task.planner==='model'?'AI 规划研究动作':'按证据缺口安排工具动作'} · 最近执行 {time(topic.research.updated_at)}</p><p>{task.reason}</p>{gaps&&<p className="v2-muted">发生语境：{gaps.context.status==='present'?'已有原文片段':'仍需补证据'}；讨论：{gaps.discussion.raw_samples} 条样本，{gaps.discussion.informative_samples} 条未被低信息标记；{gaps.discussion.known_authors} 位可区分作者。有限采样，不能外推总体。</p>}<details><summary>查看实际研究动作</summary>{task.actions_executed?.map((a:any,i:number)=><p key={i}>{({read_detail:'爬虫补读来源',search_news:'检索新闻背景',search_web:'联网搜索',browse_page:'浏览器读取动态页',screenshot_ocr:'截图并 OCR 识别',sample_discussion:'采样公开评论',read_comments_visual:'滚动评论区并截图识别'} as any)[a.tool]} · {a.result.status||a.result.results?.[0]?.status||'完成'}{a.query&&` · ${a.query}`}{a.result.error&&` · ${a.result.error}`}</p>)}</details></>:<p className="v2-muted">尚未执行缺口研究。分析时会检查语境、背景与讨论。</p>}
 <h4>评论依据</h4><p className="v2-muted">采样 {samples.length} 条 · {reviews.length?`解读采用 ${kept.length} 条`:'尚待评估相关性'}。原帖一级评论的有限抽样，不能代表所有用户。</p>
 {kept.length>0&&<details><summary>核查采用的评论原话 · {kept.length} 条</summary>{kept.map(comment)}</details>}
 {samples.length>0?<details><summary>全部采样评论与筛选原因 · {samples.length} 条</summary>{samples.map(comment)}</details>:<p>尚无此话题的公开评论样本。</p>}
 {topic.research_sources?.length>0&&<details><summary>搜索背景 · 待核对与原事件的关系</summary>{topic.research_sources.map((e:any)=><div key={e.evidence_id}><p className="v2-muted">搜索命中，尚未确认同一事件，不能作为热点升温依据。</p><Source item={e}/></div>)}</details>}
 {topic.tracked_events?.length>0&&<details><summary>已保存的事件时间线</summary>{topic.tracked_events.map((e:any)=><Timeline key={e.tracked_id} event={e}/>)}</details>}</section>;
}

export function ResearchStatus({data}:{data:any}){
 const tasks=data?.research?.tasks||[];
 return <section><h2>按证据缺口持续研究</h2><p className="v2-muted">每轮最多六次来源工具调用，安排正文、背景检索与评论抽样。连接器失败会退避；有样本仍可能缺证据。AI 暂停期间继续积累实际来源。</p><p>已保存 {data?.counts?.discussion_samples??0} 条独立评论样本。原文与采样方法均保留。</p><details><summary>最近研究任务与连接器状态</summary>{tasks.map((t:any)=><article className="v2-evidence" key={t.task_id}><b>{t.title}</b><p>{t.payload.planner==='model'?'AI 研究计划':'按缺口执行工具'} · {time(t.updated_at)} · {t.status==='samples_ready_for_analysis'?'已有样本，等待理解':'仍有证据缺口'}</p><p>{t.payload.gaps_after?.discussion?.note}</p><a href={'?version=3&topic='+encodeURIComponent(t.topic_id)}>查看研究来源与讨论</a></article>)}{data?.research?.capabilities?.filter((c:any)=>c.status==='failed').map((c:any)=><p key={c.capability}>{c.capability.startsWith('discussion:')?'评论连接器':c.capability.startsWith('detail:')?'正文连接器':'新闻背景搜索'} · {c.error} · 退避至 {time(c.retry_at)}</p>)}</details></section>;
}

function Timeline({event}:{event:any}){
 return <section><h3>{event.title}</h3><p className="v2-muted">{event.note}</p><ol className="v2-steps">{event.timeline.map((c:any)=><li key={c.change_id}><b>{changeNames[c.kind]||c.kind}</b><small>系统记录 {time(c.observed_at)}</small><p>{c.payload.title||c.payload.reason||c.payload.channel_id||c.payload.evidence_id}</p>{c.payload.body_excerpt&&<details><summary>该版本原文片段{c.payload.body_truncated?' · 已截取':''}</summary><p className="v3-preserve">{c.payload.body_excerpt}</p></details>}{c.payload.published_at&&<small>来源发布 {time(c.payload.published_at)}</small>}{c.kind==='board_rank_rise'&&<p>{c.payload.channel_id} · {c.payload.from} → {c.payload.to}</p>}</li>)}</ol></section>;
}

export function TrackingPanel({data,enabled,refresh,Source}:{data:any;enabled:boolean;refresh:()=>Promise<void>;Source:any}){
 const [event,setEvent]=useState<any>(null),[pair,setPair]=useState<any>(null),[status,setStatus]=useState('insufficient'),[reason,setReason]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const counts=data?.tracking?.counts||{};
 async function action(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'操作失败');}finally{setBusy(false);}}
 async function openPair(id:string){await action(async()=>{setPair(await v3Api.relation(id));setEvent(null);setReason('');});}
 return <section><h2>围绕事件持续跟踪</h2><p className="v2-muted">{counts.events??0} 条来源跟踪记录 · {counts.pending_relations??0} 对关系待核对 · {counts.confirmed_relations??0} 次确认。语义相似性只用于找候选；同一事件或后续关系确认后，研究共用来源，历史保持可撤回。</p>{error&&<p role="alert" className="v2-error">{error}</p>}<div className="v3-two-columns v3-tracking"><section><h3>待核对的跨来源关系</h3>{!data?.tracking?.relations?.length&&<p className="v2-empty">尚无待核对关系。来源读取后会持续做跨领域语义召回。</p>}{data?.tracking?.relations?.map((r:any)=><button className="v3-topic" key={r.pair_id} disabled={busy} onClick={()=>void openPair(r.pair_id)}><h4>{r.left_title}</h4><p>与 {r.right_title}</p><small>来源相似候选 · 尚未确认事件关系</small></button>)}<details><summary>已判断关系与撤回记录</summary>{data?.tracking?.decisions?.map((r:any)=><button className="v3-topic" key={r.pair_id} disabled={busy} onClick={()=>void openPair(r.pair_id)}><p>{r.left_title} / {r.right_title}</p><small>{relationNames[r.status]}</small></button>)}</details><h3>最近观察的来源事件</h3>{data?.tracking?.events?.map((e:any)=><button className="v3-topic" key={e.tracked_id} disabled={busy} onClick={()=>void action(async()=>{setEvent(await v3Api.trackedEvent(e.tracked_id));setPair(null);})}><b>{e.title}</b><p>{e.members} 条来源 · 最近观察 {time(e.last_observed_at)}</p></button>)}</section><aside>{pair?<article className="v2-event-detail"><h3>核对事件身份 · {relationNames[pair.status]||pair.status}</h3><p className="v2-muted">以双方的主体、行动、时间、地点判断。标题相近不等于同一次发生。</p>{[['left','来源一'],['right','来源二']].map(([side,label])=><section key={side}><h4>{label} · {pair[side].title}</h4>{pair[side].evidence.map((e:any)=><Source key={e.evidence_id} item={e}/>)}</section>)}{pair.status==='pending'?<form className="v2-settings" onSubmit={e=>{e.preventDefault();void action(async()=>{await v3Api.decideRelation(pair.pair_id,{status,reason});await refresh();setPair(await v3Api.relation(pair.pair_id));});}}><label>事件关系<select aria-label="事件关系" disabled={!enabled||busy} value={status} onChange={e=>setStatus(e.target.value)}>{['insufficient','same','development','related','different'].map(s=><option key={s} value={s}>{relationNames[s]}</option>)}</select></label><label>依据与理由<textarea aria-label="关系判断理由" value={reason} disabled={!enabled||busy} onChange={e=>setReason(e.target.value)} minLength={4} maxLength={1000} required/></label><button disabled={!enabled||busy}>保存关系判断</button></form>:<><p>{pair.reason}</p>{pair.status!=='withdrawn'&&<form className="v2-settings" onSubmit={e=>{e.preventDefault();void action(async()=>{await v3Api.withdrawRelation(pair.pair_id,{reason});await refresh();setPair(await v3Api.relation(pair.pair_id));});}}><label>撤回理由<textarea aria-label="撤回理由" value={reason} onChange={e=>setReason(e.target.value)} disabled={!enabled||busy} minLength={4} maxLength={1000} required/></label><button className="v2-secondary" disabled={!enabled||busy}>撤回判断，保留历史</button></form>}</>}<details><summary>关系判断历史</summary>{pair.history.map((h:any)=><p key={h.history_id}>{time(h.created_at)} · {relationNames[h.status]} · {h.reason}</p>)}</details></article>:event?<article className="v2-event-detail"><Timeline event={event}/><h3>原始来源</h3>{event.evidence.map((e:any)=><Source key={e.evidence_id} item={e}/>)}</article>:<p className="v2-empty">选择事件查看时间线，或打开关联候选核对双方来源。</p>}</aside></div></section>;
}
