import { useMemo, useState } from 'react';
import { Modal } from '../ui/Modal';
import { Icon } from '../ui/Icon';
import { employeeRoles, putOperation, type Operation } from './operations';
import { useEmployeePreferences } from './employeePreferences';
import { handoffKey, makeOperation, type HandoffSelection } from './handoff';
import { sessionRepository, useSessions } from './repository';

export function HandoffDialog({selection,onClose,onAdded}:{selection:HandoffSelection;onClose:()=>void;onAdded:(id:string)=>void}){
 const state=useSessions(),preferences=useEmployeePreferences();const seed=useMemo(()=>makeOperation(selection,preferences.value.role),[selection]);const existing=state.sessions.flatMap(s=>s.operations||[]).find(o=>o.opportunityKey===handoffKey(selection));
 const [draft,setDraft]=useState(seed),[copy,setCopy]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const patch=(p:Partial<Operation>)=>setDraft(d=>({...d,...p}));
 const refs=new Set(draft.handoff?.records.flatMap(r=>r.materials.map(e=>e.id)));const evidenceCount=draft.sourceIds.filter(id=>refs.has(id)).length;
 async function save(){if(busy)return;if(existing&&!copy){onAdded(existing.id);return;}if(!draft.title.trim())return;const source=sessionRepository.get(state.scope,selection.session.id)||selection.session;if((source.operations?.length||0)>=60){setError('这份研究已有 60 条行动，请先备份或建立独立研究。');return;}setBusy(true);setError('');
  const op={...draft,title:draft.title.trim(),opportunityKey:copy?draft.opportunityKey+':copy:'+draft.id:draft.opportunityKey};
  if(sessionRepository.get(state.scope,source.id))sessionRepository.update(state.scope,source.id,s=>putOperation(s,op));else sessionRepository.add(putOperation({...source,scope:state.scope},op));
  try{await sessionRepository.flush(state.scope);onAdded(op.id);}catch{setError('资料尚未写入浏览器。当前内容仍在页面中，请打开工作台备份。');}finally{setBusy(false);}
 }
 return <Modal title="把研究带入员工工作台" onClose={onClose} className="v7-handoff-modal"><form className="v7-handoff-form" onSubmit={e=>{e.preventDefault();void save();}}><p>保存加入时的方案、证据和创作快照，后续研究编辑不会覆盖交接资料。</p><div className="v7-handoff-counts"><span><b>{evidenceCount}</b>条引用来源</span><span><b>{draft.handoff?.creativeDrafts.length||0}</b>份已保存创作</span><span><b>{draft.tasks.length}</b>项初始任务</span></div>{existing&&<div className="v7-inline-note">这个方向已有行动，默认打开原记录。<label><input type="checkbox" checked={copy} onChange={e=>setCopy(e.target.checked)}/>另建一条独立行动</label></div>}<fieldset disabled={!!existing&&!copy}><label>行动名称<input aria-label="交接行动名称" value={draft.title} required maxLength={500} onChange={e=>patch({title:e.target.value})}/></label><div className="v7-form-pair"><label>运营视角<select aria-label="交接运营视角" value={draft.role} onChange={e=>patch({role:e.target.value as Operation['role']})}>{Object.entries(employeeRoles).map(([k,v])=><option value={k} key={k}>{v}</option>)}</select></label><label>负责人<input aria-label="交接负责人" value={draft.owner} maxLength={120} placeholder="手动记录姓名或职责" onChange={e=>patch({owner:e.target.value})}/></label><label>计划截止日<input aria-label="交接截止日期" type="date" value={draft.dueDate} onChange={e=>patch({dueDate:e.target.value})}/></label><label>优先级<select aria-label="交接优先级" value={draft.priority} onChange={e=>patch({priority:e.target.value as Operation['priority']})}><option value="normal">常规</option><option value="high">优先处理</option><option value="low">低优先级</option></select></label></div></fieldset><p className="v7-boundary">当前按浏览器连接保存。负责人是文字记录，尚未向团队同步、提交审批或发布内容。</p>{error&&<p className="v7-error" role="alert">{error}</p>}<div className="v7-modal-actions"><button className="v4-btn" disabled={busy||!draft.title.trim()||!state.loaded}>{busy?'正在保存交接':existing&&!copy?'打开已有行动':'保存并打开工作台'}<Icon name="arrow" size={16}/></button>{error&&<button type="button" className="v4-btn subtle" onClick={()=>onAdded(draft.id)}>打开工作台备份</button>}</div></form></Modal>;
}
