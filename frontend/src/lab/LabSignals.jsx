import { useEffect, useRef } from 'react';
import { reducedMotion } from '../lib/motion.js';

// Compact live strips from the real streams: mic and accelerometer waveforms (/ws/signal frames),
// crank per-cylinder roughness (state.anomaly.per_location). When a stream stops, the strip freezes on
// its last data and says how old it is; nothing is drawn that the backend did not send.

const KEEP = 1200; // samples kept per waveform (0.3 s at 4 kHz)
const STALE_S = 1; // a waveform with no frame for a second is stale

function Wave({ ch, label, frame, age, offlineStatus }) {
  const canvasRef = useRef(null);
  const buf = useRef([]);
  const lastT0 = useRef(null);

  useEffect(() => {
    if (!frame || frame.t0 === lastT0.current) return;
    lastT0.current = frame.t0;
    buf.current = [...buf.current, ...frame.samples].slice(-KEEP);
    draw();
  }, [frame]); // eslint-disable-line react-hooks/exhaustive-deps

  function draw() {
    const c = canvasRef.current;
    if (!c) return;
    const w = c.clientWidth;
    const h = c.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    g.strokeStyle = 'rgba(94,107,125,0.35)';
    g.beginPath(); g.moveTo(0, h / 2); g.lineTo(w, h / 2); g.stroke();
    const y = buf.current;
    if (!y.length) return;
    const peak = Math.max(0.05, ...y.map(Math.abs));
    g.strokeStyle = '#c3cbd6';
    g.lineWidth = 1.2;
    g.beginPath();
    y.forEach((v, i) => {
      const px = (i / (KEEP - 1)) * w;
      const py = h / 2 - (v / peak) * (h * 0.42);
      if (i) g.lineTo(px, py); else g.moveTo(px, py);
    });
    g.stroke();
  }

  const stale = offlineStatus || age == null || age >= STALE_S;
  return (
    <div className={`lab-sig${stale ? ' stale' : ''}`}>
      <div className="lab-sig-head">
        <span className="lab-sig-name">{label}</span>
        <span className="lab-sig-meta mono">{stale ? (age == null ? 'NO DATA' : `STALE · last data ${age} s ago`) : 'LIVE · 4 kHz display'}</span>
      </div>
      <canvas ref={canvasRef} className="lab-sig-canvas" aria-label={`${ch} waveform`} />
    </div>
  );
}

function CrankBars({ state, status }) {
  const down = status === 'OFFLINE' || status === 'DISABLING' || status === 'FAILURE';
  const per = state.localisation === 'cylinder' ? state.anomaly.per_location : null;
  const thr = state.anomaly.threshold;
  const max = Math.max(thr * 2, ...(per ?? [0]));
  return (
    <div className={`lab-sig${down ? ' stale' : ''}`}>
      <div className="lab-sig-head">
        <span className="lab-sig-name">CRANK · per-cylinder roughness</span>
        <span className="lab-sig-meta mono">{down ? 'STALE · crank offline: no per-cylinder evidence' : `limit ${thr.toFixed(2)}`}</span>
      </div>
      <div className="lab-bars">
        {(per ?? [0, 0, 0, 0]).map((v, i) => (
          <div key={i} className={`lab-bar${!down && v > thr ? ' over' : ''}`}>
            <span className="lab-bar-fill" style={{ height: `${down ? 0 : Math.min(100, (v / max) * 100)}%` }} />
            <span className="lab-bar-thr" style={{ bottom: `${(thr / max) * 100}%` }} />
            <span className="lab-bar-label mono">C{i + 1}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function LabSignals({ state, signal, ages, sensors, offline }) {
  const stillRef = useRef(reducedMotion());
  const off = (ch) => offline || ['OFFLINE', 'DISABLING', 'FAILURE'].includes(sensors[ch].status);
  return (
    <section className="lab-card lab-signals" aria-label="Live signals">
      <div className="lab-card-head"><h3>Live signals</h3><span className="lab-tag mono">{stillRef.current ? 'REDUCED MOTION' : 'SIMULATED'}</span></div>
      <Wave ch="mic" label="MIC" frame={signal.mic} age={ages.mic} offlineStatus={off('mic')} />
      <Wave ch="accel" label="ACCEL" frame={signal.accel} age={ages.accel} offlineStatus={off('accel')} />
      <CrankBars state={state} status={offline ? 'FAILURE' : sensors.crank.status} />
      <p className="lab-note faint">CAM has no display stream: its status comes from the backend watchdog ({sensors.cam.health ?? 'n/a'}).</p>
    </section>
  );
}
