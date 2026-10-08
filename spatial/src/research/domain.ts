import type { Guidance } from './guidance';
import { operationsMarkdown, type Operation } from './operations';
import { isDemoMode, type Idea, type Planet, type Universe } from '../lib/api';
import { defaultFilters, filterTopics, platformName } from '../lib/presentation';
import { groupGames, normalizeEvidence, type Material } from '../studio/catalog';
import { creativeMarkdown, experimentMarkdown, type CreativeDraft, type ExperimentPlan } from './creative';

export type Goal = 'intelligence' | 'materials' | 'strategy';
export type ResearchRequest = { question: string; game: string; platform: string; period: 'all' | 'day' | 'week'; goal: Goal; rising: boolean };
export type Origin = 'demo' | 'api' | 'unknown';
export type Evidence = Material & { sourceId: string; snapshotId: string; legacyId: string; readAt: string; usage: string; origin: Origin };
export type TopicSnapshot = { id: string; topic: Planet; readAt: string; origin: Origin; summary: string; recommendation: string; materials: Evidence[]; ideas: Idea[]; executions: { ideaId: string; where: string; steps: string[] }[]; error?: string };
export type Claim = { id: string; text: string; evidenceIds: string[]; kind: 'observation' | 'hypothesis'; updatedAt: string; provenance?:'rule'|'user' };
export type Brief = { title: string; objective: string; audience: string; angle: string; channel: string; nextAction: string; measurement: string; window: string; selectedIdea: string; claimIds: string[] };
export type BriefVersion = { id: string; number: number; createdAt: string; brief: Brief; claims: Claim[]; records: TopicSnapshot[]; evidenceIds: string[]; creativeDrafts?: CreativeDraft[]; experiment?: ExperimentPlan; operations?:Operation[] };
export type Session = { schema: 4; guidance?:Guidance; id: string; scope: string; parentId?: string; createdAt: string; updatedAt: string; revision: number; request: ResearchRequest; status: 'reading' | 'ready' | 'paused' | 'archived'; view: 'discover' | 'understand' | 'create' | 'deliver'; activeTopicId: string; records: TopicSnapshot[]; archives: TopicSnapshot[]; selectedEvidenceIds: string[]; claims: Claim[]; brief: Brief; versions: BriefVersion[]; followups: { id: string; text: string; at: string }[]; progress: { completed: number; total: number }; error?: string; creativeDrafts?: CreativeDraft[]; experiment?: ExperimentPlan; operations?:Operation[] };
export const goalNames: Record<Goal, string> = { intelligence:'找热点', materials:'找素材', strategy:'找增长创意' };
export const viewNames: Record<Session['view'], string> = { discover:'发现线索', understand:'形成判断', create:'编排方案', deliver:'整理成果' };
export const defaultRequest: ResearchRequest = { question:'', game:'all', platform:'all', period:'all', goal:'intelligence', rising:false };
export const emptyBrief = (): Brief => ({ title:'',objective:'',audience:'',angle:'',channel:'',nextAction:'',measurement:'',window:'',selectedIdea:'',claimIds:[] });
export const string = (v:unknown) => typeof v==='string'?v:'';
export const object = (v:unknown):Record<string,unknown> => v && typeof v==='object' && !Array.isArray(v)?v as Record<string,unknown>:{};
export function stableId(value:string){let h=2166136261;for(let i=0;i<value.length;i++){h^=value.charCodeAt(i);h=Math.imul(h,16777619);}return (h>>>0).toString(36);}
const score=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=1?v:null;
const count=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)&&v>=0?Math.floor(v):0;
const strings=(v:unknown)=>Array.isArray(v)?v.filter((x):x is string=>typeof x==='string').slice(0,100):[];
export function normalizePlanet(value:unknown):Planet|null{
 const p=object(value);if(!string(p.id)||!string(p.title))return null;
 const platforms=[...new Set(strings(p.platforms))];const level=['L1','L2','L3'].includes(string(p.relevanceLevel))?p.relevanceLevel as Planet['relevanceLevel']:'L3';
 return {id:string(p.id),title:string(p.title),lifecycle:string(p.lifecycle)||null,heat:score(p.heat),velocity:score(p.velocity),confidence:score(p.confidence),relevance:score(p.relevance),relevanceLevel:level,relevanceNote:string(p.relevanceNote),contentCount:count(p.contentCount),platformCount:platforms.length,platforms,entities:strings(p.entities),nIdeas:count(p.nIdeas),firstSeen:string(p.firstSeen)&&Number.isFinite(Date.parse(string(p.firstSeen)))?string(p.firstSeen):null,radius:typeof p.radius==='number'&&p.radius>0?p.radius:1,glowRank:count(p.glowRank),density:typeof p.density==='number'&&Number.isFinite(p.density)?p.density:0,game:string(p.game)||null,gameName:string(p.gameName)||null,cover:string(p.cover)||null,appId:string(p.appId)||null,...(string(p.description)?{description:string(p.description)}:{}),
 ...(p.isForming===true?{isForming:true}:{}),
 ...(string(p.category)?{category:string(p.category)}:{}),...(score(p.sentiment)!==null?{sentiment:score(p.sentiment)!}:{}),...(typeof p.change==='number'&&Number.isFinite(p.change)?{change:p.change}:{})};
}
export function normalizeData(value:Universe):Universe{
 const seen=new Set<string>();const planets=(Array.isArray(value.planets)?value.planets:[]).flatMap(v=>{const p=normalizePlanet(v);if(!p||seen.has(p.id))return [];seen.add(p.id);return [p];});
 const ideas:Universe['ideas']={};for(const [id,items] of Object.entries(object(value.ideas))){ideas[id]=Array.isArray(items)?items.flatMap(v=>{const i=object(v);return string(i.id)&&string(i.name)?[{id:string(i.id),name:string(i.name),type:string(i.type)||'未分类',score:score(i.score)??0,scoreAvailable:score(i.score)!==null,passed:i.passed===true,passedAvailable:typeof i.passed==='boolean'}]:[]}):[];}
 return {...value,planets,ideas,core:{...value.core,topicCount:planets.length,multiPlatformCount:planets.filter(p=>p.platforms.length>1).length}};
}
export function snapshot(topic:Planet,detail:unknown,ideas:Idea[],readAt=new Date().toISOString()):TopicSnapshot{
 const data=object(detail);const workspace=object(data.workspace);const root=Object.keys(workspace).length?{...data,...workspace}:data;
 const id='snapshot-'+crypto.randomUUID(),origin:Origin=isDemoMode?'demo':'api';
 const materials=normalizeEvidence(detail,topic).map(m=>{const sourceId='source-'+stableId(m.sourceReference?m.platform+'|'+m.sourceReference:m.url?m.url:topic.id+'|'+m.platform+'|'+m.title+'|'+m.excerpt.replace(/\s+/g,' ').trim());return {...m,id:'evidence-'+stableId(sourceId+'|'+m.excerpt),sourceId,snapshotId:id,legacyId:m.id,readAt,origin,usage:m.usage||'素材使用条件未提供，执行前需核实'};});
 const creatives=Array.isArray(root.creatives)?root.creatives:[];
 return {id,topic:structuredClone(topic),readAt,origin,summary:string(root.summary)||string(object(root.what_happened).summary)||string(object(root.trend_analysis).trigger),recommendation:string(root.recommendation)||string(object(root.next_step).recommendation),materials,ideas:structuredClone(ideas),executions:creatives.flatMap(v=>{const c=object(v),e=object(c.execution);return string(c.idea_id)?[{ideaId:string(c.idea_id),where:string(e.where),steps:strings(e.steps)}]:[]})};
}
export function sourceBoundary(records:TopicSnapshot[]){
 const origins=new Set(records.map(r=>r.origin));
 if(!origins.size||origins.size===1&&origins.has('demo'))return isDemoMode||origins.has('demo')?'合成演示情境，未调用真实 AI，不代表实时热点。':'当前 API 连接，本次整理未重新调用模型。';
 if(origins.size===1&&origins.has('api'))return '读取已有 API 情报；本次整理未重新生成 AI 结论。';
 const names=[origins.has('demo')?'合成演示':'',origins.has('api')?'已有 API 快照':'',origins.has('unknown')?'来源类型未标注的外部记录':''].filter(Boolean);
 return '这份研究包含'+names.join('、')+'；来源标记随档案保留，本次整理未调用模型。';
}
export function missingSnapshot(topic:Planet,ideas:Idea[],error:string):TopicSnapshot{return {...snapshot(topic,{},ideas),error};}
const generic=/^(现在|当前|今天|本周|最近|帮我|请|给我|我们|我|taptap|tap tap|pulse|全网|全平台|有哪些|有什么|哪些|怎么|如何|可以|值得|适合|推荐|发现|看看|寻找|找到|追踪|热点|热门|游戏|话题|内容|情报|素材|材料|策略|增长|创意|机会|社区|运营|研究|分析|传播|正在|升温|的|和|与|相关|做|在|想|能|一下|什么|找|关注|成为|借势|近期|热度|在涨|方向|一个|方案|？|\?|。|\s|，|,)+$/i;
export function matchRequest(universe:Universe,request:ResearchRequest){
 const scoped=filterTopics(universe.planets,{...defaultFilters,game:request.game,platform:request.platform,lifecycle:request.rising?'rising':'all',sort:request.goal==='strategy'?'velocity':'heat'},[]);
 const term=request.question.toLocaleLowerCase().trim();const names=[...new Set(universe.planets.map(p=>p.gameName).filter((s):s is string=>!!s))].filter(s=>term.includes(s.toLocaleLowerCase()));const categories=[...new Set(universe.planets.map(p=>p.category).filter((s):s is string=>!!s))].filter(s=>term.includes(s.toLocaleLowerCase()));
 const genreIds=new Set(groupGames(universe).filter(group=>group.genreSource!=='unknown'&&term.includes(group.genre.replace('（示例品类）','').toLocaleLowerCase())).flatMap(group=>group.topics.map(p=>p.id)));
 let topics=names.length?scoped.filter(p=>names.includes(p.gameName||'')):categories.length?scoped.filter(p=>categories.includes(p.category||'')):genreIds.size?scoped.filter(p=>genreIds.has(p.id)):term&&!generic.test(term)?scoped.filter(p=>term.split(/[\s，,、？?。]/).filter(Boolean).some(w=>`${p.title} ${p.gameName||''} ${p.category||''}`.toLocaleLowerCase().includes(w))):scoped;
 if(request.period!=='all'){const now=Date.now(),age=request.period==='day'?24:168;topics=topics.filter(p=>p.firstSeen&&Date.parse(p.firstSeen)<=now&&now-Date.parse(p.firstSeen)<=age*3600000);}
 return topics.slice(0,12);
}
export function newSession(scope:string,request:ResearchRequest):Session{const now=new Date().toISOString();return {schema:4,id:crypto.randomUUID(),scope,createdAt:now,updatedAt:now,revision:1,request:{...request},status:'reading',view:'discover',activeTopicId:'',records:[],archives:[],selectedEvidenceIds:[],claims:[],brief:{...emptyBrief(),title:request.question},versions:[],followups:[],progress:{completed:0,total:0}};}
export function evidenceOf(session:Session){return [...new Map([...session.records,...session.archives].flatMap(r=>r.materials).map(e=>[e.id,e])).entries()].map(([,e])=>e);}
export function readiness(session:Session){
 const evidence=evidenceOf(session);
 const selected=evidence.filter(e=>session.selectedEvidenceIds.includes(e.id));
 const claims=session.claims.filter(c=>session.brief.claimIds.includes(c.id));
 return [{label:'研究目标',ready:!!session.brief.objective.trim()},{label:'目标玩家',ready:!!session.brief.audience.trim()},{label:'选用证据',ready:selected.length>0},{label:'有引用的判断',ready:claims.some(c=>c.text.trim()&&c.evidenceIds.some(id=>evidence.some(e=>e.id===id)))},{label:'增长方向',ready:!!session.brief.angle.trim()},{label:'下一步与验证',ready:!!session.brief.nextAction.trim()&&!!session.brief.measurement.trim()}];
}
export function briefMarkdown(session:Session,version?:BriefVersion){
 const b=version?.brief||session.brief,claims=(version?.claims||session.claims).filter(c=>b.claimIds.includes(c.id)&&c.text.trim()),records=version?.records||[...session.records,...session.archives],ids=version?.evidenceIds||session.selectedEvidenceIds;
 const drafts=version?(version.creativeDrafts||[]):(session.creativeDrafts||[]);const experiment=version?version.experiment:session.experiment;
 const materials=[...new Map(records.flatMap(r=>r.materials).filter(m=>ids.includes(m.id)||claims.some(c=>c.evidenceIds.includes(m.id))||drafts.some(d=>d.sourceIds.includes(m.id))).map(e=>[e.id,e])).values()];const reference=(id:string)=>{const i=materials.findIndex(m=>m.id===id);return i<0?'[证据当前不可用]':`[${i+1}]`;};
 return `# ${b.title||session.request.question}\n\nTapTap Pulse · ${version?'版本 '+version.number:'当前草稿'}\n会话：${session.id}\n${sourceBoundary(records)}\n读取时间是前端读取快照的时间，不代表原文发布时间。\n\n## 研究需求\n${session.request.question}\n\n## 目标\n${b.objective||'待补充'}\n\n## 目标玩家\n${b.audience||'待补充'}\n\n## 判断与引用\n${claims.map(c=>`- ${c.provenance==='rule'?'规则编排的增长假设 / 待验证':c.kind==='hypothesis'?'推测 / 待验证':'用户整理的观察'}：${c.text} ${c.evidenceIds.map(reference).join(' ')}`).join('\n')||'尚未整理判断'}\n\n## TapTap 增长方向\n${b.angle||'待补充'}\n\n渠道：${b.channel||'待补充'}\n窗口：${b.window||'待核实'}\n\n## 下一步\n${b.nextAction||'待补充'}\n\n## 验证方式\n${b.measurement||'待补充'}\n\n## 证据索引\n${materials.map((m,i)=>`### [${i+1}] ${m.title}\n\n${m.excerpt}\n\n平台：${platformName(m.platform)}\n快照来源：${m.origin==='demo'?'合成演示':m.origin==='api'?'已有 API 数据':'外部档案，来源类型未标注'}\n原文：${m.url||'未提供'}\n原文发布时间：${m.publishedAt||'未提供'}\n来源标识：${m.sourceId}\n快照：${m.snapshotId}\n读取时间：${m.readAt}\n使用条件：${m.usage}\n`).join('\n')}\n\n## 数据边界\n${records.filter(r=>r.error).map(r=>`${r.topic.title}：${r.error}`).join('\n')||'已读取当前记录；引用与执行建议仍需核验。'}\n`+creativeMarkdown(drafts)+experimentMarkdown(experiment)+operationsMarkdown(version?(version.operations||[]):(session.operations||[]));
}
