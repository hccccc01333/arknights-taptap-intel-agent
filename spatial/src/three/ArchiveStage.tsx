import { Component, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import type { Planet } from '../lib/api';
import { formatScore, platformName } from '../lib/presentation';

export type ArchiveDocument = { id:string; title:string; eyebrow:string; value:string; unit:string; footer:string };
type Props = { topics: Planet[]; documents?:ArchiveDocument[]; opened: boolean; selectedId?: string | null; reducedMotion: boolean; onSelect: (id: string) => void; compact?: boolean; active?:boolean; quality?:'full'|'lite' };
const colors = ['#85e4d6', '#97aaf0', '#ffbd95'];
function wrap(ctx: CanvasRenderingContext2D, value: string, x: number, y: number, width: number, line: number, max = 3) {
 let row = ''; let n = 0;
 for (const char of value) { if (ctx.measureText(row + char).width > width && row) { ctx.fillText(row, x, y + n * line); row = ''; n++; if (n === max) return; } row += char; }
 ctx.fillText(row, x, y + n * line);
}
function print(topic: Planet | undefined, i: number, cover = false, sceneDocument?:ArchiveDocument) {
 const canvas = document.createElement('canvas'); canvas.width = 1024; canvas.height = cover ? 790 : 1400;
 const c = canvas.getContext('2d')!; const h = canvas.height;
 c.fillStyle = cover ? '#85e4d6' : colors[i % 3]; c.fillRect(0, 0, 1024, h);
 const ink = '#163c46'; c.fillStyle = ink; c.strokeStyle = ink;
 c.font = '500 29px "Pulse Sans", sans-serif'; c.fillText('TAPTAP  /  PULSE', 70, 84);
 c.textAlign = 'right'; c.fillText(cover ? 'INTELLIGENCE ARCHIVE' : `FILE ${String(i + 1).padStart(2, '0')}`, 954, 84); c.textAlign = 'left';
 c.lineWidth = 2; c.beginPath(); c.moveTo(70, 116); c.lineTo(954, 116); c.stroke();
 if (cover) {
  c.font = '850 154px "Pulse Sans", sans-serif'; c.fillText('热点', 65, 330); c.fillText('档案。', 65, 495);
  c.save(); c.translate(790, 460); c.rotate(-.18); c.lineWidth = 18; c.strokeRect(-93, -93, 186, 186); c.beginPath(); c.moveTo(-120, 0); c.lineTo(120, 0); c.moveTo(0, -120); c.lineTo(0, 120); c.stroke(); c.restore();
  c.font = '450 31px "Pulse Sans", sans-serif'; c.fillText('拆开热点，看见下一步。', 70, 660);
  c.font = '500 22px "Pulse Sans", sans-serif'; c.fillText('GAMES · EVIDENCE · POSSIBILITIES', 70, 725);
 } else {
  c.font = '550 36px "Pulse Sans", sans-serif'; c.fillText(sceneDocument?.eyebrow||topic?.gameName || '游戏情报', 70, 193);
  c.font = '760 65px "Pulse Sans", sans-serif'; wrap(c, sceneDocument?.title||topic?.title.split(' · ').slice(1).join(' · ') || topic?.title || '等待新的线索', 65, 293, 870, 82, 3);
  c.font = '850 350px "Pulse Sans", sans-serif'; c.fillText(sceneDocument?.value||formatScore(topic?.heat), 46, 865);
  c.font = '500 31px "Pulse Sans", sans-serif'; c.fillText(sceneDocument?.unit||'热度 / 100', 75, 925);
  c.lineWidth = 12; for (let k = 0; k < 8; k++) { c.beginPath(); c.moveTo(680 + k * 31, 575 + k * 14); c.lineTo(680 + k * 31, 850 - k * 14); c.stroke(); }
  c.lineWidth = 2; c.beginPath(); c.moveTo(70, 1020); c.lineTo(954, 1020); c.stroke();
  c.font = '450 28px "Pulse Sans", sans-serif'; wrap(c, topic?.platforms.map(platformName).join(' / ') || '来源等待补充', 70, 1080, 860, 44, 2);
  c.font = '650 39px "Pulse Sans", sans-serif'; c.fillText(sceneDocument?.footer||['发现线索', '整理证据', '长出策略'][i % 3], 70, 1250);
  c.font = '450 23px "Pulse Sans", sans-serif'; c.fillText('点击纸张，打开这份情报  ↗', 70, 1325);
 }
 const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = 4; return texture;
}
function Sheet({ topic, document:sheetDocument, index, open, selected, reduced, onSelect }: { topic: Planet; document?:ArchiveDocument; index: number; open: React.RefObject<number>; selected: boolean; reduced: boolean; onSelect: (id: string) => void }) {
 const ref = useRef<THREE.Group>(null); const [hover, setHover] = useState(false);
 const map = useMemo(() => print(topic, index,false,sheetDocument), [topic, index,sheetDocument]);
 const geometry = useMemo(() => { const g = new THREE.PlaneGeometry(2.25, 3.06, 16, 24); const p = g.attributes.position; for (let j = 0; j < p.count; j++) { const x = p.getX(j); const y = p.getY(j); p.setZ(j, .065 * Math.sin(y * 1.3 + index) * Math.pow(Math.abs(x) / 1.125, 2)); } g.computeVertexNormals(); return g; }, [index]);
 useEffect(() => () => { map.dispose(); geometry.dispose(); }, [map, geometry]);
 const { invalidate } = useThree();
 useEffect(() => invalidate(), [hover, selected, invalidate]);
 useFrame((state, dt) => {
  if (!ref.current) return;
  const t = open.current; const a = reduced ? 1 : 1 - Math.exp(-dt * 7);
  const targetX = [-1.9, .35, 2.45][index] * t;
  const targetY = .19 + index * .035 + t * [1.4, 2.25, 1.05][index] + ((hover || selected) && t > .6 ? .3 : 0);
  const targetZ = [.55, -.2, .7][index] * t;
  ref.current.position.lerp(new THREE.Vector3(targetX, targetY, targetZ), a);
  ref.current.rotation.y = THREE.MathUtils.lerp(ref.current.rotation.y, [-.28, .05, .28][index] * t, a);
  ref.current.rotation.x = THREE.MathUtils.lerp(ref.current.rotation.x, .55 * t, a);
  ref.current.rotation.z = THREE.MathUtils.lerp(ref.current.rotation.z, [-.15, .08, .15][index] * t, a);
  if (!reduced&&(ref.current.position.distanceTo(new THREE.Vector3(targetX,targetY,targetZ))>.001||Math.abs(ref.current.rotation.x-.55*t)>.001))invalidate();
 });
 return <group ref={ref} position={[0, .19 + index * .035, 0]}>
  <mesh geometry={geometry} rotation={[-Math.PI / 2, 0, 0]} castShadow receiveShadow onPointerOver={e => { e.stopPropagation(); setHover(true); document.body.style.cursor = 'pointer'; }} onPointerOut={() => { setHover(false); document.body.style.cursor = ''; }} onClick={e => { e.stopPropagation(); if (open.current > .6) onSelect(topic.id); }}>
   <meshPhysicalMaterial map={map} side={THREE.DoubleSide} roughness={.8} metalness={0} clearcoat={.15}/>
  </mesh>
  <mesh position={[0, -.017, 0]} castShadow><boxGeometry args={[2.25, .025, 3.06]}/><meshStandardMaterial color={colors[index]} roughness={.8}/></mesh>
  <mesh position={[.86, .003, -1.6]} castShadow><boxGeometry args={[.35, .02, .23]}/><meshStandardMaterial color={index === 1 ? '#85e4d6' : '#f1f2e9'} roughness={.5}/></mesh>
 </group>;
}
function Clip() {
 const geometry = useMemo(() => { const curve = new THREE.CatmullRomCurve3([new THREE.Vector3(-.16,0,-.38),new THREE.Vector3(-.16,0,.2),new THREE.Vector3(.12,0,.28),new THREE.Vector3(.17,0,-.38),new THREE.Vector3(-.06,0,-.43),new THREE.Vector3(-.07,0,.1),new THREE.Vector3(.04,0,.14),new THREE.Vector3(.06,0,-.27)]); return new THREE.TubeGeometry(curve, 40, .022, 8, false); }, []);
 useEffect(() => () => geometry.dispose(), [geometry]);
 return <mesh geometry={geometry} castShadow><meshStandardMaterial color="#62655c" metalness={.85} roughness={.25}/></mesh>;
}
function Archive({ topics, documents, opened, selectedId, reducedMotion, onSelect, input }: Props & { input: React.RefObject<{ x: number; y: number; rotation: number }> }) {
 const assembly = useRef<THREE.Group>(null); const lid = useRef<THREE.Group>(null); const open = useRef(reducedMotion ? Number(opened) : 0);
 const map = useMemo(() => print(undefined, 0, true), []);
 const { camera, invalidate } = useThree();
 useEffect(() => { camera.lookAt(0, 1.15, 0); invalidate(); }, [camera, invalidate]);
 useEffect(() => { invalidate(); if (reducedMotion) { const id = requestAnimationFrame(() => invalidate()); return () => cancelAnimationFrame(id); } }, [opened, selectedId, reducedMotion, invalidate]);
 useEffect(() => () => { map.dispose(); document.body.style.cursor = ''; }, [map]);
 useFrame((_, dt) => {
  const target = Number(opened); open.current = reducedMotion ? target : THREE.MathUtils.damp(open.current, target, 4.2, dt);
  if (lid.current) lid.current.rotation.x = -open.current * 1.8;
  if (assembly.current) { const a = reducedMotion ? 1 : 1 - Math.exp(-dt * 3.5); assembly.current.rotation.y = THREE.MathUtils.lerp(assembly.current.rotation.y, -.15 + input.current.rotation + input.current.x * .12, a); assembly.current.rotation.z = THREE.MathUtils.lerp(assembly.current.rotation.z, input.current.y * -.025, a); }
  if(!reducedMotion&&(Math.abs(open.current-target)>.001||assembly.current&&(Math.abs(assembly.current.rotation.y-(-.15+input.current.rotation+input.current.x*.12))>.001||Math.abs(assembly.current.rotation.z-input.current.y*-.025)>.001)))invalidate();
 }, -1);
 return <>
  <ambientLight intensity={1.5}/><hemisphereLight args={['#ffffff', '#afb49d', 1.3]}/>
  <directionalLight position={[-4, 10, 5]} intensity={3.4} castShadow shadow-mapSize={[1024,1024]} shadow-camera-left={-10} shadow-camera-right={10} shadow-camera-top={10} shadow-camera-bottom={-10} shadow-normalBias={.03}/>
  <directionalLight position={[6,3,-5]} intensity={1.3} color="#fff4db"/>
  <group ref={assembly} rotation={[0, -.15, 0]}>
   <mesh castShadow receiveShadow><boxGeometry args={[5.4,.09,4.1]}/><meshPhysicalMaterial color="#85e4d6" roughness={.6}/></mesh>
   <mesh position={[-1.6,.058,-2.14]} castShadow><boxGeometry args={[1.6,.025,.38]}/><meshStandardMaterial color="#85e4d6" roughness={.65}/></mesh>
   <mesh position={[0,.17,-2.05]} castShadow><boxGeometry args={[5.4,.3,.055]}/><meshStandardMaterial color="#85e4d6" roughness={.7}/></mesh>
   {topics.slice(0,3).map((p,i) => <Sheet key={p.id} topic={p} document={documents?.find(d=>d.id===p.id)} index={i} open={open} selected={p.id===selectedId} reduced={reducedMotion} onSelect={onSelect}/>)}
   <group ref={lid} position={[0,.34,-2.05]}>
    <mesh position={[0,0,2.05]} castShadow receiveShadow><boxGeometry args={[5.4,.035,4.1]}/><meshPhysicalMaterial color="#85e4d6" roughness={.62} clearcoat={.15}/></mesh>
    <mesh position={[0,.021,2.05]} rotation={[-Math.PI/2,0,0]}><planeGeometry args={[5.4,4.1]}/><meshStandardMaterial map={map} roughness={.7}/></mesh>
    <mesh position={[0,-.021,2.05]} rotation={[Math.PI/2,0,0]}><planeGeometry args={[5.4,4.1]}/><meshStandardMaterial map={map} roughness={.7}/></mesh>
    <group position={[1.95,.07,.33]} rotation={[0,.2,0]}><Clip/></group>
   </group>
  </group>
  <mesh rotation={[-Math.PI/2,0,0]} position={[0,-.6,0]} receiveShadow><planeGeometry args={[200,200]}/><shadowMaterial transparent opacity={.13}/></mesh>
 </>;
}
class Guard extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
 state = { failed: false }; static getDerivedStateFromError() { return { failed: true }; } render() { return this.state.failed ? this.props.fallback : this.props.children; }
}
export default function ArchiveStage(props: Props) {
 const [ready,setReady] = useState(false); const [lost,setLost] = useState(false); const input = useRef({x:0,y:0,rotation:0}); const drag = useRef<{x:number;y:number;rotation:number}|null>(null);const dragged=useRef(false); const invalidateRef=useRef<()=>void>(()=>{});
 useEffect(() => { let live=true; document.fonts.ready.then(()=>{if(live)setReady(true);});return()=>{live=false;document.body.style.cursor='';}; }, []);
 const fallback = <div className="archive-fallback">{props.topics.slice(0,3).map((p,i)=>{const sheet=props.documents?.find(d=>d.id===p.id);return <button key={p.id} onClick={()=>props.onSelect(p.id)} style={{background:colors[i],color:i===1?'#f1f2e9':'#20221d'}}><span>{sheet?.eyebrow||p.gameName}</span><strong>{sheet?.title||p.title}</strong><b>{sheet?.value||formatScore(p.heat)}</b><small>{sheet?.footer||'打开热点'} ↗</small></button>;})}</div>;
 return <div className={'archive-stage '+(props.compact?'archive-compact':'')} role="group" aria-label="可以拆开和旋转的3D热点档案" onPointerMove={e=>{if(props.reducedMotion)return;const box=e.currentTarget.getBoundingClientRect();input.current.x=(e.clientX-box.left)/box.width*2-1;input.current.y=(e.clientY-box.top)/box.height*2-1;if(drag.current){if(Math.hypot(e.clientX-drag.current.x,e.clientY-drag.current.y)>7)dragged.current=true;input.current.rotation=drag.current.rotation+(e.clientX-drag.current.x)*.005;}invalidateRef.current();}} onPointerDown={e=>{dragged.current=false;drag.current={x:e.clientX,y:e.clientY,rotation:input.current.rotation};}} onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;dragged.current=true;}} onPointerLeave={()=>{drag.current=null;input.current.x=0;input.current.y=0;invalidateRef.current();}} onDoubleClick={()=>{input.current.rotation=0;invalidateRef.current();}}>
  {ready&&!lost?<Guard fallback={fallback}><Canvas shadows={props.quality==='lite'?false:'soft'} dpr={props.quality==='lite'?1:[1,1.6]} frameloop={props.active===false?'never':'demand'} camera={{position:[5.4,6.4,11.3],fov:36,near:.1,far:100}} gl={{antialias:true,alpha:true,powerPreference:'high-performance'}} onCreated={({gl,invalidate})=>{invalidateRef.current=invalidate;gl.domElement.addEventListener('webglcontextlost',()=>setLost(true),{once:true});}}><Archive {...props} onSelect={id=>{if(!dragged.current)props.onSelect(id);}} reducedMotion={props.reducedMotion||props.active===false} input={input}/></Canvas></Guard>:ready?fallback:<div className="archive-loading">正在装订情报档案…</div>}
 </div>;
}
