import { useSyncExternalStore } from 'react';
import { useEnvironment } from './data';
import type { EmployeeRole } from './operations';
export type EmployeePreferences={role:EmployeeRole;games:string[];genres:string[];categories:string[];triage:Record<string,'watch'|'hold'|'ignore'>;reviewedAt:string;baseline:{id:string;heat:number|null;velocity:number|null}[]};
type State={value:EmployeePreferences;error:string};const states=new Map<string,State>(),listeners=new Map<string,Set<()=>void>>();const key=(scope:string)=>'taptap-pulse-employee:v7:'+scope;
const defaults=():EmployeePreferences=>({role:'community',games:[],genres:[],categories:[],triage:{},reviewedAt:'',baseline:[]});
const strings=(v:unknown)=>Array.isArray(v)?[...new Set(v.filter((v):v is string=>typeof v==='string').map(s=>s.slice(0,200)))].slice(0,100):[];
const obj=(v:unknown):Record<string,unknown>=>v&&typeof v==='object'&&!Array.isArray(v)?v as Record<string,unknown>:{};
export function parseEmployeePreferences(v:unknown):EmployeePreferences{
 const x=obj(v),score=(n:unknown)=>typeof n==='number'&&Number.isFinite(n)&&n>=0&&n<=1?n:null;
 const triage=Object.fromEntries(Object.entries(obj(x.triage)).slice(0,1000).filter(([k,v])=>k.length<=200&&['watch','hold','ignore'].includes(String(v)))) as EmployeePreferences['triage'];
 return {role:x.role==='growth'?'growth':'community',games:strings(x.games),genres:strings(x.genres),categories:strings(x.categories),triage,reviewedAt:typeof x.reviewedAt==='string'&&Number.isFinite(Date.parse(x.reviewedAt))?x.reviewedAt:'',baseline:Array.isArray(x.baseline)?x.baseline.slice(0,1000).flatMap(v=>{const p=obj(v);return typeof p.id==='string'?[{id:p.id.slice(0,200),heat:score(p.heat),velocity:score(p.velocity)}]:[]}):[]};
}
function state(scope:string){if(!states.has(scope)){let value=defaults(),error='';try{const raw=localStorage.getItem(key(scope));if(raw)value=parseEmployeePreferences(JSON.parse(raw));}catch{error='关注设置暂时无法读取。';}states.set(scope,{value,error});}return states.get(scope)!;}
export function employeePreferences(scope:string){return state(scope).value;}
export function patchEmployeePreferences(scope:string,patch:Partial<EmployeePreferences>){const value=parseEmployeePreferences({...state(scope).value,...patch});let error='';try{localStorage.setItem(key(scope),JSON.stringify(value));}catch{error='关注设置尚未保存到浏览器。';}states.set(scope,{value,error});listeners.get(scope)?.forEach(fn=>fn());}
export function useEmployeePreferences(){const scope=useEnvironment();const s=useSyncExternalStore(fn=>{if(!listeners.has(scope))listeners.set(scope,new Set());listeners.get(scope)!.add(fn);return()=>listeners.get(scope)!.delete(fn);},()=>state(scope),()=>state(scope));return {...s,scope,patch:(p:Partial<EmployeePreferences>)=>patchEmployeePreferences(scope,p)};}
window.addEventListener('storage',e=>{for(const scope of states.keys())if(e.key===key(scope)){states.delete(scope);listeners.get(scope)?.forEach(fn=>fn());}});
