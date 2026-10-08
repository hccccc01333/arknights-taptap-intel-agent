import { useSyncExternalStore } from 'react';
import { api, apiBaseUrl, auth, isDemoMode, type Universe } from '../lib/api';
import { normalizeData, object, stableId } from './domain';

export function environment(){let actor='';try{actor=localStorage.getItem('spatial_actor')||'';}catch{}return isDemoMode?'demo':`api:${apiBaseUrl||'same-origin'}:account:${auth.get()?actor||stableId(auth.get()):'guest'}`;}
const identityListeners=new Set<()=>void>();
if(typeof window!=='undefined'){for(const event of ['storage','pulse-auth-change'])window.addEventListener(event,()=>identityListeners.forEach(fn=>fn()));}
export function useEnvironment(){return useSyncExternalStore(fn=>{identityListeners.add(fn);return()=>identityListeners.delete(fn);},environment,environment);}
type Entry<T>={value?:T;readAt?:string;promise?:Promise<T>;controller?:AbortController;users:number};
const entries=new Map<string,Entry<unknown>>();const TTL=90_000;
function read<T>(key:string,load:(signal:AbortSignal)=>Promise<T>,options:{signal?:AbortSignal;force?:boolean}={}):Promise<T>{
 if(options.signal?.aborted)return Promise.reject(new DOMException('读取已取消','AbortError'));
 let entry=entries.get(key) as Entry<T>|undefined;
 if(!entry){entry={users:0};entries.set(key,entry);}
 if(entry.value!==undefined&&!options.force&&entry.readAt&&Date.now()-Date.parse(entry.readAt)<TTL)return Promise.resolve(entry.value);
 if(!entry.promise){const controller=new AbortController();entry.controller=controller;const target=entry;target.promise=load(controller.signal).then(value=>{target.value=value;target.readAt=new Date().toISOString();return value;}).finally(()=>{target.promise=undefined;target.controller=undefined;});}
 entry.users++;const target=entry;
 return new Promise<T>((resolve,reject)=>{let settled=false;const finish=(fn:()=>void)=>{if(settled)return;settled=true;options.signal?.removeEventListener('abort',cancel);target.users--;fn();if(target.users===0&&target.promise){target.controller?.abort();if(entries.get(key)===target)entries.delete(key);}};const cancel=()=>finish(()=>reject(new DOMException('读取已取消','AbortError')));options.signal?.addEventListener('abort',cancel,{once:true});target.promise!.then(value=>finish(()=>resolve(value)),error=>finish(()=>reject(error)));});
}
export const intelligenceData={
 universe:(options:{signal?:AbortSignal;force?:boolean}={})=>read<Universe>(environment()+':universe',async signal=>normalizeData(await api.universe(240,signal)),options),
 topic:(id:string,options:{signal?:AbortSignal;force?:boolean}={})=>read<Record<string,unknown>>(environment()+':topic:'+id,async signal=>{const value=await api.topic(id,signal);if(!value||typeof value!=='object'||Array.isArray(value))throw Error('话题响应缺少可用的情报对象。');return object(value);},options),
 readAt:(id:string)=>entries.get(environment()+':topic:'+id)?.readAt,
};
