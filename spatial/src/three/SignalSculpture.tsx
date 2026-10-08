import { Component, useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import type { Planet } from '../lib/api';
import { formatScore } from '../lib/presentation';

type SculptureMode = 'signal' | 'network' | 'strategy';

export type SignalSculptureProps = {
  progress: number;
  pointer: { x: number; y: number };
  reducedMotion: boolean;
  phase?: SculptureMode;
  mode?: SculptureMode;
  signals?: Planet[];
  onSignalSelect?: (id: string) => void;
  interactive?: boolean;
};

type SculptureMotion = { unfold: number; network: number; time: number; energy: number };
type SculptureInput = { x: number; y: number; vx: number; vy: number; dragging: boolean; hovered: boolean; invalidate: () => void };

const COLORS = ['#ffaf98', '#c9b6f7', '#b4e8d2'];
const EDGE_COLORS = ['#ffd5bd', '#e3d5ff', '#d3fff1'];
const LOOP_ROTATIONS = [new THREE.Euler(0.36, -0.5, -0.28), new THREE.Euler(1.02, 0.56, 0.47), new THREE.Euler(0.13, 1.17, -0.57)];
const LOOP_MATRICES = LOOP_ROTATIONS.map(rotation => new THREE.Matrix4().makeRotationFromEuler(rotation));
const INITIAL_CAMERA = { position: [0, 0, 12.8] as [number, number, number], fov: 38, near: 0.1, far: 100 };
const PROFILE = [[-1, 0.48], [-0.94, 1], [0.94, 1], [1, 0.48], [1, -0.48], [0.94, -1], [-0.94, -1], [-1, -0.48]];
const SIDES = PROFILE.length;
const STRIDE = SIDES * 3;
const clamp = (value: number, min = 0, max = 1) => Math.min(max, Math.max(min, Number.isFinite(value) ? value : 0));
const ease = (value: number) => { const t = clamp(value); return t * t * (3 - 2 * t); };
const phaseProgress = (progress: number, phase: SculptureMode | undefined) => clamp(phase === 'network' ? Math.max(progress, 0.43) : phase === 'strategy' ? Math.max(progress, 0.78) : progress);

/** All three forms share their vertices, so a choice reshapes the actual surface. */
function centerAt(u: number, strand: number, unfold: number, network: number, out: THREE.Vector3) {
  const angle = u * Math.PI * 2;
  const variation = strand * 0.71;
  const scale = [1, 0.94, 0.85][strand];
  out.set((Math.cos(angle) * 2.6 + Math.sin(angle * 3) * 0.17) * scale,
    (Math.sin(angle) * 1.62 + Math.sin(angle * 2) * 0.21) * scale,
    Math.sin(angle * 2 + variation) * 0.38);
  out.applyMatrix4(LOOP_MATRICES[strand]);
  out.x += [0, 0.18, -0.24][strand];
  out.y += [0.04, 0.12, -0.15][strand];
  const networkAngle = angle + strand * 0.18;
  const radius = 2.55 + Math.sin(angle * 3 + variation) * 0.11;
  const networkX = Math.cos(networkAngle) * radius;
  const networkY = (strand - 1) * 1.08 + Math.sin(networkAngle) * 0.52 + Math.cos(angle * 2 + variation) * 0.11;
  const networkZ = Math.sin(networkAngle) * 1.76 + Math.sin(angle * 2 + variation) * 0.16;
  out.set(THREE.MathUtils.lerp(out.x, networkX, network), THREE.MathUtils.lerp(out.y, networkY, network), THREE.MathUtils.lerp(out.z, networkZ, network));
  const x = (u - 0.5) * 8.9;
  const y = Math.sin(u * Math.PI * 2 + variation) * 0.83 + (strand - 1) * 0.92 + Math.sin(u * Math.PI) * 0.25;
  const z = Math.sin(u * Math.PI * 2 + variation + 0.6) * 0.92;
  out.set(THREE.MathUtils.lerp(out.x, x, unfold), THREE.MathUtils.lerp(out.y, y, unfold), THREE.MathUtils.lerp(out.z, z, unfold));
  // Give the transition a depth arc: opposite tangents otherwise nearly cancel
  // when a closed network ring opens into its long strategy stream.
  out.z += Math.cos(angle + variation) * Math.sin(unfold * Math.PI) * 0.8;
}

function createRibbon(segments: number, strand: number) {
  const geometry = new THREE.BufferGeometry();
  const positions = new Float32Array((segments + 1) * STRIDE);
  const colors = new Float32Array(positions.length);
  const uvs = new Float32Array((segments + 1) * SIDES * 2);
  const indices: number[] = [];
  const tint = new THREE.Color(COLORS[strand]);
  for (let step = 0; step <= segments; step++) {
    const u = step / segments;
    for (let corner = 0; corner < SIDES; corner++) {
      const offset = (step * SIDES + corner) * 3;
      const luminance = 0.87 + 0.11 * Math.sin(u * Math.PI * 2 + strand);
      colors[offset] = tint.r * luminance; colors[offset + 1] = tint.g * luminance; colors[offset + 2] = tint.b * luminance;
      uvs[(step * SIDES + corner) * 2] = u;
      uvs[(step * SIDES + corner) * 2 + 1] = (PROFILE[corner][0] + 1) / 2;
      if (step < segments) {
        const a = step * SIDES + corner, b = step * SIDES + (corner + 1) % SIDES, c = (step + 1) * SIDES + (corner + 1) % SIDES, d = (step + 1) * SIDES + corner;
        indices.push(a, b, d, b, c, d);
      }
    }
  }
  const last = segments * SIDES;
  for (let corner = 1; corner < SIDES - 1; corner++) {
    indices.push(0, corner + 1, corner, last, last + corner, last + corner + 1);
  }
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  const edgeGeometry = new THREE.BufferGeometry();
  edgeGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(segments * 4 * 2 * 3), 3));
  return { geometry, edgeGeometry };
}

