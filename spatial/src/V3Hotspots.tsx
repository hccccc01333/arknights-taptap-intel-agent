import {ResearchEvidence} from './V3Research';
import {SourceReferences} from './V3Delivery';

const time=(v?:string)=>v?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(v)):'时间未确认';
const heatName:Record<string,string>={board_presence:'榜单收录',board_rank_rise:'同榜排名上升',imported_ranking:'导入榜单线索'};
const polarityName:Record<string,string>={positive:'正面',neutral:'正常中性',negative:'负面',mixed:'正反争议',unknown:'风险待评估'};

export function RiskNotice({value}:{value:any}){
 if(!value)return null;
 return <div className="v3-risk" data-allowed={value.growth_allowed?'yes':'no'}><b>{polarityName[value.polarity]||'风险待评估'} · {value.growth_allowed?'可评估增长机会':'停止推广创意与传播素材'}</b><p>{value.gate_reason||value.reason}</p><small>情报可保留用于监测；判断依据可在原文中复核。</small>{value.facts?.length>0&&<details><summary>核查正负面判断依据</summary>{value.facts.map((f:any,i:number)=><blockquote key={i}>{f.quote}<small>{f.evidence_id}</small></blockquote>)}</details>}</div>;
}

export function GameCoverage({value,openTopic}:{value:any;openTopic:(id:string)=>void}){
 if(!value)return null;
 return <section aria-label="近期游戏内容" className="v3-game-feed"><h3>近期游戏与玩家动态</h3><p className="v2-muted">近七天有发布日期的游戏渠道来源 {value.published_recent_sources} 条 · 已形成游戏解读 {value.interpreted} 条。{(value.by_platform||[]).map((p:any)=>`${({bilibili:'B站',taptap:'TapTap',gamemedia:'游戏媒体'} as any)[p.platform]||p.platform} ${p.count}`).join(' / ')}</p>
  <div className="v3-materials">{(value.briefs||[]).map((b:any)=><button className="v3-topic" key={b.topic_id} onClick={()=>openTopic(b.topic_id)}><small>{b.kind==='hotspot'?'游戏热点':'游戏情报线索 · 热度待证'} · {polarityName[b.risk_assessment?.polarity]||'风险待评估'}</small><h4>{b.payload.headline}</h4><p>{b.payload.one_line}</p></button>)}</div>
  {!value.briefs?.length&&<p className="v2-muted">游戏渠道正在采集，尚无充分解读。系统按证据与价值筛选后研究。</p>}
  <small>{value.note}</small>
 </section>;
}

