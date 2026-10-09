import {useEffect,useState} from 'react';
import {auth,v3Api} from './lib/api';
import {isPublicResults} from './lib/publicResults';
import {HotspotWorkbench,GameCoverage,RiskNotice} from './V3Hotspots';
import {Library,IntelligenceLibrary,materialGroups,growthGroups} from './V3Library';
import {DeliveryActions,SourceReferences} from './V3Delivery';
import './v2-app.css';
import './v3-app.css';

const views=[['discover','热点发现'],['tracking','事件跟踪'],['intelligence','情报档案'],['materials','素材库'],['growth','增长创意']] as const;
type View=typeof views[number][0];
const time=(v?:string)=>v?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(v)):'未确认';
const kind:Record<string,string>={...materialGroups};

export default function V3Workspace(){
 const [user,setUser]=useState<any>(null),[actor,setActor]=useState('运营A'),[password,setPassword]=useState('demo');
 const [data,setData]=useState<any>(null),[view,setView]=useState<View>('discover'),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [topic,setTopic]=useState<any>(null),[domain,setDomain]=useState(''),[filtered,setFiltered]=useState<any[]>([]);
 async function refresh(){setData(await v3Api.overview());setError('');}
 async function openTopic(id:string,switchView=true){setError('');try{setTopic(await v3Api.topic(id));if(switchView)setView('discover');}catch(e){setError((e as Error).message);}}
 useEffect(()=>{document.title='TapTap 情报与素材 AI · V3';let live=true;if(isPublicResults){setUser({actor:'公开成果'});void refresh().catch(e=>live&&setError(e.message));}else if(auth.get())void v3Api.me().then(async u=>{if(live){setUser(u);await refresh();}}).catch(e=>live&&setError(e.message));return()=>{live=false;};},[]);
 useEffect(()=>{if(!user)return;const timer=window.setInterval(()=>void refresh().catch(()=>setError('成果连接暂时中断，正在自动重连；已载入内容可以继续查看。')),isPublicResults?60000:15000);return()=>window.clearInterval(timer);},[user?.actor]);
 useEffect(()=>{if(!isPublicResults||data||!error)return;const timer=window.setInterval(()=>void refresh().catch(e=>setError(e.message)),5000);return()=>window.clearInterval(timer);},[data,error]);
 useEffect(()=>{if(!data||!topic?.topic_id)return;let live=true;void v3Api.topic(topic.topic_id).then(t=>live&&setTopic(t)).catch(()=>{});return()=>{live=false;};},[data,topic?.topic_id]);
 useEffect(()=>{if(!data||!domain)return;let live=true;void v3Api.topics(domain).then(r=>live&&setFiltered(r.hotspots||[])).catch(()=>{});return()=>{live=false;};},[domain,data]);
 useEffect(()=>{if(!user)return;const id=new URLSearchParams(location.search).get('topic');if(id)void openTopic(id);},[user?.actor]);
 if(isPublicResults&&!data)return <main className="agentv2 agentv2-login"><section><p className="v2-eyebrow">TAPTAP / V3 ALPHA 17</p><h1>正在读取已整理的成果</h1><p role="status">{error||'情报、素材和增长创意正在载入…'}</p></section></main>;
 if(!user)return <main className="agentv2 agentv2-login"><section><p className="v2-eyebrow">TAPTAP / V3 ALPHA 17</p><h1>情报、素材与增长创意，打开即可查看。</h1><p>后台持续发现、研究并归类；你可以查看成果，也可以沿来源核查。</p><form onSubmit={e=>{e.preventDefault();setBusy(true);void v3Api.login(actor,password).then(async u=>{setUser(u);setPassword('');await refresh();}).catch(e=>setError(e.message)).finally(()=>setBusy(false));}}><label>账号<input autoComplete="username" value={actor} onChange={e=>setActor(e.target.value)} required/></label><label>密码<input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required/></label><button disabled={busy}>进入</button></form>{error&&<p role="alert">{error}</p>}<small>本地账号：运营A / 观察E，密码 demo。</small></section></main>;
 const hotspots=domain?filtered:(data?.hotspots||[]);
 const tracked=[...(data?.hotspots||[]),...(data?.game_coverage?.briefs||[])].filter((v,i,a)=>a.findIndex(x=>x.topic_id===v.topic_id)===i);
 const materials=data?.materials||[],creatives=data?.creatives||[];
 return <div className="agentv2 agentv3"><header className="v2-header"><a className="v2-brand" href="?version=3">TapTap <span>情报与素材 AI / V3 Alpha 17</span></a><div><span>{user.actor}</span>{!isPublicResults&&<><a href="?version=2">V2</a><a href="?version=1">V1</a><button onClick={()=>{auth.clear();setUser(null);setData(null);}}>退出</button></>}</div></header><main className="v2-shell"><div className="v2-heading"><div><p className="v2-eyebrow">自动发现 · 持续补查 · 独立判断 · 归类交付</p><h1>为 TapTap 发现下一次增长机会</h1><p>先看已经整理好的成果；需要核查时，打开事件解读、来源和版本。</p></div></div>
  {error&&<p className="v2-error" role="alert">{error}</p>}
  <div className="v2-metrics"><span>已解读热点<b>{data?.counts.hotspots??'—'}</b></span><span>游戏情报<b>{data?.counts.game_signal??'—'}</b></span><span>可用素材<b>{data?.counts.usable_materials??'—'}</b></span><span>增长创意<b>{creatives.length}</b></span></div>
  {data&&<p className="v3-auto-summary" role="status">{isPublicResults?<>公开成果 · 最近交付 {time(data.latest_delivery_at)} · 同步 {time(data.publication?.generated_at)}。{Date.now()-Date.parse(data.publication?.generated_at)>90*60000?'后台成果已超过90分钟未同步，当前展示最后一次保存的内容。':data.publication?.automation_status?.ai==='waiting_quota'?`${data.publication.automation_status.collection==='running'?'采集继续':'采集暂停'} · AI 整理等待额度恢复，已有成果可查看。`:data.publication?.automation_status?.ai==='waiting_provider'?'AI 服务等待重试，网站自动读取已交付成果。':data.publication?.automatic_enabled?'后台自动处理，网站自动读取更新。':'后台已暂停，当前展示保存的成果。'}</>:<>{data.schedule.enabled?'后台持续采集':'后台采集已暂停'} · {data.active_run?'正在处理新线索，已交付内容可继续查看':`最近交付 ${time(data.latest_delivery_at)}`}。{data.model_gate.status==='deferred'?(data.model_gate.manual_resume?'AI 整理等待额度恢复，已有成果可查看。':'AI 服务暂时等待重试，已有成果保留。'):'情报、素材和创意分别评估；缺少依据的事项会自动安排补查。'}</>}</p>}
  <nav className="v2-nav" aria-label="V3 工作台">{views.map(([id,label])=><button key={id} aria-current={view===id?'page':undefined} onClick={()=>setView(id)}>{label}</button>)}</nav>
  {view==='discover'&&<><div className="v2-section-head"><div><h2>热点发现与解读</h2><p className="v2-muted">标题说明发生了什么，打开后查看背景、时间线和讨论。</p></div><label className="v3-domain-filter">领域<select aria-label="发现领域" value={domain} onChange={e=>{setFiltered([]);setDomain(e.target.value);setTopic(null);}}><option value="">全部</option>{['综合','社会','娱乐','文化','生活方式','游戏'].map(d=><option key={d}>{d}</option>)}</select></label></div><HotspotWorkbench hotspots={hotspots} candidates={[]} topic={topic} openTopic={id=>void openTopic(id)} Source={Source}/>{(!domain||domain==='游戏')&&<GameCoverage value={data?.game_coverage} openTopic={id=>void openTopic(id)}/>}</>}
  {view==='tracking'&&<><h2>事件跟踪</h2><p className="v2-muted">已有解读的事件持续补查，新增证据持续写入；后续进展保留独立事件，再通过有来源的关联连接。</p><HotspotWorkbench hotspots={tracked} candidates={[]} topic={topic} openTopic={id=>void openTopic(id,false)} Source={Source}/></>}
  {view==='intelligence'&&<section><h2>情报档案</h2><p className="v2-muted">面向 TapTap 内容与运营：游戏变化、玩家需求、社区创作及值得留意的风险。选择分类后打开详情。</p><IntelligenceLibrary items={data?.game_signals||[]} openTopic={id=>void openTopic(id)}/></section>}
  {view==='materials'&&<section><h2>素材库</h2><p className="v2-muted">按用途整理可编辑的文案、脚本、表达与游戏改编。每项保留适用场景、使用边界和来源。</p><Library items={materials} groups={materialGroups} category={m=>m.kind} id={m=>m.material_id} title={m=>m.title} summary={m=>m.application?.delivery?.body||m.content} empty="暂时没有符合条件的素材。主 Agent 会独立判断可用表达，并持续制作游戏内容。">{m=><Material item={m}/>}</Library></section>}
  {view==='growth'&&<section><h2>增长创意</h2><p className="v2-muted">按目标归类的方案，包含人群、创意机制、传播路径、TapTap 承接动作和制作内容。效果尚未验证。</p><Library items={creatives} groups={growthGroups} category={i=>i.payload.category||'unclassified'} id={i=>i.creative_id} title={i=>i.payload.title} summary={i=>i.payload.hook} empty="暂时没有符合条件的增长创意。后台会继续寻找机会；负面、争议和证据不足的事件不会强行制作。">{i=><Creative item={i} openTopic={id=>void openTopic(id)}/>}</Library></section>}
  <footer className="v2-footer">V3 Alpha 17 · {isPublicResults?'网站每分钟检查成果更新；完整原文与研究截图在本地核查。':'后台自动处理，页面每15秒更新。'}公开来源有覆盖限制；事实与假设、来源表达与原创内容分别保留。V1 / V2 可独立查看。</footer>
 </main></div>;
}

