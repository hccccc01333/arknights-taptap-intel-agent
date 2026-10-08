import { evidenceOf, type Session } from './domain';

export const guideSteps=[
 {id:'direction',label:'选热点与方向',view:'discover',tab:'opportunities'},
 {id:'sources',label:'查看来源依据',view:'understand',tab:'intelligence'},
 {id:'create',label:'编辑方案与素材',view:'create',tab:'materials'},
 {id:'verify',label:'写验证办法',view:'create',tab:'experiment'},
 {id:'deliver',label:'保存成果',view:'deliver',tab:undefined},
 {id:'handoff',label:'交给工作台',view:'deliver',tab:undefined},
] as const;
export type GuideStep=typeof guideSteps[number]['id'];
export type Guidance={enabled:boolean;step:GuideStep};
export function parseGuidance(value:unknown):Guidance|undefined{if(!value||typeof value!=='object')return;const v=value as Record<string,unknown>;if(!guideSteps.some(s=>s.id===v.step))return;return {enabled:v.enabled!==false,step:v.step as GuideStep};}
export function currentGuideStep(s:Session):GuideStep{const saved=guideSteps.find(step=>step.id===s.guidance?.step);return saved?.view===s.view?saved.id:s.view==='discover'?'direction':s.view==='understand'?'sources':s.view==='create'?'create':'deliver';}
export function guideState(s:Session){const selected=new Set(s.selectedEvidenceIds);const count=evidenceOf(s).filter(e=>selected.has(e.id)).length;const ready={direction:s.request.goal==='intelligence'?s.records.some(r=>!r.error):!!s.brief.angle.trim(),sources:count>0,create:!!s.brief.angle.trim()&&!!s.brief.objective.trim(),verify:!!s.experiment?.hypothesis.trim()&&!!s.experiment?.metric.trim(),deliver:s.versions.length>0,handoff:!!s.operations?.length};return {ready,evidenceCount:count,savedCount:Object.values(ready).filter(Boolean).length};}
export function projectStage(s:Session){if(s.status==='reading')return '正在读取热点';if(s.status==='paused')return '读取已暂停';if(s.operations?.length)return '已建立行动';if(s.versions.length)return '已有成果版本';if(s.brief.angle.trim()||s.creativeDrafts?.length)return '方案与素材编辑中';return '正在整理热点与来源';}
