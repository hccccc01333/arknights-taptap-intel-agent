import { parseGuidance } from './guidance';
import { parseOperations, type HandoffPackage } from './operations';
import { parseCreativeDrafts, parseExperiment } from './creative';
import { useEffect, useSyncExternalStore } from 'react';
import { useEnvironment } from './data';
import { defaultRequest, emptyBrief, normalizePlanet, object, string, type Brief, type BriefVersion, type Claim, type Evidence, type Origin, type ResearchRequest, type Session, type TopicSnapshot } from './domain';

type State={sessions:Session[];loaded:boolean;pending:number;error:string;fallback:boolean};
const states=new Map<string,State>();const listeners=new Map<string,Set<()=>void>>();const loads=new Map<string,Promise<void>>();
const empty=():State=>({sessions:[],loaded:false,pending:0,error:'',fallback:false});
const get=(scope:string)=>{if(!states.has(scope))states.set(scope,empty());return states.get(scope)!;};
function publish(scope:string,patch:Partial<State>){states.set(scope,{...get(scope),...patch});listeners.get(scope)?.forEach(fn=>fn());}
const text=(v:unknown,max=4000)=>string(v).slice(0,max);const ids=(v:unknown)=>Array.isArray(v)?[...new Set(v.filter((s):s is string=>typeof s==='string'))]:[];
const validTime=(v:unknown)=>string(v)&&Number.isFinite(Date.parse(string(v)))?string(v):new Date().toISOString();
function parseBrief(value:unknown):Brief{const raw=object(value),b=emptyBrief();for(const key of ['title','objective','audience','angle','channel','nextAction','measurement','window','selectedIdea'] as const)b[key]=text(raw[key]);b.claimIds=ids(raw.claimIds);return b;}
function parseClaims(value:unknown):Claim[]{return Array.isArray(value)?value.flatMap(v=>{const c=object(v);return string(c.id)?[{id:text(c.id,120),text:text(c.text),evidenceIds:ids(c.evidenceIds),kind:c.kind==='hypothesis'?'hypothesis' as const:'observation' as const,provenance:c.provenance==='rule'?'rule' as const:'user' as const,updatedAt:validTime(c.updatedAt)}]:[]}):[];}
function safeUrl(value:unknown){try{const u=new URL(string(value));return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:undefined;}catch{return undefined;}}
function parseRecords(value:unknown,fallbackOrigin:Origin='unknown'):TopicSnapshot[]{return Array.isArray(value)?value.flatMap(v=>{const r=object(v),topic=normalizePlanet(r.topic);if(!topic||!string(r.id))return [];const readAt=validTime(r.readAt),origin:Origin=r.origin==='demo'||r.origin==='api'?r.origin:fallbackOrigin;const materials:Evidence[]=Array.isArray(r.materials)?r.materials.flatMap(v=>{const m=object(v);if(!string(m.id)||!string(m.sourceId)||!string(m.title))return [];const url=safeUrl(m.url);return [{id:text(m.id,250),sourceId:text(m.sourceId,250),snapshotId:text(m.snapshotId,250)||string(r.id),legacyId:text(m.legacyId,1200),topicId:topic.id,topicTitle:topic.title,title:text(m.title,1000),excerpt:text(m.excerpt,12000),platform:text(m.platform,80)||'unknown',readAt,origin,usage:text(m.usage)||'使用条件未提供',...(url?{url}:{}),...(string(m.sourceReference)?{sourceReference:text(m.sourceReference,250)}:{}),...(string(m.publishedAt)&&Number.isFinite(Date.parse(string(m.publishedAt)))?{publishedAt:string(m.publishedAt)}:{})}];}):[];
 const ideas=Array.isArray(r.ideas)?r.ideas.flatMap(v=>{const i=object(v);return string(i.id)&&string(i.name)?[{id:text(i.id,250),name:text(i.name,1000),type:text(i.type,200),score:typeof i.score==='number'&&Number.isFinite(i.score)&&i.score>=0&&i.score<=1?i.score:0,scoreAvailable:i.scoreAvailable!==false&&typeof i.score==='number'&&Number.isFinite(i.score)&&i.score>=0&&i.score<=1,passed:i.passed===true,passedAvailable:i.passedAvailable!==false&&typeof i.passed==='boolean'}]:[]}):[];
 const executions=Array.isArray(r.executions)?r.executions.flatMap(v=>{const e=object(v);return string(e.ideaId)?[{ideaId:text(e.ideaId,250),where:text(e.where),steps:ids(e.steps).map(s=>s.slice(0,4000))}]:[]}):[];
 return [{id:text(r.id,250),topic,readAt,origin,materials,ideas,executions,summary:text(r.summary,12000),recommendation:text(r.recommendation,12000),...(string(r.error)?{error:text(r.error,1000)}:{})}];}):[];}
