/**
 * Universe —— 3D World。锚点 Industrial：平面、单一信号色、无圆角无阴影。
 * 空间感来自布局（相关性引力轴）与深度雾，不来自发光。
 */

import { useMemo, useRef, useState } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { T } from "../lib/tokens";
import type { Placed } from "../lib/layout";
import type { Idea } from "../lib/api";

/** 三态只用「信号色 + 明度差」，不引入第二色相 */
function planetColor(p: { relevanceLevel: string; lifecycle: string | null }): string {
  if (p.relevanceLevel === "L1") return T.color.tap;          // 真实相关性 → 全亮
  if (p.relevanceLevel === "L2") return T.color.tapDim;       // 推测 → 中亮
  return T.color.tx3;                                              // 未知 → 灰
}

/* ---------------- TapTap Core：原点，不是发光体 ---------------- */
function Core() {
  const ring = useRef<THREE.Mesh>(null!);
  useFrame((s) => { if (ring.current) ring.current.rotation.z = s.clock.elapsedTime * 0.05; });
  return (
    <group>
      <mesh>
        <icosahedronGeometry args={[T.scale.coreRadius, 1]} />
        <meshBasicMaterial color={T.color.surface} wireframe />
      </mesh>
      <mesh ref={ring}>
        <torusGeometry args={[T.scale.coreRadius * 1.35, 0.05, 6, 64]} />
        <meshBasicMaterial color={T.color.tap} />
      </mesh>
      {/* 轴线：可读的散点图基线 */}
      <mesh rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.035, 0.035, T.scale.axisSpan * 2, 6]} />
        <meshBasicMaterial color={T.color.lineHi} transparent opacity={0.5} />
      </mesh>
    </group>
  );
}

/* ---------------- 星球 ---------------- */
function Planet({ p, selected, dimmed, onSelect, onHover }: {
  p: Placed; selected: boolean; dimmed: boolean;
  onSelect: (id: string) => void; onHover: (id: string | null) => void;
}) {
  const { planet, pos } = p;
  const mesh = useRef<THREE.Mesh>(null!);
  const [hover, setHover] = useState(false);
  const k = useRef(1);
  const color = planetColor(planet);
  const rising = planet.lifecycle === "EMERGING";

  useFrame((s, dt) => {
    // 自转：起势=在转，降温=停（运动必须有语义）
    if (mesh.current && rising) mesh.current.rotation.y += dt * 0.25;
    const target = hover || selected ? 1.25 : 1;
    k.current += (target - k.current) * Math.min(1, dt * 10);
  });

  const r = planet.radius * (hover || selected ? 1.25 : 1);
  return (
    <group position={pos} scale={k.current}
      onPointerOver={(e) => { e.stopPropagation(); setHover(true); onHover(planet.id); }}
      onPointerOut={(e) => { e.stopPropagation(); setHover(false); onHover(null); }}
      onClick={(e) => { e.stopPropagation(); onSelect(planet.id); }}
    >
      <mesh ref={mesh}>
        <icosahedronGeometry args={[r, 1]} />
        {/* 真实相关=实心不透明；推测=半透；未知=线框 —— 状态一眼可分 */}
        {planet.relevanceLevel === "L1"
          ? <meshBasicMaterial color={color} />
          : planet.relevanceLevel === "L2"
            ? <meshBasicMaterial color={color} transparent opacity={0.42} wireframe />
            : <meshBasicMaterial color={color} wireframe transparent opacity={0.55} />}
      </mesh>
      {/* 参与度：外圈点阵，不用粒子发光 */}
      {planet.density > 0.35 && !dimmed && (
        <Satellites count={Math.round(planet.density * 10)} r={r * 1.9} seed={planet.id} />
      )}
    </group>
  );
}

/** 参与度：确定性分布的小方块（工业感的"数据点"，不是柔光粒子） */
function Satellites({ count, r, seed }: { count: number; r: number; seed: string }) {
  const pts = useMemo(() => {
    const out: [number, number, number][] = [];
    let h = 0;
    for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
    for (let i = 0; i < count; i++) {
      h = (h * 1103515245 + 12345) >>> 0;
      const th = (h / 4294967296) * Math.PI * 2;
      h = (h * 1103515245 + 12345) >>> 0;
      const ph = Math.acos(2 * (h / 4294967296) - 1);
      out.push([
        r * Math.sin(ph) * Math.cos(th),
        r * Math.sin(ph) * Math.sin(th) * 0.5,
        r * Math.cos(ph),
      ]);
    }
    return out;
  }, [count, r, seed]);
  return (
    <>
      {pts.map((p, i) => (
        <mesh key={i} position={p}>
          <boxGeometry args={[0.22, 0.22, 0.22]} />
          <meshBasicMaterial color={T.color.tap} transparent opacity={0.75} />
        </mesh>
      ))}
    </>
  );
}

/* ---------------- 信号管道：真实平台 → 星球 ---------------- */
const DIR: Record<string, [number, number, number]> = {
  bilibili: [0, 0.35, 1], weibo: [0, 0.2, -1], douyin: [0, -0.3, 0.6], taptap: [0, 0.55, 0],
};

