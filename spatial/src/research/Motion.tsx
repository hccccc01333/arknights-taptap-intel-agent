import { useEffect, useRef, type ReactNode } from 'react';
import gsap from 'gsap';
export function Motion({children,className='',reduced=false}:{children:ReactNode;className?:string;reduced?:boolean}){
 const ref=useRef<HTMLDivElement>(null);
 useEffect(()=>{const node=ref.current;if(!node||reduced||matchMedia('(prefers-reduced-motion:reduce)').matches)return;const context=gsap.context(()=>{const items=node.querySelectorAll('[data-reveal]');gsap.fromTo(items,{y:20,opacity:0},{y:0,opacity:1,duration:.65,stagger:.085,ease:'power3.out',clearProps:'all'});},node);return()=>context.revert();},[reduced]);
 return <div ref={ref} className={className}>{children}</div>;
}