export function parseSession(value:unknown,scope:string,restore=false):Session|null{
 const s=object(value);if(s.schema!==4||!string(s.id)||!string(object(s.request).question))return null;
 const r=object(s.request);const request:ResearchRequest={...defaultRequest,question:text(r.question,500),game:text(r.game,200)||'all',platform:text(r.platform,100)||'all',period:r.period==='day'||r.period==='week'?r.period:'all',goal:r.goal==='materials'||r.goal==='strategy'?r.goal:'intelligence',rising:r.rising===true};
 const origin:Origin=s.scope==='demo'?'demo':string(s.scope).startsWith('api:')?'api':'unknown';const records=parseRecords(s.records,origin),archives=parseRecords(s.archives,origin);const allEvidence=new Set([...records,...archives].flatMap(r=>r.materials.map(m=>m.id)));const selectedEvidenceIds=ids(s.selectedEvidenceIds).filter(id=>allEvidence.has(id));
 const versions:BriefVersion[]=Array.isArray(s.versions)?s.versions.flatMap(v=>{const b=object(v);return string(b.id)?[{id:text(b.id,120),number:typeof b.number==='number'&&b.number>0?Math.floor(b.number):1,createdAt:validTime(b.createdAt),brief:parseBrief(b.brief),claims:parseClaims(b.claims),records:parseRecords(b.records,origin),evidenceIds:ids(b.evidenceIds),creativeDrafts:parseCreativeDrafts(b.creativeDrafts),experiment:parseExperiment(b.experiment),operations:parseOperations(b.operations,parseHandoffPackage,parseExperiment)}]:[]}):[];
 const progress=object(s.progress);const status=s.status==='archived'?'archived':s.status==='reading'?(restore?'paused':'reading'):s.status==='paused'?'paused':'ready';
 return {schema:4,guidance:parseGuidance(s.guidance),id:text(s.id,120),scope,...(string(s.parentId)?{parentId:text(s.parentId,120)}:{}),createdAt:validTime(s.createdAt),updatedAt:validTime(s.updatedAt),revision:typeof s.revision==='number'&&s.revision>0?Math.floor(s.revision):1,request,status,view:['discover','understand','create','deliver'].includes(string(s.view))?s.view as Session['view']:'discover',activeTopicId:records.some(r=>r.topic.id===s.activeTopicId)?string(s.activeTopicId):records[0]?.topic.id||'',records,archives,selectedEvidenceIds,claims:parseClaims(s.claims),brief:parseBrief(s.brief),versions,creativeDrafts:parseCreativeDrafts(s.creativeDrafts),experiment:parseExperiment(s.experiment),operations:parseOperations(s.operations,parseHandoffPackage,parseExperiment),followups:Array.isArray(s.followups)?s.followups.flatMap(v=>{const f=object(v);return string(f.id)&&string(f.text)?[{id:text(f.id,120),text:text(f.text,1000),at:validTime(f.at)}]:[]}):[],progress:{completed:typeof progress.completed==='number'?Math.max(0,progress.completed):0,total:typeof progress.total==='number'?Math.max(0,progress.total):records.length},...(status==='paused'&&restore&&s.status==='reading'?{error:'上次读取被中断，已保留完成的快照，可以继续读取。'}:string(s.error)?{error:text(s.error,1000)}:{})};
}
export function parseHandoffPackage(value:unknown):HandoffPackage|undefined{
 const raw=object(value);if(!string(raw.sourceSessionId))return;
 const parsed=parseSession({schema:4,id:'handoff',scope:'portable',request:raw.request,status:'ready',brief:raw.brief,claims:raw.claims,records:raw.records,selectedEvidenceIds:raw.evidenceIds,creativeDrafts:raw.creativeDrafts,experiment:raw.experiment,versions:[],operations:[]},'portable');
 if(!parsed)return;return {sourceSessionId:text(raw.sourceSessionId,120),sourceVersion:typeof raw.sourceVersion==='number'&&Number.isInteger(raw.sourceVersion)&&raw.sourceVersion>0?raw.sourceVersion:null,at:validTime(raw.at),request:parsed.request,brief:parsed.brief,claims:parsed.claims,records:parsed.records,evidenceIds:parsed.selectedEvidenceIds,creativeDrafts:parsed.creativeDrafts||[],experiment:parsed.experiment};
}
let database:Promise<IDBDatabase>|undefined;
function db():Promise<IDBDatabase>{
 if(database)return database;
 const pending=new Promise<IDBDatabase>((resolve,reject)=>{
  if(!window.indexedDB){reject(Error('浏览器不支持 IndexedDB'));return;}
  const request=indexedDB.open('taptap-pulse-research-v4',1);let settled=false;
  const fail=(error:Error)=>{settled=true;reject(error);};
  request.onupgradeneeded=()=>{request.result.createObjectStore('sessions',{keyPath:'key'});};
  request.onsuccess=()=>{if(settled){request.result.close();return;}settled=true;resolve(request.result);};
  request.onerror=()=>fail(request.error||Error('无法打开研究数据库'));
  request.onblocked=()=>fail(Error('研究数据库被其他标签占用'));
 });
 database=pending;
 void pending.catch(()=>{if(database===pending)database=undefined;});
 return pending;
}
async function readDisk(scope:string){const store=(await db()).transaction('sessions','readonly').objectStore('sessions');return new Promise<unknown[]>((resolve,reject)=>{const request=store.getAll();request.onsuccess=()=>resolve(request.result.filter(v=>v.scope===scope).map(v=>v.value));request.onerror=()=>reject(request.error);});}
async function writeDisk(session:Session){const tx=(await db()).transaction('sessions','readwrite');return new Promise<void>((resolve,reject)=>{tx.objectStore('sessions').put({key:session.scope+'|'+session.id,scope:session.scope,value:session});tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error||Error('无法保存研究'));tx.onabort=()=>reject(tx.error||Error('研究保存被中断'));});}
const fallbackKey=(scope:string)=>'taptap-pulse-sessions:v4:'+scope;
async function load(scope:string){
 if(get(scope).loaded)return;if(loads.has(scope))return loads.get(scope);
 const promise=(async()=>{
  let disk:unknown[]=[],backup:unknown[]=[],fallback=false,error='';
  try{disk=await readDisk(scope);}catch{fallback=true;}
  try{
   const raw=JSON.parse(localStorage.getItem(fallbackKey(scope))||'[]');
   if(!Array.isArray(raw))throw Error('本地研究备份格式无效。');backup=raw;
  }catch{error='本地研究备份无法读取，已保留其他可恢复的记录，请导出可用研究。';}
  const parsedDisk=disk.flatMap(value=>{const session=parseSession(value,scope,true);return session?[session]:[];});
  const parsedBackup=backup.flatMap(value=>{const session=parseSession(value,scope,true);return session?[session]:[];});
  if(parsedDisk.length<disk.length||parsedBackup.length<backup.length)error='部分研究记录格式异常，已恢复可用档案，请备份后检查。';
  // A transient database failure may leave a newer local backup. Never replace it with an older database revision.
  const newest=new Map<string,Session>();
  for(const session of [...parsedDisk,...parsedBackup,...get(scope).sessions]){
   const previous=newest.get(session.id);
   if(!previous||session.revision>previous.revision||session.revision===previous.revision&&Date.parse(session.updatedAt)>Date.parse(previous.updatedAt))newest.set(session.id,session);
  }
  const sessions=[...newest.values()];
  if(!fallback){
   const diskById=new Map(parsedDisk.map(session=>[session.id,session]));
   const recovered=sessions.filter(session=>{const previous=diskById.get(session.id);return !previous||previous.revision<session.revision||previous.revision===session.revision&&Date.parse(previous.updatedAt)<Date.parse(session.updatedAt);});
   try{for(const session of recovered)await writeDisk(session);}catch{fallback=true;}
  }
  // Edits may arrive while a recovered backup is being written. Preserve the latest in-memory revision.
  for(const session of get(scope).sessions){const previous=newest.get(session.id);if(!previous||session.revision>previous.revision||session.revision===previous.revision&&Date.parse(session.updatedAt)>Date.parse(previous.updatedAt))newest.set(session.id,session);}
  publish(scope,{sessions:[...newest.values()],loaded:true,fallback,error});
 })();
 loads.set(scope,promise);await promise;
}
function persist(session:Session){const scope=session.scope;publish(scope,{pending:get(scope).pending+1,error:''});const save=async()=>{try{if(get(scope).fallback)localStorage.setItem(fallbackKey(scope),JSON.stringify(get(scope).sessions));else await writeDisk(session);}catch(e){try{localStorage.setItem(fallbackKey(scope),JSON.stringify(get(scope).sessions));publish(scope,{fallback:true});}catch{publish(scope,{error:e instanceof Error?e.message:'浏览器无法保存，请导出研究档案。'});}}finally{publish(scope,{pending:Math.max(0,get(scope).pending-1)});}};void save();}
export const sessionRepository={
 ready:load,
 state:get,
 add:(session:Session)=>{const state=get(session.scope);const parsed=parseSession(session,session.scope);if(!parsed)throw Error('研究会话格式无效。');publish(session.scope,{sessions:[parsed,...state.sessions.filter(s=>s.id!==parsed.id)]});persist(parsed);return parsed;},
 update:(scope:string,id:string,change:(session:Session)=>Session)=>{const state=get(scope),current=state.sessions.find(s=>s.id===id);if(!current)return;const next={...change(structuredClone(current)),id:current.id,scope,updatedAt:new Date().toISOString(),revision:current.revision+1};publish(scope,{sessions:state.sessions.map(s=>s.id===id?next:s)});persist(next);return next;},
 get:(scope:string,id:string)=>get(scope).sessions.find(s=>s.id===id),
 subscribe:(scope:string,fn:()=>void)=>{if(!listeners.has(scope))listeners.set(scope,new Set());listeners.get(scope)!.add(fn);return()=>listeners.get(scope)!.delete(fn);},
 flush:(scope:string)=>new Promise<void>((resolve,reject)=>{
  const check=()=>{const state=get(scope);if(state.pending)return;stop();if(state.error)reject(Error(state.error));else resolve();};
  const stop=sessionRepository.subscribe(scope,check);check();
 }),
};
if(typeof window!=='undefined'){
 const sync=()=>{for(const scope of states.keys()){if(get(scope).pending||get(scope).sessions.some(s=>s.status==='reading'))continue;loads.delete(scope);publish(scope,{loaded:false});void load(scope);}};
 window.addEventListener('focus',sync);
 window.addEventListener('storage',e=>{if(e.key?.startsWith('taptap-pulse-sessions:v4:'))sync();});
}
export function useSessions(){const scope=useEnvironment();const state=useSyncExternalStore(fn=>sessionRepository.subscribe(scope,fn),()=>get(scope),()=>get(scope));useEffect(()=>{void load(scope);},[scope]);return {...state,scope};}
