import { Component, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { OrbitControls, RoundedBox } from '@react-three/drei';
import * as THREE from 'three';
import { Icon } from '../ui/Icon';

export type LensItem={id:string;label:string;detail:string;value?:number|null};
type Props={items:LensItem[];phase?:number;selectedId?:string;reducedMotion:boolean;compact?:boolean;onSelect:(id:string)=>void};
const palette=['#67e9d7','#aab7ff','#ffbb96','#85c9f1','#c0ede0','#e7b6e8'];
class LensBoundary extends Component<{children:ReactNode;fallback:ReactNode},{failed:boolean}>{state={failed:false};static getDerivedStateFromError(){return {failed:true};}render(){return this.state.failed?this.props.fallback:this.props.children;}}
function Signal({item,index,phase,selected,onSelect,reduced}:{item:LensItem;index:number;phase:number;selected:boolean;onSelect:(id:string)=>void;reduced:boolean}){
 const group=useRef<THREE.Group>(null),{invalidate}=useThree();const [hover,setHover]=useState(false);
 const target=useMemo(()=>{const a=index*Math.PI/3+.18;if(phase===1)return new THREE.Vector3((index%3-1)*1.48,(Math.floor(index/3)-.5)*1.42,.3);if(phase===2)return new THREE.Vector3((index%3-1)*1.6,(Math.floor(index/3)-.5)*1.55,.4);if(phase===3)return new THREE.Vector3((index%3-1)*1.5,(Math.floor(index/3)-.5)*1.55,.5+index*.1);return new THREE.Vector3(Math.cos(a)*2.35,Math.sin(a)*1.95,Math.sin(a*2)*.42);},[index,phase]);
 useEffect(()=>invalidate(),[phase,selected,hover,invalidate]);
 useFrame((_,dt)=>{if(!group.current)return;const g=group.current;const t=target.clone();if(selected)t.z+=.55;const scale=hover||selected?1.16:1;const next=reduced?1:1-Math.exp(-dt*8);g.position.lerp(t,next);g.scale.lerp(new THREE.Vector3(scale,scale,scale),next);g.rotation.y=THREE.MathUtils.lerp(g.rotation.y,phase===0?index%2===0?.25:-.25:0,next);if(g.position.distanceTo(t)>.002||Math.abs(g.scale.x-scale)>.002)invalidate();});
 const down=useRef<[number,number]>([0,0]);const click=(event:ThreeEvent<MouseEvent>)=>{event.stopPropagation();if(Math.hypot(event.clientX-down.current[0],event.clientY-down.current[1])<6)onSelect(item.id);};
 return <group ref={group} position={[target.x*.8,target.y*.8,target.z]} onPointerDown={e=>{down.current=[e.clientX,e.clientY];}} onClick={click} onPointerOver={e=>{e.stopPropagation();setHover(true);}} onPointerOut={()=>setHover(false)}>
  {phase===0?<><mesh rotation={[.3,index*.4,.2]}><octahedronGeometry args={[.24+(item.value??.5)*.24,0]}/><meshPhysicalMaterial color={palette[index%6]} metalness={.18} roughness={.23} clearcoat={1}/></mesh><mesh rotation={[0,0,index*.2]}><torusGeometry args={[.59,.011,8,64]}/><meshBasicMaterial color={palette[index%6]} transparent opacity={selected?.85:.3}/></mesh></>:<RoundedBox args={[1.16,1.02,.16]} radius={.14} smoothness={3}><meshPhysicalMaterial color={palette[index%6]} roughness={.26} metalness={.12} clearcoat={1}/></RoundedBox>}
  {phase>0&&<mesh position={[0,0,.091]}><planeGeometry args={[.75,.014]}/><meshBasicMaterial color="#195568"/></mesh>}
 </group>;
}
function Optics({phase,reduced,items,selectedId,onSelect}:{phase:number;reduced:boolean;items:LensItem[];selectedId?:string;onSelect:(id:string)=>void}){
 const ref=useRef<THREE.Group>(null),{invalidate}=useThree(),start=useRef(0);
 const glass=useMemo(()=>new THREE.LatheGeometry([new THREE.Vector2(0,-.12),new THREE.Vector2(.3,-.11),new THREE.Vector2(.72,-.06),new THREE.Vector2(1.04,.0),new THREE.Vector2(1.04,.04),new THREE.Vector2(.72,.11),new THREE.Vector2(.3,.17),new THREE.Vector2(0,.18)],80),[]);
 useEffect(()=>()=>glass.dispose(),[glass]);
 useEffect(()=>{start.current=0;invalidate();},[phase,reduced,invalidate]);
 useFrame((state,dt)=>{if(!ref.current)return;if(!start.current)start.current=state.clock.elapsedTime;const target=phase===0?.25:phase===1?-.2:phase===2?.5:0;ref.current.rotation.z=THREE.MathUtils.lerp(ref.current.rotation.z,target,reduced?1:1-Math.exp(-dt*4));const elapsed=state.clock.elapsedTime-start.current;if(!reduced&&elapsed<2.8){ref.current.rotation.y=Math.sin(elapsed*.85)*.13;invalidate();}else if(Math.abs(ref.current.rotation.z-target)>.002)invalidate();});
 return <><ambientLight intensity={1.35}/><directionalLight position={[4,5,6]} intensity={3.5} color="#d8fbff"/><pointLight position={[-3,-1,3]} intensity={18} color="#66decc"/>
 <group ref={ref} rotation={[-.34,-.55,-.25]}>
  {[0,1,2].map(i=><group key={i} position={[0,0,(i-1)*.57]} rotation={[0,0,i*.4]}>
   <mesh><torusGeometry args={[1.12,.052,12,100]}/><meshPhysicalMaterial color={i===1?'#a6cafb':'#74e0c6'} roughness={.18} metalness={.48} clearcoat={1}/></mesh>
   <mesh geometry={glass} rotation={[Math.PI/2,0,0]}><meshPhysicalMaterial color={i===1?'#a5c8ff':'#97e4d2'} roughness={.12} metalness={.1} clearcoat={1} transparent opacity={.23} side={THREE.DoubleSide} depthWrite={false}/></mesh>
   {[0,1,2,3,4,5,6,7,8,9,10,11].map(k=><mesh key={k} position={[Math.cos(k*Math.PI/6)*1.17,Math.sin(k*Math.PI/6)*1.17,.03]} rotation={[0,0,k*Math.PI/6]}><boxGeometry args={[.06,.02,.025]}/><meshBasicMaterial color="#bdeddf" transparent opacity={.6}/></mesh>)}
  </group>)}
  <RoundedBox args={[.73,.95,.1]} position={[0,0,.23]} radius={.08} smoothness={3}><meshPhysicalMaterial color="#ddf6ed" metalness={.1} roughness={.2} clearcoat={1}/></RoundedBox>
  {[0,1,2].map(i=><mesh key={i} position={[-.06,.16-i*.16,.292]}><boxGeometry args={[.35-i*.05,.045,.008]}/><meshBasicMaterial color={i===0?'#79b3c0':'#a3c8c8'}/></mesh>)}
  <mesh position={[0,0,1.12]}><torusGeometry args={[1.35,.009,6,96,Math.PI*1.4]}/><meshBasicMaterial color="#96c8d3" transparent opacity={.5}/></mesh>
  <mesh rotation={[0,0,.5]}><torusGeometry args={[2.68,.01,6,120,Math.PI*1.6]}/><meshBasicMaterial color="#79dbd3" transparent opacity={.24}/></mesh>
 </group>
 {items.slice(0,6).map((item,i)=><Signal key={item.id} item={item} index={i} phase={phase} selected={item.id===selectedId} onSelect={onSelect} reduced={reduced}/>)}
 <OrbitControls enablePan={false} enableZoom={false} enableDamping={!reduced} dampingFactor={.13} minPolarAngle={.65} maxPolarAngle={2.4} onChange={()=>invalidate()}/></>;
}
export default function SignalLens({items,phase=0,selectedId,reducedMotion,compact=false,onSelect}:Props){
 const ref=useRef<HTMLDivElement>(null);const [visible,setVisible]=useState(true),[failed,setFailed]=useState(false),[frozen,setFrozen]=useState(false);
 useEffect(()=>{let inside=true;const update=()=>setVisible(inside&&!document.hidden);const observer=new IntersectionObserver(([e])=>{inside=e.isIntersecting;update();});if(ref.current)observer.observe(ref.current);document.addEventListener('visibilitychange',update);return()=>{observer.disconnect();document.removeEventListener('visibilitychange',update);};},[]);
 const fallback=<div className="lens-fallback"><Icon name="orbit" size={80}/><p>热点透镜</p><span>通过下方标签打开实际内容</span></div>;
 return <div className={'signal-lens '+(compact?'compact':'')} ref={ref} data-phase={phase}>
  <div className="lens-coordinate" aria-hidden="true"><i/><i/><i/><i/></div>
  {failed?fallback:<LensBoundary fallback={fallback}><Suspense fallback={fallback}><Canvas frameloop={visible?'demand':'never'} dpr={[1,compact?1.25:1.5]} camera={{position:[0,0,8.8],fov:42}} gl={{alpha:true,antialias:true,powerPreference:'low-power'}} onCreated={({gl})=>gl.domElement.addEventListener('webglcontextlost',e=>{e.preventDefault();setFailed(true);})}><Optics items={items} phase={phase} selectedId={selectedId} reduced={reducedMotion||frozen} onSelect={onSelect}/></Canvas></Suspense></LensBoundary>}
  <div className="lens-labels">{items.slice(0,6).map((item,i)=><button key={item.id} className={selectedId===item.id?'selected':''} style={{'--signal-color':palette[i%6]} as React.CSSProperties} onClick={()=>onSelect(item.id)}><i/><span>{item.label}<small>{item.detail}</small></span><Icon name="arrow" size={13}/></button>)}</div>
  <div className="lens-caption"><span>{['聚合热点信号','展开来源关系','编排创作素材','形成增长提案'][phase]}<small>拖动探索空间</small></span><button onClick={()=>setFrozen(v=>!v)} aria-label={frozen?'开启动画':'暂停空间动画'} aria-pressed={frozen}><Icon name={frozen?'play':'pause'} size={15}/></button></div>
 </div>;
}