export function HotspotWorkbench({hotspots,candidates,topic,openTopic,Source}:{hotspots:any[];candidates:any[];topic:any;openTopic:(id:string)=>void;Source:any}){
 const interpretation=topic?.interpretation?.payload;
 return <div className={'v3-two-columns v3-discovery'+(topic?' v3-selected':'')}>
  <section aria-label="已解读热点">
   {!hotspots.length&&<p className="v2-empty">暂无具有解读和热度依据的热点。待研究内容保留在候选中，系统自动筛选、补查并更新。</p>}
   {hotspots.map(h=><button className="v3-topic" key={h.topic_id} onClick={()=>openTopic(h.topic_id)}>
    <small>解读 {time(h.created_at)}{h.current_heat_evidence?.some((e:any)=>e.observed_at||e.to_at)&&` · 榜单观测 ${time(h.current_heat_evidence.map((e:any)=>e.observed_at||e.to_at||'').sort().at(-1))}`}</small><h3>{h.payload.headline}</h3><p>{h.payload.one_line}</p>
    <small>{(h.current_heat_evidence||h.payload.heat_evidence).map((e:any)=>`${heatName[e.kind]||e.kind}${e.channel_id?` · ${e.channel_id}`:''}`).slice(0,3).join(' / ')}</small>
   </button>)}
   {candidates.length>0&&<details className="v3-candidates"><summary>候选与待补线索 · {candidates.length} 条展示记录</summary>
    <p className="v2-muted">这里是采集线索，尚未全部形成热点。你可以核查；后台处理无需逐条点击。</p>
    {candidates.map(t=><button className="v3-topic" key={t.topic_id} onClick={()=>openTopic(t.topic_id)}><small>采集线索 · {time(t.last_seen_at)}</small><h3>{t.title}</h3></button>)}
   </details>}
  </section>
  <aside>{topic?<article className="v2-event-detail">
   {interpretation?<><small>{interpretation.status==='ready'?'事件解读':'解读待补证据'}{!interpretation.heat_evidence.length?' · 热度依据不足':''}</small>
    <h2>{interpretation.headline}</h2><p>{interpretation.one_line}</p>
    {(['background','core'] as const).map(k=>interpretation[k]?.length>0&&<section key={k}><h3>{k==='background'?'背景':'核心经过'}</h3>{interpretation[k].map((c:any,i:number)=><p key={i}>{c.text}</p>)}</section>)}
    <h3>时间线</h3>{interpretation.timeline.length?<ol>{interpretation.timeline.map((n:any,i:number)=><li key={i}><small>{n.time_text||'具体时间未确认'} · {n.time_kind==='event'?'事件时间':n.time_kind==='report'?'报道时间':'时间性质未确认'}</small><p>{n.text}</p></li>)}</ol>:<p className="v2-muted">来源尚不足以整理时间线。</p>}
    {interpretation.views.length>0&&<section><h3>讨论焦点</h3><p className="v2-muted">基于有限样本归纳，不代表所有用户。</p>{interpretation.views.map((v:any,i:number)=><article key={i}><p>{v.text}</p><small>{v.kind==='actual_comment'?`实际评论${v.sample_count?` · ${v.sample_count} 条依据`:''}`:'报道转述观点'}</small></article>)}</section>}
    {interpretation.controversies.length>0&&<section><h3>质疑与争议</h3>{interpretation.controversies.map((v:any,i:number)=><p key={i}>{v.text}</p>)}</section>}
    {interpretation.unknowns.length>0&&<section><h3>尚未确认</h3><ul>{interpretation.unknowns.map((s:string,i:number)=><li key={i}>{s}</li>)}</ul></section>}
    {topic.followups?.length>0&&<section><h3>自动补查</h3>{topic.followups.filter((f:any)=>f.status!=='superseded').map((f:any)=><details key={f.followup_id}><summary>{f.question} · {({pending:'已安排',running:'正在补查',deferred:'等待重试',resolved:'已查证',awaiting_source:'等待新证据'} as any)[f.status]||f.status}</summary><p>已尝试 {f.attempts} 次{f.status==='deferred'&&` · 再查 ${time(f.retry_at)}`}</p>{f.payload?.resolution&&<p>{f.payload?.resolution.reason}</p>}{f.payload?.history?.map((h:any,i:number)=><p key={i}>{time(h.at)} · {h.answer?.reason}</p>)}</details>)}</section>}
    {topic.tracked_events?.length>0&&<details><summary>系统跟踪进展</summary>{topic.tracked_events.map((e:any)=><section key={e.tracked_id}><h4>当前来源的观测记录</h4>{e.timeline?.slice(0,8).map((n:any,i:number)=><p key={i}>{time(n.observed_at)} · {n.payload?.note||({source_added:'纳入事件跟踪',content_version:'保存正文版本',channel_observed:'观察到渠道收录',board_rank_rise:'榜单排名上升'} as any)[n.kind]||'新增事件观测'}</p>)}</section>)}</details>}
    <details><summary>核查解读的引用</summary>{['background','core','timeline','views','controversies'].flatMap(k=>interpretation[k]||[]).map((c:any,i:number)=><section key={i}><p>{c.text}</p>{c.facts.map((f:any,j:number)=><blockquote key={j}>{f.quote}<small>{f.evidence_id}</small></blockquote>)}</section>)}</details>
   </>:<><h2>待研究线索</h2><p>这条采集记录尚未形成可用解读，不能据此确定事件背景或玩家需求。</p></>}
   <HotspotOutputs outputs={topic.business_outputs}/>
   <details><summary>核查原文与研究过程</summary>{topic.evidence.map((e:any)=><Source key={e.evidence_id} item={e}/>)}{topic.public_projection?<p className="v2-muted">公开页提供整理后的解读、短引用和来源链接。完整评论、截图及工具执行记录在本地档案中核查。</p>:<ResearchEvidence topic={topic} Source={Source}/>}</details>
  </article>:<p className="v2-empty">打开热点，查看背景、核心、时间线和讨论；来源保留在核查入口。</p>}</aside>
 </div>;
}

