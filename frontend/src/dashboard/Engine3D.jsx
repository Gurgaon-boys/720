import { useEffect, useRef, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { PerformanceMonitor } from '@react-three/drei';
import { cylLabel, decisionText, fault, state3d } from '../lib/faults.js';
import { FIRING_ORDER, SLOW_FACTOR, realCycleMs } from '../lib/geometry.js';
import { fmtNum } from '../lib/format.js';
import { reducedMotion } from '../lib/motion.js';
import { N_CYL, glowLevel } from './engine3d/kinematics.js';
import EngineScene from './engine3d/EngineScene.jsx';
import PanelBoundary from './PanelBoundary.jsx';
import SourceBadge from './SourceBadge.jsx';
import './engine3d.css';

// 3D engine view (lazy-loaded tab, CLAUDE.md §9). Reads only the `state` message the 2D dashboard
// already gets (mock transport or live WebSocket, same stream); computes no diagnosis.
//   - pistons follow operating_point.rpm, slowed by a factor the operator picks (always labelled)
//   - per-cylinder glow from anomaly.per_location, only when localisation === "cylinder"
//   - colour follows decision.state (lib/faults.js STATE_3D), always with the state name as text
//   - ALARM on a cylinder: camera eases there, label "Cyl N · <fault>" (+ decision.guidance if sent)
// Rendering stops while the tab is not shown, the page is hidden or the view is scrolled away.
// A WebGL failure stays inside this panel: its own boundary, separate from the 2D dashboard's.

const SLOW_CHOICES = [10, SLOW_FACTOR, SLOW_FACTOR * 4]; // SLOW_FACTOR (30×) matches the 2D bank

function webglAvailable() {
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2') || c.getContext('webgl');
    gl?.getExtension('WEBGL_lose_context')?.loseContext(); // give the probe context back
    return Boolean(gl);
  } catch {
    return false;
  }
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

// true while any part of the element is on screen (the intro sits above the views)
function useOnScreen(ref) {
  const [on, setOn] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const io = new IntersectionObserver(([e]) => setOn(e.isIntersecting));
    io.observe(el);
    return () => io.disconnect();
  }, [ref]);
  return on;
}

function StateBadge({ decision, localisation }) {
  const s = state3d(decision.state);
  const text = decisionText(decision, localisation);
  return (
    <div className="e3d-state" style={{ '--state': s.color }} role="status" aria-live="polite">
      <span className="e3d-state-name">{s.label}</span>
      <span className="e3d-state-title">{text.title}</span>
    </div>
  );
}

function AckNote({ decision }) {
  if (decision.state !== 'ALARM' || decision.alarm_id == null) return null;
  return (
    <span className="e3d-ack mono">
      {decision.acked ? `Acknowledged by ${decision.acked_by ?? 'operator'}` : 'Unacknowledged · ACK on the 2D dashboard'}
    </span>
  );
}

export default function Engine3D({ state, offline, active }) {
  const wrapRef = useRef(null);
  const pageVisible = usePageVisible();
  const onScreen = useOnScreen(wrapRef);

  // one outer element for the whole life of the view, so the on-screen observer stays attached
  return (
    <div ref={wrapRef} className="e3d-wrap">
      {state ? (
        <EngineView state={state} offline={offline} running={active && pageVisible && onScreen} />
      ) : (
        <section className="panel e3d">
          <h2 className="panel-label">3D ENGINE</h2>
          <p className="panel-note">Waiting for the first state message…</p>
        </section>
      )}
    </div>
  );
}

