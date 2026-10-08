/* Bounded visible comment reader. Locators pierce open shadow DOM, never login. */
const REGION='bili-comments,#comment,#comments,.reply-list,.comment-list,.comments-list,.moment-comments,[data-testid="comments"]';
async function locate(page){
 for(let attempt=0;attempt<9;attempt++){
  const nodes=page.locator(REGION);
  for(let i=0;i<Math.min(await nodes.count(),12);i++){
   const node=nodes.nth(i),box=await node.boundingBox();
   if(box&&box.height>30&&box.width>150){await node.evaluate(n=>n.scrollIntoView({block:'start'}));await page.evaluate(()=>window.scrollBy(0,-85));await page.waitForTimeout(900);return node;}
  }
  if(attempt<8){await page.evaluate(()=>window.scrollBy(0,750));await page.waitForTimeout(450);}
 }
 return null;
}
async function cards(region){
 return region.evaluate(root=>{
  const all=(node,selector)=>{const found=[...node.querySelectorAll(selector)];for(const e of node.querySelectorAll('*'))if(e.shadowRoot)found.push(...all(e.shadowRoot,selector));return found;};
  const rect=node=>{const b=node.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height};};
  const out=[];
  const nodes=all(root,'bili-comment-renderer,.reply-item,.comment-item,.comment-card,[data-comment-id],[data-rpid]');
  for(const card of nodes){
   // Replies nested inside a first-level card are a separate sampling scope.
   if(nodes.some(n=>n!==card&&n.contains(card)))continue;
   const contents=all(card,'.reply-content,.comment-content,[data-testid="comment-content"],#content,.root-reply-container .content,.content');
   const content=contents.find(n=>{const b=n.getBoundingClientRect();return b.width>20&&b.height>5;});
   if(!content)continue;
   const bbox=rect(content);if(bbox.y<0||bbox.y+bbox.height>innerHeight||bbox.x<0||bbox.x+bbox.width>innerWidth)continue;
   const date=all(card,'time,.reply-time,.comment-time,#pubdate,.time')[0];
   const text=(content.innerText||content.textContent||'').trim();
   out.push({id:card.getAttribute('data-rpid')||card.getAttribute('data-comment-id')||null,text,bbox,
    date_text:date?.innerText||date?.textContent||'',published_at:date?.getAttribute('datetime')||null});
   if(out.length>=12)break;
  }
  return out;
 });
}
module.exports={locate,cards};