function HotspotOutputs({outputs}:{outputs:any}){
 if(!outputs?.judged_at)return <section aria-label="热点产出"><h3>热点产出</h3><RiskNotice value={outputs?.risk_assessment}/><p className="v2-muted">主 Agent 尚未完成这条热点的业务判断。系统会分别评估游戏情报、素材和增长机会。</p></section>;
 const signals=outputs.game_signals||[],materials=outputs.materials||[],creatives=outputs.creatives||[],opportunity=outputs.opportunity;
 const pending=outputs.jobs?.find((j:any)=>j.stage==='creative'&&['pending','running','deferred'].includes(j.status));
 return <section aria-label="热点产出"><h3>这个热点能用于什么</h3><small>判断更新 {time(outputs.judged_at)} · 三项分别评估</small>
  <RiskNotice value={outputs.risk_assessment}/>
  {outputs.held_creatives?.length>0&&<p className="v2-muted">已停用 {outputs.held_creatives.length} 条旧创意，版本保留在历史记录中。</p>}
  <article><h4>游戏情报 · {signals.length} 条</h4><p>{outputs.assessments?.intelligence||(!signals.length?'尚未保存有依据的游戏变化线索。':'已保存带来源的游戏变化线索。')}</p>{signals.map((s:any)=><details key={s.signal_id}><summary>{s.payload.title}</summary><p>{s.payload.observed_change}</p><p>{s.payload.why_it_matters}</p><small>影响判断仍需验证</small></details>)}</article>
  <article><h4>可用素材 · {materials.length} 项</h4><p>{outputs.assessments?.materials||(!materials.length?'尚未保存可用于游戏内容创作的表达。':'已有具体游戏用法，商业使用条件仍需确认。')}</p>{materials.map((m:any)=><details key={m.material_id}><summary>{m.title} · {({source_quote:'来源原话',expression_pattern:'表达模式',game_adaptation:'原创改编'} as any)[m.kind]||m.kind}</summary><p className="v3-preserve">{m.content}</p><p><b>适用场景</b> {m.application.game_application}</p><p><b>使用示例</b> {m.application.usage_example}</p><small>{m.rights_status}</small></details>)}</article>
  <article><h4>增长创意 · {creatives.length} 个草案</h4><p>{outputs.risk_assessment?.growth_allowed?opportunity?.reason:outputs.risk_assessment?.gate_reason}</p>{!creatives.length&&<p className="v2-muted">{outputs.risk_assessment?.growth_allowed&&opportunity?.decision==='opportunity'?(pending?.status==='deferred'?'创意制作待重试，已保存机会判断。':'已识别机会，创意草案待制作。'):'暂未形成值得测试的 TapTap 增长方案。'}</p>}{creatives.map((c:any)=><details key={c.creative_id}><summary>{c.payload.title} · 待审核、待测试</summary><p><b>目标人群</b> {c.payload.audience}</p><p><b>创意切口</b> {c.payload.hook}</p><p><b>传播</b> {c.payload.distribution}</p><p><b>TapTap 承接</b> {c.payload.placement}</p><p><b>用户动作</b> {c.payload.user_action}</p><p className="v3-preserve">{c.payload.copy}</p><p><b>验证</b> {c.payload.validation_plan||c.payload.measurement}</p><small>完整脚本与执行方法见增长创意页</small></details>)}</article>
 </section>;
}

export function GameSignal({item,openTopic}:{item:any;openTopic:(id:string)=>void}){
 const p=item.payload;
 return <article className="v3-material"><small>游戏情报 · {item.fingerprint===item.current_fingerprint?'当前来源版本':'历史来源版本'}</small>
  <RiskNotice value={item.risk_assessment}/>
  <h4>{p.title}</h4><p><b>涉及场景</b> {p.game_context}</p><p><b>观察到的变化</b> {p.observed_change}</p>
  <p><b>为何关注</b> {p.why_it_matters}</p>{p.hypothesis&&<p><b>影响假设 · 待验证</b> {p.hypothesis}</p>}
  <p><b>继续观察</b> {p.next_watch}</p><SourceReferences ids={(p.facts||[]).map((f:any)=>f.evidence_id)}>{p.facts?.map((f:any,i:number)=><blockquote key={i}>{f.quote}</blockquote>)}</SourceReferences><button className="v2-secondary" onClick={()=>openTopic(item.topic_id)}>查看事件解读与原文</button>
 </article>;
}
