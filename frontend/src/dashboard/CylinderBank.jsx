import { useEffect, useRef, useState } from 'react';
import { fault } from '../lib/faults.js';
import { FIRING_ORDER, SLOW_FACTOR, fireDeg, realCycleMs } from '../lib/geometry.js';
import SourceBadge from './SourceBadge.jsx';
import { fmtNum } from '../lib/format.js';

// Pistons and spark flashes follow a crank angle that advances at operating_point.rpm,
// slowed by SLOW_FACTOR (labelled on screen). Driven by requestAnimationFrame rather than CSS
// so rpm changes are smooth. Each piston is at top dead centre at its own firing angle.
const N = FIRING_ORDER.length;
const STROKE_PX = 62;
const FLASH_DEG = 60; // a normal combustion flash fades over 60° of crank
const KNOCK_DEG = 25; // knock: shorter, sharper flash

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

// offline: the UI watchdog has tripped; the crank freezes (nothing moves on stale data)
export default function CylinderBank({ state, offline }) {
  const { decision, operating_point: op, localisation, anomaly } = state;
  const geometry = localisation === 'cylinder';
  const active = decision.state === 'ALARM' || decision.state === 'ADVISORY';
  const f = fault(decision.fault);

  // errors inside requestAnimationFrame are invisible to error boundaries: catch them, stop the
  // loop, and re-throw on the next render so the panel's boundary shows them
  const [loopError, setLoopError] = useState(null);
  if (loopError) throw loopError;
  const pistons = useRef([]);
  const flashes = useRef([]);
  const live = useRef({});
  // latest data for the animation loop (read every frame, no re-render needed)
  live.current = { rpm: op.rpm, decision, offline };

  useEffect(() => {
    if (!geometry) return undefined;
    const still = reducedMotion();
    let crank = 0;
    let last = performance.now();
    let raf = 0;
    const frame = (now) => {
      try {
        const { rpm, decision: d, offline: frozen } = live.current;
        if (!still && !frozen && rpm) crank = (crank + ((now - last) / 1000) * ((rpm / 60) * 360) / SLOW_FACTOR) % 720;
        last = now;
        for (let cyl = 1; cyl <= N; cyl++) {
          const rel = (crank - fireDeg(cyl) + 720) % 720; // crank degrees since this cylinder fired
          const p = pistons.current[cyl];
          if (p) p.style.transform = `translateY(${((1 - Math.cos((rel * Math.PI) / 180)) / 2) * STROKE_PX}px)`;
          const fl = flashes.current[cyl];
          if (fl) {
            const alarmHere = d.state === 'ALARM' && d.location === cyl;
            const span = alarmHere && d.fault === 'knock' ? KNOCK_DEG : FLASH_DEG;
            // misfire: no combustion, no flash
            const o = alarmHere && d.fault === 'misfire' ? 0 : Math.max(0, 1 - rel / span);
            fl.style.opacity = still ? 0 : o.toFixed(3);
          }
        }
      } catch (e) {
        setLoopError(e);
        return; // stop the loop
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [geometry]);

  const tiles = [
    // schema 1.3: rpm and load_pct are null when unknown (real AudioSet clips skip order tracking)
    { label: 'Engine speed', value: fmtNum(op.rpm), unit: op.rpm == null ? '' : 'rpm' },
    { label: 'Load', value: fmtNum(op.load_pct), unit: op.load_pct == null ? '' : '%' },
    { label: 'Regime', value: op.regime, unit: '' },
    { label: 'Run time', value: state.t.toFixed(1), unit: 's' },
  ];
  // round rpm to 50 so the label doesn't flicker with ±1 % jitter
  const cycleMs = op.rpm ? Math.round(realCycleMs(Math.round(op.rpm / 50) * 50)) : null;

  return (
    <section className="panel bank">
      <div className="panel-head">
        <h2 className="panel-label">CYLINDER BANK</h2>
        <SourceBadge source={state.source} />
      </div>
      {geometry ? (
        <>
          <div className="bank-row" style={{ gridTemplateColumns: `repeat(${N}, minmax(0, 1fr))` }}>
            {Array.from({ length: N }, (_, i) => {
              const cyl = i + 1;
              const isFault = active && decision.location === cyl;
              const alarm = isFault && decision.state === 'ALARM';
              const shake = alarm && decision.fault === 'knock';
              const score = anomaly.per_location[i];
              const over = score > anomaly.threshold;
              return (
                <div key={cyl} className="cyl">
                  <div className={`cyl-box${shake ? ' shake' : ''}`} style={isFault ? { borderColor: f.color } : undefined}>
                    {/* ISA-101: a healthy firing is a dim neutral flash; colour only on alarm */}
                    <div ref={(el) => { flashes.current[cyl] = el; }} className="cyl-flash"
                      style={{ background: alarm ? f.color : 'var(--faint)' }} />
                    <div ref={(el) => { pistons.current[cyl] = el; }} className="cyl-piston" />
                  </div>
                  <div className="cyl-num mono">CYL {cyl}</div>
                  <div className="cyl-tag mono" style={isFault ? { background: f.color, color: 'var(--ink)' } : undefined}>
                    {/* HIGH: this cylinder's own score is over the threshold, even if the decision names no cylinder */}
                    {isFault ? (decision.state === 'ADVISORY' ? 'WATCH' : f.short) : over ? 'HIGH' : 'OK'}
                  </div>
                  {score != null && (
                    <div className="loc" title={`Anomaly score ${score.toFixed(2)}, threshold ${anomaly.threshold.toFixed(2)}`}>
                      <div className="loc-track">
                        <div className={`loc-fill${over ? ' over' : ''}`} style={{ width: `${score * 100}%` }} />
                        <div className="loc-thresh" style={{ left: `${anomaly.threshold * 100}%` }} />
                      </div>
                      <span className={`mono loc-val${over ? ' over' : ''}`}>{score.toFixed(2)}</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <p className="bank-note mono">
            Anomaly per cylinder · line = threshold {anomaly.threshold.toFixed(2)}
            <br />
            Motion slowed {SLOW_FACTOR}× · 1 cycle = {cycleMs} ms real
          </p>
        </>
      ) : (
        <p className="panel-note">Engine geometry unknown on real clips: no per-cylinder view.</p>
      )}
      <div className="tiles">
        {tiles.map((t) => (
          <div key={t.label}>
            <div className="eyebrow">{t.label}</div>
            <div className="tile-val mono">{t.value}{t.unit && <span> {t.unit}</span>}</div>
          </div>
        ))}
      </div>
    </section>
  );
}
