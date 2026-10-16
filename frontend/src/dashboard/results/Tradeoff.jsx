import { useState } from 'react';
import { fmtSig } from '../../lib/format.js';
import ChartFrame, { columnName, contextLine } from './ChartFrame.jsx';

// Section 4: the operating point. Each threshold setting of the healthy model trades false
// "Unclassified" alerts on healthy validation engines (x) against how many held-out valve-tick runs
// it catches (y, one line per severity). The committed point was chosen on validation and not moved
// after seeing results (DECISIONS D106); it is marked, not recomputed. 0 /h has its own column
// because a log axis can't show 0, so no stand-in value is drawn.

const W = 760;
const H = 380;
const M = { l: 58, r: 150, t: 18, b: 58 };
const ZERO_W = 46; // the "0 /h" column
const GAP = 26;
const DOMAIN = [1, 200]; // log x-domain for non-zero rates (validation episodes /h)
const SEVERITIES = [
  { key: '0.3', label: 'severity 0.3', color: '#6f86a6', shape: 'circle' },
  { key: '0.6', label: 'severity 0.6', color: '#a6b8d0', shape: 'square' },
  { key: '1', label: 'severity 1.0', color: '#e3eaf3', shape: 'diamond' },
];

const zeroX = M.l + ZERO_W / 2;
const logX0 = M.l + ZERO_W + GAP;
const logX1 = W - M.r;
const xOf = (v) => (v <= 0 ? zeroX : logX0 + ((Math.log10(v) - Math.log10(DOMAIN[0])) / (Math.log10(DOMAIN[1]) - Math.log10(DOMAIN[0]))) * (logX1 - logX0));
const yOf = (p) => M.t + (1 - p) * (H - M.t - M.b);

function Marker({ shape, x, y, color, r = 4.5 }) {
  if (shape === 'square') return <rect x={x - r} y={y - r} width={2 * r} height={2 * r} rx="1" fill={color} stroke="var(--panel)" strokeWidth="2" />;
  if (shape === 'diamond') return <path d={`M${x},${y - r - 1} L${x + r + 1},${y} L${x},${y + r + 1} L${x - r - 1},${y} Z`} fill={color} stroke="var(--panel)" strokeWidth="2" />;
  return <circle cx={x} cy={y} r={r} fill={color} stroke="var(--panel)" strokeWidth="2" />;
}