function Source({item:s}:{item:any}){return <article className="v2-evidence"><h4>{s.title}</h4><small>{s.platform||'来源快照'} · 发布 {time(s.published_at)} · 读取范围 {s.content_scope||s.scope||'待核查'}</small><p className="v3-preserve">{s.public_projection?'公开页保留来源链接，完整原文在本地档案中核查。':s.body||s.content||'目前仅有标题，正文待补查。'}</p>{s.url&&<a href={s.url} target="_blank" rel="noreferrer">打开原始来源 ↗</a>}</article>;}

function Material({item:m}:{item:any}){
 const d=m.application?.delivery;
 return <article><small>{kind[m.kind]||m.kind} · {m.origin==='original'?'原创草案':m.origin==='source'?'来源表达':'表达分析'}</small><h3>{m.title}</h3><DeliveryActions item={m} type="material"/>{d?<><p><b>使用者与位置</b> {d.audience} · {d.placement}</p><h4>可编辑内容</h4><p className="v3-preserve">{d.body}</p><h4>替换与使用方法</h4><ol>{d.adaptation_steps.map((s:string,i:number)=><li key={i}>{s}</li>)}</ol><p><b>使用边界</b> {d.usage_boundary}</p><details><summary>来源表达 / 模式</summary><p>{m.content}</p></details></>:<p className="v3-preserve">{m.content}</p>}{m.application&&<><p><b>玩家场景</b> {m.application.game_application}</p><p><b>使用示例</b> {m.application.usage_example}</p></>}<p className="v2-muted">{m.rights_status}</p><SourceReferences ids={m.evidence_ids||[]}/></article>;
}

