import { useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Grid, Html, PerformanceMonitor } from '@react-three/drei';
import { CatmullRomCurve3, Vector3 } from 'three';
import { state3d } from '../lib/faults.js';
import { SLOW_FACTOR } from '../lib/geometry.js';
import { reducedMotion } from '../lib/motion.js';
import { N_CYL, glowLevel } from '../dashboard/engine3d/kinematics.js';
import EngineScene from '../dashboard/engine3d/EngineScene.jsx';
import PanelBoundary from '../dashboard/PanelBoundary.jsx';
import { SENSOR_INFO } from './evidence.js';

// The Resilience Lab's 3D stage (D138): Livana's engine (EngineScene, reused unchanged) with the four
// physical sensors mounted on it, wired by cables to a "720 EDGE" unit (the rule layer, with a lamp per
// witness and the decision light) and on to an OPC UA mast. Every visual follows backend facts only:
//   cable pulses     only while the watchdog reports the sensor NORMAL
//   cable fracture   only once the watchdog reports FAILURE (amber while a command awaits confirmation)
//   witness lamps    physics: a rule fired / quiet / no input; ML: model output present or not (state.models)
//   decision light   the rule layer's decision.state
//   mast rings       only while the OPC UA server is running and writing (GET /industrial)
// The engine is scaled into the left of EngineScene's fixed home view; its camera is never re-aimed.

// composition space: the engine at the origin scaled by S, the rig to its right; the whole composition is
// shifted by COMP so it sits centred on EngineScene's home camera target (0, 1.25, 0)
const S = 0.8;
const COMP = [-1.75, 0.35, 0.2];
const w = ([x, y, z]) => new Vector3(x * S, y * S, z * S);

// sensor mount points in engine coordinates (block x ±2.65, deck y ≈ 2.1, head top ≈ 2.5, crank on y = 0)
const MOUNT = {
  mic: [-1.0, 3.75, 1.35],
  accel: [1.1, 1.35, 0.8],
  crank: [-2.95, -0.15, 0.45],
  cam: [2.9, 2.32, 0.25],
};
const EDGE = new Vector3(3.15, 0.25, -0.9);
const EDGE_SIZE = [0.78, 1.0, 0.5];
const PORT_Y = { mic: 0.6, accel: 0.4, crank: 0.2, cam: 0.0 };
const MAST = new Vector3(4.3, -0.64, -2.1);

const COL = { live: '#8e9aab', hot: '#e6edf5', pending: '#ffb020', broken: '#ff4d4f', idle: '#2c3a4e' };
const CABLE = { LIVE: 'live', SENDING: 'pending', DISABLING: 'pending', RECOVERING: 'pending', OFFLINE: 'broken', FAILURE: 'broken', UNKNOWN: 'idle' };

function cableCurve(from, to, sag = 0.55) {
  const mid = from.clone().lerp(to, 0.5);
  mid.y = Math.min(from.y, to.y) - sag;
  return new CatmullRomCurve3([from, from.clone().lerp(mid, 0.5).setY(from.y - sag * 0.4), mid, to]);
}

function Pulses({ curve, color, n = 3, speed = 0.28, frozen }) {
  const refs = useRef([]);
  useFrame(({ clock }) => {
    if (frozen) return;
    const t = clock.getElapsedTime();
    refs.current.forEach((m, k) => {
      if (m) m.position.copy(curve.getPointAt(((t * speed) + k / n) % 1));
    });
  });
  return Array.from({ length: n }, (_, k) => (
    <mesh key={k} ref={(m) => { refs.current[k] = m; }} position={curve.getPointAt(k / n)}>
      <sphereGeometry args={[0.045, 12, 12]} />
      <meshBasicMaterial color={color} toneMapped={false} />
    </mesh>
  ));
}

function Spark({ at, frozen }) {
  const ref = useRef();
  useFrame(({ clock }) => {
    if (!ref.current || frozen) return;
    const s = 1 + 0.35 * Math.sin(clock.getElapsedTime() * 9);
    ref.current.scale.setScalar(s);
  });
  return (
    <mesh ref={ref} position={at}>
      <icosahedronGeometry args={[0.075, 0]} />
      <meshBasicMaterial color="#ff4d4f" toneMapped={false} />
    </mesh>
  );
}

