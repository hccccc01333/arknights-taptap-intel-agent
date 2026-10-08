/**
 * 3D 卡片墙 —— TapTap 首页排版的 3D 版。
 *
 * 卡片 = 封面图板（竖版 3:4，同 TapTap）+ 下方信息条（标题/热度/相关性）。
 * 无封面的卡片用纯色卡（不放假图）。
 * 相关性决定它排在第几带（引力轴）—— 卡片墙同时是可读的散点图。
 */

import { useMemo, useRef, useState } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { T } from "../lib/tokens";
import { CARD, type Placed } from "../lib/layout";
import type { Idea } from "../lib/api";

function bandColor(level: "L1" | "L2" | "L3"): string {
  return level === "L1" ? T.color.tap : level === "L2" ? T.color.tapDim : T.color.tx3;
}

function Card({ p, selected, dimmed, onSelect, onHover }: {
  p: Placed; selected: boolean; dimmed: boolean;
  onSelect: (id: string) => void; onHover: (id: string | null) => void;
}) {
  const { planet, pos } = p;
  const g = useRef<THREE.Group>(null!);
  const [hover, setHover] = useState(false);
  const [tex, setTex] = useState<THREE.Texture | null>(null);
  const edge = bandColor(planet.relevanceLevel);

  useMemo(() => {
    if (!planet.cover) { setTex(null); return; }
    new THREE.TextureLoader().load(planet.cover, (t) => {
      t.colorSpace = THREE.SRGBColorSpace;
      setTex(t);
    }, undefined, () => setTex(null));
  }, [planet.cover]);

  useFrame((_, dt) => {
    if (!g.current) return;
    const target = selected ? 1.16 : hover ? 1.08 : 1;
    const cur = g.current.scale.x;
    const k = cur + (target - cur) * Math.min(1, dt * 10);
    g.current.scale.setScalar(k);
    // 正在起势的卡轻微浮沉（运动有语义）
    const float = planet.lifecycle === "EMERGING" ? Math.sin(Date.now() * 0.0012 + p.col) * 0.35 : 0;
    g.current.position.y = pos[1] + float + (hover || selected ? 1.4 : 0);
  });

  return (
    <group ref={g} position={pos}
      onPointerOver={(e) => { e.stopPropagation(); setHover(true); onHover(planet.id); }}
      onPointerOut={(e) => { e.stopPropagation(); setHover(false); onHover(null); }}
      onClick={(e) => { e.stopPropagation(); onSelect(planet.id); }}
    >
      {/* 底板：保证卡片轮廓始终可见（封面图未加载时也不至于消失） */}
      <mesh position={[0, CARD.h / 2 - 4, -0.1]}>
        <planeGeometry args={[CARD.w + 1.2, CARD.h + 1.2]} />
        <meshBasicMaterial color="#252A20" toneMapped={false} />
      </mesh>
      {/* 封面 */}
      <mesh position={[0, CARD.h / 2 - 4, 0]}>
        <planeGeometry args={[CARD.w, CARD.h]} />
        {tex ? <meshBasicMaterial map={tex} toneMapped={false} transparent={false} />
              : <meshBasicMaterial color={dimmed ? "#1A1E16" : "#2A3024"} toneMapped={false} />}
      </mesh>
      {/* 无封面时的诚实占位：一条对角纹，不画假图 */}
      {!tex && (
        <group position={[0, CARD.h / 2 - 4, 0.03]}>
          <mesh>
            <planeGeometry args={[CARD.w * 0.62, CARD.h * 0.035]} />
            <meshBasicMaterial color={T.color.tx3} toneMapped={false} />
          </mesh>
          <mesh position={[0, -CARD.h * 0.12, 0]}>
            <planeGeometry args={[CARD.w * 0.62, CARD.h * 0.012]} />
            <meshBasicMaterial color={T.color.lineHi} toneMapped={false} />
          </mesh>
        </group>
      )}
      {/* 信息条（TapTap 卡片下方的标题区） */}
      <mesh position={[0, -CARD.h / 2 + 1.2, 0]}>
        <planeGeometry args={[CARD.w, 5.4]} />
        <meshBasicMaterial color={T.color.surface} transparent opacity={dimmed ? 0.5 : 0.94} />
      </mesh>
      {/* 相关性标记：实心=实测 / 虚线感（低透明）=推测 / 极暗=未知 */}
      <mesh position={[-CARD.w / 2 + 1.4, -CARD.h / 2 + 1.2, 0.05]}>
        <planeGeometry args={[2.6, 2.6]} />
        <meshBasicMaterial color={edge}
          transparent opacity={planet.relevanceLevel === "L1" ? 1 : 0.5} />
      </mesh>
      {/* 热度条（长度=排名分位） */}
      <mesh position={[2, -CARD.h / 2 + 1.2, 0.05]}>
        <planeGeometry args={[Math.max(1, (planet.radius - 1) * 2.6), 1.2]} />
        <meshBasicMaterial color={T.color.tap} transparent opacity={dimmed ? 0.25 : 0.9} />
      </mesh>
      {/* 边框：1px 硬线（Industrial），选中时用品牌色 */}
      {([[0, CARD.h / 2 - 4, CARD.w], [0, -CARD.h / 2 + 1.2, CARD.w]] as const).map(([, y, w], i) => (
        <mesh key={i} position={[0, y, 0.04]}>
          <planeGeometry args={[w, 0.18]} />
          <meshBasicMaterial color={selected ? T.color.act : T.color.lineHi}
            transparent opacity={dimmed ? 0.2 : 0.75} />
        </mesh>
      ))}
      {([-CARD.w / 2, CARD.w / 2] as const).map((x, i) => (
        <mesh key={"v" + i} position={[x, CARD.h / 2 - 4 + (CARD.h - 5.4) / 2, 0.04]}>
          <planeGeometry args={[0.18, CARD.h - 5.4]} />
          <meshBasicMaterial color={selected ? T.color.act : T.color.lineHi}
            transparent opacity={dimmed ? 0.2 : 0.75} />
        </mesh>
      ))}
      {/* 方案数角标 */}
      {planet.nIdeas > 0 && (
        <mesh position={[CARD.w / 2 - 2.2, CARD.h / 2 - 2, 0.06]}>
          <planeGeometry args={[3.4, 3.4]} />
          <meshBasicMaterial color={T.color.act} transparent opacity={dimmed ? 0.3 : 1} />
        </mesh>
      )}
    </group>
  );
}