function Creative({item,openTopic}:{item:any;openTopic:(id:string)=>void}){
 const c=item.payload;
 return <article><h3>{c.title}</h3><DeliveryActions item={item} type="creative"/><p className="v2-muted">可编辑草案 · 增长效果尚未验证；使用前核查游戏能力、入口和素材条件。</p>{[['audience','目标玩家'],['growth_goal','用户动作目标'],['hook','创意机制'],['distribution','传播位置'],['placement','TapTap 承接'],['user_action','用户动作']].map(([key,label])=><p key={key}><b>{label}</b> {c[key]}</p>)}<p><b>用户路径</b> {c.journey?.join(' → ')}</p><h4>可编辑文案</h4><blockquote className="v3-preserve">{c.copy}</blockquote><h4>脚本与制作素材</h4>{c.deliverables?.filter((m:any)=>m.kind!=='copy').map((m:any,i:number)=><Material item={m} key={i}/>)}<h4>执行方法</h4><ol>{c.steps?.map((s:string,i:number)=><li key={i}>{s}</li>)}</ol><p><b>时机</b> {c.timing}</p><p><b>资源条件</b> {c.resources}</p><p><b>待验证假设</b> {c.growth_hypothesis}</p><p><b>上线前核查</b> {c.prerequisites?.join('；')}</p><details><summary>建议验证口径与风险</summary><p>{c.validation_plan||c.measurement}</p><p>{c.risks?.join('；')}</p></details><CreativeTrace item={item} openTopic={openTopic}/></article>;
}

function CreativeTrace({item,openTopic}:{item:any;openTopic:(id:string)=>void}){
 const [event,setEvent]=useState<any>(null),[loading,setLoading]=useState(false),[error,setError]=useState('');
 async function load(){setLoading(true);setError('');try{setEvent(await v3Api.creativeEvent(item.creative_id,item.event_id));}catch(e){setError((e as Error).message);}finally{setLoading(false);}}
 return <section className="v3-creative-trace"><button className="v2-secondary" disabled={loading} onClick={()=>void load()}>溯源：查看机会与事件依据</button>{error&&<p role="alert">{error}</p>}{event&&<><h3>机会依据：{event.title}</h3><RiskNotice value={event.risk_assessment}/><p>{event.assessment.summary}</p><p>{event.assessment.unknowns?.join('；')}</p>{event.assessment.topic_id&&<button className="v2-secondary" onClick={()=>openTopic(event.assessment.topic_id)}>查看热点解读与自动补查</button>}<details><summary>交付时的来源与历史版本</summary>{event.source_snapshots?.map((s:any)=><Source key={s.evidence_id} item={s}/>)}{event.versions?.map((v:any,i:number)=><p key={i}>{time(v.created_at)} · {v.assessment.summary}</p>)}</details></>}</section>;
}