function Pipes({ placed, activeId }: { placed: Placed[]; activeId: string | null }) {
  const lines = useMemo(() => {
    const out: { from: THREE.Vector3; to: THREE.Vector3; key: string }[] = [];
    placed.forEach((p) => {
      const list = p.planet.platforms.length ? p.planet.platforms : ["taptap"];
      list.forEach((pf) => {
        const d = DIR[pf] || DIR.taptap;
        out.push({
          from: new THREE.Vector3(d[0] * 150, d[1] * 120, d[2] * 150),
          to: new THREE.Vector3(...p.pos),
          key: `${pf}-${p.planet.id}`,
        });
      });
    });
    return out;
  }, [placed]);
  return (
    <group>
      {lines.map((l) => (
        <PipeLine key={l.key} from={l.from} to={l.to}
          on={!activeId || l.key.endsWith(activeId)} />
      ))}
    </group>
  );
}

function PipeLine({ from, to, on }: { from: THREE.Vector3; to: THREE.Vector3; on: boolean }) {
  const geo = useMemo(() => new THREE.BufferGeometry().setFromPoints([from, to]), [from, to]);
  const mat = useRef<THREE.LineBasicMaterial>(null!);
  const dot = useRef<THREE.Mesh>(null!);
  useFrame((s) => {
    if (mat.current) mat.current.opacity = on ? 0.22 : 0.03;
    if (dot.current && on) {
      const t = (s.clock.elapsedTime * 0.08) % 1;
      dot.current.position.lerpVectors(from, to, t);
    }
  });
  return (
    <>
      <line geometry={geo}>
        <lineBasicMaterial ref={mat} color={T.color.tapDim} transparent opacity={0.2} />
      </line>
      <mesh ref={dot} visible={on}>
        <boxGeometry args={[0.5, 0.5, 0.5]} />
        <meshBasicMaterial color={T.color.tap} />
      </mesh>
    </>
  );
}

/* ---------------- 创意卫星：绕星球公转 ---------------- */
export function IdeaSatellites({ pos, ideas, onOpen, dimmed }:
  { pos: [number, number, number]; ideas: Idea[]; onOpen: (id: string) => void; dimmed: boolean }) {
  const g = useRef<THREE.Group>(null!);
  useFrame((s) => {
    if (g.current) { g.current.rotation.y = s.clock.elapsedTime * 0.3; g.current.rotation.x = 0.3; }
  });
  if (!ideas.length || dimmed) return null;
  return (
    <group position={pos}>
      <group ref={g}>
        {ideas.slice(0, 6).map((idea, i) => {
          const a = (i / ideas.length) * Math.PI * 2;
          const r = 8 + (i % 2) * 2.5;
          return (
            <mesh key={idea.id}
              position={[Math.cos(a) * r, 1.6 + Math.sin(a * 1.7) * 1.2, Math.sin(a) * r]}
              onClick={(e) => { e.stopPropagation(); onOpen(idea.id); }}>
              <boxGeometry args={[1.1, 1.1, 1.1]} />
              <meshBasicMaterial color={T.color.tap} wireframe />
            </mesh>
          );
        })}
      </group>
    </group>
  );
}

/* ---------------- 相机：距离 = 信息层级 ---------------- */
export function CameraRig({ target, mode }: {
  target: [number, number, number] | null; mode: "universe" | "topic" | "idea";
}) {
  const { camera } = useThree();
  const goal = useRef(new THREE.Vector3(0, 0, 168));
  const look = useRef(new THREE.Vector3(0, 0, 0));
  const pos = useRef(new THREE.Vector3(0, 30, 168));
  useFrame((_, dt) => {
    if (target && mode !== "universe") {
      look.current.set(target[0] * 0.55, 0, 0);
      goal.current.set(target[0] * 0.3, 12, target[2] + (mode === "idea" ? 26 : 52));
    } else {
      look.current.set(0, 0, 0);
      goal.current.set(0, 26, 168);
    }
    pos.current.lerp(goal.current, Math.min(1, dt * 2));
    camera.position.copy(pos.current);
    camera.lookAt(look.current);
  });
  return null;
}

export function UniverseScene({
  placed, ideas, selectedId, hoveredId, mode, onSelect, onHover, onOpenIdea,
}: {
  placed: Placed[]; ideas: Record<string, Idea[]>;
  selectedId: string | null; hoveredId: string | null;
  mode: "universe" | "topic" | "idea";
  onSelect: (id: string) => void; onHover: (id: string | null) => void;
  onOpenIdea: (id: string) => void;
}) {
  const sel = placed.find((p) => p.planet.id === selectedId);
  const focus = mode === "universe" ? null : sel?.pos ?? null;
  return (
    <>
      <color attach="background" args={[T.color.bg]} />
      <fog attach="fog" args={[T.color.bg, 150, 420]} />
      <ambientLight intensity={0.7} />
      <directionalLight position={[30, 50, 20]} intensity={0.45} />
      <CameraRig target={focus} mode={mode} />
      <Core />
      {placed.map((p) => (
        <Planet key={p.planet.id} p={p} selected={p.planet.id === selectedId}
          dimmed={!!selectedId && p.planet.id !== selectedId}
          onSelect={onSelect} onHover={onHover} />
      ))}
      <Pipes placed={placed} activeId={mode === "universe" ? null : selectedId} />
      {sel && selectedId && (
        <IdeaSatellites pos={sel.pos} ideas={ideas[sel.planet.id] || []}
          onOpen={onOpenIdea} dimmed={false} />
      )}
    </>
  );
}
