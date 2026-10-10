import {useEffect,useRef,useState} from 'react';
import {v3Api} from './lib/api';

const labels:Record<string,string>={default:'模型默认',none:'关闭思考',enabled:'开启思考（按 token 预算）',minimal:'最少',low:'低',medium:'中',high:'高',xhigh:'极高',max:'最大'};
const modes:Record<string,string>={omit:'不发送推理参数',reasoning_effort:'reasoning_effort（OpenAI / Gemini）',reasoning_object:'reasoning 对象（OpenRouter）',deepseek:'thinking + reasoning_effort（DeepSeek）',thinking:'thinking 开关（Kimi / GLM / 豆包）',enable_thinking:'enable_thinking + thinking_budget（千问 / 硅基流动）',anthropic_effort:'output_config.effort（Claude）'};
const clean=(p:any,template:any)=>Object.fromEntries(Object.keys(template).map(k=>[k,p[k]??template[k]]));

export function ProviderSettings({refresh}:{refresh:()=>Promise<void>}){
 const [catalog,setCatalog]=useState<any>(null),[form,setForm]=useState<any>(null),[origin,setOrigin]=useState('new:openai');
 const [model,setModel]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 useEffect(()=>{setModel(catalog?.model?.model||'');},[catalog?.model?.model]);
 useEffect(()=>{let live=true;async function load(first=false){try{const c=await v3Api.models();if(!c.model?.model||!Array.isArray(c.model_options)||!Array.isArray(c.presets))throw new Error('模型配置接口暂不可用，请检查后台版本后重试。');if(live){setCatalog(c);if(first)setForm({...c.presets[0]});}}catch(e){if(live)setError((e as Error).message);}}void load(true);const timer=window.setInterval(()=>void load(),15000);return()=>{live=false;window.clearInterval(timer);};},[]);
 const locked=busy||!!catalog?.active_run;
 function choose(value:string){
  setOrigin(value);setMessage('');setError('');
  const [kind,id]=value.split(':');
  if(kind==='saved')setForm(clean(catalog.profiles.find((p:any)=>p.id===id),catalog.presets[0]));
  else if(id==='custom')setForm({...catalog.presets[0],id:'custom',label:'自定义接口',base_url:'',model_id:'',api_key_env:'CUSTOM_MODEL_API_KEY',reasoning_mode:'omit',reasoning_effort:'default',token_field:'max_tokens',output_mode:'prompt'});
  else setForm({...catalog.presets.find((p:any)=>p.id===id)});
 }
 function change(key:string,value:any){setForm((p:any)=>({...p,[key]:value}));setMessage('');}
 async function save(){
  setBusy(true);setError('');setMessage('');try{
   const result=await v3Api.configure('provider',form);setCatalog(await v3Api.models());setOrigin('saved:'+result.id);setForm(clean(result,catalog.presets[0]));void refresh().catch(()=>{});
   setMessage(catalog.model.model===result.model?'已更新当前模型配置，后续自动运行使用新参数。':'已保存配置；选择“切换使用”后才用于自动运行。');
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 async function activate(){setBusy(true);setError('');setMessage('');try{await v3Api.configure('model',{model});setCatalog(await v3Api.models());void refresh().catch(()=>{});setMessage('已切换，主 Agent 和研究子 Agent 共用此配置；已有额度暂停继续生效。');}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 if(!catalog)return <div className="v3-provider-settings">{error?<p role="alert">{error}</p>:<p role="status">正在读取供应商配置…</p>}</div>;
 const data=catalog,options=catalog.model_options||[];
 return <div className="v3-provider-settings">
  <p>当前使用：<b>{data.model.label||data.model.model}</b>。{data.model_gate?.status==='deferred'?`AI 等待：${data.model_gate.reason}`:'后台按既有计划自动处理。'}</p>
  {data.active_run&&<p role="status">自动运行正在处理，结束后可保存或切换配置。</p>}
  {error&&<p className="v3-provider-error" role="alert">{error}</p>}{message&&<p className="v3-provider-success" role="status">{message}</p>}
  <form onSubmit={e=>{e.preventDefault();void activate();}} className="v3-provider-activation">
   <label>自动运行的模型<select aria-label="自动运行的模型" value={model} disabled={locked} onChange={e=>setModel(e.target.value)}>
    {options.map((p:any)=><option key={p.model} value={p.model} disabled={!p.configured}>{p.label||p.model}{p.configured?'':' · 凭证待配置'}</option>)}
    {!options.some((p:any)=>p.model===data.model.model)&&<option value={data.model.model}>{data.model.model}</option>}
   </select></label><button disabled={locked||model===data.model.model}>切换使用</button>
  </form>
  <p className="v2-muted">切换后后台会自动调用所选服务，费用与可用额度由供应商决定。保存未启用的配置不会发起请求。</p>
  {!form?<p role="status">{error?'配置暂未载入，请关闭后重试。':'正在读取供应商配置…'}</p>:<form onSubmit={e=>{e.preventDefault();void save();}}>
   <label>配置来源<select aria-label="配置来源" value={origin} disabled={locked} onChange={e=>choose(e.target.value)}>
    <optgroup label="主流供应商预设">{catalog.presets.map((p:any)=><option key={p.id} value={'new:'+p.id}>{p.label}</option>)}<option value="new:custom">自定义接口</option></optgroup>
    {!!catalog.profiles.length&&<optgroup label="已保存配置">{catalog.profiles.map((p:any)=><option key={p.id} value={'saved:'+p.id}>{p.label}</option>)}</optgroup>}
   </select></label>
   <div className="v3-provider-grid">
    <label>配置编号<input aria-label="配置编号" required pattern="[a-z][a-z0-9_-]{0,39}" maxLength={40} disabled={locked} readOnly={origin.startsWith('saved:')} value={form.id} onChange={e=>change('id',e.target.value)}/></label>
    <label>显示名称<input aria-label="显示名称" required maxLength={80} disabled={locked} value={form.label} onChange={e=>change('label',e.target.value)}/></label>
    <label>接口协议<select aria-label="接口协议" disabled={locked} value={form.protocol} onChange={e=>setForm({...form,protocol:e.target.value,reasoning_mode:'omit',reasoning_effort:'default',output_mode:'prompt',budget_scope:'combined'})}><option value="openai_chat">OpenAI Chat Completions 兼容</option><option value="openai_responses">OpenAI Responses 兼容</option><option value="anthropic_messages">Anthropic Messages 兼容</option></select></label>
    <label>模型 ID<input aria-label="模型 ID" required maxLength={180} disabled={locked} placeholder={form.id==='doubao'?'控制台模型 ID 或 ep-接入点编号':'供应商控制台中的实际模型名称'} value={form.model_id} onChange={e=>change('model_id',e.target.value)}/></label>
   </div>
   <label>API 基础地址<input aria-label="API 基础地址" required type="url" maxLength={400} disabled={locked} placeholder="https://example.com/v1" value={form.base_url} onChange={e=>change('base_url',e.target.value)}/></label>
   <label>API Key 环境变量<input aria-label="API Key 环境变量" required={form.auth_required} maxLength={100} disabled={locked} placeholder="OPENAI_API_KEY" value={form.api_key_env} onChange={e=>change('api_key_env',e.target.value)}/></label>
   <p className="v2-muted">在运行后台的系统或用户环境变量中设置对应密钥，此处只填写变量名。模型名、地区地址及能力以你账号的供应商控制台为准；同一供应商可保存多份配置。</p>
   <div className="v3-provider-grid">
    <label>推理参数方式<select aria-label="推理参数方式" disabled={locked} value={form.reasoning_mode} onChange={e=>setForm({...form,reasoning_mode:e.target.value,reasoning_effort:'default',budget_scope:'combined'})}>{Object.entries(modes).filter(([k])=>form.protocol==='anthropic_messages'?['omit','anthropic_effort'].includes(k):form.protocol==='openai_responses'?['omit','reasoning_effort'].includes(k):k!=='anthropic_effort').map(([k,label])=><option key={k} value={k}>{label}</option>)}</select></label>
    <label>推理强度<select aria-label="推理强度" disabled={locked} value={form.reasoning_effort} onChange={e=>change('reasoning_effort',e.target.value)}>{catalog.reasoning_modes[form.reasoning_mode].map((k:string)=><option key={k} value={k}>{labels[k]}</option>)}</select></label>
    <label>输出方式<select aria-label="输出方式" disabled={locked} value={form.output_mode} onChange={e=>change('output_mode',e.target.value)}><option value="prompt">提示词约束 JSON</option>{form.protocol!=='anthropic_messages'&&<option value="json_object">JSON Object 模式</option>}<option value="json_schema">供应商 JSON Schema 模式</option></select></label>
    <label>单次输出上限（token）<input aria-label="单次输出上限" type="number" required min={256} max={32768} step={1} disabled={locked} value={form.output_limit} onChange={e=>change('output_limit',Number(e.target.value))}/></label>
   </div>
   <p className="v2-muted">不同模型支持的强度与 JSON Schema 子集不同，预设可以调整。所有输出都会再次经过本地业务与证据校验，推理过程不作为交付。</p>
   <details><summary>兼容参数与本地服务</summary><div className="v3-provider-grid">
    {form.protocol==='openai_chat'&&<label>输出 token 字段<select aria-label="输出 token 字段" disabled={locked} value={form.token_field} onChange={e=>change('token_field',e.target.value)}><option value="max_tokens">max_tokens</option><option value="max_completion_tokens">max_completion_tokens</option></select></label>}
    {form.reasoning_mode==='enable_thinking'&&<><label>思考 token 上限<input aria-label="思考 token 上限" type="number" min={128} max={32768} step={1} disabled={locked} value={form.thinking_budget} onChange={e=>change('thinking_budget',Number(e.target.value))}/></label><label>供应商如何计算输出预算<select aria-label="输出预算计算方式" disabled={locked} value={form.budget_scope} onChange={e=>change('budget_scope',e.target.value)}><option value="combined">输出上限包含思考</option><option value="separate">输出与思考分开，程序从总上限分配</option></select></label></>}
   </div><label className="v3-provider-check"><input type="checkbox" disabled={locked} checked={form.auth_required} onChange={e=>change('auth_required',e.target.checked)}/>需要 API Key</label><label className="v3-provider-check"><input type="checkbox" disabled={locked} checked={form.allow_http} onChange={e=>change('allow_http',e.target.checked)}/>允许 HTTP（用于本地或自有服务）</label>{form.protocol==='openai_chat'&&<label className="v3-provider-check"><input type="checkbox" disabled={locked} checked={form.reasoning_split} onChange={e=>change('reasoning_split',e.target.checked)}/>发送 reasoning_split（MiniMax 分离推理）</label>}</details>
   <button disabled={locked}>保存配置</button>
  </form>}
 </div>;
}

export default function ProviderDialog({refresh,close,returnFocus}:{refresh:()=>Promise<void>;close:()=>void;returnFocus:HTMLElement|null}){
 const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{const node=dialog.current;if(node&&!node.open)node.showModal();const previous=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{node?.close();document.body.style.overflow=previous;if(returnFocus?.isConnected)returnFocus.focus();};},[]);
 return <dialog ref={dialog} className="v3-reading-dialog" aria-label="模型设置" onCancel={e=>{e.preventDefault();close();}} onClick={e=>{if(e.target===e.currentTarget)close();}}><div className="v3-reading-sheet"><header><b>模型供应商与自定义接口</b><button className="v2-secondary" autoFocus onClick={close}>关闭模型设置</button></header><div className="v3-reading-body"><ProviderSettings refresh={refresh}/></div></div></dialog>;
}
