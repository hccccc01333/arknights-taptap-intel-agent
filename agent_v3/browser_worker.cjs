/* Isolated public browsing tool. No user profile, cookies or login state. */
const fs=require('fs');
const dns=require('dns').promises;
const net=require('net');
const path=require('path');
const os=require('os');
const BLOCK=/验证码|安全验证|访问过于频繁|人机验证|Access Denied|captcha|verify you are human/i;
function publicIp(ip){
 if(net.isIP(ip)===4){const p=ip.split('.').map(Number);return !(
  [0,10,127].includes(p[0])||p[0]>=224||p[0]===169&&p[1]===254||p[0]===172&&p[1]>=16&&p[1]<=31||
  p[0]===192&&(p[1]===168||p[1]===0||p[1]===2)||p[0]===100&&p[1]>=64&&p[1]<=127||p[0]===198&&[18,19,51].includes(p[1])||p[0]===203&&p[1]===0);}
 const v=ip.toLowerCase();return net.isIP(ip)===6&&!/^(::|fc|fd|fe[89ab]|ff|2001:db8)/.test(v);
}
async function safe(url){
 const u=new URL(url);if(u.protocol!=='https:'||u.username||u.password||u.port&&!['443'].includes(u.port))throw Error('public_https_required');
 const addresses=await dns.lookup(u.hostname,{all:true});if(!addresses.length||addresses.some(a=>!publicIp(a.address)))throw Error('non_public_address');return u.href;
}
async function main(input){
 const mod=process.env.V3_PLAYWRIGHT_MODULE||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
 const {chromium}=require(mod);await safe(input.url);
 const exe=process.env.V3_BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
 const browser=await chromium.launch({headless:true,executablePath:exe});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',serviceWorkers:'block',acceptDownloads:false});
  const cache=new Map();let requests=0,blocked=0,admitted=0;
  await context.route('**/*',async route=>{try{
   requests++;
   if(route.request().resourceType()==='media'||/\/log\/(web|report)|data\.bilibili\.com\//.test(route.request().url())||++admitted>200){blocked++;return await route.abort();}
   const url=route.request().url(),host=new URL(url).hostname;
   if(!cache.has(host))cache.set(host,safe(url).then(()=>true,()=>false));
   // Validate protocol, credentials and host on every request, DNS once per host.
   const u=new URL(url);if(u.protocol!=='https:'||u.username||u.password||u.port&&u.port!=='443'||!await cache.get(host))throw Error('blocked');
   await route.continue();
  }catch{blocked++;await route.abort();}});
  const page=await context.newPage();page.setDefaultTimeout(10000);page.on('dialog',d=>d.dismiss());
  const response=await page.goto(input.url,{waitUntil:'domcontentloaded',timeout:22000});
  await page.waitForTimeout(1800);await safe(page.url());
  await page.addStyleTag({content:'video,audio{visibility:hidden!important}'});
  await page.evaluate(()=>document.querySelectorAll('video,audio').forEach(v=>v.pause()));
  const data=await page.evaluate(()=>{
   const videoRegions=location.hostname.endsWith('bilibili.com')?Array.from(document.querySelectorAll('.video-info-container,.video-desc-container')):[];
   const region=document.querySelector('article,#artibody,#articleContent,.article-content,.content-detail,main');
   const selected=videoRegions.length?videoRegions:[region||document.body];
   const bodies=selected.map(node=>{const clone=node.cloneNode(true);clone.querySelectorAll('script,style,nav,footer,header,form,aside').forEach(n=>n.remove());return clone.innerText||clone.textContent||'';});
   const meta=document.querySelector('meta[property="article:published_time"],meta[name="pubdate"],meta[itemprop="datePublished"]');
   return {title:document.title,body:bodies.join('\n'),reading_region:videoRegions.length?'video_title_and_description':region?'article_or_main':'visible_page_with_navigation',published_at:meta?.content||null,
    links:Array.from(document.querySelectorAll('#b_results .b_algo h2 a,.result h3 a')).slice(0,8).map(a=>({title:a.innerText,url:a.href})),
    height:document.documentElement.scrollHeight};
  });
  const denied=response&&[401,403,429].includes(response.status())||BLOCK.test(data.body.slice(0,1600));
  const captures=[];const count=input.visual?3:1;
  let comment_region={status:'not_requested'},comment_cards=[];
  const commentTools=require('./comment_region.cjs');
  const region=input.region==='comments'&&!denied?await commentTools.locate(page):null;
  if(region){for(let i=0;i<5;i++){if((await commentTools.cards(region)).length)break;await page.waitForTimeout(900);}}
  if(input.region==='comments')comment_region={status:denied?'blocked':region?'found':'not_found'};
  const anchor=region?await page.evaluate(()=>window.scrollY):0;
  for(let i=0;i<count;i++){
   if(input.region==='comments'&&!region&&i)break;
   const height=await page.evaluate(()=>document.documentElement.scrollHeight);
   const y=Math.min(anchor+i*850,Math.max(0,height-1000));if(i&&y===captures.at(-1).scroll_y)break;
   await page.evaluate(y=>window.scrollTo(0,y),y);await page.waitForTimeout(350);
   const sampled=region?await commentTools.cards(region):[];
   const file=path.join(input.output_dir,'frame-'+i+'.png');await page.screenshot({path:file,fullPage:false,animations:'disabled'});
   const after=region?await commentTools.cards(region):[];
   const stable=sampled.filter(c=>after.some(n=>(c.id?c.id===n.id:c.text===n.text)&&['x','y','width','height'].every(k=>Math.abs(c.bbox[k]-n.bbox[k])<3)));
   captures.push({file,scroll_y:y,width:1440,height:1000,comment_count:stable.length});
   comment_cards.push(...stable.map(c=>({...c,frame_index:i})));
   if(region&&comment_cards.length>=12)break;
  }
  if(region&&!comment_cards.length){const text=await region.innerText().catch(()=>'');comment_region.status=/登录.*评论|登录后|请先登录/.test(text)?'login_required':'empty_or_unrecognized';}
  return {status:denied?'blocked':'ok',http_status:response?.status(),requested_url:input.url,resolved_url:page.url(),
   title:data.title,body:denied?'':data.body.replace(/\s+\n/g,'\n').slice(0,12000),body_truncated:data.body.length>12000,reading_region:data.reading_region,video_played:false,
   published_at:data.published_at,links:denied?[]:data.links,captures,comment_region,comment_cards:comment_cards.slice(0,12),network_requests:requests,blocked_requests:blocked,
   note:denied?'访问限制页面，截图仅供核查，不能作为事件正文':'有限滚动可见区域，未读完整站点或全部评论'};
 }finally{await browser.close();}
}
let raw='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>raw+=s);
process.stdin.on('end',()=>main(JSON.parse(raw)).then(r=>process.stdout.write(JSON.stringify(r))).catch(e=>{
 process.stdout.write(JSON.stringify({status:'failed',error:String(e.message).slice(0,200)}));process.exitCode=1;
}));