function EngineView({ state, offline, running: shown }) {
  const [slow, setSlow] = useState(SLOW_FACTOR);
  const [dpr, setDpr] = useState(1.5);
  const [canvasKey, setCanvasKey] = useState(0);
  const [lost, setLost] = useState(false);
  const [webgl] = useState(webglAvailable);
  const [still] = useState(reducedMotion);
  const live = useRef({});

  const d = state.decision;
  const s3 = state3d(d.state);
  const geometry = state.localisation === 'cylinder';
  const rpm = state.operating_point.rpm;
  const { per_location: perLoc, threshold } = state.anomaly;
  const glows = Array.from({ length: N_CYL }, (_, i) => (geometry ? glowLevel(perLoc[i], threshold) : 0));
  const located = geometry && Number.isInteger(d.location) && d.location >= 1 && d.location <= N_CYL;
  const focusCyl = located && d.state === 'ALARM' ? d.location : null;
  // decision.guidance (contract 1.5, optional, D105): the backend sends it since L3; the mock does not
  const guidance = typeof d.guidance === 'string' && d.guidance.trim() ? d.guidance.trim() : null;
  const label = located && (d.state === 'ALARM' || d.state === 'ADVISORY')
    ? {
      cyl: d.location,
      kicker: s3.label,
      // same words as the banner and decision card ("Misfire · Cyl 3")
      title: d.fault ? fault(d.fault).title(d.location) : `${decisionText(d).title} · ${cylLabel(d.location)}`,
      guidance,
      color: s3.color,
    }
    : null;
  const running = shown && !lost;

  // read by the animation loop every frame
  live.current = { rpm, slow, frozen: offline || still, decision: d, geometry };

  const cycleMs = rpm ? Math.round(realCycleMs(Math.round(rpm / 50) * 50)) : null;
  let motion;
  if (offline) motion = 'Diagnostics offline: motion frozen on the last state';
  else if (!rpm) motion = 'Engine speed unknown on this source: crank shown still';
  else if (still) motion = `Motion off (reduced-motion setting) · ${fmtNum(rpm)} rpm`;
  else motion = `${fmtNum(rpm)} rpm · slowed ${slow}× · 1 cycle = ${cycleMs} ms real`;

  const onCreated = ({ gl }) => {
    gl.domElement.addEventListener('webglcontextlost', (e) => {
      e.preventDefault();
      console.error('[720] 3D view: WebGL context lost');
      setLost(true);
    });
  };

  return (
    <section className={`panel e3d${offline ? ' offline' : ''}`} aria-label="3D engine view">
      <div className="e3d-head">
        <div className="e3d-head-left">
          <h2 className="panel-label">3D ENGINE</h2>
          <SourceBadge source={state.source} />
          <span className="e3d-meta mono">
            {state.run_id} · {geometry ? `4-cyl, firing order ${FIRING_ORDER.join('-')}` : 'engine geometry unknown'}
          </span>
        </div>
        <div className="e3d-head-right">
          <StateBadge decision={d} localisation={state.localisation} />
          <AckNote decision={d} />
        </div>
      </div>

      {!geometry && (
        <p className="panel-note e3d-note">
          Engine geometry unknown on real clips: no per-cylinder glow or cylinder numbers. The state applies to the
          whole engine; the block is illustrative.
        </p>
      )}
      {/* no cylinder label to sit under: show the guidance here instead */}
      {!label && guidance && <p className="e3d-guidance">{guidance}</p>}

      <div className="e3d-stage">
        {!webgl ? (
          <div className="e3d-fallback" role="alert">
            <p>3D view unavailable: this browser has no WebGL.</p>
            <p className="panel-note">The 2D dashboard is unaffected.</p>
          </div>
        ) : lost ? (
          <div className="e3d-fallback" role="alert">
            <p>3D view stopped: the graphics context was lost.</p>
            <button type="button" className="ack-btn" onClick={() => { setLost(false); setCanvasKey((k) => k + 1); }}>Restart 3D view</button>
          </div>
        ) : (
          <PanelBoundary name="3D renderer" resetKey={canvasKey}>
            <Canvas
              key={canvasKey}
              className="e3d-canvas"
              frameloop={running ? 'always' : 'never'}
              dpr={dpr}
              camera={{ position: [3.9, 3.4, 6.7], fov: 38, near: 0.1, far: 60 }}
              gl={{ antialias: true, powerPreference: 'high-performance' }}
              onCreated={onCreated}
            >
              {/* drop resolution if the demo laptop can't hold the frame rate */}
              <PerformanceMonitor onDecline={() => setDpr(1)} onIncline={() => setDpr(1.5)} />
              <EngineScene
                live={live}
                glows={glows}
                glowColor={s3.color}
                flashAlarmColor={s3.color}
                cylLabels={geometry}
                focusCyl={focusCyl}
                label={label}
                still={still}
              />
            </Canvas>
          </PanelBoundary>
        )}
      </div>

      <div className="e3d-foot">
        <span className="mono e3d-motion">{motion}</span>
        <div className="e3d-slow" role="group" aria-label="Slow-motion factor">
          <span className="e3d-slow-label">Slow motion</span>
          {SLOW_CHOICES.map((f) => (
            <button
              key={f}
              type="button"
              className={`run-pill e3d-pill${f === slow ? ' on' : ''}`}
              aria-pressed={f === slow}
              onClick={() => setSlow(f)}
            >
              {f}×
            </button>
          ))}
        </div>
        <span className="e3d-hint">Drag to orbit · scroll to zoom</span>
      </div>
      {geometry && (
        <p className="e3d-legend mono">
          Glow = anomaly score above threshold {threshold.toFixed(2)} · colour = decision state · grey flash = normal combustion
        </p>
      )}
    </section>
  );
}
