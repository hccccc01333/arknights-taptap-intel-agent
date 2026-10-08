import { Component, createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Billboard, Line, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { Planet } from "../lib/api";

type SceneProps = {
  planets: Planet[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  view: "orbit" | "sphere";
  paused: boolean;
  resetKey: number;
  reducedMotion: boolean;
  focusId?: string | null;
  showConnections?: boolean;
  autoRotate?: boolean;
  quality?: "balanced" | "high";
};

type PositionedPlanet = { planet: Planet; position: [number, number, number]; size: number; color: string };
type LabelNodes = RefObject<Map<string, HTMLElement>>;
type PlatformHub = { id: string; name: string; color: string; position: [number, number, number]; count: number };
type SourceConnection = { id: string; hub: PlatformHub; item: PositionedPlanet };
type SourceNetwork = { hubs: PlatformHub[]; connections: SourceConnection[] };
const AQUA = "#b4e8d2";
const TEAL = "#8ccdb7";
const VIOLET = "#c9b6f7";
const CORAL = "#ffaf98";
const AMBER = "#f1ad99";
const SYSTEM_FONT = "var(--font-ui, 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', 'Noto Sans CJK SC', sans-serif)";
const GLOBAL_POSITION: [number, number, number] = [0.4, 6.2, 10];
const CAMERA_OPTIONS = { position: GLOBAL_POSITION, fov: 43, near: 0.1, far: 70 };
const GlowTextureContext = createContext<THREE.Texture | null>(null);
const SurfaceTextureContext = createContext<THREE.Texture | null>(null);
const PLATFORM_META: Record<string, { name: string; color: string }> = {
  taptap: { name: "TapTap", color: AQUA }, bilibili: { name: "哔哩哔哩", color: "#b9c5f0" },
  weibo: { name: "微博", color: CORAL }, baidu: { name: "百度", color: "#a9a4e5" },
  steam: { name: "Steam", color: "#6dacc3" }, douyin: { name: "抖音", color: "#b9a0d7" },
};
const normalizedPlatforms = (planet: Planet) => [...new Set(planet.platforms.map(p => p.trim().toLowerCase()).filter(Boolean))];

/** Every edge represents an explicit source entry. No topic-to-topic relation is inferred. */
function buildSourceNetwork(items: PositionedPlanet[], focusId: string | null, visible: boolean): SourceNetwork {
  if (!visible) return { hubs: [], connections: [] };
  const connected = focusId ? items.filter(item => item.planet.id === focusId) : items.slice(0, 4);
  const platforms = [...new Set(connected.flatMap(item => normalizedPlatforms(item.planet)))].sort((a, b) => a === "taptap" ? -1 : b === "taptap" ? 1 : a.localeCompare(b));
  const hubs: PlatformHub[] = platforms.map((platform, index) => {
    const angle = index / Math.max(1, platforms.length) * Math.PI * 2 + 0.32;
    return { id: platform, name: PLATFORM_META[platform]?.name ?? platform, color: PLATFORM_META[platform]?.color ?? "#83a5b0",
      position: [Math.cos(angle) * 5.55, -0.5, Math.sin(angle) * 5.55], count: connected.filter(item => normalizedPlatforms(item.planet).includes(platform)).length };
  });
  return { hubs, connections: connected.flatMap(item => normalizedPlatforms(item.planet).flatMap(platform => {
    const hub = hubs.find(candidate => candidate.id === platform);
    return hub ? [{ id: `${item.planet.id}:${platform}`, hub, item }] : [];
  })) };
}

function lifecycleColor(lifecycle: string | null) {
  const value = (lifecycle ?? "").toUpperCase();
  if (/DECAY|COOL|DECLIN|DORMANT|降温|衰退/.test(value)) return VIOLET;
  if (/PEAK|MATURE|爆发|高峰|成熟/.test(value)) return AMBER;
  return AQUA;
}

/** A deterministic layout keeps a selected topic stable when the view changes. */
function positionPlanets(planets: Planet[], view: SceneProps["view"], selectedId: string | null, focusId: string | null): PositionedPlanet[] {
  const sorted = [...planets].sort((a, b) => (b.relevance ?? 0) - (a.relevance ?? 0) || (b.heat ?? 0) - (a.heat ?? 0));
  const visible = sorted.slice(0, 24);
  for (const id of [...new Set([selectedId, focusId].filter(Boolean))]) {
    const selected = planets.find(p => p.id === id);
    if (selected && !visible.some(p => p.id === selected.id)) {
      let replace = visible.length - 1;
      while (replace >= 0 && (visible[replace].id === selectedId || visible[replace].id === focusId)) replace--;
      if (replace >= 0) visible[replace] = selected;
    }
  }
  const maxHeat = Math.max(0.01, ...visible.map((p) => p.heat ?? 0));
  const perRing = Math.max(1, Math.ceil(visible.length / 3));
  return visible.map((planet, index) => {
    const ring = Math.min(2, Math.floor(index / perRing));
    const ringCount = Math.min(perRing, visible.length - ring * perRing);
    const angle = (index % perRing) / ringCount * Math.PI * 2 + ring * 0.58 + 0.28;
    const radius = [2.35, 3.5, 4.7][ring];
    let position: PositionedPlanet["position"] = [Math.cos(angle) * radius, Math.sin(angle * 2 + ring) * 0.32 + (ring - 1) * 0.13, Math.sin(angle) * radius];
    if (view === "sphere") {
      const y = 1 - (index + 0.5) / Math.max(1, visible.length) * 2;
      const latitudeRadius = Math.sqrt(1 - y * y);
      const theta = index * Math.PI * (3 - Math.sqrt(5));
      const shellRadius = 3.5 + ring * 0.34;
      position = [Math.cos(theta) * latitudeRadius * shellRadius, y * shellRadius * 0.83, Math.sin(theta) * latitudeRadius * shellRadius];
    }
    return { planet, position, size: 0.15 + Math.sqrt(Math.max(0, planet.heat ?? 0) / maxHeat) * 0.24, color: lifecycleColor(planet.lifecycle) };
  });
}

function Ring({ radius, opacity = 0.18, color = TEAL, tilt = 0 }: { radius: number; opacity?: number; color?: string; tilt?: number }) {
  const points = useMemo(() => Array.from({ length: 129 }, (_, i) => {
    const a = i / 128 * Math.PI * 2;
    return [Math.cos(a) * radius, 0, Math.sin(a) * radius] as [number, number, number];
  }), [radius]);
  return <group rotation={[tilt, 0, 0]}><Line points={points} color={color} transparent opacity={opacity} lineWidth={0.75} /></group>;
}

function Glow({ color, scale, opacity }: { color: string; scale: number; opacity: number }) {
  const texture = useContext(GlowTextureContext);
  return texture ? <sprite scale={[scale, scale, 1]}><spriteMaterial map={texture} color={color} opacity={opacity} transparent depthWrite={false} blending={THREE.AdditiveBlending} /></sprite> : null;
}

function makeGlowTexture() {
    const surface = document.createElement("canvas");
    surface.width = 64;
    surface.height = 64;
    const context = surface.getContext("2d");
    if (context) {
      const gradient = context.createRadialGradient(32, 32, 0, 32, 32, 32);
      gradient.addColorStop(0, "rgba(255,255,255,.7)");
      gradient.addColorStop(0.2, "rgba(255,255,255,.28)");
      gradient.addColorStop(0.55, "rgba(255,255,255,.07)");
      gradient.addColorStop(1, "rgba(255,255,255,0)");
      context.fillStyle = gradient;
      context.fillRect(0, 0, 64, 64);
    }
    return new THREE.CanvasTexture(surface);
}

function Atmosphere({ radius, color, opacity = 0.25 }: { radius: number; color: string; opacity?: number }) {
  const uniforms = useMemo(() => ({ tint: { value: new THREE.Color(color) }, strength: { value: opacity } }), [color, opacity]);
  return <mesh><sphereGeometry args={[radius, 28, 20]} /><shaderMaterial uniforms={uniforms} transparent depthWrite={false} blending={THREE.AdditiveBlending}
    vertexShader="varying vec3 surfaceNormal; varying vec3 toEye; void main(){ vec4 p=modelViewMatrix*vec4(position,1.0); surfaceNormal=normalize(normalMatrix*normal); toEye=-p.xyz; gl_Position=projectionMatrix*p; }"
    fragmentShader="uniform vec3 tint; uniform float strength; varying vec3 surfaceNormal; varying vec3 toEye; void main(){ float edge=pow(1.0-max(dot(normalize(surfaceNormal),normalize(toEye)),0.0),2.7); gl_FragColor=vec4(tint,edge*strength); }" />
  </mesh>;
}

function IntelligenceCore({ moving }: { moving: boolean }) {
  const outer = useRef<THREE.Group>(null);
  const inner = useRef<THREE.Mesh>(null);
  useFrame((_, delta) => {
    if (!moving) return;
    if (outer.current) { outer.current.rotation.y += delta * 0.08; outer.current.rotation.z += delta * 0.025; }
    if (inner.current) inner.current.rotation.y -= delta * 0.11;
  });
  return <group>
    <Glow color={TEAL} scale={4.7} opacity={0.3} />
    <mesh><icosahedronGeometry args={[0.72, 1]} /><meshPhysicalMaterial color="#534264" metalness={0.58} roughness={0.18} clearcoat={1} clearcoatRoughness={0.16} emissive={TEAL} emissiveIntensity={0.15} flatShading /></mesh>
    <mesh ref={inner}><icosahedronGeometry args={[0.79, 1]} /><meshBasicMaterial color={AQUA} wireframe transparent opacity={0.54} /></mesh>
    <Atmosphere radius={0.84} color={AQUA} opacity={0.38} />
    <mesh rotation={[0.35, 0.7, 0.2]}><octahedronGeometry args={[0.94, 0]} /><meshBasicMaterial color="#b79cd4" wireframe transparent opacity={0.22} /></mesh>
    <group ref={outer} rotation={[0.42, 0.2, 0.18]}>
      <mesh rotation={[Math.PI / 2, 0, 0]}><torusGeometry args={[1.06, 0.011, 8, 100]} /><meshBasicMaterial color={AQUA} transparent opacity={0.8} /></mesh>
      <mesh rotation={[0.2, 0.45, 0.6]}><torusGeometry args={[1.22, 0.007, 6, 100]} /><meshBasicMaterial color={TEAL} transparent opacity={0.5} /></mesh>
      <mesh position={[1.06, 0, 0]}><sphereGeometry args={[0.055, 12, 12]} /><meshBasicMaterial color="#f7ddca" /></mesh>
      <mesh position={[-1.06, 0, 0]}><sphereGeometry args={[0.034, 10, 10]} /><meshBasicMaterial color={TEAL} /></mesh>
      {Array.from({ length: 12 }, (_, i) => <mesh key={i} position={[Math.cos(i / 12 * Math.PI * 2) * 1.38, 0, Math.sin(i / 12 * Math.PI * 2) * 1.38]} rotation={[0, -i / 12 * Math.PI * 2, 0]}>
        <boxGeometry args={[0.035, 0.012, 0.075]} /><meshBasicMaterial color={TEAL} transparent opacity={i % 3 === 0 ? 0.65 : 0.22} />
      </mesh>)}
    </group>
  </group>;
}

function SignalPlanet({ item, index, selected, focused, hovered, labelled, onSelect, onHover, moving, labelNodes, quality }: {
  item: PositionedPlanet; index: number; selected: boolean; focused: boolean; hovered: boolean; labelled: boolean; onSelect: SceneProps["onSelect"]; onHover: (id: string | null) => void; moving: boolean; labelNodes: LabelNodes; quality: "balanced" | "high";
}) {
  const mesh = useRef<THREE.Mesh>(null);
  const group = useRef<THREE.Group>(null);
  const phase = useRef(index * 0.72);
  const { gl, camera, size: viewportSize } = useThree();
  const labelPosition = useMemo(() => new THREE.Vector3(), []);
  const { planet, position, size, color } = item;
  const active = selected || focused || hovered;
  useFrame((_, delta) => {
    if (moving) {
      phase.current += delta * 0.35;
      if (mesh.current) mesh.current.rotation.y += delta * 0.1;
      if (group.current) group.current.position.y = position[1] + Math.sin(phase.current) * 0.07;
    }
    // One DOM tree owns all labels. Project without creating secondary React roots.
    const label = labelNodes.current.get(planet.id);
    if (label && group.current) {
      group.current.getWorldPosition(labelPosition);
      labelPosition.x += Math.sign(position[0]) * 0.15;
      labelPosition.y -= size + 0.34;
      labelPosition.project(camera);
      const visible = (labelled || active) && Math.abs(labelPosition.x) < 1 && Math.abs(labelPosition.y) < 0.86 && Math.abs(labelPosition.z) < 1;
      label.style.visibility = visible ? "visible" : "hidden";
      if (visible) {
        const halfWidth = Math.min(hovered ? 138 : 84, viewportSize.width * 0.43);
        const x = Math.max(halfWidth + 8, Math.min(viewportSize.width - halfWidth - 8, (labelPosition.x + 1) * viewportSize.width / 2));
        const y = (1 - labelPosition.y) * viewportSize.height / 2;
        label.style.transform = `translate3d(${x}px,${y}px,0) translate(-50%,-50%)`;
        label.style.borderColor = active ? `${color}77` : "#a485b633";
        label.style.background = active ? "#3b264aef" : "#281b36c9";
        label.style.color = active ? "#f3e5ff" : "#c7b0dc";
      }
    }
  });
  useEffect(() => { if (group.current) group.current.position.set(...position); }, [position]);
  useEffect(() => () => { gl.domElement.style.cursor = "grab"; }, [gl]);
  const pointer = (event: ThreeEvent<PointerEvent>, value: boolean) => {
    event.stopPropagation(); onHover(value ? planet.id : null); gl.domElement.style.cursor = value ? "pointer" : "grab";
  };
  return <group ref={group} position={position}>
    <group onPointerOver={(e) => pointer(e, true)} onPointerOut={(e) => pointer(e, false)} onClick={(e) => { e.stopPropagation(); onSelect(planet.id); }}>
      <mesh ref={mesh} scale={active ? 1.17 : 1}>
        <sphereGeometry args={[size, quality === "high" ? 44 : 28, quality === "high" ? 32 : 20]} />
        <meshPhysicalMaterial color={color} metalness={0.46} roughness={0.2} clearcoat={1} clearcoatRoughness={0.12} iridescence={0.3} emissive={color} emissiveIntensity={active ? 0.22 : 0.045} />
      </mesh>
      <mesh><sphereGeometry args={[size + 0.15, 12, 10]} /><meshBasicMaterial transparent opacity={0} depthWrite={false} /></mesh>
    </group>
    <Glow color={color} scale={size * (active ? 6.5 : 4.4)} opacity={active ? 0.53 : 0.2} />
    {quality === "high" && <Atmosphere radius={size * 1.08} color={color} opacity={active ? 0.37 : 0.2} />}
    {active && <Billboard><mesh><ringGeometry args={[size + 0.12, size + 0.135, 64]} /><meshBasicMaterial color={color} transparent opacity={0.85} side={THREE.DoubleSide} depthWrite={false} /></mesh></Billboard>}
  </group>;
}

const labelStyle: CSSProperties = {
  fontFamily: SYSTEM_FONT, fontSize: 10, fontWeight: 500, whiteSpace: "nowrap", padding: "6px 9px", border: "1px solid", borderRadius: 6,
  cursor: "pointer", backdropFilter: "blur(8px)", transition: "border-color 180ms, background 180ms", maxWidth: 168, outlineOffset: 3,
};

function StarDust({ quality }: { quality: "balanced" | "high" }) {
  const vertices = useMemo(() => {
    const count = quality === "high" ? 160 : 90;
    const points = new Float32Array(count * 3);
    let seed = 7319;
    const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
    for (let i = 0; i < count; i++) { points[i * 3] = (random() - 0.5) * 25; points[i * 3 + 1] = (random() - 0.5) * 14; points[i * 3 + 2] = -3 - random() * 13; }
    return points;
  }, [quality]);
  return <points><bufferGeometry><bufferAttribute attach="attributes-position" args={[vertices, 3]} /></bufferGeometry><pointsMaterial color="#84adba" size={0.018} transparent opacity={0.16} sizeAttenuation depthWrite={false} /></points>;
}

function PlatformMarker({ hub, labelNodes }: { hub: PlatformHub; labelNodes: LabelNodes }) {
  const group = useRef<THREE.Group>(null);
  const point = useMemo(() => new THREE.Vector3(), []);
  const { camera, size } = useThree();
  useFrame(() => {
    const label = labelNodes.current.get(`platform:${hub.id}`);
    if (!label || !group.current) return;
    group.current.getWorldPosition(point);
    point.y -= 0.25;
    point.project(camera);
    const visible = Math.abs(point.x) < 0.97 && Math.abs(point.y) < 0.86 && Math.abs(point.z) < 1;
    label.style.visibility = visible ? "visible" : "hidden";
    if (visible) label.style.transform = `translate3d(${(point.x + 1) * size.width / 2}px,${(1 - point.y) * size.height / 2}px,0) translate(-50%,-50%)`;
  });
  return <group ref={group} position={hub.position}>
    <Glow color={hub.color} scale={0.85} opacity={0.22} />
    <mesh rotation={[0.28, 0.4, 0]}><octahedronGeometry args={[0.115, 0]} /><meshStandardMaterial color={hub.color} emissive={hub.color} emissiveIntensity={0.25} metalness={0.35} roughness={0.45} /></mesh>
    <Billboard><mesh><ringGeometry args={[0.18, 0.19, 6]} /><meshBasicMaterial color={hub.color} transparent opacity={0.5} side={THREE.DoubleSide} /></mesh></Billboard>
  </group>;
}

function SourceLink({ connection, moving, focused }: { connection: SourceConnection; moving: boolean; focused: boolean }) {
  const beacon = useRef<THREE.Mesh>(null);
  const phase = useRef(0.18);
  const { curve, points } = useMemo(() => {
    const from = new THREE.Vector3(...connection.hub.position);
    const to = new THREE.Vector3(...connection.item.position);
    const middle = from.clone().lerp(to, 0.5);
    middle.y += 0.45 + from.distanceTo(to) * 0.04;
    const curve = new THREE.QuadraticBezierCurve3(from, middle, to);
    return { curve, points: curve.getPoints(28) };
  }, [connection]);
  useFrame((_, delta) => {
    if (!moving) return;
    phase.current = (phase.current + delta * 0.085) % 1;
    if (beacon.current) curve.getPoint(phase.current, beacon.current.position);
  });
  return <group>
    <Line points={points} color={connection.hub.color} transparent opacity={focused ? 0.35 : 0.13} lineWidth={focused ? 1 : 0.65} />
    <mesh ref={beacon} position={curve.getPoint(0.18)}><sphereGeometry args={[0.023, 8, 8]} /><meshBasicMaterial color={connection.hub.color} transparent opacity={focused ? 0.85 : 0.42} /></mesh>
  </group>;
}

function SceneContents({ items, view, selectedId, onSelect, onHover, hoveredId, moving, onContextLost, labelNodes, resetKey, focusId, network, autoRotate, reducedMotion, quality }: {
  items: PositionedPlanet[]; view: SceneProps["view"]; selectedId: string | null; onSelect: SceneProps["onSelect"]; onHover: (id: string | null) => void; hoveredId: string | null; moving: boolean; onContextLost: () => void; labelNodes: LabelNodes; resetKey: number;
  focusId: string | null; network: SourceNetwork; autoRotate: boolean; reducedMotion: boolean; quality: "balanced" | "high";
}) {
  const { gl } = useThree();
  const texture = useMemo(makeGlowTexture, []);
  useEffect(() => () => texture.dispose(), [texture]);
  const labelIndices = items.length > 7 ? [Math.ceil(items.length / 3) + 1, Math.min(items.length - 1, Math.ceil(items.length / 3) * 2 + 2)] : [0, Math.min(items.length - 1, 2)];
  useEffect(() => {
    const lost = (event: Event) => { event.preventDefault(); onContextLost(); };
    gl.domElement.addEventListener("webglcontextlost", lost);
    gl.domElement.style.cursor = "grab";
    return () => gl.domElement.removeEventListener("webglcontextlost", lost);
  }, [gl, onContextLost]);
  return <GlowTextureContext.Provider value={texture}>
    <ambientLight intensity={0.75} />
    <directionalLight position={[3, 7, 6]} intensity={1.55} color="#d1fff1" />
    <pointLight position={[0, 2.5, 2]} intensity={19} distance={16} color="#45deb7" />
    <pointLight position={[-5, 3, -3]} intensity={15} distance={18} color="#8c86ff" />
    <StarDust quality={quality} />
    <IntelligenceCore moving={moving} />
    {view === "orbit" ? <>
      <Ring radius={2.35} opacity={0.24} />
      <Ring radius={3.5} opacity={0.18} />
      <Ring radius={4.7} opacity={0.13} />
      <Ring radius={5.18} opacity={0.05} color="#7795af" />
    </> : <>
      <Ring radius={3.8} opacity={0.15} />
      <Ring radius={3.8} tilt={Math.PI / 2} opacity={0.09} />
      <group rotation={[0, 0, Math.PI / 2]}><Ring radius={3.8} opacity={0.09} /></group>
    </>}
    {network.connections.map(connection => <SourceLink key={connection.id} connection={connection} moving={moving} focused={Boolean(focusId)} />)}
    {network.hubs.map(hub => <PlatformMarker key={hub.id} hub={hub} labelNodes={labelNodes} />)}
    {items.map((item, index) => <SignalPlanet key={item.planet.id} item={item} index={index} labelled={labelIndices.includes(index)} selected={item.planet.id === selectedId} focused={item.planet.id === focusId} hovered={item.planet.id === hoveredId} onSelect={onSelect} onHover={onHover} moving={moving} labelNodes={labelNodes} quality={quality} />)}
    <SceneControls resetKey={resetKey} focusId={focusId} items={items} moving={moving} autoRotate={autoRotate} reducedMotion={reducedMotion} />
  </GlowTextureContext.Provider>;
}

function SceneControls({ resetKey, focusId, items, moving, autoRotate, reducedMotion }: { resetKey: number; focusId: string | null; items: PositionedPlanet[]; moving: boolean; autoRotate: boolean; reducedMotion: boolean }) {
  const controls = useRef<OrbitControlsImpl>(null);
  const { camera, size, invalidate } = useThree();
  const transition = useRef(false);
  const cameraGoal = useRef(new THREE.Vector3(...GLOBAL_POSITION));
  const targetGoal = useRef(new THREE.Vector3());
  const previousReset = useRef(resetKey);
  const suppressedFocus = useRef<string | null>(null);
  const item = items.find(candidate => candidate.planet.id === focusId);
  const [fx, fy, fz] = item?.position ?? [0, 0, 0];
  const begin = useCallback((focus: THREE.Vector3 | null) => {
    const aspect = size.width / Math.max(1, size.height);
    const fov = aspect < 1.2 ? 58 : 43;
    if (camera instanceof THREE.PerspectiveCamera) {
      camera.fov = fov;
      camera.updateProjectionMatrix();
    }
    targetGoal.current.copy(focus ?? new THREE.Vector3());
    const direction = focus ? camera.position.clone().sub(controls.current?.target ?? new THREE.Vector3()) : new THREE.Vector3(...GLOBAL_POSITION);
    if (direction.lengthSq() < 0.1) direction.set(...GLOBAL_POSITION);
    const distance = focus ? (aspect < 1.2 ? 7.4 : 6.1) : Math.max(new THREE.Vector3(...GLOBAL_POSITION).length(), 5.9 / (Math.tan(THREE.MathUtils.degToRad(fov / 2)) * aspect));
    cameraGoal.current.copy(targetGoal.current).add(direction.normalize().multiplyScalar(distance));
    if (controls.current) controls.current.autoRotate = false;
    transition.current = !reducedMotion;
    if (reducedMotion) {
      camera.position.copy(cameraGoal.current);
      controls.current?.target.copy(targetGoal.current);
      controls.current?.update();
    }
    invalidate();
  }, [camera, size.width, size.height, reducedMotion, invalidate]);

  useEffect(() => {
    // An explicit reset wins over an unchanged focusId until a new focus is requested.
    if (previousReset.current !== resetKey) {
      previousReset.current = resetKey;
      suppressedFocus.current = focusId;
      begin(null);
      return;
    }
    if (suppressedFocus.current !== focusId) suppressedFocus.current = null;
    begin(item && focusId !== suppressedFocus.current ? new THREE.Vector3(fx, fy, fz) : null);
  }, [focusId, resetKey, fx, fy, fz, Boolean(item), begin]);

  useFrame((_, delta) => {
    const control = controls.current;
    if (!control) return;
    control.autoRotate = autoRotate && moving && !transition.current;
    if (!transition.current) return;
    const alpha = 1 - Math.exp(-Math.min(delta, 0.06) * 5.5);
    camera.position.lerp(cameraGoal.current, alpha);
    control.target.lerp(targetGoal.current, alpha);
    control.update();
    if (camera.position.distanceToSquared(cameraGoal.current) + control.target.distanceToSquared(targetGoal.current) < 0.00002) {
      camera.position.copy(cameraGoal.current);
      control.target.copy(targetGoal.current);
      control.update();
      transition.current = false;
    } else invalidate();
  }, -0.5);
  return <OrbitControls ref={controls} makeDefault enablePan={false} enableDamping dampingFactor={0.065} autoRotate={autoRotate && moving} autoRotateSpeed={0.16}
    minDistance={3} maxDistance={24} minPolarAngle={0.25} maxPolarAngle={Math.PI * 0.8} rotateSpeed={0.55} zoomSpeed={0.6}
    onStart={() => { transition.current = false; }} />;
}

function FlatScene({ items, selectedId, focusId, onSelect, onHover, hoveredId, view, network }: {
  items: PositionedPlanet[]; selectedId: string | null; focusId: string | null; onSelect: SceneProps["onSelect"]; onHover: (id: string | null) => void; hoveredId: string | null; view: SceneProps["view"]; network: SourceNetwork;
}) {
  const glowId = useId().replace(/:/g, "");
  const positions = items.map((item, index) => {
    const count = Math.max(1, Math.ceil(items.length / 3));
    const ring = Math.min(2, Math.floor(index / count));
    const angle = index % count / Math.min(count, items.length - ring * count) * Math.PI * 2 + ring * 0.58 + 0.28;
    const radius = [95, 150, 215][ring];
    return { ...item, x: 400 + Math.cos(angle) * radius * 1.38, y: 205 + Math.sin(angle) * radius * (view === "sphere" ? 0.85 : 0.63) };
  });
  const hubs = network.hubs.map((hub, index) => ({ ...hub, x: 400 + Math.cos(index / network.hubs.length * Math.PI * 2 + 0.32) * 338, y: 205 + Math.sin(index / network.hubs.length * Math.PI * 2 + 0.32) * 174 }));
  const hover = items.find(item => item.planet.id === hoveredId);
  return <div style={{ position: "relative", width: "100%", height: "100%", minHeight: 300 }}>
    <svg viewBox="0 0 800 430" style={{ width: "100%", height: "100%", overflow: "visible" }} role="group" aria-label="TapTap 热点星图，二维兼容模式">
      <defs><radialGradient id={glowId}><stop offset="0" stopColor={TEAL} stopOpacity=".28" /><stop offset="1" stopColor={TEAL} stopOpacity="0" /></radialGradient></defs>
      <ellipse cx="400" cy="205" rx="140" ry="110" fill={`url(#${glowId})`} />
      {[95, 150, 215].map((r) => <ellipse key={r} cx="400" cy="205" rx={r * 1.38} ry={view === "sphere" ? r * 0.85 : r * 0.63} fill="none" stroke={TEAL} strokeWidth=".7" opacity=".2" />)}
      <polygon points="400,157 439,180 439,225 400,247 361,225 361,180" fill="#3d2a50" stroke={AQUA} strokeWidth=".8" />
      <path d="M400 157L400 247M361 180L439 225M439 180L361 225M361 180L400 202L439 180M361 225L400 202L439 225" stroke={AQUA} opacity=".65" strokeWidth=".6" />
      <text x="400" y="276" textAnchor="middle" fill="#e0fff7" fontFamily={SYSTEM_FONT} fontSize="14" fontWeight="700" letterSpacing="4">TAPTAP</text>
      <text x="400" y="293" textAnchor="middle" fill="#6a9b9a" fontFamily={SYSTEM_FONT} fontSize="7" letterSpacing="2">GROWTH INTELLIGENCE</text>
      {network.connections.map(connection => {
        const hub = hubs.find(h => h.id === connection.hub.id);
        const target = positions.find(item => item.planet.id === connection.item.planet.id);
        return hub && target ? <path key={connection.id} data-platform={hub.id} d={`M${hub.x},${hub.y} Q${(hub.x + target.x) / 2},${(hub.y + target.y) / 2 - 25} ${target.x},${target.y}`} stroke={hub.color} fill="none" opacity={focusId ? 0.42 : 0.2} strokeWidth=".7" /> : null;
      })}
      {hubs.map(hub => <g key={hub.id}><polygon points={`${hub.x},${hub.y - 6} ${hub.x + 6},${hub.y} ${hub.x},${hub.y + 6} ${hub.x - 6},${hub.y}`} fill={hub.color} opacity=".7" /><text x={hub.x} y={hub.y + 20} textAnchor="middle" fill={hub.color} opacity=".75" fontSize="9" fontFamily={SYSTEM_FONT}>{hub.name}</text></g>)}
      {positions.map(({ planet, color, size, x, y }, index) => {
        const active = planet.id === selectedId || planet.id === focusId;
        return <g key={planet.id} role="button" tabIndex={0} aria-label={`查看话题：${planet.title}`} aria-pressed={active} style={{ cursor: "pointer", outlineOffset: 4 }}
          onMouseEnter={() => onHover(planet.id)} onMouseLeave={() => onHover(null)} onFocus={() => onHover(planet.id)} onBlur={() => onHover(null)}
          onClick={() => onSelect(planet.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(planet.id); } }}>
          <title>{`${planet.title} · 热度 ${heatLabel(planet.heat)} · ${planet.platformCount} 个平台`}</title>
          <circle cx={x} cy={y} r={18} fill="transparent" />
          {active && <circle cx={x} cy={y} r={size * 23 + 6} stroke={color} strokeWidth="1" fill="none" opacity=".7" />}
          <circle cx={x} cy={y} r={size * 23} fill={color} />
          {(index === 0 || index === Math.floor(items.length / 2) || active) && <text x={x} y={y + 24} textAnchor="middle" fill={active ? "#e7fff6" : "#a5c2ca"} fontFamily={SYSTEM_FONT} fontSize="10">{planet.title.slice(0, 10)}{planet.title.length > 10 ? "…" : ""}</text>}
        </g>;
      })}
    </svg>
    {hover && <div style={{ position: "absolute", top: 12, left: "50%", transform: "translateX(-50%)", maxWidth: "80%", padding: "8px 12px", border: `1px solid ${hover.color}44`, borderRadius: 7, background: "#382446ee", color: "#dfc8ee", fontSize: 11, lineHeight: 1.5, pointerEvents: "none" }}><b>{hover.planet.title}</b><div style={{ color: "#b291c9", marginTop: 3 }}>热度 {heatLabel(hover.planet.heat)} · {hover.planet.platformCount} 个平台</div></div>}
    <span style={{ position: "absolute", right: 14, bottom: 13, color: "#61858f", fontSize: 10, fontFamily: SYSTEM_FONT }}>二维兼容模式 · 点击查看热点</span>
  </div>;
}

class SceneBoundary extends Component<{ fallback: ReactNode; children: ReactNode; resetKey: number }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(previous: { resetKey: number }) { if (previous.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false }); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function supportsWebGL() {
  if (typeof document === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("webgl2");
    if (!context) return false;
    context.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch { return false; }
}

const heatLabel = (value: number | null) => value == null ? "—" : `${Math.round(value * 100)} / 100`;

export default function SignalScene({ planets, selectedId, onSelect, view, paused, resetKey, reducedMotion, focusId = null, showConnections = true, autoRotate = true, quality = "balanced" }: SceneProps) {
  const [webgl, setWebgl] = useState(supportsWebGL);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const labelNodes = useRef(new Map<string, HTMLElement>());
  const items = useMemo(() => positionPlanets(planets, view, selectedId, focusId), [planets, view, selectedId, focusId]);
  const network = useMemo(() => buildSourceNetwork(items, focusId, showConnections), [items, focusId, showConnections]);
  const onContextLost = useCallback(() => setWebgl(false), []);
  useEffect(() => { if (hoveredId && !items.some(item => item.planet.id === hoveredId)) setHoveredId(null); }, [items, hoveredId]);
  const moving = !paused && !reducedMotion;
  const fallback = <FlatScene items={items} selectedId={selectedId} focusId={focusId} onSelect={onSelect} onHover={setHoveredId} hoveredId={hoveredId} view={view} network={network} />;
  return <div data-scene-focus={focusId ?? ""} data-scene-quality={quality} style={{ position: "relative", width: "100%", height: "100%", minHeight: 300, background: "radial-gradient(ellipse at 50% 48%, #48315b38 0%, #26192f28 42%, transparent 72%)" }}>
    <SceneBoundary fallback={fallback} resetKey={resetKey}>
      {webgl ? <><Canvas camera={CAMERA_OPTIONS} dpr={[1, quality === "high" ? 1.8 : 1.35]} frameloop={moving ? "always" : "demand"}
        gl={{ antialias: true, alpha: true, powerPreference: "low-power" }} fallback={fallback} style={{ position: "absolute", inset: 0 }}>
        <SceneContents items={items} view={view} selectedId={selectedId} onSelect={onSelect} onHover={setHoveredId} hoveredId={hoveredId} moving={moving} onContextLost={onContextLost} labelNodes={labelNodes} resetKey={resetKey} focusId={focusId} network={network} autoRotate={autoRotate} reducedMotion={reducedMotion} quality={quality} />
      </Canvas>
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "hidden", zIndex: 2 }}>
        {network.hubs.map(hub => <span key={`platform:${hub.id}`} data-platform-hub={hub.id} ref={(node) => { if (node) labelNodes.current.set(`platform:${hub.id}`, node); else labelNodes.current.delete(`platform:${hub.id}`); }}
          title={`${hub.name} · ${hub.count} 个当前连接话题`} style={{ position: "absolute", top: 0, left: 0, visibility: "hidden", color: hub.color, opacity: 0.7, fontFamily: SYSTEM_FONT, fontSize: 9, whiteSpace: "nowrap", letterSpacing: ".4px", padding: "3px 6px", background: "#2d1c3cc9", borderRadius: 4 }}>{hub.name}</span>)}
        {items.map(({ planet, color }) => <button key={planet.id} type="button" data-signal-label={planet.id} data-expanded={hoveredId === planet.id} ref={(node) => { if (node) labelNodes.current.set(planet.id, node); else labelNodes.current.delete(planet.id); }}
          onClick={() => onSelect(planet.id)} aria-label={`查看话题：${planet.title}`} title={planet.title}
          onMouseEnter={() => setHoveredId(planet.id)} onMouseLeave={() => setHoveredId(null)} onFocus={() => setHoveredId(planet.id)} onBlur={() => setHoveredId(null)}
          style={{ ...labelStyle, position: "absolute", top: 0, left: 0, visibility: "hidden", pointerEvents: "auto", color: "#c7b0dc", borderColor: "#a485b633", background: "#281b36c9", maxWidth: hoveredId === planet.id ? 276 : 168, width: hoveredId === planet.id ? "max-content" : undefined, whiteSpace: hoveredId === planet.id ? "normal" : "nowrap", textAlign: "left", lineHeight: 1.5, padding: hoveredId === planet.id ? "10px 12px" : "6px 9px" }}>
          <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", background: color, marginRight: 6, verticalAlign: "middle" }} />
          {hoveredId === planet.id ? planet.title : planet.title.length > 11 ? `${planet.title.slice(0, 11)}…` : planet.title}
          {hoveredId === planet.id && <span style={{ display: "block", marginTop: 5, color: "#83a8a7", fontSize: 9 }}>热度 {heatLabel(planet.heat)}<span style={{ opacity: 0.4, margin: "0 7px" }}>·</span>{planet.platformCount} 个平台</span>}
        </button>)}
      </div>
    <div aria-hidden="true" style={{ position: "absolute", left: "50%", bottom: 25, transform: "translateX(-50%)", width: 180, textAlign: "center", fontFamily: SYSTEM_FONT, pointerEvents: "none", userSelect: "none", zIndex: 1 }}>
      <div style={{ fontSize: 13, fontWeight: 750, letterSpacing: 4, color: "#ead7ff", textShadow: "0 0 20px #ba8edb50" }}>TAPTAP</div>
      <div style={{ color: "#709b98", fontSize: 7, letterSpacing: 1.8, marginTop: 5 }}>GROWTH INTELLIGENCE</div>
    </div></> : fallback}
    </SceneBoundary>
    {network.hubs.length > 0 && <span style={{ position: "absolute", top: 12, right: 15, fontFamily: SYSTEM_FONT, fontSize: 9, color: "#648b87", letterSpacing: ".5px", pointerEvents: "none" }}>平台来源 · {focusId ? "当前话题" : "相关度前 4 个话题"}</span>}
    {planets.length > 24 && <span style={{ position: "absolute", left: 14, top: 14, color: "#729497", fontFamily: SYSTEM_FONT, fontSize: 10, pointerEvents: "none" }}>展示关联度最高的 24 个话题 · 全部话题见列表</span>}
  </div>;
}