function Cable({ curve, state, frozen, radius = 0.024 }) {
  const color = COL[state];
  const broken = state === 'broken';
  const parts = useMemo(() => {
    if (!broken) return [curve];
    const pts = curve.getSpacedPoints(60);
    const a = new CatmullRomCurve3(pts.slice(0, 26));
    const b = new CatmullRomCurve3(pts.slice(35));
    return [a, b];
  }, [curve, broken]);
  return (
    <group>
      {parts.map((c, i) => (
        <mesh key={i}>
          <tubeGeometry args={[c, 64, radius, 8, false]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={state === 'idle' ? 0 : broken ? 0.9 : 0.35} roughness={0.5} metalness={0.3} />
        </mesh>
      ))}
      {broken && <Spark at={curve.getPointAt(0.5)} frozen={frozen} />}
      {(state === 'live' || state === 'hot') && <Pulses curve={curve} color={state === 'hot' ? '#ffffff' : '#dfe6ef'} frozen={frozen} />}
    </group>
  );
}

function Tag({ position, title, status, tone, wide, rows }) {
  return (
    <Html position={position} center distanceFactor={4.6} zIndexRange={[20, 0]}>
      <div className={`s3-tag t-${tone}${wide ? ' wide' : ''}`}>
        <span className="s3-tag-title">{title}</span>
        {status && <span className="s3-tag-status">{status}</span>}
        {rows && rows.map((r) => <span key={r.k} className={`s3-tag-row t-${r.tone}`}><b>{r.k}</b>{r.v}</span>)}
      </div>
    </Html>
  );
}

const TONE = { LIVE: 'ok', SENDING: 'amber', DISABLING: 'amber', RECOVERING: 'amber', OFFLINE: 'red', FAILURE: 'red', UNKNOWN: 'idle' };
const STATUS = { LIVE: 'LIVE', SENDING: 'SENDING…', DISABLING: 'AWAITING WATCHDOG', RECOVERING: 'RECOVERING', OFFLINE: 'OFFLINE', FAILURE: 'FAILURE', UNKNOWN: 'NO DATA' };

function SensorBody({ ch, pos, tone }) {
  const ring = { ok: '#c3cbd6', amber: '#ffb020', red: '#ff4d4f', idle: '#3a4759' }[tone];
  const metal = <meshStandardMaterial color="#56606f" metalness={0.7} roughness={0.35} />;
  return (
    <group position={pos}>
      {ch === 'mic' && (
        <>
          <mesh position={[0, -0.75, 0]}><cylinderGeometry args={[0.012, 0.012, 1.4, 8]} />{metal}</mesh>
          <mesh rotation={[0.5, 0, 0]}><capsuleGeometry args={[0.07, 0.16, 6, 16]} /><meshStandardMaterial color="#2b323d" roughness={0.6} /></mesh>
        </>
      )}
      {ch === 'accel' && <mesh><boxGeometry args={[0.16, 0.16, 0.12]} />{metal}</mesh>}
      {(ch === 'crank' || ch === 'cam') && <mesh rotation={[Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.055, 0.055, 0.24, 16]} />{metal}</mesh>}
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.15, 0.014, 8, 32]} />
        <meshBasicMaterial color={ring} toneMapped={false} />
      </mesh>
    </group>
  );
}

function Lamp({ position, color, size = 0.07, pulse, frozen }) {
  const ref = useRef();
  useFrame(({ clock }) => {
    if (!ref.current) return;
    ref.current.scale.setScalar(pulse && !frozen ? 1 + 0.18 * Math.sin(clock.getElapsedTime() * 6) : 1);
  });
  return (
    <mesh ref={ref} position={position}>
      <sphereGeometry args={[size, 20, 20]} />
      <meshBasicMaterial color={color} toneMapped={false} />
    </mesh>
  );
}

function Edge({ decision, physicsTone, mlTone, frozen }) {
  const s3 = state3d(decision.state);
  const lamp = { ok: '#c3cbd6', amber: '#ffb020', red: '#ff4d4f', idle: '#2c3a4e' };
  const front = EDGE_SIZE[2] / 2 + 0.01;
  return (
    <group position={EDGE}>
      <mesh><boxGeometry args={EDGE_SIZE} /><meshStandardMaterial color="#161d28" metalness={0.6} roughness={0.35} /></mesh>
      <mesh position={[0, 0, front]}><planeGeometry args={[EDGE_SIZE[0] * 0.82, EDGE_SIZE[1] * 0.78]} /><meshStandardMaterial color="#0b1017" roughness={0.2} /></mesh>
      <Lamp position={[-0.17, 0.24, front + 0.03]} size={0.05} color={lamp[physicsTone]} />
      <Lamp position={[0.17, 0.24, front + 0.03]} size={0.05} color={lamp[mlTone]} />
      <Lamp position={[0, -0.12, front + 0.04]} size={0.11} color={decision.state === 'NORMAL' ? '#8e9aab' : s3.color} pulse={decision.state === 'ALARM' && !decision.acked} frozen={frozen} />
      <mesh position={[0, -EDGE_SIZE[1] / 2 - 0.125, 0]}><boxGeometry args={[0.36, 0.25, 0.32]} /><meshStandardMaterial color="#111720" /></mesh>
    </group>
  );
}

