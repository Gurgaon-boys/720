import { useEffect, useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { CameraControls, Edges, Html } from '@react-three/drei';
import { AdditiveBlending, DoubleSide } from 'three';
import {
  BORE_R, CRANK_R, DECK_Y, LINER_BOTTOM, N_CYL, PISTON_H, PIN_OFFSET, PITCH, ROD_L,
  advance, cylX, flashLevel, slider, throwDeg,
} from './kinematics.js';

// The engine, built from primitives only (no imported model). Cutaway: the front half of the block,
// head and liners is glass, so pistons, rods and crank stay visible. Everything that moves is
// written to the meshes in useFrame from `live` (a ref), so the 10 Hz state stream never re-renders
// the scene per frame. Nothing moves unless the data says so: no rpm (real clips) or offline → still.

const CYLS = Array.from({ length: N_CYL }, (_, i) => i + 1);
const LEN = N_CYL * PITCH + 0.5; // block length along the crank
const DEPTH = 1.5;
const HEAD_H = 0.42;
const CASE_BOTTOM = -0.8;
const STEEL = '#566273';
const ALLOY = '#8994a5';
const GLASS = '#9fb3c8';
const FLASH_NEUTRAL = '#c3cbd6'; // ISA-101: a healthy combustion is a dim neutral flash
const FLASH_DEG = 60; // normal flash fades over 60° of crank (same as the 2D bank)
const KNOCK_DEG = 25; // knock: shorter, sharper
const DEG = Math.PI / 180;

// camera: overview, and close on one cylinder when an ALARM names it
const HOME = { pos: [3.9, 3.4, 6.7], target: [0, 1.25, 0] };
function focusOn(cyl) {
  const x = cylX(cyl);
  // frames the bore, its crank throw and the label above the head
  return { pos: [x * 0.5 + 1.2, 3.5, 5.6], target: [x, 1.85, 0] };
}

function Glass({ opacity = 0.07 }) {
  return <meshStandardMaterial color={GLASS} transparent opacity={opacity} depthWrite={false} side={DoubleSide} roughness={0.2} />;
}

// Block, head and crankcase: solid back half, glass front half, outlined so the shape reads.
function Block() {
  const deckH = DECK_Y - LINER_BOTTOM;
  const caseH = LINER_BOTTOM - CASE_BOTTOM;
  return (
    <group>
      {/* back wall (solid) */}
      <mesh position={[0, (DECK_Y + HEAD_H + CASE_BOTTOM) / 2, -DEPTH / 2 - 0.05]}>
        <boxGeometry args={[LEN, DECK_Y + HEAD_H - CASE_BOTTOM, 0.1]} />
        <meshStandardMaterial color="#1b2330" roughness={0.9} />
      </mesh>
      {/* cylinder block and crankcase as glass with outlines */}
      <mesh position={[0, LINER_BOTTOM + deckH / 2, 0]}>
        <boxGeometry args={[LEN, deckH, DEPTH]} />
        <Glass opacity={0.05} />
        <Edges color="#3d4b60" />
      </mesh>
      <mesh position={[0, CASE_BOTTOM + caseH / 2, 0]}>
        <boxGeometry args={[LEN, caseH, DEPTH]} />
        <Glass opacity={0.035} />
        <Edges color="#2c3a4e" />
      </mesh>
      {/* head: back half solid, front half glass */}
      <mesh position={[0, DECK_Y + HEAD_H / 2, -DEPTH / 4]}>
        <boxGeometry args={[LEN, HEAD_H, DEPTH / 2]} />
        <meshStandardMaterial color="#2a3442" roughness={0.7} metalness={0.2} />
        <Edges color="#3d4b60" />
      </mesh>
      <mesh position={[0, DECK_Y + HEAD_H / 2, DEPTH / 4]}>
        <boxGeometry args={[LEN, HEAD_H, DEPTH / 2]} />
        <Glass opacity={0.06} />
        <Edges color="#3d4b60" />
      </mesh>
    </group>
  );
}

// One bore: back half of the liner (solid, takes the glow), front half glass, a halo shell that
// carries the glow colour, the spark plug and the combustion flash.
function Bore({ cyl, glow, glowColor, flashRef, label }) {
  const x = cylX(cyl);
  const h = DECK_Y - LINER_BOTTOM;
  const y = LINER_BOTTOM + h / 2;
  return (
    <group position={[x, 0, 0]}>
      <mesh position={[0, y, 0]}>
        {/* three.js theta runs from +z: π/2..3π/2 is the back (−z) half */}
        <cylinderGeometry args={[BORE_R, BORE_R, h, 40, 1, true, Math.PI / 2, Math.PI]} />
        <meshStandardMaterial
          color="#3a4659" side={DoubleSide} roughness={0.5} metalness={0.3}
          emissive={glow > 0 ? glowColor : '#000000'} emissiveIntensity={glow * 0.9}
        />
      </mesh>
      <mesh position={[0, y, 0]}>
        <cylinderGeometry args={[BORE_R, BORE_R, h, 40, 1, true, -Math.PI / 2, Math.PI]} />
        <Glass opacity={0.08} />
      </mesh>
      {glow > 0 && (
        <mesh position={[0, y, 0]}>
          <cylinderGeometry args={[BORE_R + 0.06, BORE_R + 0.06, h, 40, 1, true]} />
          <meshBasicMaterial color={glowColor} transparent opacity={0.12 + glow * 0.3} blending={AdditiveBlending} depthWrite={false} side={DoubleSide} />
        </mesh>
      )}
      {/* spark plug */}
      <mesh position={[0, DECK_Y + HEAD_H + 0.12, 0]}>
        <cylinderGeometry args={[0.05, 0.05, 0.3, 12]} />
        <meshStandardMaterial color="#b9c2cf" roughness={0.4} />
      </mesh>
      {/* combustion flash: opacity and colour written per frame */}
      <mesh ref={flashRef} position={[0, DECK_Y - 0.12, 0]} scale={[1, 0.35, 1]}>
        <sphereGeometry args={[BORE_R * 0.85, 20, 12]} />
        <meshBasicMaterial color={FLASH_NEUTRAL} transparent opacity={0} blending={AdditiveBlending} depthWrite={false} />
      </mesh>
      {label && (
        <Html position={[0, DECK_Y + HEAD_H + 0.5, 0]} center zIndexRange={[20, 0]} className="e3d-cyl-num">
          {label}
        </Html>
      )}
    </group>
  );
}

// Crankshaft: main journal, a pair of webs, a pin and a counterweight per cylinder, and a
// flywheel with a timing mark so the rotation reads even when the pistons are hidden.
function Crankshaft({ groupRef }) {
  return (
    <group ref={groupRef}>
      <mesh rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.11, 0.11, LEN + 0.6, 16]} />
        <meshStandardMaterial color={STEEL} metalness={0.6} roughness={0.35} />
      </mesh>
      {CYLS.map((cyl) => {
        const a = throwDeg(cyl) * DEG;
        return (
          <group key={cyl} position={[cylX(cyl), 0, 0]} rotation={[a, 0, 0]}>
            {[-0.22, 0.22].map((dx) => (
              <mesh key={dx} position={[dx, CRANK_R / 2 - 0.04, 0]}>
                <boxGeometry args={[0.1, CRANK_R + 0.32, 0.34]} />
                <meshStandardMaterial color={STEEL} metalness={0.6} roughness={0.35} />
              </mesh>
            ))}
            <mesh position={[0, CRANK_R, 0]} rotation={[0, 0, Math.PI / 2]}>
              <cylinderGeometry args={[0.1, 0.1, 0.44, 16]} />
              <meshStandardMaterial color="#7a8698" metalness={0.7} roughness={0.3} />
            </mesh>
            {/* counterweight opposite the pin */}
            <mesh position={[0, -0.24, 0]}>
              <boxGeometry args={[0.54, 0.16, 0.5]} />
              <meshStandardMaterial color="#3f4a59" metalness={0.5} roughness={0.5} />
            </mesh>
          </group>
        );
      })}
      <group position={[-LEN / 2 - 0.25, 0, 0]}>
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.72, 0.72, 0.12, 48]} />
          <meshStandardMaterial color="#2f3946" metalness={0.5} roughness={0.5} />
        </mesh>
        <mesh position={[-0.02, 0.64, 0]}>
          <boxGeometry args={[0.14, 0.14, 0.06]} />
          <meshStandardMaterial color="#c3cbd6" />
        </mesh>
      </group>
    </group>
  );
}

