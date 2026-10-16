import { fmtSig } from '../../lib/format.js';
import ChartFrame, { contextLine } from './ChartFrame.jsx';
import LineChart, { Marker } from './LineChart.jsx';

// Misfire signature across RPM (SIM 2.1, matched runs from robustness_v2.json). y = median
// half-order energy of the windows holding a true misfire, divided by the healthy threshold of that
// RPM band; the threshold is 1.25 x the 99.9th percentile of HEALTHY windows in the band, so the
// physics rule fires above 1x and 99.9 % of healthy windows sit below 0.8x. One line per misfire
// probability present in both regions (blue ramp, darker = rarer misfire); held-out RPMs are the
// dashed part. Nothing is computed here: every point is the file's own separation value.

const PS = [
  { p: '0.3', color: '#3f74c9', shape: 'circle' },
  { p: '0.5', color: '#8fb7f0', shape: 'square' },
  { p: '1.0', color: '#d6e5fb', shape: 'diamond' },
];
const parse = (k) => {
  const m = /^(\d+)rpm_(\d+)Nm$/.exec(k);
  return m ? { rpm: Number(m[1]), load: Number(m[2]) } : null;
};
const sepTicks = [0, 1, 2, 3, 4, 5, 6];

export default function Signature({ loaded }) {
  const d = loaded?.data;
  const conds = [
    ...Object.entries(d?.p_sweep ?? {}).map(([k, v]) => ({ k, v, held: false, ...parse(k) })),
    ...Object.entries(d?.held_out_region ?? {}).map(([k, v]) => ({ k, v, held: true, ...parse(k) })),
  ].filter((c) => c.rpm != null);
  const loads = [...new Set(conds.map((c) => c.load))].sort((a, b) => a - b);
  const rpms = [...new Set(conds.map((c) => c.rpm))].sort((a, b) => a - b);
  const firstHeld = Math.min(...conds.filter((c) => c.held).map((c) => c.rpm));
  const ticks = rpms.map((r) => ({ v: r, label: String(r), sub: r === firstHeld ? 'held out' : undefined }));
  const yMax = Math.max(6, Math.ceil(Math.max(0, ...conds.flatMap((c) => Object.values(c.v).map((r) => r.separation_median_x_threshold ?? 0)))));

  const point = (c, ps, load) => {
    const r = c.v[ps.p] ?? c.v[String(Number(ps.p))];
    if (!r || typeof r.separation_median_x_threshold !== 'number') return null;
    return {
      x: c.rpm, y: r.separation_median_x_threshold,
      tip: `${c.rpm} rpm · ${load} N·m${c.held ? ' (held out)' : ''} · p ${ps.p}\nmedian ${fmtSig(r.separation_median_x_threshold)}× threshold\nover ${r.windows_with_misfire} misfire windows · detected ${fmtSig(r.detection * 100)}%`,
    };
  };
  const seriesFor = (load) => PS.flatMap((ps) => {
    const at = conds.filter((c) => c.load === load).sort((a, b) => a.rpm - b.rpm);
    const trained = at.filter((c) => !c.held).map((c) => point(c, ps, load)).filter(Boolean);
    const held = at.filter((c) => c.held).map((c) => point(c, ps, load)).filter(Boolean);
    const bridge = trained.length && held.length ? [trained[trained.length - 1], ...held] : held;
    return [
      { key: `${ps.p}-t`, label: `p ${ps.p}`, color: ps.color, shape: ps.shape, points: trained },
      { key: `${ps.p}-h`, label: `p ${ps.p} held out`, color: ps.color, shape: 'triangle', dash: '5 4', points: bridge },
    ];
  });

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">condition</th><th scope="col">p</th><th scope="col">median × threshold</th><th scope="col">misfire windows</th><th scope="col">detection</th></tr></thead>
      <tbody>
        {conds.flatMap((c) => PS.map((ps) => {
          const r = c.v[ps.p] ?? c.v[String(Number(ps.p))];
          return r ? (
            <tr key={`${c.k}-${ps.p}`}><th scope="row">{c.rpm} rpm · {c.load} N·m{c.held ? ' (held out)' : ''}</th><td>{ps.p}</td>
              <td>{fmtSig(r.separation_median_x_threshold)}</td><td>{r.windows_with_misfire}</td><td>{fmtSig(r.detection)}</td></tr>
          ) : null;
        }))}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title="Misfire signature across RPM"
      lede="How far a misfire pushes its signature (half-order crank energy) above the healthy threshold, at each engine speed. Above 1× the physics rule fires; healthy windows sit below 0.8×."
      file="robustness_v2.json"
      loaded={loaded}
      column={d?.column}
      tagNote={d ? `${d.engines_per_condition} engines per condition` : null}
      about={(
        <>
          <p>{contextLine([d && `sim ${d.sim_version}`, d && `${d.engines_per_condition} engines per condition`, d && `${d.faulty_seconds_per_run} s of fault per run`, d?.thresholds && `feature ${d.thresholds.feature}`])}</p>
          <p>Separation: {d?.definitions?.separation}. Threshold per RPM band = 1.25 × the 99.9th percentile of healthy windows (dsp/calibrate_misfire.py), so it is the healthy reference. p = share of cycles that misfire. Held-out RPMs were never used for calibration. Same runs as the misfire detection chart below.</p>
        </>
      )}
      table={table}
    >
      <div className="rs-legend" aria-hidden="true">
        {PS.map((ps) => (
          <span key={ps.p} className="rs-key">
            <svg width="22" height="12" viewBox="0 0 22 12"><line x1="1" x2="21" y1="6" y2="6" stroke={ps.color} strokeWidth="2" /><Marker shape={ps.shape} x={11} y={6} color={ps.color} r={3.5} /></svg>
            misfire p {ps.p}
          </span>
        ))}
        <span className="rs-key">
          <svg width="22" height="12" viewBox="0 0 22 12"><line x1="1" x2="21" y1="6" y2="6" stroke="var(--text-2)" strokeWidth="2" strokeDasharray="5 4" /><Marker shape="triangle" x={11} y={6} color="var(--text-2)" r={3.5} /></svg>
          held-out RPM
        </span>
      </div>
      <div className="rs-multiples">
        {loads.map((load) => (
          <div key={load} className="rs-multiple rs-card sim">
            <p className="rs-mtitle"><strong>{load} N·m load</strong></p>
            <LineChart xDomain={[rpms[0] - 200, rpms[rpms.length - 1] + 200]} xTicks={ticks} series={seriesFor(load)} width={460} height={260} labelEnds={false}
              yDomain={[0, yMax]} yTicks={sepTicks.filter((t) => t <= yMax)} yFmt={(v) => `${v}×`}
              refLines={[{ y: 1, label: 'rule threshold (1×)' }]}
              yTitle="misfire ÷ healthy threshold" xTitle="engine speed (rpm)" ariaLabel={`Misfire half-order energy relative to the healthy threshold by RPM at ${load} N·m`} />
          </div>
        ))}
      </div>
    </ChartFrame>
  );
}
