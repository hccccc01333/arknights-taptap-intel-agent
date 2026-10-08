/** Real curated output from the local Agent, with no production credentials. */
export const publicResultsUrl=(import.meta.env.VITE_PUBLIC_RESULTS_URL||'').trim();
export const isPublicResults=!!publicResultsUrl;
let current:any=null;
let pending:Promise<any>|null=null;

export async function loadPublicResults(refresh=false):Promise<any>{
 if(current&&!refresh)return current;
 if(pending)return pending;
 pending=(async()=>{
  const controller=new AbortController(),timer=window.setTimeout(()=>controller.abort(),20000);
  try{
   const url=new URL(publicResultsUrl,location.href);
   url.searchParams.set('v',String(Math.floor(Date.now()/60000)));
   const response=await fetch(url,{signal:controller.signal,cache:'no-store',credentials:'omit'});
   if(!response.ok)throw Error(`成果同步服务暂时不可用（HTTP ${response.status}）。`);
   const data=await response.json();
   if(data.schema!=='v3-public-results-1'||!data.overview||!data.topics||!data.events)throw Error('成果数据格式不兼容，请等待网站更新。');
   current=data;return data;
  }catch(error){if(error instanceof Error&&error.name==='AbortError')throw Error('读取成果超时，正在自动重连。');if(error instanceof TypeError)throw Error('成果连接暂时中断，正在自动重连。');throw error;}
  finally{window.clearTimeout(timer);pending=null;}
 })();
 return pending;
}
export async function publicItem(kind:'topics'|'events'|'creative_events',id:string){
 const data=await loadPublicResults();
 if(!data[kind]?.[id])throw Error('这项内容已更新或不在公开成果中。');
 return data[kind][id];
}