export default function EngineScene({ live, glows, glowColor, flashAlarmColor, cylLabels, focusCyl, label, still }) {
  const theta = useRef(0);
  const crank = useRef(null);
  const pistons = useRef([]);
  const rods = useRef([]);
  const flashes = useRef([]);
  const controls = useRef(null);

  useFrame((_, delta) => {
    const L = live.current;
    if (!L.frozen) theta.current = advance(theta.current, delta, L.rpm, L.slow);
    const th = theta.current;
    if (crank.current) crank.current.rotation.x = th * DEG;
    const d = L.decision;
    for (const cyl of CYLS) {
      const s = slider(th, cyl);
      const p = pistons.current[cyl];
      if (p) p.position.y = s.wrist + PIN_OFFSET;
      const r = rods.current[cyl];
      if (r) {
        r.position.set(cylX(cyl), (s.wrist + s.pinY) / 2, s.pinZ / 2);
        r.rotation.x = s.rod;
      }
      const fl = flashes.current[cyl];
      if (fl) {
        // misfire on the alarmed cylinder: no combustion, no flash; knock: short, sharp, coloured
        const alarmHere = L.geometry && d?.state === 'ALARM' && d.location === cyl;
        const knock = alarmHere && d.fault === 'knock';
        const level = still || (alarmHere && d.fault === 'misfire') ? 0 : flashLevel(th, cyl, knock ? KNOCK_DEG : FLASH_DEG);
        fl.material.opacity = knock ? level : level * 0.45;
        fl.material.color.set(knock ? flashAlarmColor : FLASH_NEUTRAL);
      }
    }
  });

  // ease the camera only when the focus changes (ALARM on a cylinder ↔ overview); the operator can
  // orbit freely in between. Reduced motion: jump, don't fly.
  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    const { pos, target } = focusCyl ? focusOn(focusCyl) : HOME;
    c.setLookAt(...pos, ...target, !still);
  }, [focusCyl, still]);

  const pistonGeo = useMemo(() => [BORE_R - 0.03, BORE_R - 0.03, PISTON_H, 32], []);

  return (
    <>
      <color attach="background" args={['#0a0e15']} />
      <ambientLight intensity={0.55} />
      <hemisphereLight args={['#c9d6e6', '#10151d', 0.6]} />
      <directionalLight position={[5, 9, 7]} intensity={1.5} />
      <directionalLight position={[-6, 3, -4]} intensity={0.35} />

      <Block />
      {/* no CYL number on the labelled cylinder: its label already names it */}
      {CYLS.map((cyl) => (
        <Bore
          key={cyl}
          cyl={cyl}
          glow={glows[cyl - 1] ?? 0}
          glowColor={glowColor}
          flashRef={(el) => { flashes.current[cyl] = el; }}
          label={cylLabels && label?.cyl !== cyl ? `CYL ${cyl}` : null}
        />
      ))}
      {CYLS.map((cyl) => (
        <group key={cyl}>
          <mesh ref={(el) => { pistons.current[cyl] = el; }} position={[cylX(cyl), ROD_L + CRANK_R + PIN_OFFSET, 0]}>
            <cylinderGeometry args={pistonGeo} />
            <meshStandardMaterial color={ALLOY} metalness={0.55} roughness={0.35} />
          </mesh>
          <mesh ref={(el) => { rods.current[cyl] = el; }}>
            <boxGeometry args={[0.13, ROD_L, 0.09]} />
            <meshStandardMaterial color="#6b7789" metalness={0.6} roughness={0.4} />
          </mesh>
        </group>
      ))}
      <Crankshaft groupRef={crank} />

      {label && (
        <Html position={[cylX(label.cyl), DECK_Y + HEAD_H + (focusCyl ? 0.75 : 1.05), 0.3]} center zIndexRange={[30, 0]}>
          <div className="e3d-label" style={{ '--state': label.color }} role="status">
            <span className="e3d-label-kicker">{label.kicker}</span>
            <strong>{label.title}</strong>
            {label.guidance && <span className="e3d-label-guidance">{label.guidance}</span>}
          </div>
        </Html>
      )}

      <CameraControls
        ref={controls}
        makeDefault
        smoothTime={still ? 0 : 0.55}
        minDistance={3}
        maxDistance={14}
        maxPolarAngle={Math.PI * 0.55}
      />
    </>
  );
}