/* ---------------- 相机：滚轮推拉 + 点击推进（TapTap 的 scroll → dolly） ---------------- */
export function CameraRig({ target, offsetX, length, onScrollRef }: {
  target: [number, number, number] | null;
  offsetX: number;      // 长廊走位（滚轮/拖动）
  length: number;       // 长廊总长
  onScrollRef: (d: number) => void;
}) {
  const { camera, gl } = useThree();
  // 三排卡片（Y 跨度 48，X 每列 18.5）→ Z=190 时 FOV42° 可见约 145×82，容 7 列
  const DIST = 190;
  const goal = useRef(new THREE.Vector3(0, 0, DIST));
  const look = useRef(new THREE.Vector3(0, 0, 0));
  const pos = useRef(new THREE.Vector3(0, 0, DIST + 40));
  const focus = useRef<[number, number, number] | null>(null);

  useMemo(() => { focus.current = target; }, [target]);

  useMemo(() => {
    const el = gl.domElement;
    const onWheel = (e: WheelEvent) => { e.preventDefault(); onScrollX.current(e.deltaX + e.deltaY); };
    const onDown = (e: PointerEvent) => { dragX.current = e.clientX; dragging.current = true; };
    const onMove = (e: PointerEvent) => {
      if (!dragging.current) return;
      onScrollX.current((dragX.current - e.clientX) * 0.06);
      dragX.current = e.clientX;
    };
    const onUp = () => { dragging.current = false; };
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [gl]);

  const onScrollX = useRef((d: number) => {});
  useMemo(() => { onScrollX.current = onScrollRef; }, [onScrollRef]);

  useFrame((_, dt) => {
    if (focus.current) {
      const t = focus.current;
      goal.current.set(t[0], t[1] + 2, t[2] + 34);
      look.current.set(t[0], t[1], t[2]);
    } else {
      // 长廊走位：相机 X = 视口中心，永远从 offsetX（=滚轮量）开始
      // ★ 曾经的 bug：按 length/2 居中 → 相机跑到 650，而可见半宽只有 129 → 画面全黑
      const halfW = DIST * Math.tan((42 * Math.PI / 180) / 2) * (1600 / 902);
      const maxCx = Math.max(halfW, length - halfW);
      const cx = Math.max(halfW, Math.min(maxCx, offsetX + halfW));
      goal.current.set(cx, 0, DIST);
      look.current.set(cx, 0, 0);
    }
    pos.current.lerp(goal.current, Math.min(1, dt * 2.4));
    camera.position.copy(pos.current);
    camera.lookAt(look.current);
  });
  return null;
}

const dragX = { current: 0 } as { current: number };
const dragging = { current: false } as { current: boolean };
let onScrollRef: (d: number) => void = () => {};

export function CardWall({
  placed, ideas, selectedId, hoveredId, onSelect, onHover, onOpenIdea, onScroll, offsetX, length,
}: {
  placed: Placed[]; ideas: Record<string, Idea[]>;
  selectedId: string | null; hoveredId: string | null;
  onSelect: (id: string) => void; onHover: (id: string | null) => void;
  onOpenIdea: (id: string) => void; onScroll: (d: number) => void;
  offsetX: number; length: number;
}) {
  const sel = placed.find((p) => p.planet.id === selectedId);
  return (
    <>
      <color attach="background" args={[T.color.bg]} />
      {/* 不加雾：3D 长廊里雾会把中后段卡片整片吃掉（实测全黑） */}
      <ambientLight intensity={0.8} />
      <directionalLight position={[40, 60, 80]} intensity={0.4} />
      <CameraRig target={sel && selectedId ? [sel.pos[0], sel.pos[1], sel.pos[2]] : null}
        offsetX={offsetX} length={length} onScrollRef={(d: number) => onScroll(d)} />
      <group>
        {placed.map((p) => (
          <Card key={p.planet.id} p={p} selected={p.planet.id === selectedId}
            dimmed={!!selectedId && p.planet.id !== selectedId}
            onSelect={onSelect} onHover={onHover} />
        ))}
        {sel && selectedId && (
          <IdeaOrbit pos={[sel.pos[0], sel.pos[1], sel.pos[2]]}
            ideas={ideas[sel.planet.id] || []} onOpen={onOpenIdea} />
        )}
      </group>
    </>
  );
}

/* ---------------- 创意卫星：绕选中卡片公转 ---------------- */
function IdeaOrbit({ pos, ideas, onOpen }:
  { pos: [number, number, number]; ideas: Idea[]; onOpen: (id: string) => void }) {
  const g = useRef<THREE.Group>(null!);
  useFrame((s) => {
    if (g.current) { g.current.rotation.y = s.clock.elapsedTime * 0.35; g.current.rotation.x = 0.25; }
  });
  if (!ideas.length) return null;
  return (
    <group position={pos}>
      <group ref={g}>
        {ideas.slice(0, 6).map((idea, i) => {
          const a = (i / ideas.length) * Math.PI * 2;
          const r = 15 + (i % 2) * 4;
          return (
            <mesh key={idea.id} position={[Math.cos(a) * r, 4 + Math.sin(a * 1.6) * 3, Math.sin(a) * r]}
              onClick={(e) => { e.stopPropagation(); onOpen(idea.id); }}>
              <boxGeometry args={[2.4, 2.4, 2.4]} />
              <meshBasicMaterial color={T.color.act} wireframe />
            </mesh>
          );
        })}
      </group>
    </group>
  );
}
