import { useEffect, useState } from 'react';
import { v3Api } from './lib/api';

type Props = {data:any;enabled:boolean;busy:boolean;act:(fn:()=>Promise<unknown>)=>Promise<void>;refresh:()=>Promise<void>};
const labels:Record<string,string>={none:'关闭思考',low:'低',medium:'中',high:'高',xhigh:'极高',max:'最大',default:'模型默认'};

export default function ModelSettings({data,enabled,busy,act,refresh}:Props){
 const [model,setModel]=useState(data.model.model);
 const [effort,setEffort]=useState(data.reasoning?.effort||'low');
 const [output,setOutput]=useState(data.output?.max_tokens||16384);
 useEffect(()=>{setModel(data.model.model);setEffort(data.reasoning?.effort||'low');setOutput(data.output?.max_tokens||16384);},[data.model.model,data.reasoning?.effort,data.output?.max_tokens]);
 const deepseek=model==='deepseek-flash'||model==='deepseek-v4-flash';
 const bunny=model==='spacebunny/space-bunny-alpha';
 const canReason=deepseek||bunny||model.startsWith('opencode/ling-');
 const efforts=deepseek?['none','low','high','max','default']:bunny?['low','medium','high','xhigh','max','default']:['low','medium','high','default'];
 const locked=!enabled||busy||!!data.active_run;
 return <>
  <form className="v2-settings" onSubmit={e=>{e.preventDefault();void act(async()=>{await v3Api.configure('model',{model});await refresh();});}}>
   <label>AI 服务<select aria-label="AI 服务" disabled={locked} value={model} onChange={e=>{setModel(e.target.value);setEffort('low');}}>
    {data.model_options.map((m:any)=><option key={m.model} disabled={!m.configured} value={m.model}>{m.label||m.model}{m.configured?(m.transport==='opencode-agent'?' · CLI 已安装':' · 已配置凭证'):' · 未配置'}</option>)}
    {!data.model_options.some((m:any)=>m.model===data.model.model)&&<option value={data.model.model}>{data.model.model} · 已保存的旧配置</option>}
   </select></label>
   <p className="v2-muted">DeepSeek 使用官方 API，按 token 计费，当前 Flash 的实际版本记录在任务结果中。Zen 使用官方 OpenCode Agent，额度暂停仅适用于 Zen。太空兔使用独立官网 API。服务失败保留任务和实际费用状态。</p>
   <button disabled={locked}>保存模型</button>
  </form>
  {canReason&&<form className="v2-settings" onSubmit={e=>{e.preventDefault();void act(async()=>{await v3Api.configure('reasoning',{effort});await refresh();});}}>
   <label>推理强度<select aria-label="推理强度" disabled={locked} value={effort} onChange={e=>setEffort(e.target.value)}>{efforts.map(v=><option key={v} value={v}>{labels[v]}</option>)}</select></label>
   <p className="v2-muted">{deepseek?'默认使用低强度；高和最大可能消耗更多时间与输出 token。最终成果只使用最终回答，运行记录保留推理用量。':'默认使用低强度；更高强度仍受任务时间和输出预算限制。'}请先保存所选模型，再调整推理强度。</p>
   <button disabled={locked||model!==data.model.model}>保存推理强度</button>
  </form>}
  {deepseek&&<form className="v2-settings" onSubmit={e=>{e.preventDefault();void act(async()=>{await v3Api.configure('output',{max_tokens:output});await refresh();});}}>
   <label>每次调用的输出总上限（token）<input aria-label="输出总上限" type="number" min={256} max={32768} step={1} value={output} disabled={locked} onChange={e=>setOutput(Number(e.target.value))}/></label>
   <p className="v2-muted">包含思考与最终回答。研究计划、情报、创意制作按任务分配预算，并受此上限约束。截断、空回答或不符合交付格式时保留待修正状态，不计为完成；最多按现有任务流程修正一次。</p>
   <button disabled={locked||model!==data.model.model}>保存输出预算</button>
  </form>}
 </>;
}