/** Physical pearl/glass shading with continuous luminous channels along the ribbon. */
function createRibbonMaterial(strand: number) {
  const uniforms = { uSignalTime: { value: 0 }, uSignalEnergy: { value: 0 }, uSignalTint: { value: new THREE.Color(EDGE_COLORS[strand]) } };
  const material = new THREE.MeshPhysicalMaterial({
    vertexColors: true, color: '#ffffff', metalness: [0.28, 0.07, 0.16][strand], roughness: [0.2, 0.13, 0.17][strand],
    transmission: [0.12, 0.52, 0.3][strand], thickness: 0.22, ior: 1.42, clearcoat: 1, clearcoatRoughness: 0.12,
    iridescence: 0.46, iridescenceIOR: 1.24, iridescenceThicknessRange: [110, 310],
    attenuationColor: COLORS[strand], attenuationDistance: 2.8, emissive: COLORS[strand], emissiveIntensity: 0.018,
    envMapIntensity: 1.3, side: THREE.DoubleSide,
  });
  material.onBeforeCompile = shader => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader.replace('#include <common>', '#include <common>\nvarying vec2 vSignalUv;')
      .replace('#include <uv_vertex>', '#include <uv_vertex>\nvSignalUv = uv;');
    shader.fragmentShader = shader.fragmentShader.replace('#include <common>', '#include <common>\nvarying vec2 vSignalUv;\nuniform float uSignalTime;\nuniform float uSignalEnergy;\nuniform vec3 uSignalTint;')
      .replace('#include <color_fragment>', `#include <color_fragment>
        float signalTravel = fract(vSignalUv.x * 3.0 - uSignalTime * 0.095 + ${strand.toFixed(1)} * 0.19);
        float signalPulse = smoothstep(0.65, 0.91, signalTravel) * (1.0 - smoothstep(0.94, 1.0, signalTravel));
        float signalLanes = pow(0.5 + 0.5 * cos(vSignalUv.y * 43.9823), 18.0);
        float signalRim = smoothstep(0.41, 0.48, abs(vSignalUv.y - 0.5));
        diffuseColor.rgb = mix(diffuseColor.rgb, uSignalTint, signalPulse * (0.12 + signalLanes * 0.12));
      `)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
        totalEmissiveRadiance += uSignalTint * (signalPulse * (0.045 + signalLanes * 0.28) + signalRim * 0.025) * (1.0 + uSignalEnergy * 0.9);
      `);
  };
  material.customProgramCacheKey = () => `taptap-ribbon-flow-${strand}`;
  return { material, uniforms };
}

function Ribbon({ strand, motion, segments }: { strand: number; motion: RefObject<SculptureMotion>; segments: number }) {
  const { invalidate } = useThree();
  const { geometry, edgeGeometry } = useMemo(() => createRibbon(segments, strand), [segments, strand]);
  const { material, uniforms } = useMemo(() => createRibbonMaterial(strand), [strand]);
  const layer = useRef<THREE.Group>(null);
  const previous = useRef({ unfold: -1, network: -1 });
  const scratch = useMemo(() => ({ point: new THREE.Vector3(), before: new THREE.Vector3(), after: new THREE.Vector3(), tangent: new THREE.Vector3(), lastTangent: new THREE.Vector3(), normal: new THREE.Vector3(), binormal: new THREE.Vector3(), axis: new THREE.Vector3(), widthDirection: new THREE.Vector3(), depthDirection: new THREE.Vector3(), firstWidth: new THREE.Vector3(), lastWidth: new THREE.Vector3(), cornerOffset: new THREE.Vector3(), zAxis: new THREE.Vector3(0, 0, 1), yAxis: new THREE.Vector3(0, 1, 0) }), []);
  useEffect(() => {
    previous.current = { unfold: -1, network: -1 }; invalidate();
    return () => { geometry.dispose(); edgeGeometry.dispose(); };
  }, [geometry, edgeGeometry, invalidate]);
  useEffect(() => () => material.dispose(), [material]);
  useFrame(() => {
    const { unfold, network, time, energy } = motion.current;
    uniforms.uSignalTime.value = time;
    uniforms.uSignalEnergy.value = energy;
    if (layer.current) {
      layer.current.position.y = Math.sin(time * 0.31 + strand * 1.8) * 0.045;
      layer.current.rotation.y = Math.sin(time * 0.21 + strand) * 0.022;
    }
    if (Math.abs(previous.current.unfold - unfold) < 0.00008 && Math.abs(previous.current.network - network) < 0.00008) return;
    previous.current = { unfold, network };
    const positions = geometry.getAttribute('position') as THREE.BufferAttribute;
    const values = positions.array as Float32Array;
    const { point, before, after, tangent, lastTangent, normal, binormal, axis, widthDirection, depthDirection, firstWidth, lastWidth, cornerOffset, zAxis, yAxis } = scratch;
    for (let i = 0; i <= segments; i++) {
      const u = i / segments;
      centerAt(u, strand, unfold, network, point);
      centerAt(u - 0.0005, strand, unfold, network, before); centerAt(u + 0.0005, strand, unfold, network, after);
      tangent.subVectors(after, before).normalize();
      if (i === 0) {
        normal.crossVectors(zAxis, tangent).normalize();
        if (normal.lengthSq() < 0.5) normal.crossVectors(yAxis, tangent).normalize();
      } else {
        axis.crossVectors(lastTangent, tangent);
        if (axis.lengthSq() > 0.0000001) normal.applyAxisAngle(axis.normalize(), Math.acos(clamp(lastTangent.dot(tangent), -1, 1)));
      }
      binormal.crossVectors(tangent, normal).normalize();
      const twist = Math.sin(u * Math.PI * 4 + strand * 0.6) * (0.48 - unfold * 0.18) * (1 - network * 0.55) + strand * 0.11;
      widthDirection.copy(normal).multiplyScalar(Math.cos(twist)).addScaledVector(binormal, Math.sin(twist));
      depthDirection.crossVectors(tangent, widthDirection).normalize();
      const width = [0.78, 0.65, 0.56][strand] * (1 - unfold * 0.4) * (0.83 + 0.17 * Math.sin(u * Math.PI * 2 + strand) ** 2);
      const thickness = [0.085, 0.075, 0.07][strand];
      for (let corner = 0; corner < SIDES; corner++) {
        const offset = (i * SIDES + corner) * 3;
        const across = PROFILE[corner][0] * width / 2, depth = PROFILE[corner][1] * thickness / 2;
        values[offset] = point.x + widthDirection.x * across + depthDirection.x * depth;
        values[offset + 1] = point.y + widthDirection.y * across + depthDirection.y * depth;
        values[offset + 2] = point.z + widthDirection.z * across + depthDirection.z * depth;
      }
      lastTangent.copy(tangent);
    }
    const end = segments * STRIDE;
    firstWidth.set(values[6] - values[3], values[7] - values[4], values[8] - values[5]).normalize();
    lastWidth.set(values[end + 6] - values[end + 3], values[end + 7] - values[end + 4], values[end + 8] - values[end + 5]).normalize();
    firstWidth.addScaledVector(lastTangent, -firstWidth.dot(lastTangent)).normalize();
    // Only the closed initial surface needs transport compensation. Fade it out
    // before the open form can cross atan2's branch and suddenly flip its frame.
    const closureWeight = 1 - ease(unfold / 0.18);
    const closure = closureWeight > 0 ? Math.atan2(lastTangent.dot(axis.crossVectors(lastWidth, firstWidth)), lastWidth.dot(firstWidth)) * closureWeight : 0;
    if (Math.abs(closure) > 0.000001) for (let i = 1; i <= segments; i++) {
      const u = i / segments;
      centerAt(u, strand, unfold, network, point);
      centerAt(u - 0.0005, strand, unfold, network, before); centerAt(u + 0.0005, strand, unfold, network, after);
      tangent.subVectors(after, before).normalize();
      for (let corner = 0; corner < SIDES; corner++) {
        const offset = (i * SIDES + corner) * 3;
        cornerOffset.set(values[offset] - point.x, values[offset + 1] - point.y, values[offset + 2] - point.z).applyAxisAngle(tangent, closure * u);
        values[offset] = point.x + cornerOffset.x; values[offset + 1] = point.y + cornerOffset.y; values[offset + 2] = point.z + cornerOffset.z;
      }
    }
    positions.needsUpdate = true;
    geometry.computeVertexNormals();
    if (unfold < 0.000001) {
      const normals = geometry.getAttribute('normal') as THREE.BufferAttribute;
      const normalValues = normals.array as Float32Array;
      for (let corner = 0; corner < SIDES; corner++) {
        const first = corner * 3, last = end + first;
        normal.set(normalValues[first] + normalValues[last], normalValues[first + 1] + normalValues[last + 1], normalValues[first + 2] + normalValues[last + 2]).normalize();
        normalValues[first] = normalValues[last] = normal.x;
        normalValues[first + 1] = normalValues[last + 1] = normal.y;
        normalValues[first + 2] = normalValues[last + 2] = normal.z;
      }
      normals.needsUpdate = true;
    }
    geometry.computeBoundingSphere();
    const edge = edgeGeometry.getAttribute('position') as THREE.BufferAttribute;
    const edgeValues = edge.array as Float32Array;
    const edgeCorners = [1, 2, 5, 6];
    for (let i = 0; i < segments; i++) for (let side = 0; side < 4; side++) for (let endPoint = 0; endPoint < 2; endPoint++) {
      const from = ((i + endPoint) * SIDES + edgeCorners[side]) * 3, to = (i * 8 + side * 2 + endPoint) * 3;
      edgeValues[to] = values[from]; edgeValues[to + 1] = values[from + 1]; edgeValues[to + 2] = values[from + 2];
    }
    edge.needsUpdate = true; edgeGeometry.computeBoundingSphere();
  });
  return <group ref={layer}>
    <mesh geometry={geometry} material={material} frustumCulled={false} />
    <lineSegments geometry={edgeGeometry} frustumCulled={false}><lineBasicMaterial color={EDGE_COLORS[strand]} transparent opacity={0.2} blending={THREE.AdditiveBlending} depthWrite={false} /></lineSegments>
  </group>;
}

/** A local studio environment gives the glass crisp, shaped reflections. */
function StudioLight() {
  const { gl, scene, invalidate } = useThree();
  useEffect(() => {
    const surface = document.createElement('canvas'); surface.width = 1024; surface.height = 512;
    const context = surface.getContext('2d');
    if (!context) return;
    context.fillStyle = '#171021'; context.fillRect(0, 0, 1024, 512);
    const light = (x: number, y: number, rx: number, ry: number, color: string) => {
      context.save(); context.translate(x, y); context.scale(rx, ry);
      const gradient = context.createRadialGradient(0, 0, 0.08, 0, 0, 1); gradient.addColorStop(0, color); gradient.addColorStop(0.4, color); gradient.addColorStop(1, '#171021');
      context.fillStyle = gradient; context.fillRect(-1, -1, 2, 2); context.restore();
    };
    light(220, 150, 220, 70, '#fff4e5'); light(755, 145, 90, 185, '#e2d5ff'); light(480, 390, 320, 32, '#cefff1');
    light(80, 240, 140, 14, '#ffffff'); light(935, 270, 26, 170, '#fff9ef');
    const source = new THREE.CanvasTexture(surface); source.colorSpace = THREE.SRGBColorSpace; source.mapping = THREE.EquirectangularReflectionMapping;
    const generator = new THREE.PMREMGenerator(gl); const target = generator.fromEquirectangular(source);
    const previous = scene.environment; scene.environment = target.texture; invalidate();
    return () => { scene.environment = previous; target.dispose(); generator.dispose(); source.dispose(); };
  }, [gl, scene, invalidate]);
  return null;
}

function SculptureScene({ progress, pointer, reducedMotion, phase, mode, input, onLost, beaconNodes, signals = [] }: SignalSculptureProps & { input: RefObject<SculptureInput>; onLost: () => void; beaconNodes: RefObject<Map<string, HTMLButtonElement>> }) {
  const { camera, size, gl, invalidate } = useThree();
  const group = useRef<THREE.Group>(null);
  const lamp = useRef<THREE.PointLight>(null);
  const motion = useRef<SculptureMotion>({ unfold: 0, network: 0, time: 0, energy: 0 });
  const mobile = size.width < 720;
  const targetUnfold = mode === 'strategy' ? 1 : mode ? 0 : ease((phaseProgress(progress, phase) - 0.05) / 0.84);
  const targetNetwork = mode === 'network' ? 1 : 0;
  useEffect(() => {
    input.current.invalidate = invalidate;
    const lost = (event: Event) => { event.preventDefault(); onLost(); };
    gl.domElement.addEventListener('webglcontextlost', lost);
    return () => { input.current.invalidate = () => {}; gl.domElement.removeEventListener('webglcontextlost', lost); };
  }, [gl, input, onLost, invalidate]);
  useEffect(() => { invalidate(); }, [progress, pointer.x, pointer.y, phase, mode, reducedMotion, invalidate]);
  useEffect(() => {
    // A cold demand canvas gets another frame after effects build its surfaces
    // and local reflection texture; it never depends on an animation loop.
    let followUp = 0;
    const first = requestAnimationFrame(() => { invalidate(); followUp = requestAnimationFrame(invalidate); });
    return () => { cancelAnimationFrame(first); cancelAnimationFrame(followUp); };
  }, [invalidate]);
  useFrame((_, delta) => {
    const elapsed = Math.min(delta, 0.06);
    const alpha = reducedMotion ? 1 : 1 - Math.exp(-elapsed * 3.8);
    const state = motion.current;
    // A network/stream switch first gathers into the woven core. This keeps the
    // two deformation families separate, even when choices change mid-morph.
    const gathering = !reducedMotion && ((targetNetwork > 0 && state.unfold > 0) || (targetUnfold > 0 && state.network > 0));
    const nextUnfold = gathering ? 0 : targetUnfold, nextNetwork = gathering ? 0 : targetNetwork;
    const morphAlpha = reducedMotion ? 1 : 1 - Math.exp(-elapsed * 5.5);
    state.unfold = THREE.MathUtils.lerp(state.unfold, nextUnfold, morphAlpha);
    state.network = THREE.MathUtils.lerp(state.network, nextNetwork, morphAlpha);
    if (nextUnfold === 0 && state.unfold < 0.003) state.unfold = 0;
    if (nextNetwork === 0 && state.network < 0.003) state.network = 0;
    state.energy = THREE.MathUtils.lerp(state.energy, input.current.dragging ? 1 : input.current.hovered ? 0.45 : 0, alpha);
    if (!reducedMotion) {
      state.time += elapsed;
      if (!input.current.dragging) {
        input.current.x = clamp(input.current.x + input.current.vx * elapsed, -1, 1);
        input.current.y = clamp(input.current.y + input.current.vy * elapsed, -2.2, 2.2);
        input.current.vx *= Math.exp(-elapsed * 6); input.current.vy *= Math.exp(-elapsed * 6);
      }
    } else if (!input.current.dragging) {
      input.current.vx = input.current.vy = 0;
    }
    const aspect = size.width / Math.max(1, size.height);
    const verticalHalfFov = THREE.MathUtils.degToRad(19);
    const horizontalHalfFov = Math.atan(Math.tan(verticalHalfFov) * aspect);
    const radius = THREE.MathUtils.lerp(3.5, 5.3, state.unfold);
    const sculptureX = mobile ? 0.02 : THREE.MathUtils.lerp(0.6, 1.4, state.unfold);
    const cameraDistance = Math.max(12.8, radius / Math.sin(verticalHalfFov) * 1.06, (radius + Math.abs(sculptureX)) / Math.sin(horizontalHalfFov) * 1.035);
    camera.position.z = THREE.MathUtils.lerp(camera.position.z, cameraDistance, alpha);
    const desiredFar = Math.max(100, cameraDistance + radius * 3);
    if (camera instanceof THREE.PerspectiveCamera && Math.abs(camera.far - desiredFar) > 0.5) {
      camera.far = desiredFar; camera.updateProjectionMatrix();
    }
    if (group.current) {
      const desiredX = sculptureX + clamp(pointer.x, -1, 1) * 0.08;
      group.current.position.x = THREE.MathUtils.lerp(group.current.position.x, desiredX, alpha);
      group.current.position.y = THREE.MathUtils.lerp(group.current.position.y, (mobile ? 0.45 : -0.05) - clamp(pointer.y, -1, 1) * 0.055, alpha);
      group.current.rotation.x = THREE.MathUtils.lerp(group.current.rotation.x, -0.12 + clamp(pointer.y, -1, 1) * 0.16 + input.current.x + (reducedMotion ? 0 : Math.sin(state.time * 0.17) * 0.045), alpha);
      group.current.rotation.y = THREE.MathUtils.lerp(group.current.rotation.y, -0.16 + clamp(pointer.x, -1, 1) * 0.28 + input.current.y + (reducedMotion ? 0 : Math.sin(state.time * 0.11) * 0.11), alpha);
      group.current.rotation.z = THREE.MathUtils.lerp(group.current.rotation.z, 0.035 + (reducedMotion ? 0 : Math.sin(state.time * 0.14) * 0.03), alpha);
    }
    if (lamp.current) lamp.current.position.x = 4 + clamp(pointer.x, -1, 1) * 1.8;
  }, -0.5);
  return <>
    <StudioLight />
    <ambientLight intensity={0.42} color="#ece0fb" />
    <directionalLight position={[2.5, 5, 7]} intensity={2.4} color="#ffecde" />
    <directionalLight position={[-5, 1, -1]} intensity={1.7} color="#bba5ff" />
    <pointLight ref={lamp} position={[4, -2, 5]} intensity={20} color="#ddffe8" distance={18} />
    <group ref={group} position={[0.6, -0.05, 0]}><PulseLens motion={motion} reducedMotion={reducedMotion}/>{[0, 1, 2].map(strand => <Ribbon key={strand} strand={strand} motion={motion} segments={mobile ? 104 : 192} />)}{signals.map((topic,index)=><SignalBeacon key={topic.id} topic={topic} index={index} nodes={beaconNodes} motion={motion} reducedMotion={reducedMotion}/>)}</group>
  </>;

}

function PulseLens({motion,reducedMotion}:{motion:RefObject<SculptureMotion>;reducedMotion:boolean}) {
 const ref=useRef<THREE.Mesh>(null);const mat=useRef<THREE.MeshPhysicalMaterial>(null);
 const geometry=useMemo(()=>{const g=new THREE.SphereGeometry(1.17,48,36);const p=g.getAttribute('position');for(let i=0;i<p.count;i++){const x=p.getX(i),y=p.getY(i),z=p.getZ(i);const angle=Math.atan2(z,x);const ripple=1+Math.sin(angle*5+y*1.4)*.065;p.setXYZ(i,x*ripple,y*.84,z*ripple);}g.computeVertexNormals();return g;},[]);
 useEffect(()=>()=>geometry.dispose(),[geometry]);
 useFrame(()=>{if(!ref.current)return;const state=motion.current;const s=Math.max(.015,(1-state.unfold)*(.82+state.network*.2));ref.current.scale.setScalar(s);if(!reducedMotion){ref.current.rotation.y=state.time*.085;ref.current.rotation.z=Math.sin(state.time*.12)*.09;}if(mat.current)mat.current.emissiveIntensity=.04+state.energy*.07;});
 return <group><mesh ref={ref} position={[.15,.04,-.25]} geometry={geometry}><meshPhysicalMaterial ref={mat} color="#b8a7dc" transmission={.58} thickness={2.2} metalness={.12} roughness={.14} ior={1.38} clearcoat={1} clearcoatRoughness={.08} iridescence={.9} iridescenceIOR={1.3} iridescenceThicknessRange={[200,650]} attenuationColor="#b7a0cf" attenuationDistance={2.5} emissive="#9470ac" emissiveIntensity={.04}/></mesh></group>;
}

const BEACON_POSITIONS:[number,number,number][]=[[-2.85,.5,1.8],[2.8,1.5,.3],[2.65,-1.45,-.2]];
function SignalBeacon({topic,index,nodes,motion,reducedMotion}:{topic:Planet;index:number;nodes:RefObject<Map<string,HTMLButtonElement>>;motion:RefObject<SculptureMotion>;reducedMotion:boolean}) {
 const ref=useRef<THREE.Group>(null);const point=useMemo(()=>new THREE.Vector3(),[]);const {camera,size}=useThree();const base=BEACON_POSITIONS[index%3];
 useFrame(()=>{const node=nodes.current.get(topic.id);if(!node||!ref.current)return;ref.current.position.y=base[1]+(reducedMotion?0:Math.sin(motion.current.time*.6+index)*.085);ref.current.getWorldPosition(point);point.y-=.18;point.project(camera);const visible=Math.abs(point.z)<1&&Math.abs(point.y)<.7&&(size.width>720||index===1);node.style.visibility=visible?'visible':'hidden';if(visible){const half=size.width<720?84:108;const x=Math.max(half+16,Math.min(size.width-half-16,(point.x+1)*size.width/2));node.style.transform=`translate3d(${x}px,${(1-point.y)*size.height/2}px,0) translate(-50%,-50%)`;}});
 return <group ref={ref} position={base}><mesh><octahedronGeometry args={[.095,1]}/><meshPhysicalMaterial color={EDGE_COLORS[index%3]} roughness={.16} metalness={.5} emissive={COLORS[index%3]} emissiveIntensity={.22}/></mesh><mesh rotation={[Math.PI/2,.2,0]}><torusGeometry args={[.17,.004,5,44]}/><meshBasicMaterial color={COLORS[index%3]} transparent opacity={.45}/></mesh></group>;
}

function StaticSculpture({ progress, mode }: { progress: number; mode?: SculptureMode }) {
  const unique = useMemo(() => `sculpture-${Math.random().toString(36).slice(2, 9)}`, []);
  const unfolded = mode === 'strategy' || (!mode && progress > 0.5);
  const curves = mode === 'network' ? [
    'M550 370C550 260 1185 255 1185 370C1185 495 550 495 550 370',
    'M550 510C550 405 1185 400 1185 510C1185 635 550 635 550 510',
    'M550 650C550 540 1185 535 1185 650C1185 775 550 775 550 650',
  ] : unfolded ? ['M220 455C415 205 795 720 1145 360', 'M230 560C550 315 715 675 1150 445', 'M245 650C555 420 850 765 1170 525'] : [
    'M690 595C485 540 590 185 875 265C1155 345 1030 805 690 595',
    'M655 660C405 340 865 115 1065 495C1245 840 785 815 655 660',
    'M1020 665C1250 615 1115 185 870 350C600 535 695 875 1020 665',
  ];
  return <svg viewBox="0 0 1440 1000" preserveAspectRatio="xMidYMid meet" width="100%" height="100%" aria-hidden="true">
    <defs>{COLORS.map((color, i) => <linearGradient key={color} id={`${unique}-${i}`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor={color} /><stop offset=".42" stopColor={EDGE_COLORS[i]} /><stop offset=".58" stopColor={color} /><stop offset="1" stopColor={COLORS[(i + 1) % 3]} /></linearGradient>)}</defs>
    {curves.map((d, i) => <g key={i}><path d={d} fill="none" stroke="#090710" strokeWidth={unfolded ? 24 : 58} opacity=".2" transform="translate(4 8)" /><path d={d} fill="none" stroke={`url(#${unique}-${i})`} strokeWidth={unfolded ? 21 : [46, 39, 34][i]} strokeLinecap="round" opacity=".92" /><path d={d} fill="none" stroke={EDGE_COLORS[i]} strokeWidth="1" opacity=".35" /></g>)}
  </svg>;
}

class SculptureBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function supportsWebGL() {
  if (typeof document === 'undefined') return false;
  try {
    const canvas = document.createElement('canvas'); const context = canvas.getContext('webgl2');
    if (!context) return false;
    context.getExtension('WEBGL_lose_context')?.loseContext(); return true;
  } catch { return false; }
}

export default function SignalSculpture(props: SignalSculptureProps) {
  const beaconNodes=useRef(new Map<string,HTMLButtonElement>());
  const [webgl, setWebgl] = useState(supportsWebGL);
  const container = useRef<HTMLDivElement>(null);
  const input = useRef<SculptureInput>({ x: 0, y: 0, vx: 0, vy: 0, dragging: false, hovered: false, invalidate: () => {} });
  const onLost = useCallback(() => setWebgl(false), []);
  useEffect(() => {
    const state = input.current;
    if (!props.interactive || !webgl) { state.dragging = false; state.hovered = false; state.vx = state.vy = 0; state.invalidate(); return; }
    let active: { id: number; startX: number; startY: number; rotationX: number; rotationY: number; lastX: number; lastY: number; time: number; target: Element | null } | null = null;
    const blocked = (target: EventTarget | null) => target instanceof Element && !!target.closest('a,button,input,textarea,select,[role="button"],[data-sculpture-ignore]');
    const hit = (event: PointerEvent | MouseEvent) => {
      if (blocked(event.target) || !container.current) return false;
      const bounds = container.current.getBoundingClientRect();
      const cx = bounds.left + bounds.width * 0.58, cy = bounds.top + bounds.height * 0.49;
      const rx = Math.min(bounds.width * 0.34, bounds.height * 0.41), ry = bounds.height * 0.32;
      return bounds.width > 0 && bounds.height > 0 && ((event.clientX - cx) / rx) ** 2 + ((event.clientY - cy) / ry) ** 2 < 1;
    };
    const finish = () => {
      const target = active?.target, id = active?.id;
      active = null; state.dragging = false;
      if (target && id !== undefined && target.hasPointerCapture?.(id)) target.releasePointerCapture(id);
      state.invalidate();
    };
    const down = (event: PointerEvent) => {
      if (event.button !== 0 || active || !hit(event)) return;
      active = { id: event.pointerId, startX: event.clientX, startY: event.clientY, rotationX: state.x, rotationY: state.y, lastX: event.clientX, lastY: event.clientY, time: event.timeStamp, target: event.target instanceof Element ? event.target : null };
      state.dragging = true; state.vx = state.vy = 0;
      try { active.target?.setPointerCapture?.(event.pointerId); } catch { /* The hit element can leave the DOM during navigation. */ }
      state.invalidate();
    };
    const move = (event: PointerEvent) => {
      state.hovered = hit(event);
      if (active && active.id === event.pointerId && container.current) {
        const dx = event.clientX - active.startX, dy = event.clientY - active.startY;
        if (event.pointerType === 'touch' && Math.abs(dy) > 9 && Math.abs(dy) > Math.abs(dx) * 1.25) { state.vx = state.vy = 0; finish(); return; }
        const bounds = container.current.getBoundingClientRect();
        const priorX = state.x, priorY = state.y;
        state.x = clamp(active.rotationX + dy / Math.max(260, bounds.height) * 2.5, -1, 1);
        state.y = clamp(active.rotationY + dx / Math.max(280, bounds.width) * 3.6, -2.2, 2.2);
        const seconds = Math.max(0.008, (event.timeStamp - active.time) / 1000);
        state.vx = clamp((state.x - priorX) / seconds, -1.6, 1.6) * 0.6;
        state.vy = clamp((state.y - priorY) / seconds, -2, 2) * 0.6;
        active.time = event.timeStamp; active.lastX = event.clientX; active.lastY = event.clientY;
      }
      state.invalidate();
    };
    const up = (event: PointerEvent) => { if (active?.id === event.pointerId) finish(); };
    const cancel = () => { state.vx = state.vy = 0; finish(); };
    const lostCapture = (event: PointerEvent) => { if (active?.id === event.pointerId) cancel(); };
    const reset = (event: MouseEvent) => { if (!hit(event)) return; state.x = state.y = state.vx = state.vy = 0; finish(); };
    window.addEventListener('pointerdown', down);
    window.addEventListener('pointermove', move, { passive: true });
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
    window.addEventListener('lostpointercapture', lostCapture);
    window.addEventListener('blur', cancel);
    window.addEventListener('dblclick', reset);
    return () => {
      finish(); state.hovered = false; state.vx = state.vy = 0;
      window.removeEventListener('pointerdown', down); window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', cancel);
      window.removeEventListener('lostpointercapture', lostCapture);
      window.removeEventListener('blur', cancel); window.removeEventListener('dblclick', reset);
    };
  }, [props.interactive, webgl]);
  const fallback = <StaticSculpture progress={phaseProgress(props.progress, props.phase)} mode={props.mode} />;
  return <div ref={container} data-signal-sculpture={props.interactive ? 'interactive' : 'decorative'} aria-hidden={props.signals?.length?undefined:true} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', overflow: 'hidden', zIndex: 0 }}>
    <SculptureBoundary fallback={fallback}>{webgl ? <Canvas camera={INITIAL_CAMERA} dpr={[1, 1.5]} frameloop={props.reducedMotion ? 'demand' : 'always'} onCreated={({ invalidate }) => invalidate()}
      gl={{ antialias: true, alpha: true, powerPreference: 'low-power' }} fallback={fallback} style={{ pointerEvents: 'none' }}>
      <SculptureScene {...props} input={input} onLost={onLost} beaconNodes={beaconNodes}/>
    </Canvas> : fallback}</SculptureBoundary>
    {props.signals?.map((topic,index)=><button key={topic.id} ref={node=>{if(node)beaconNodes.current.set(topic.id,node);else beaconNodes.current.delete(topic.id);}} className={'sculpture-beacon sculpture-beacon-'+index} style={!webgl?{left:['37%','79%','75%'][index%3],top:['45%','32%','65%'][index%3],visibility:'visible',transform:'translate(-50%,-50%)'}:undefined} aria-label={'探索信号'+topic.title} onClick={()=>props.onSignalSelect?.(topic.id)}><i style={{background:COLORS[index%3]}}/><span><b>{topic.gameName||'游戏话题'}</b><small>热度 {formatScore(topic.heat)} · {topic.platformCount} 个平台</small></span><em>↗</em></button>)}
  </div>;
}
