import type { Planet, Universe } from '../lib/api';
import { environment, intelligenceData } from './data';
import { matchRequest, missingSnapshot, newSession, snapshot, type ResearchRequest, type Session } from './domain';
import { sessionRepository } from './repository';
type ReadingJob={controller:AbortController;finished:Promise<void>};
const running=new Map<string,ReadingJob>();
export async function startResearch(scope:string,request:ResearchRequest,universe:Universe,options?:{topics?:Planet[];guided?:boolean}){await sessionRepository.ready(scope);if(environment()!==scope)throw Error('连接已切换，请在当前连接重新创建项目。');const next=newSession(scope,request);next.guidance={enabled:options?.guided!==false,step:'direction'};const session=sessionRepository.add(next);void readResearch(scope,session.id,universe,options?.topics||matchRequest(universe,request));return session.id;}
export function pauseResearch(scope:string,id:string){running.get(scope+'|'+id)?.controller.abort();sessionRepository.update(scope,id,s=>({...s,status:'paused',error:'读取已暂停，完成的证据快照已保留。'}));}
export async function readResearch(scope:string,id:string,universe:Universe,topics?:Planet[],force=false):Promise<void>{
 if(environment()!==scope)return;
 const key=scope+'|'+id;
 const active=running.get(key);
 if(active){if(!active.controller.signal.aborted)return;await active.finished;return readResearch(scope,id,universe,topics,force);}
 const current=sessionRepository.get(scope,id);if(!current)return;
 const candidates=(topics||matchRequest(universe,current.request)).filter(p=>force||!current.records.some(r=>r.topic.id===p.id&&!r.error));
 const controller=new AbortController();let finish!:()=>void;
 const job={controller,finished:new Promise<void>(resolve=>{finish=resolve;})};running.set(key,job);
 sessionRepository.update(scope,id,s=>({...s,status:'reading',error:undefined,progress:{completed:0,total:candidates.length}}));
 let cursor=0;const worker=async()=>{while(cursor<candidates.length&&!controller.signal.aborted){if(environment()!==scope){pauseResearch(scope,id);return;}const topic=candidates[cursor++];let record;try{const data=await intelligenceData.topic(topic.id,{signal:controller.signal,force});record=snapshot(topic,data,universe.ideas[topic.id]||[],intelligenceData.readAt(topic.id));}catch(e){if(controller.signal.aborted)return;record=missingSnapshot(topic,universe.ideas[topic.id]||[],e instanceof Error?e.message:'情报读取失败');}
  if(controller.signal.aborted)return;if(environment()!==scope){pauseResearch(scope,id);return;}sessionRepository.update(scope,id,s=>({...s,archives:[...s.archives,...s.records.filter(r=>r.topic.id===topic.id)],records:[...s.records.filter(r=>r.topic.id!==topic.id),record],activeTopicId:s.activeTopicId||topic.id,progress:{...s.progress,completed:s.progress.completed+1}}));}};
 try{await Promise.all(Array.from({length:Math.min(3,candidates.length)},worker));if(!controller.signal.aborted)sessionRepository.update(scope,id,s=>({...s,status:'ready'}));}finally{if(running.get(key)===job)running.delete(key);finish();}
}
function pauseOtherConnections(){for(const [key,job] of running){const split=key.lastIndexOf('|'),scope=key.slice(0,split),id=key.slice(split+1);if(scope!==environment()){job.controller.abort();sessionRepository.update(scope,id,s=>({...s,status:'paused',error:'数据连接已切换，原研究的读取已暂停。'}));}}}
window.addEventListener('pulse-auth-change',pauseOtherConnections);
window.addEventListener('storage',pauseOtherConnections);
export function branchResearch(session:Session,question:string){const next={...structuredClone(session),id:crypto.randomUUID(),parentId:session.id,createdAt:new Date().toISOString(),revision:1,status:'ready' as const,guidance:session.guidance?{...session.guidance}:undefined,versions:[],operations:[],request:{...session.request,question:question.trim()||session.request.question},brief:{...session.brief,title:question.trim()||session.brief.title},followups:[...session.followups,{id:crypto.randomUUID(),text:question.trim()||'沿用当前证据，建立一个研究分支。',at:new Date().toISOString()}]};return sessionRepository.add(next).id;}
