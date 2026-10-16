import { useEffect, useRef, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { PerformanceMonitor } from '@react-three/drei';
import { decisionText, fault, state3d } from '../lib/faults.js';
import { SLOW_FACTOR } from '../lib/geometry.js';
import { fmtNum } from '../lib/format.js';
import { reducedMotion } from '../lib/motion.js';
import { N_CYL, glowLevel } from '../dashboard/engine3d/kinematics.js';
import EngineScene from '../dashboard/engine3d/EngineScene.jsx';
import PanelBoundary from '../dashboard/PanelBoundary.jsx';
import { SENSOR_INFO, STATUS_TEXT } from './evidence.js';

// The lab's centre: Livana's 3D engine scene (EngineScene, unchanged and reused as is) in its own
// canvas, with the four sensors drawn around it and wired to the block. A wire carries flowing pulses
// only while the backend watchdog reports the sensor NORMAL; it breaks only once the watchdog reports
// FAILURE, and rejoins only once it reports NORMAL again. Cylinder glow and the alarm focus come from
// the state message exactly as in the 3D tab (per_location, decision.location).

// topology in a 1600x900 frame; the canvas behind it fills the same box
const NODES = {
  mic: { x: 190, y: 170, ax: 640, ay: 300, side: 'left' },
  accel: { x: 1410, y: 170, ax: 960, ay: 330, side: 'right' },
  crank: { x: 190, y: 730, ax: 660, ay: 600, side: 'left' },
  cam: { x: 1410, y: 730, ax: 950, ay: 250, side: 'right' },
};
const NODE_W = 260;
const NODE_H = 100;

function wire(n) {
  const sx = n.side === 'left' ? n.x + NODE_W / 2 : n.x - NODE_W / 2;
  const mx = (sx + n.ax) / 2;
  return { sx, sy: n.y, d: `M ${sx} ${n.y} C ${mx} ${n.y}, ${mx} ${n.ay}, ${n.ax} ${n.ay}`, mx, my: (n.y + n.ay) / 2 };
}

// a wire split in two at its middle, so a break can pull the halves apart
function halves(n) {
  const w = wire(n);
  const sx = w.sx;
  const q1x = (sx + w.mx) / 2;
  const q2x = (w.mx + n.ax) / 2;
  return {
    a: `M ${sx} ${n.y} Q ${q1x} ${n.y}, ${w.mx} ${w.my}`,
    b: `M ${w.mx} ${w.my} Q ${q2x} ${n.ay}, ${n.ax} ${n.ay}`,
    mid: { x: w.mx, y: w.my },
    full: w.d,
  };
}

// short enough for the node box at any stage size
const NODE_TEXT = { ...STATUS_TEXT, DISABLING: 'AWAITING WATCHDOG' };

const WIRE_STATE = { LIVE: 'live', SENDING: 'pending', DISABLING: 'pending', OFFLINE: 'broken', RECOVERING: 'recovering', FAILURE: 'broken', UNKNOWN: 'pending' };

function Topology({ sensors, frozen }) {
  return (
    <svg className="lab-topo" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <defs>
        <radialGradient id="lab-hub" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#c3cbd6" stopOpacity="0.16" />
          <stop offset="100%" stopColor="#c3cbd6" stopOpacity="0" />
        </radialGradient>
      </defs>
      {Object.entries(NODES).map(([ch, n]) => {
        const st = sensors[ch].status;
        const ws = WIRE_STATE[st] ?? 'pending';
        const h = halves(n);
        const dirX = n.side === 'left' ? -1 : 1;
        return (
          <g key={ch} className={`lab-wire w-${ws}${frozen ? ' frozen' : ''}`}>
            <path className="lab-wire-a" d={h.a} style={{ '--dx': `${dirX * 16}px`, '--dy': '-10px' }} />
            <path className="lab-wire-b" d={h.b} style={{ '--dx': `${-dirX * 16}px`, '--dy': '10px' }} />
            {ws === 'live' && <path className="lab-pulse" d={h.full} />}
            <circle className="lab-anchor" cx={n.ax} cy={n.ay} r="7" />
            {(ws === 'broken' || ws === 'recovering') && (
              <g className="lab-break" transform={`translate(${h.mid.x} ${h.mid.y})`}>
                <circle r="15" />
                <path d="M -6 -6 L 6 6 M 6 -6 L -6 6" />
              </g>
            )}
          </g>
        );
      })}
      {Object.entries(NODES).map(([ch, n]) => {
        const s = sensors[ch];
        const info = SENSOR_INFO[ch];
        return (
          <g key={ch} className={`lab-node st-${s.status.toLowerCase()}`} transform={`translate(${n.x - NODE_W / 2} ${n.y - NODE_H / 2})`}>
            <rect width={NODE_W} height={NODE_H} rx="14" />
            <circle className="lab-node-led" cx="28" cy="34" r="8" />
            <text className="lab-node-name" x="48" y="45">{info.short}</text>
            <text className="lab-node-status" x="22" y="80">{NODE_TEXT[s.status]}</text>
          </g>
        );
      })}
    </svg>
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

export default function LabStage({ state, sensors, offline, active }) {
  const [dpr, setDpr] = useState(1.5);
  const [still] = useState(reducedMotion);
  const [lost, setLost] = useState(false);
  const [canvasKey, setCanvasKey] = useState(0);
  const pageVisible = usePageVisible();
  const live = useRef({});

  const d = state.decision;
  const s3 = state3d(d.state);
  const geometry = state.localisation === 'cylinder';
  const rpm = state.operating_point.rpm;
  const { per_location: perLoc, threshold } = state.anomaly;
  const glows = Array.from({ length: N_CYL }, (_, i) => (geometry ? glowLevel(perLoc[i], threshold) : 0));
  const located = geometry && Number.isInteger(d.location) && d.location >= 1 && d.location <= N_CYL;
  const focusCyl = located && d.state === 'ALARM' ? d.location : null;
  const label = located && (d.state === 'ALARM' || d.state === 'ADVISORY')
    ? { cyl: d.location, kicker: s3.label, title: `Cyl ${d.location} · ${d.fault ? fault(d.fault).name : decisionText(d).title}`, guidance: null, color: s3.color }
    : null;
  live.current = { rpm, slow: SLOW_FACTOR, frozen: offline || still, decision: d, geometry };
  const running = active && pageVisible && !lost;

  return (
    <div className={`lab-stage ds-${d.state.toLowerCase()}`} style={{ '--state': s3.color }}>
      <div className="lab-canvas">
        {lost ? (
          <div className="e3d-fallback" role="alert">
            <p>3D view stopped: the graphics context was lost.</p>
            <button type="button" className="lab-btn" onClick={() => { setLost(false); setCanvasKey((k) => k + 1); }}>Restart 3D view</button>
          </div>
        ) : (
          <PanelBoundary name="Sabotage Lab 3D" resetKey={canvasKey}>
            <Canvas
              key={canvasKey}
              frameloop={running ? 'always' : 'never'}
              dpr={dpr}
              camera={{ position: [3.9, 3.4, 6.7], fov: 38, near: 0.1, far: 60 }}
              gl={{ antialias: true, powerPreference: 'high-performance' }}
              onCreated={({ gl }) => gl.domElement.addEventListener('webglcontextlost', (e) => { e.preventDefault(); setLost(true); })}
            >
              <PerformanceMonitor onDecline={() => setDpr(1)} onIncline={() => setDpr(1.5)} />
              <EngineScene
                live={live} glows={glows} glowColor={s3.color} flashAlarmColor={s3.color}
                cylLabels={geometry} focusCyl={focusCyl} label={label} still={still}
              />
            </Canvas>
          </PanelBoundary>
        )}
      </div>
      <Topology sensors={sensors} frozen={offline || still} />
      <div className="lab-stage-foot mono">
        <span>{state.run_id} · {offline ? 'diagnostics offline: frozen on the last state' : `${fmtNum(rpm)} rpm · motion slowed ${SLOW_FACTOR}×`}</span>
        <span>Glow = per-cylinder anomaly above {threshold.toFixed(2)} (needs crank)</span>
      </div>
    </div>
  );
}
