import { fmtSig } from '../../lib/format.js';
import ChartFrame, { contextLine } from './ChartFrame.jsx';
import LineChart, { Marker } from './LineChart.jsx';

// Section 5: how detection depends on how bad the fault is.
// Misfire: detection vs misfire probability, one chart per load, one line per engine speed (blue
// ramp, darker = slower; checked as an ordinal palette), held-out speeds as grey dashed lines.
// The 95 % intervals and the right-cylinder share are in the hover and the table view.
// Injector clog: watch (yellow) and ALARM (red) rates vs clog severity, the dashboard's colours.
// Nothing is fitted or smoothed.

const RPM_RAMP = ['#3f74c9', '#5f97e4', '#8fb7f0', '#c6dbfa'];
const HELD_SHAPES = ['triangle', 'diamond'];
const WATCH = { key: 'advisory', label: 'Watch', color: '#ffd43b', shape: 'square' };
const ALARM = { key: 'alarm', label: 'ALARM', color: '#ff5a6e', shape: 'circle' };

const parse = (k) => {
  const m = /^(\d+)rpm_(\d+)Nm$/.exec(k);
  return m ? { rpm: Number(m[1]), load: Number(m[2]) } : null;
};
const pctTxt = (v) => `${Math.round(v * 100)}%`;

function seriesFor(byP, name) {
  return Object.entries(byP).map(([p, r]) => ({ p: Number(p), r })).sort((a, b) => a.p - b.p).map(({ p, r }) => ({
    x: p, y: r.detection,
    tip: `${name} · p ${p}\ndetected ${r.detected}/${r.windows_with_misfire} windows (${pctTxt(r.detection)})\n95% CI ${fmtSig(r.detection_ci95?.[0])}–${fmtSig(r.detection_ci95?.[1])}${typeof r.localisation === 'number' ? `\nright cylinder ${pctTxt(r.localisation)} of detected` : ''}`,
  }));
}

export function MisfireSweep({ loaded }) {
  const d = loaded?.data;
  const conds = [
    ...Object.entries(d?.p_sweep ?? {}).map(([k, v]) => ({ k, v, held: false, ...parse(k) })),
    ...Object.entries(d?.held_out_region ?? {}).map(([k, v]) => ({ k, v, held: true, ...parse(k) })),
  ].filter((c) => c.rpm != null);
  const loads = [...new Set(conds.map((c) => c.load))].sort((a, b) => a - b);
  const trainRpms = [...new Set(conds.filter((c) => !c.held).map((c) => c.rpm))].sort((a, b) => a - b);
  const heldRpms = [...new Set(conds.filter((c) => c.held).map((c) => c.rpm))].sort((a, b) => a - b);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((v) => ({ v, label: String(v) }));

  const seriesForLoad = (load) => conds.filter((c) => c.load === load).sort((a, b) => a.rpm - b.rpm).map((c) => {
    const name = `${c.rpm} rpm${c.held ? ' (held out)' : ''}`;
    return c.held
      ? { key: c.k, label: name, color: '#8e9aab', shape: HELD_SHAPES[heldRpms.indexOf(c.rpm) % HELD_SHAPES.length], dash: '5 4', points: seriesFor(c.v, `${name} · ${load} Nm`) }
      : { key: c.k, label: name, color: RPM_RAMP[trainRpms.indexOf(c.rpm) % RPM_RAMP.length], shape: 'circle', points: seriesFor(c.v, `${name} · ${load} Nm`) };
  });

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">condition</th><th scope="col">p</th><th scope="col">detected / windows</th><th scope="col">detection [95% CI]</th><th scope="col">right cylinder</th><th scope="col">runs</th></tr></thead>
      <tbody>
        {conds.flatMap((c) => Object.entries(c.v).map(([p, r]) => (
          <tr key={`${c.k}-${p}`}>
            <th scope="row">{c.rpm} rpm · {c.load} Nm{c.held ? ' (held out)' : ''}</th><td>{p}</td><td>{r.detected}/{r.windows_with_misfire}</td>
            <td>{fmtSig(r.detection)} [{fmtSig(r.detection_ci95?.[0])}–{fmtSig(r.detection_ci95?.[1])}]</td>
            <td>{typeof r.localisation === 'number' ? fmtSig(r.localisation) : '—'}</td><td>{r.runs}</td>
          </tr>
        )))}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title="Misfire: how rare a misfire is still caught"
      lede="x = share of cycles that misfire. Each line is one engine speed; dashed grey lines are speeds held out of calibration."
      file="robustness_v2.json"
      loaded={loaded}
      column={d?.column}
      tagNote={d ? `${d.engines_per_condition} engines per condition` : null}
      about={(
        <p>{contextLine([d?.column, d && `sim ${d.sim_version}`, d && `${d.engines_per_condition} engines per condition`, d && `${d.faulty_seconds_per_run} s of fault per run`, 'detection = windows holding a real misfire that the physics rule flagged', 'hover a point for its 95% CI and right-cylinder share'])}</p>
      )}
      table={table}
    >
      {/* the lines converge near 100 %, so a legend (colour + marker) is clearer than end labels */}
      <div className="rs-legend" aria-hidden="true">
        {(loads.length ? seriesForLoad(loads[0]) : []).map((s) => (
          <span key={s.key} className="rs-key">
            <svg width="22" height="12" viewBox="0 0 22 12">
              <line x1="1" x2="21" y1="6" y2="6" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash} />
              <Marker shape={s.shape} x={11} y={6} color={s.color} r={3.5} />
            </svg>
            {s.label}
          </span>
        ))}
      </div>
      <div className="rs-multiples">
        {loads.map((load) => (
          <div key={load} className="rs-multiple rs-card sim">
            <p className="rs-mtitle"><strong>{load} Nm load</strong> <span className="rs-dim">{load === Math.min(...loads) ? '· light' : '· heavy'}</span></p>
            <LineChart xDomain={[0, 1]} xTicks={ticks} series={seriesForLoad(load)} width={460} height={250} labelEnds={false}
              yTitle="misfires detected" xTitle="misfire probability p" ariaLabel={`Misfire detection vs probability at ${load} Nm, per engine speed`} />
          </div>
        ))}
      </div>
    </ChartFrame>
  );
}

