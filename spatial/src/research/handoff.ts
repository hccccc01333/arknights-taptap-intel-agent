import { emptyBrief, type BriefVersion, type Session } from './domain';
import { seedOperation, type EmployeeRole, type HandoffPackage, type Operation } from './operations';
import type { Opportunity } from './opportunities';

export type HandoffSelection={session:Session;op?:Opportunity;version?:BriefVersion};
export const handoffKey=(s:HandoffSelection)=>s.session.id+'|'+(s.version?'version:'+s.version.id:s.op?s.op.topicId+'|'+s.op.variant:'brief');
export function makeHandoff({session:s,op,version}:HandoffSelection):HandoffPackage{
 const drafts=version?(version.creativeDrafts||[]):op?(s.creativeDrafts||[]).filter(d=>d.opportunityKey===op.topicId+'|'+op.variant):(s.creativeDrafts||[]);
 const brief=version?.brief||op?.brief||s.brief,claims=version?.claims||op?.claims||s.claims;
 const ids=[...new Set([...(version?.evidenceIds||op?.evidence.map(e=>e.id)||s.selectedEvidenceIds),...claims.filter(c=>brief.claimIds.includes(c.id)).flatMap(c=>c.evidenceIds),...drafts.flatMap(d=>d.sourceIds)])];
 const records=version?.records||[...s.records,...s.archives];
 return structuredClone({sourceSessionId:s.id,sourceVersion:version?.number||null,at:new Date().toISOString(),request:s.request,brief,claims:claims.filter(c=>brief.claimIds.includes(c.id)),records:op?records.filter(r=>r.id===op.record.id||r.materials.some(e=>ids.includes(e.id))):records,evidenceIds:ids,creativeDrafts:drafts,experiment:version?version.experiment:s.experiment});
}
export function makeOperation(selection:HandoffSelection,role:EmployeeRole):Operation{
 const pack=makeHandoff(selection),s=selection.session,record=selection.op?.record||pack.records.find(r=>r.topic.id===s.activeTopicId)||pack.records[0],at=new Date().toISOString();
 const base:Operation=selection.op?seedOperation(selection.op,role):{id:crypto.randomUUID(),opportunityKey:handoffKey(selection),topicId:record?.topic.id||'manual:'+s.id,title:pack.brief.title||s.request.question,game:record?.topic.gameName||'未指定游戏',origin:record?.origin||(s.scope==='demo'?'demo':'unknown'),role,gameStage:'unknown',status:'verify',priority:'normal',owner:'',dueDate:'',objective:pack.brief.objective,channel:pack.brief.channel,metric:pack.brief.measurement,notes:'',outcome:'',decision:'pending',tasks:[],sourceIds:pack.evidenceIds,history:[{id:crypto.randomUUID(),at,text:'由研究资料建立行动；状态为手动记录，尚未确认执行。'}],createdAt:at,updatedAt:at};
 const steps=pack.brief.nextAction.split('\n').map(s=>s.trim().replace(/^\d+[.、]\s*/,'' )).filter(Boolean);
 return {...base,opportunityKey:handoffKey(selection),proposalKey:selection.op?selection.op.topicId+'|'+selection.op.variant:undefined,title:pack.brief.title||base.title,objective:pack.brief.objective||base.objective,channel:pack.brief.channel||base.channel,metric:pack.brief.measurement||base.metric,sourceIds:pack.evidenceIds,tasks:steps.length?steps.slice(0,40).map(text=>({id:crypto.randomUUID(),text,done:false})):base.tasks,handoff:pack,experiment:pack.experiment?structuredClone(pack.experiment):undefined,archived:false};
}
export function handoffSession(source:Session,p:HandoffPackage):Session{return {...source,request:p.request,brief:p.brief||emptyBrief(),claims:p.claims,records:p.records,archives:[],selectedEvidenceIds:p.evidenceIds,creativeDrafts:p.creativeDrafts,experiment:p.experiment,operations:[],versions:[],view:'deliver',status:'ready',activeTopicId:p.records[0]?.topic.id||'',updatedAt:p.at};}
