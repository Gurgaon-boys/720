import { fmtSig } from '../../lib/format.js';
import ChartFrame, { contextLine } from './ChartFrame.jsx';
import { Marker } from './LineChart.jsx';

// Detection latency by RPM band (SIM 2.1, robustness_v2.json "latency"): first misfired combustion
// to the end of the first ADVISORY window. Dot = median, line to the p95; one row per band, one
// mark per misfire probability. Drawn from the file as is.

const BANDS = [
  ['idle', 'Idle', '< 1,100 rpm'],
  ['mid', 'Mid', '1,100–2,000 rpm'],
  ['high', 'High', '2,000–3,000 rpm'],
  ['held-out', 'Held out', '3,000–3,500 rpm'],
];
const PS = [
  { p: '1.0', color: '#d6e5fb', shape: 'diamond', dy: -7 },
  { p: '0.5', color: '#8fb7f0', shape: 'square', dy: 7 },
];
const W = 640;
const ROW = 46;
const M = { l: 150, r: 24, t: 10, b: 44 };

export default function Latency({ loaded }) {
  const d = loaded?.data;
  const lat = d?.latency ?? {};
  const rows = BANDS.filter(([b]) => PS.some((ps) => lat[`p${ps.p}_${b}`]));
  const max = Math.max(200, ...Object.values(lat).map((r) => r.p95_ms ?? 0));
  const xMax = Math.ceil(max / 200) * 200;
  const H = M.t + rows.length * ROW + M.b;
  const xOf = (ms) => M.l + (ms / xMax) * (W - M.l - M.r);
  const ticks = Array.from({ length: xMax / 200 + 1 }, (_, i) => i * 200);

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">band</th><th scope="col">p</th><th scope="col">runs</th><th scope="col">median ms</th><th scope="col">p95 ms</th><th scope="col">max ms</th><th scope="col">missed</th></tr></thead>
      <tbody>
        {rows.flatMap(([b, name]) => PS.map((ps) => {
          const r = lat[`p${ps.p}_${b}`];
          return r ? <tr key={`${b}-${ps.p}`}><th scope="row">{name}</th><td>{ps.p}</td><td>{r.n}</td><td>{fmtSig(r.median_ms)}</td><td>{fmtSig(r.p95_ms)}</td><td>{fmtSig(r.max_ms)}</td><td>{r.missed_within_run}</td></tr> : null;
        }))}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title="How fast a misfire is flagged, by engine speed"
      lede="Time from the first misfire to the first watch. A window is 4 engine cycles, so it lasts longer at low RPM and detection is slower there."
      file="robustness_v2.json"
      loaded={loaded}
      column={d?.column}
      tagNote={d ? `sim ${d.sim_version}` : null}
      about={<p>{contextLine([d?.definitions?.latency, 'dot = median, line to the 95th percentile', 'missed = runs where no watch came at all', d && `compute per window, median ${fmtSig(d.compute_ms_per_window_median)} ms`])}</p>}
      table={table}
    >
      <div className="rs-legend" aria-hidden="true">
        {PS.map((ps) => (
          <span key={ps.p} className="rs-key">
            <svg width="14" height="14" viewBox="0 0 14 14"><Marker shape={ps.shape} x={7} y={7} color={ps.color} r={4} /></svg>
            misfire p {ps.p}
          </span>
        ))}
        <span className="rs-key">mark = median, line to the 95th percentile; labels read median / p95</span>
      </div>
      <div className="rs-svgwrap">
        <svg viewBox={`0 0 ${W} ${H}`} className="rs-svg" role="img" aria-label="Median and 95th-percentile misfire detection latency by RPM band">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={xOf(t)} x2={xOf(t)} y1={M.t} y2={H - M.b} className="rs-grid" />
              <text x={xOf(t)} y={H - M.b + 16} textAnchor="middle" className="rs-axis">{t}</text>
            </g>
          ))}
          <text x={(M.l + W - M.r) / 2} y={H - 8} textAnchor="middle" className="rs-axis-title">milliseconds</text>
          {rows.map(([b, name, range], i) => {
            const cy = M.t + i * ROW + ROW / 2;
            return (
              <g key={b}>
                <text x={8} y={cy - 6} className="rs-direct">{name}</text>
                <text x={8} y={cy + 10} className="rs-axis">{range}</text>
                {PS.map((ps) => {
                  const r = lat[`p${ps.p}_${b}`];
                  if (!r) return null;
                  const y = cy + ps.dy;
                  return (
                    <g key={ps.p}>
                      <title>{`${name} · p ${ps.p}: median ${fmtSig(r.median_ms)} ms, p95 ${fmtSig(r.p95_ms)} ms, ${r.n} runs, ${r.missed_within_run} missed`}</title>
                      <line x1={xOf(r.median_ms)} x2={xOf(r.p95_ms)} y1={y} y2={y} stroke={ps.color} strokeWidth="2" strokeOpacity="0.6" />
                      <Marker shape={ps.shape} x={xOf(r.median_ms)} y={y} color={ps.color} r={4.5} />
                      {/* label right of the p95 end, or left of the median when it would run off the chart */}
                      <text x={xOf(r.p95_ms) + 110 < W ? xOf(r.p95_ms) + 8 : xOf(r.median_ms) - 10} y={y} dominantBaseline="middle"
                        textAnchor={xOf(r.p95_ms) + 110 < W ? 'start' : 'end'} className="rs-axis">{fmtSig(r.median_ms)} / {fmtSig(r.p95_ms)} ms</text>
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
    </ChartFrame>
  );
}
