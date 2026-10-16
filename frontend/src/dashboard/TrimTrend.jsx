import { fmtNum } from '../lib/format.js';
import SourceBadge from './SourceBadge.jsx';

// OBD branch v1 on a REAL replay (contract 1.6): one point per 30 s window (15 s hop), plotted at the
// window's end in file time. Two small multiples, each on its own scale (never a shared axis):
//   total fuel trim (STFT + LTFT window mean) against the trim-drift rule's limit
//   anomaly score (Mahalanobis per regime, scaled so the limit is 0.5)
// The backend computes every number; this panel only draws them. Grey by ISA-101 until a point is over.

const W = 420;
const H = 92;
const PAD = { t: 8, b: 16, l: 4, r: 8 };

function Chart({ pts, value, limit, label, unit }) {
  const xs = pts.map((p) => p.t);
  const ys = pts.map(value);
  const lim = limit(pts[pts.length - 1]);
  const lo = Math.min(0, ...ys, lim);
  const hi = Math.max(...ys, lim) * 1.1 || 1;
  const x0 = xs[0];
  const span = Math.max(xs[xs.length - 1] - x0, 60);
  const x = (t) => PAD.l + ((t - x0) / span) * (W - PAD.l - PAD.r);
  const y = (v) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(value(p)).toFixed(1)}`).join(' ');
  const last = ys[ys.length - 1];
  const over = last > lim;
  return (
    <div className="trend-cell">
      <div className="obd-cell-head">
        <span className="eyebrow">{label}</span>
        <span className={`mono trend-val${over ? ' over' : ''}`}>{fmtNum(last)}{unit} {over ? '>' : '≤'} {fmtNum(lim)}{unit}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="spark trend" role="img"
        aria-label={`${label}: latest ${fmtNum(last)}${unit}, limit ${fmtNum(lim)}${unit}, ${pts.length} windows`}>
        <line x1={PAD.l} x2={W - PAD.r} y1={y(lim)} y2={y(lim)} className="trend-limit" />
        <text x={W - PAD.r} y={y(lim) - 3} textAnchor="end" className="trend-label">limit</text>
        <path d={d} className="spark-line" />
        {pts.map((p) => (
          <circle key={p.t} cx={x(p.t)} cy={y(value(p))} r="2.5" className={value(p) > limit(p) ? 'trend-dot over' : 'trend-dot'}>
            <title>{`window ending ${fmtNum(p.t)} s: ${fmtNum(value(p))}${unit}`}</title>
          </circle>
        ))}
        <text x={PAD.l} y={H - 3} className="trend-label">{fmtNum(x0)} s</text>
        <text x={W - PAD.r} y={H - 3} textAnchor="end" className="trend-label">{fmtNum(xs[xs.length - 1])} s (file time)</text>
      </svg>
    </div>
  );
}

export default function TrimTrend({ windows, state }) {
  return (
    <section className="panel trend-panel">
      <div className="panel-head">
        <div className="panel-title">
          <h2 className="panel-label">OBD BRANCH V1 · 30 S WINDOWS</h2>
          <SourceBadge source={state.source} label={state.source_label} />
        </div>
        <span className="mono bank-note">{windows.length} windows · 15 s hop</span>
      </div>
      {windows.length < 2 ? (
        <p className="panel-note">Waiting for the first 30 s windows of the log…</p>
      ) : (
        <div className="trend-grid">
          <Chart pts={windows} value={(p) => p.trim} limit={(p) => p.trimMax} label="Total fuel trim (STFT + LTFT)" unit=" %" />
          <Chart pts={windows} value={(p) => p.score} limit={() => 0.5} label="Anomaly score (healthy model)" unit="" />
        </div>
      )}
      <p className="bank-note mono">Trim above its limit fires the trim-drift rule (physics witness, injector clog). A high anomaly score alone is UNCLASSIFIED.</p>
    </section>
  );
}
