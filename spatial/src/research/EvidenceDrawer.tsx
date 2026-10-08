import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { platformName } from '../lib/presentation';
import { Icon } from '../ui/Icon';
import { evidenceOf, type Evidence, type Session } from './domain';

export function EvidenceDrawer({session,evidence,onSelect,onClose,onQuote,onToast}:{session:Session;evidence:Evidence;onSelect:()=>void;onClose:()=>void;onQuote:()=>void;onToast:(message:string)=>void}){
 const [copied,setCopied]=useState(false);const chosen=session.selectedEvidenceIds.includes(evidence.id);const available=evidenceOf(session).some(m=>m.id===evidence.id);
 const ref=useRef<HTMLElement>(null),close=useRef(onClose);close.current=onClose;
 const [overlay,setOverlay]=useState(()=>matchMedia('(max-width:1250px)').matches);
 useEffect(()=>{const media=matchMedia('(max-width:1250px)');const update=()=>setOverlay(media.matches);media.addEventListener('change',update);return()=>media.removeEventListener('change',update);},[]);
 useEffect(()=>{setCopied(false);},[evidence.id,evidence.snapshotId]);
 useEffect(()=>{
  const previous=document.activeElement as HTMLElement|null,scroll=document.body.style.overflow;
  const content=overlay?Array.from(document.querySelectorAll('[data-app-content]')).map(node=>({node,inert:node.hasAttribute('inert')})):[];
  if(overlay){document.body.style.overflow='hidden';content.forEach(({node})=>node.setAttribute('inert',''));}
  const focusable=()=>Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href]')||[]).filter(node=>node.getClientRects().length>0);
  focusable()[0]?.focus({preventScroll:true});
  const key=(event:KeyboardEvent)=>{
   if(!overlay&&document.querySelector('[aria-modal=true]'))return;
   if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close.current();}
   if(!overlay||event.key!=='Tab')return;
   const nodes=focusable(),first=nodes[0],last=nodes.at(-1);
   if(!nodes.includes(document.activeElement as HTMLElement)){event.preventDefault();(event.shiftKey?last:first)?.focus();}
   else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
   else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  };
  document.addEventListener('keydown',key,true);
  return()=>{if(overlay){document.body.style.overflow=scroll;content.forEach(({node,inert})=>{if(!inert)node.removeAttribute('inert');});}document.removeEventListener('keydown',key,true);if(previous?.isConnected&&!previous.closest('[inert]'))previous.focus({preventScroll:true});};
 },[overlay]);
 async function copy(){try{await navigator.clipboard.writeText(`${evidence.title}\n${evidence.excerpt}\n原文：${evidence.url||'未提供'}\n快照：${evidence.snapshotId}\n读取时间：${evidence.readAt}`);setCopied(true);}catch{onToast('浏览器无法复制，可以通过研究档案导出这条证据。');}}
 const panel=<aside ref={ref} className="v4-evidence-drawer" role={overlay?'dialog':undefined} aria-modal={overlay||undefined} aria-label="证据详情"><header><span>证据 / {platformName(evidence.platform)}</span><button aria-label="关闭证据详情" onClick={onClose}><Icon name="close" size={18}/></button></header><h2>{evidence.title}</h2><p className="v4-evidence-topic">{evidence.topicTitle}</p><blockquote>{evidence.excerpt||'这条记录没有提供正文片段。'}</blockquote><dl><dt>快照来源</dt><dd>{evidence.origin==='demo'?'合成演示':evidence.origin==='api'?'已有 API 数据':'外部档案，来源类型未标注'}</dd><dt>原始来源</dt><dd>{evidence.url?<a href={evidence.url} target="_blank" rel="noopener noreferrer">查看原文 ↗</a>:'原文链接未提供'}</dd><dt>原文发布时间</dt><dd>{evidence.publishedAt?new Date(evidence.publishedAt).toLocaleString('zh-CN'):'未提供'}</dd><dt>快照读取时间</dt><dd>{new Date(evidence.readAt).toLocaleString('zh-CN')}</dd><dt>来源标识</dt><dd>{evidence.sourceId}</dd><dt>证据快照</dt><dd>{evidence.snapshotId}</dd><dt>素材使用条件</dt><dd>{evidence.usage}</dd></dl><p className="v4-source-boundary">读取时间是本次保存快照的时间，不推断原文发布或传播时间。</p><footer><button className={'v4-btn '+(chosen?'selected':'')} aria-pressed={chosen} disabled={!available} onClick={onSelect}><Icon name={chosen?'check':'plus'} size={16}/>{!available?'历史版本的证据':chosen?'已选用这条证据':'选用这条证据'}</button><button className="v4-btn subtle" disabled={!available} onClick={onQuote}>引用到我的判断 <Icon name="arrow" size={16}/></button><button className="v4-text-btn" onClick={()=>void copy()}>{copied?'已复制正文与来源':'复制正文与来源'}</button></footer></aside>;
 return overlay?createPortal(<div className="v4-evidence-scrim" onMouseDown={event=>{if(event.target===event.currentTarget)onClose();}}>{panel}</div>,document.querySelector('[data-modal-root]')||document.body):panel;
}