function Mast({ on, frozen }) {
  const rings = useRef([]);
  useFrame(({ clock }) => {
    rings.current.forEach((r, k) => {
      if (!r) return;
      const p = on && !frozen ? ((clock.getElapsedTime() * 0.6 + k / 3) % 1) : 0;
      r.scale.setScalar(0.4 + p * 1.6);
      r.material.opacity = on ? (frozen ? 0.25 : 0.65 * (1 - p)) : 0;
    });
  });
  return (
    <group position={MAST}>
      <mesh position={[0, 1.4, 0]}><cylinderGeometry args={[0.03, 0.05, 2.8, 10]} /><meshStandardMaterial color="#56606f" metalness={0.7} roughness={0.35} /></mesh>
      <mesh position={[0, 2.85, 0]}><sphereGeometry args={[0.08, 16, 16]} /><meshBasicMaterial color={on ? '#e6edf5' : '#3a4759'} toneMapped={false} /></mesh>
      {[0, 1, 2].map((k) => (
        <mesh key={k} ref={(m) => { rings.current[k] = m; }} position={[0, 2.85, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[0.25, 0.008, 6, 48]} />
          <meshBasicMaterial color="#c3cbd6" transparent opacity={0} toneMapped={false} />
        </mesh>
      ))}
    </group>
  );
}

function Rig({ sensors, classifier, physics, decision, industrial, frozen }) {
  const curves = useMemo(() => Object.fromEntries(Object.entries(MOUNT).map(([ch, p]) => {
    const port = new Vector3(EDGE.x - EDGE_SIZE[0] / 2, PORT_Y[ch], EDGE.z + 0.15);
    return [ch, cableCurve(w(p), port, ch === 'mic' ? 0.9 : 0.5)];
  })), []);
  const uplink = useMemo(() => cableCurve(new Vector3(EDGE.x + EDGE_SIZE[0] / 2, 1.2, EDGE.z), MAST.clone().setY(0.6), 0.35), []);
  const fired = physics.filter((r) => r.fired);
  const physicsTone = fired.length ? 'amber' : physics.some((r) => r.available) ? 'ok' : 'red';
  const mlTone = classifier.status === 'ACTIVE' ? 'ok' : 'red';
  const opcOn = Boolean(industrial?.running);
  const s3 = state3d(decision.state);
  return (
    <group>
      {Object.keys(MOUNT).map((ch) => {
        const st = sensors[ch].status;
        return (
          <group key={ch}>
            <SensorBody ch={ch} pos={w(MOUNT[ch])} tone={TONE[st]} />
            <Cable curve={curves[ch]} state={CABLE[st] ?? 'idle'} frozen={frozen} />
            <Tag position={w(MOUNT[ch]).add(new Vector3(0, 0.34, 0))} title={SENSOR_INFO[ch].short} status={STATUS[st]} tone={TONE[st]} />
          </group>
        );
      })}
      <Edge decision={decision} physicsTone={physicsTone} mlTone={mlTone} frozen={frozen} />
      <Tag
        position={[EDGE.x, EDGE.y + EDGE_SIZE[1] / 2 + 0.55, EDGE.z]} title="720 EDGE · RULE LAYER" wide
        tone={{ NORMAL: 'ok', ADVISORY: 'amber', UNCLASSIFIED: 'amber', ALARM: 'red' }[decision.state] ?? 'idle'}
        rows={[
          { k: 'PHYSICS', v: fired.length ? `fired · ${fired.map((r) => r.rule.replace('_', ' ')).join(', ')}` : physicsTone === 'red' ? 'no input' : 'quiet', tone: physicsTone },
          { k: 'ML', v: classifier.status === 'ACTIVE' ? `${classifier.label.replace('_', ' ')} · p ${Number(classifier.p).toFixed(2)}` : 'no input', tone: mlTone },
          { k: 'DECISION', v: `${decision.state}${decision.fault ? ` · ${decision.fault.replace('_', ' ')}` : ''}${decision.location ? ` c${decision.location}` : ''}`, tone: { NORMAL: 'ok', ADVISORY: 'amber', UNCLASSIFIED: 'amber', ALARM: 'red' }[decision.state] ?? 'idle' },
        ]}
      />
      <Cable curve={uplink} state={opcOn ? 'live' : industrial ? 'broken' : 'idle'} frozen={frozen} radius={0.03} />
      <Mast on={opcOn} frozen={frozen} />
      <Tag position={[MAST.x, MAST.y + 3.25, MAST.z]} title="OPC UA" status={opcOn ? 'PUBLISHING' : industrial ? 'OFFLINE' : '…'} tone={opcOn ? 'ok' : 'red'} />
      <pointLight position={[EDGE.x, EDGE.y, EDGE.z + 1.2]} intensity={decision.state === 'ALARM' ? 2.2 : 0} color={s3.color} distance={4} />
    </group>
  );
}

function usePageVisible() {
  const [visible, setVisible] = useState(() => !document.hidden);
  useEffect(() => {
    const on = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', on);
    return () => document.removeEventListener('visibilitychange', on);
  }, []);
  return visible;
}

export default function LabScene({ state, sensors, classifier, physics, industrial, offline, active }) {
  const [dpr, setDpr] = useState(1.5);
  const [still] = useState(reducedMotion);
  const [lost, setLost] = useState(false);
  const [key, setKey] = useState(0);
  const visible = usePageVisible();
  const live = useRef({});
  const d = state.decision;
  const s3 = state3d(d.state);
  const geometry = state.localisation === 'cylinder';
  const { per_location: perLoc, threshold } = state.anomaly;
  const glows = Array.from({ length: N_CYL }, (_, i) => (geometry ? glowLevel(perLoc[i], threshold) : 0));
  const located = geometry && Number.isInteger(d.location) && d.location >= 1 && d.location <= N_CYL;
  const label = located && (d.state === 'ALARM' || d.state === 'ADVISORY')
    ? { cyl: d.location, kicker: s3.label, title: `Cyl ${d.location} · ${d.fault ? d.fault.replace('_', ' ') : ''}`, guidance: null, color: s3.color }
    : null;
  live.current = { rpm: state.operating_point.rpm, slow: SLOW_FACTOR, frozen: offline || still, decision: d, geometry };
  const frozen = offline || still;

  return (
    <div className={`s3 ds-${d.state.toLowerCase()}`}>
      {lost ? (
        <div className="s3-fallback" role="alert">
          <p>3D view stopped: the graphics context was lost.</p>
          <button type="button" className="lab-btn" onClick={() => { setLost(false); setKey((k) => k + 1); }}>Restart 3D view</button>
        </div>
      ) : (
        <PanelBoundary name="Resilience Lab 3D" resetKey={key}>
          <Canvas
            key={key}
            frameloop={active && visible ? 'always' : 'never'}
            dpr={dpr}
            camera={{ position: [3.9, 3.4, 6.7], fov: 38, near: 0.1, far: 60 }}
            gl={{ antialias: true, powerPreference: 'high-performance' }}
            onCreated={({ gl }) => gl.domElement.addEventListener('webglcontextlost', (e) => { e.preventDefault(); setLost(true); })}
          >
            <PerformanceMonitor onDecline={() => setDpr(1)} onIncline={() => setDpr(1.5)} />
            <fog attach="fog" args={['#0a0e15', 10, 24]} />
            <group position={COMP}>
              <group scale={S}>
                <EngineScene
                  live={live} glows={glows} glowColor={s3.color} flashAlarmColor={s3.color}
                  cylLabels={geometry} focusCyl={null} label={label} still={still}
                />
              </group>
              <Rig sensors={sensors} classifier={classifier} physics={physics} decision={d} industrial={industrial} frozen={frozen} />
            </group>
            <Grid position={[0, COMP[1] - 0.8 * S - 0.01, 0]} args={[30, 30]} cellSize={0.5} cellThickness={0.6} cellColor="#1a2330"
              sectionSize={2.5} sectionThickness={1} sectionColor="#253042" fadeDistance={16} fadeStrength={1.5} infiniteGrid
            />
          </Canvas>
        </PanelBoundary>
      )}
      <div className="s3-legend mono">
        <span><i className="dot ok" />data flowing</span>
        <span><i className="dot amber" />awaiting backend</span>
        <span><i className="dot red" />confirmed failure</span>
        <span className="s3-hint">Drag to orbit · scroll to zoom · engine motion slowed {SLOW_FACTOR}×</span>
      </div>
    </div>
  );
}