export default function Tradeoff({ loaded }) {
  const [tip, setTip] = useState(null);
  const d = loaded?.data;
  const configs = Object.entries(d?.configs ?? {});
  const family = configs.filter(([name]) => name.startsWith('family '));
  const committed = d?.configs?.after;
  // which family setting has exactly the committed thresholds (checked, not assumed)
  const sameAsCommitted = new Set(family
    .filter(([, c]) => committed && JSON.stringify(c.thresholds) === JSON.stringify(committed.thresholds))
    .map(([n]) => n));

  const point = (name, c, sev) => {
    const v = c.valve_tick?.[sev.key];
    if (!v) return null;
    return { name, sev, x: xOf(c.val_unclassified_episodes_per_hour), y: yOf(v.value), rate: c.val_unclassified_episodes_per_hour, v };
  };
  const lines = SEVERITIES.map((sev) => ({ sev, pts: family.map(([n, c]) => point(n, c, sev)).filter(Boolean) }));
  // direct labels at each line's right end, pushed apart vertically when two ends coincide
  const labels = lines.filter((l) => l.pts.length).map(({ sev, pts }) => {
    const last = pts.reduce((a, b) => (b.x > a.x ? b : a));
    return { sev, x: last.x + 10, y: last.y };
  }).sort((a, b) => a.y - b.y);
  labels.forEach((l, i) => { if (i > 0 && l.y - labels[i - 1].y < 15) l.y = labels[i - 1].y + 15; });

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">setting</th><th scope="col">windows</th><th scope="col">val. Unclassified /h</th>{SEVERITIES.map((s) => <th key={s.key} scope="col">valve tick {s.label.replace('severity ', '')}</th>)}</tr></thead>
      <tbody>
        {configs.map(([name, c]) => (
          <tr key={name}>
            <th scope="row">{name}{name === 'after' ? ' (committed)' : ''}</th>
            <td>{c.unclassified_windows_required}</td><td>{fmtSig(c.val_unclassified_episodes_per_hour)}</td>
            {SEVERITIES.map((s) => { const v = c.valve_tick?.[s.key]; return <td key={s.key}>{v ? `${v.successes}/${v.n}` : '—'}</td>; })}
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title="The operating point: catching the unknown vs false alerts"
      lede="Each point is one threshold setting. Lower thresholds catch more held-out valve-tick faults but raise more false Unclassified alerts on healthy engines. The committed setting was chosen on validation data only and not moved after seeing results."
      file="valve_tick_cost.json"
      loaded={loaded}
      context={contextLine([d && `${d.label}`, d && columnName(d.column), d?.split, d && `${d.runs_per_severity} runs per severity, ${d.fault_s} s of fault`, d && `sim ${d.sim_version}`])}
      table={table}
    >
      <div className="rs-legend" aria-hidden="true">
        {SEVERITIES.map((s) => (
          <span key={s.key} className="rs-key">
            <svg width="14" height="14" viewBox="0 0 14 14"><Marker shape={s.shape} x={7} y={7} color={s.color} r={4} /></svg>
            valve tick {s.label}
          </span>
        ))}
        <span className="rs-key"><svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.5" fill="none" stroke="var(--text)" strokeWidth="1.5" strokeDasharray="3 2" /></svg>committed setting</span>
      </div>
      <div className="rs-svgwrap" onMouseLeave={() => setTip(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} className="rs-svg" role="img"
          aria-label="Trade-off between false Unclassified alerts per hour on validation engines and the share of held-out valve-tick runs caught, per severity">
          {/* grid + y axis */}
          {[0, 0.25, 0.5, 0.75, 1].map((p) => (
            <g key={p}>
              <line x1={M.l} x2={W - M.r} y1={yOf(p)} y2={yOf(p)} className="rs-grid" />
              <text x={M.l - 8} y={yOf(p)} className="rs-axis" textAnchor="end" dominantBaseline="middle">{Math.round(p * 100)}%</text>
            </g>
          ))}
          {/* x: the 0 column, then the log axis */}
          <rect x={M.l} y={M.t} width={ZERO_W} height={H - M.t - M.b} className="rs-zero" />
          <text x={zeroX} y={H - M.b + 18} className="rs-axis" textAnchor="middle">0</text>
          {[1, 10, 100].map((v) => (
            <g key={v}>
              <line x1={xOf(v)} x2={xOf(v)} y1={M.t} y2={H - M.b} className="rs-grid" />
              <text x={xOf(v)} y={H - M.b + 18} className="rs-axis" textAnchor="middle">{v}</text>
            </g>
          ))}
          <text x={(M.l + logX1) / 2} y={H - 12} className="rs-axis-title" textAnchor="middle">
            false Unclassified episodes per hour on healthy validation engines (log scale; 0 shown in its own column)
          </text>
          <text x={14} y={(M.t + H - M.b) / 2} className="rs-axis-title" textAnchor="middle" transform={`rotate(-90 14 ${(M.t + H - M.b) / 2})`}>
            valve-tick runs caught
          </text>

          {/* one line per severity */}
          {lines.map(({ sev, pts }) => (
            <g key={sev.key}>
              <polyline points={pts.map((p) => `${p.x},${p.y}`).join(' ')} fill="none" stroke={sev.color} strokeWidth="2" strokeLinejoin="round" />
              {pts.map((p) => <Marker key={p.name} shape={sev.shape} x={p.x} y={p.y} color={sev.color} />)}
            </g>
          ))}
          {labels.map((l) => <text key={l.sev.key} x={l.x} y={l.y} className="rs-direct" dominantBaseline="middle">{l.sev.label}</text>)}

          {/* committed setting: marked from the file's "after" config, never recomputed */}
          {committed && SEVERITIES.map((sev) => {
            const p = point('after', committed, sev);
            return p && <circle key={sev.key} cx={p.x} cy={p.y} r="9" fill="none" stroke="var(--text)" strokeWidth="1.5" strokeDasharray="3 2" />;
          })}
          {committed && (
            <text x={zeroX + ZERO_W / 2 + 8} y={yOf(1) + 22} className="rs-direct">committed: chosen on validation (D106)</text>
          )}

          {/* hover targets, larger than the marks */}
          {lines.flatMap(({ sev, pts }) => pts.map((p) => (
            <circle key={`${sev.key}-${p.name}`} cx={p.x} cy={p.y} r="11" fill="transparent"
              onMouseEnter={() => setTip({ x: p.x, y: p.y, p })} onFocus={() => setTip({ x: p.x, y: p.y, p })} tabIndex={0}
              aria-label={`${p.name}, ${sev.label}: ${p.v.successes} of ${p.v.n} caught at ${fmtSig(p.rate)} false episodes per hour`} />
          )))}
        </svg>
        {tip && (
          <div className="rs-tip mono" style={{ left: `${(tip.x / W) * 100}%`, top: `${(tip.y / H) * 100}%` }}>
            <strong>{tip.p.name}{sameAsCommitted.has(tip.p.name) ? ' (= committed thresholds)' : ''}</strong><br />
            valve tick {tip.p.sev.label}: {tip.p.v.successes}/{tip.p.v.n}
            {Array.isArray(tip.p.v.ci_95) && ` [${fmtSig(tip.p.v.ci_95[0])}–${fmtSig(tip.p.v.ci_95[1])}]`}<br />
            false Unclassified: {fmtSig(tip.p.rate)} /h (validation)
          </div>
        )}
      </div>
      <p className="rs-note">The validation exposure hours behind each false-alert rate are not in this file, so no bound is drawn on x. Valve tick counts are out of {d?.runs_per_severity} runs per severity.</p>
    </ChartFrame>
  );
}