export function ClogSweep({ loaded }) {
  const d = loaded?.data;
  const rows = Object.entries(d?.per_severity ?? {}).map(([s, v]) => ({ s: Number(s), v })).sort((a, b) => a.s - b.s);
  const series = [WATCH, ALARM].map((meta) => ({
    ...meta,
    points: rows.filter(({ v }) => v[meta.key]).map(({ s, v }) => {
      const r = v[meta.key];
      return {
        x: s, y: r.value, ci: r.ci_95,
        tip: `clog ${s} (expected trim +${fmtSig(v.expected_trim_pct)}%)\n${meta.label}: ${r.successes}/${r.n} runs [${fmtSig(r.ci_95?.[0])}–${fmtSig(r.ci_95?.[1])}]${typeof r.latency_s_median === 'number' ? `\nmedian latency ${fmtSig(r.latency_s_median)} s` : ''}`,
      };
    }),
  }));
  const ticks = rows.map(({ s, v }) => ({ v: s, label: String(s), sub: `+${fmtSig(v.expected_trim_pct)}%` }));
  const half = d?.smallest_clog_alarm_rate_ge_0_5 ?? d?.['smallest_clog_alarm_rate_ge_0.5'];
  const most = d?.smallest_clog_alarm_rate_ge_0_9 ?? d?.['smallest_clog_alarm_rate_ge_0.9'];

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">clog severity</th><th scope="col">expected trim %</th><th scope="col">runs</th><th scope="col">watch</th><th scope="col">ALARM</th><th scope="col">ALARM right cylinder</th></tr></thead>
      <tbody>
        {rows.map(({ s, v }) => (
          <tr key={s}><th scope="row">{s}</th><td>{fmtSig(v.expected_trim_pct)}</td><td>{v.n_runs}</td>
            <td>{v.advisory.successes}/{v.advisory.n}</td><td>{v.alarm.successes}/{v.alarm.n}</td><td>{v.alarm_right_cylinder}</td></tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title="Injector clog: how small a clog is caught"
      lede="Clog = share of fuel lost on one cylinder; below each value, the fuel-trim shift it causes. The microphone barely hears it, so trims and crank speed do the work."
      file="clog_sweep.json"
      loaded={loaded}
      column={d?.column}
      tagNote={rows[0] ? `${rows[0].v.n_runs} runs per severity` : null}
      about={<p>{contextLine([d && `column ${d.column}`, d?.split, d && `sim ${d.sim_version}`, d && `${d.fault_s} s of fault`, d?.operating_point, 'whisker = Wilson 95% CI'])}</p>}
      table={table}
    >
      <LineChart xDomain={[0, Math.max(0.65, ...rows.map((r) => r.s))]} xTicks={ticks} series={series}
        yTitle="runs flagged" xTitle="clog severity (fuel-trim shift below)" width={640} height={280}
        ariaLabel="Injector clog watch and ALARM rate vs clog severity" />
      {(half !== undefined || most !== undefined) && (
        <p className="rs-callout">
          ALARM in at least half the runs from clog <strong>{half ?? '—'}</strong>
          {most === null ? '; in 90% of runs: not reached in this sweep.' : most !== undefined ? `; in 90% of runs from clog ${most}.` : '.'}
        </p>
      )}
    </ChartFrame>
  );
}
