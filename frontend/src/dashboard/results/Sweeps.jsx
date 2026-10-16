import { fmtSig } from '../../lib/format.js';
import ChartFrame, { contextLine } from './ChartFrame.jsx';
import LineChart, { Marker } from './LineChart.jsx';

// Section 6: how detection depends on how bad the fault is.
// Misfire: detection rate vs misfire probability, one small panel per speed/load condition on a
// shared scale, plus the held-out RPM region. Injector clog: watch and ALARM rates vs clog severity.
// Whiskers are the file's 95 % intervals. Nothing is fitted or smoothed.

const DETECT = { key: 'det', label: 'detected', color: '#e3eaf3', shape: 'circle' };
const LOCATE = { key: 'loc', label: 'right cylinder', color: '#7f95b5', shape: 'square' };
const WATCH = { key: 'advisory', label: 'Watch (advisory)', color: '#7f95b5', shape: 'square' };
const ALARM = { key: 'alarm', label: 'Two-witness ALARM', color: '#e3eaf3', shape: 'circle' };

const conditionName = (k) => k.replace(/^(\d+)rpm_(\d+)Nm$/, '$1 rpm · $2 Nm');
const pctTxt = (v) => `${Math.round(v * 100)}%`;

function misfireSeries(byP, cond) {
  const pts = Object.entries(byP).map(([p, r]) => ({ p: Number(p), r })).sort((a, b) => a.p - b.p);
  const det = pts.map(({ p, r }) => ({
    x: p, y: r.detection, ci: r.detection_ci95,
    tip: `${cond} · p ${p}\ndetected ${r.detected}/${r.windows_with_misfire} windows (${pctTxt(r.detection)})\n95% CI ${fmtSig(r.detection_ci95?.[0])}–${fmtSig(r.detection_ci95?.[1])} · ${r.runs} runs`,
  }));
  const loc = pts.filter(({ r }) => typeof r.localisation === 'number').map(({ p, r }) => ({
    x: p, y: r.localisation, ci: r.localisation_ci95,
    tip: `${cond} · p ${p}\nright cylinder ${r.located}/${r.detected} detected (${pctTxt(r.localisation)})`,
  }));
  return [{ ...DETECT, points: det }, { ...LOCATE, points: loc }];
}

export function MisfireSweep({ loaded }) {
  const d = loaded?.data;
  const panels = [
    ...Object.entries(d?.p_sweep ?? {}).map(([k, v]) => ({ key: k, title: conditionName(k), held: false, byP: v })),
    ...Object.entries(d?.held_out_region ?? {}).map(([k, v]) => ({ key: `held-${k}`, title: conditionName(k), held: true, byP: v })),
  ];
  const ticks = [0, 0.5, 1].map((v) => ({ v, label: String(v) }));

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">condition</th><th scope="col">p</th><th scope="col">detected / windows</th><th scope="col">detection [95% CI]</th><th scope="col">right cylinder</th><th scope="col">runs</th></tr></thead>
      <tbody>
        {panels.flatMap((pn) => Object.entries(pn.byP).map(([p, r]) => (
          <tr key={`${pn.key}-${p}`}>
            <th scope="row">{pn.title}{pn.held ? ' (held out)' : ''}</th><td>{p}</td><td>{r.detected}/{r.windows_with_misfire}</td>
            <td>{fmtSig(r.detection)} [{fmtSig(r.detection_ci95?.[0])}–{fmtSig(r.detection_ci95?.[1])}]</td>
            <td>{typeof r.localisation === 'number' ? fmtSig(r.localisation) : '—'}</td><td>{r.runs}</td>
          </tr>
        )))}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title="Misfire: detection vs how often the cylinder misfires"
      lede="Misfire probability p = the share of cycles that misfire. Each panel is one speed and load; the last panels are an engine region held out of calibration. Detection = windows containing a real misfire that the physics rule flagged."
      file="robustness_v2.json"
      loaded={loaded}
      context={contextLine([d?.column, d && `sim ${d.sim_version}`, d && `${d.engines_per_condition} engines per condition`, d && `${d.faulty_seconds_per_run} s of fault per run`, 'whisker = 95% CI'])}
      table={table}
    >
      <div className="rs-legend" aria-hidden="true">
        {[DETECT, LOCATE].map((s) => (
          <span key={s.key} className="rs-key"><svg width="14" height="14" viewBox="0 0 14 14"><Marker shape={s.shape} x={7} y={7} color={s.color} r={4} /></svg>{s.label}</span>
        ))}
        <span className="rs-key rs-dim">x = misfire probability p · y = share</span>
      </div>
      <div className="rs-grid-multiples">
        {panels.map((pn) => (
          <div key={pn.key} className={`rs-mini${pn.held ? ' held' : ''}`}>
            <p className="rs-mtitle"><strong>{pn.title}</strong>{pn.held && <span className="rs-dim mono"> · held-out region</span>}</p>
            <LineChart compact width={340} height={170} xDomain={[0, 1]} xTicks={ticks}
              series={misfireSeries(pn.byP, pn.title)} ariaLabel={`Misfire detection vs probability, ${pn.title}`} />
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
  const smallest = Object.entries(d ?? {}).filter(([k]) => k.startsWith('smallest_clog'));

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
      lede="Clog severity = share of fuel lost on one cylinder. The microphone barely hears it; fuel trims and crank speed do. Below each severity: the fuel-trim shift it should cause."
      file="clog_sweep.json"
      loaded={loaded}
      context={contextLine([d && `column ${d.column}`, d?.split, d && `sim ${d.sim_version}`, d && `${d.fault_s} s of fault`, d?.operating_point])}
      table={table}
    >
      <LineChart xDomain={[0, Math.max(0.65, ...rows.map((r) => r.s))]} xTicks={ticks} series={series}
        yTitle="runs flagged" xTitle="clog severity (expected fuel-trim shift below)" width={640} height={280}
        ariaLabel="Injector clog watch and ALARM rate vs clog severity" />
      {smallest.length > 0 && (
        <p className="rs-note">
          From the file: {smallest.map(([k, v]) => `${k.replace(/_/g, ' ').replace(' ge ', ' ≥ ')}: ${v == null ? 'not reached in this sweep' : typeof v === 'object' ? JSON.stringify(v) : `clog ${v}`}`).join(' · ')}.
        </p>
      )}
    </ChartFrame>
  );
}
