import { useRef } from 'react';
import { cylLabel, traceColor } from '../lib/faults.js';
import { useSmoothed } from '../lib/motion.js';
import { FIRING_ORDER, hasSignature, polarPath, polarPoint } from '../lib/geometry.js';
import PolarAxes from '../lib/PolarAxes.jsx';
import SourceBadge from './SourceBadge.jsx';

const CX = 300;
const CY = 300;
const GEO = { cx: CX, cy: CY, inner: 40, scale: 175 };
const AMP_RINGS = [0.5, 1.0];
const AXIS_R = 240;
const FIRING_DEG = FIRING_ORDER.map((_, i) => (720 / FIRING_ORDER.length) * i);
const TRACE_TAU_MS = 140; // display smoothing of the observed trace: well under one 0.5 s window

// The observed trace morphs toward each new reading instead of being replaced 10× a second.
// `d` is written straight to the path (no React re-render per frame); a new point count snaps.
function SmoothTrace({ deg, amp, ...rest }) {
  const ref = useRef(null);
  const degRef = useRef(deg);
  degRef.current = deg;
  useSmoothed(amp, {
    tau: TRACE_TAU_MS,
    onFrame: (a) => { if (ref.current) ref.current.setAttribute('d', polarPath(degRef.current, a, GEO)); },
  });
  return <path ref={ref} {...rest} />;
}

export default function CrankSignature({ state }) {
  const { signature: sig, decision } = state;

  // Real clips: signature arrives empty. No fingerprint, no cylinder labels.
  if (!hasSignature(sig)) {
    return (
      <section className="panel sig">
        <div className="sig-head">
          <h2 className="panel-label">720° CRANK SIGNATURE</h2>
          <SourceBadge source={state.source} />
        </div>
        <div className="sig-empty">
          <p>Engine geometry unknown on real clips.</p>
          <p className="panel-note">No crank-angle fingerprint or cylinder localisation. See the spectral view below.</p>
        </div>
      </section>
    );
  }

  const trace = traceColor(decision);
  const hitCyl = decision.state === 'ALARM' || decision.state === 'ADVISORY' ? decision.location : null;

  return (
    <section className="panel sig">
      <div className="sig-head">
        <div className="panel-title">
          <h2 className="panel-label">720° CRANK SIGNATURE</h2>
          <SourceBadge source={state.source} />
        </div>
        <div className="legend">
          <span><svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke={trace} strokeWidth="2" /></svg> observed</span>
          <span><svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke="#8e9aab" strokeWidth="1.5" strokeDasharray="4 3" /></svg> healthy baseline</span>
        </div>
      </div>
      <svg viewBox="0 0 600 600" className="sig-svg" role="img"
        aria-label={`Crank-angle signature, decision ${decision.state}, against the healthy baseline`}>
        {AMP_RINGS.map((a) => {
          const r = GEO.inner + a * GEO.scale;
          const at = polarPoint(45, r, CX, CY); // label between the 0° and 90° spokes
          return (
            <g key={a}>
              <circle cx={CX} cy={CY} r={r} fill="none" stroke="#1e2836" strokeDasharray="2 4" />
              <text x={at.x + 4} y={at.y}
                fontFamily="JetBrains Mono, monospace" fontSize="10" fill="#5e6b7d">{a.toFixed(1)}</text>
            </g>
          );
        })}
        <PolarAxes cx={CX} cy={CY} r={AXIS_R} inner={GEO.inner} labelR={AXIS_R + 18} fontSize={11} skipLabels={FIRING_DEG} />

        <path d={polarPath(sig.crank_deg, sig.baseline_amplitude, GEO)} fill="none" stroke="#8e9aab" strokeOpacity="0.7" strokeWidth="1.25" strokeDasharray="4 3" />
        <SmoothTrace deg={sig.crank_deg} amp={sig.amplitude} fill="none" stroke={trace} strokeWidth="1.75" strokeLinejoin="round"
          className="sig-observed" />

        {FIRING_ORDER.map((cyl, i) => {
          const p = polarPoint(FIRING_DEG[i], AXIS_R + 26, CX, CY);
          const hit = hitCyl === cyl;
          return (
            <text key={cyl} x={p.x} y={p.y - 7} textAnchor="middle" dominantBaseline="middle"
              fontFamily="JetBrains Mono, monospace" fontSize="14" fontWeight={hit ? 700 : 500} fill={hit ? trace : 'var(--text-2)'}>
              {cylLabel(cyl)}
              <tspan x={p.x} dy="15" fontSize="10" fontWeight="400" fill="#8e9aab">{FIRING_DEG[i]}°</tspan>
            </text>
          );
        })}
      </svg>
      <p className="sig-caption mono">angle: crank angle over one 720° cycle · radius: amplitude (a.u.) · Cyl n: its firing angle</p>
    </section>
  );
}
