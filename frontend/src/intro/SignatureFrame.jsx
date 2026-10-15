import { FIRING_ORDER, hasSignature, polarPath } from '../lib/geometry.js';
import PolarAxes from '../lib/PolarAxes.jsx';

// Final intro stage: the live fingerprint as a HUD over the combustion footage.
// Drawn on a 1600×900 canvas. The parent sets one CSS variable while scrolling:
//   --draw  0→1  how much of the trace has been drawn
const CX = 800;
const CY = 450;
const GEO = { cx: CX, cy: CY, inner: 60, scale: 160 };

// `signature` is empty on real clips: the dial is drawn without a trace.
export function SignatureFrame({ signature: sig, trace }) {
  // firing angles are labelled by the HTML cylinder labels (C1 0° …), so skip them here
  const firingDeg = FIRING_ORDER.map((_, i) => (720 / FIRING_ORDER.length) * i);

  return (
    <svg viewBox="0 0 1600 900" aria-hidden="true" className="sig-hud">
      <defs>
        <radialGradient id="sigVeil" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#070a10" stopOpacity="0.78" />
          <stop offset="70%" stopColor="#070a10" stopOpacity="0.55" />
          <stop offset="100%" stopColor="#070a10" stopOpacity="0" />
        </radialGradient>
      </defs>
      {/* dark disc behind the dial so the trace reads over the flames */}
      <circle cx={CX} cy={CY} r="400" fill="url(#sigVeil)" />
      <circle cx={CX} cy={CY} r={GEO.inner + 0.5 * GEO.scale} fill="none" stroke="#5e6b7d" strokeOpacity="0.5" strokeDasharray="2 6" />
      <circle cx={CX} cy={CY} r={GEO.inner + 1.0 * GEO.scale} fill="none" stroke="#5e6b7d" strokeOpacity="0.5" strokeDasharray="2 6" />
      <PolarAxes cx={CX} cy={CY} r={322} inner={GEO.inner} labelR={352} fontSize={16}
        stroke="#5e6b7d" text="#bac4d2" skipLabels={firingDeg} />
      {hasSignature(sig) && (
        <>
          <path d={polarPath(sig.crank_deg, sig.baseline_amplitude, GEO)} pathLength="1" className="sig-trace"
            fill="none" stroke="#bac4d2" strokeOpacity="0.55" strokeWidth="1.5" />
          <path d={polarPath(sig.crank_deg, sig.amplitude, GEO)} pathLength="1" className="sig-trace"
            fill="none" stroke={trace} strokeWidth="2.75" strokeLinejoin="round" />
        </>
      )}
      <circle cx={CX} cy={CY} r="3" fill="#bac4d2" />
    </svg>
  );
}
