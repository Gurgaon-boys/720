import { fmtSig } from '../../lib/format.js';
import ChartFrame, { SourceTag, contextLine } from './ChartFrame.jsx';

// False Unclassified alerts on healthy engines, before and after the L3 changes (DECISIONS D100,
// D101). Real carOBD and simulated are separate cards with their own scales: they measure
// different models on different data. Bar = episodes per hour, whisker = the file's exact Poisson
// 95 % upper bound; counts and hours beside every rate. These are Unclassified episodes, not
// ALARMs (ALARM cannot occur on carOBD, which has no classifier witness).

const unc = (o) => o?.levels?.unclassified?.episodes ?? o?.unclassified;
const pct = (v, max) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;

function Row({ label, r, max, kind }) {
  if (!r) return null;
  return (
    <div className="rs-row" title={`${r.events} episodes in ${fmtSig(r.exposure_hours)} h · 95% upper ${fmtSig(r.upper_95_per_hour)} /h`}>
      <span className="rs-row-label">{label}</span>
      <span className="rs-track" aria-hidden="true">
        <span className={`rs-bar ${kind}`} style={{ width: pct(r.per_hour, max) }} />
        <span className="rs-whisker" style={{ left: pct(r.per_hour, max), width: `calc(${pct(r.upper_95_per_hour, max)} - ${pct(r.per_hour, max)})` }} />
      </span>
      <span className="rs-row-val mono">{fmtSig(r.per_hour)}<span className="rs-dim"> episodes/h · ≤ {fmtSig(r.upper_95_per_hour)} · {r.events} in {fmtSig(r.exposure_hours)} h</span></span>
    </div>
  );
}

function Card({ kind, column, title, rows, changed }) {
  const present = rows.filter((x) => x.r);
  if (!present.length) {
    return <div className={`rs-multiple rs-card ${kind}`}><p className="rs-mtitle"><SourceTag column={column} /> <strong>{title}</strong></p><p className="panel-note">Results pending: not in the file.</p></div>;
  }
  const max = Math.max(...present.map((x) => x.r.upper_95_per_hour)) * 1.05;
  return (
    <div className={`rs-multiple rs-card ${kind}`}>
      <p className="rs-mtitle"><SourceTag column={column} /> <strong>{title}</strong></p>
      {present.map((x) => <Row key={x.label} label={x.label} r={x.r} max={max} kind={x.muted ? 'muted' : kind} />)}
      <p className="rs-note">{changed}</p>
    </div>
  );
}

export default function BeforeAfter({ real, sim }) {
  const c = real?.data;
  const s = sim?.data;
  const realRows = [
    { label: 'Before (1 window)', r: unc(c?.overall_before_l3), muted: true },
    { label: 'After (2 windows)', r: unc(c?.overall) },
  ];
  const simRows = [
    { label: 'Before', r: unc(s?.configs?.before), muted: true },
    { label: '2-window rule only', r: unc(s?.configs?.persist2), muted: true },
    { label: 'Committed setting', r: unc(s?.configs?.after) },
  ];
  const both = [
    ...realRows.map((x) => ({ ...x, src: 'REAL carOBD' })),
    ...simRows.map((x) => ({ ...x, src: 'SIMULATED soak' })),
  ].filter((x) => x.r);

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">source</th><th scope="col">setting</th><th scope="col">episodes</th><th scope="col">hours</th><th scope="col">per h</th><th scope="col">95% upper</th></tr></thead>
      <tbody>
        {both.map((x) => (
          <tr key={`${x.src}-${x.label}`}><th scope="row">{x.src}</th><td>{x.label}</td><td>{x.r.events}</td><td>{fmtSig(x.r.exposure_hours)}</td><td>{fmtSig(x.r.per_hour)}</td><td>{fmtSig(x.r.upper_95_per_hour)}</td></tr>
        ))}
      </tbody>
    </table>
  );

  const ok = real?.status === 'loaded' || sim?.status === 'loaded';
  return (
    <ChartFrame
      title="False Unclassified episodes: before and after the fix"
      lede="On healthy engines every Unclassified episode is a false alert. These are Unclassified episodes, not ALARMs. Real and simulated are measured separately, each on its own scale."
      srcPath="eval/results/column_c_detail.json, eval/results/soak_l3_long.json"
      loaded={ok ? { status: 'loaded' } : (real ?? sim)}
      about={(
        <>
          <p>REAL: {contextLine([c?.source, unc(c?.overall)?.split, c && `${c.overall?.files} held-out files`, 'the same files replayed twice, nothing refitted (eval/column_c.py)'])}.</p>
          <p>SIMULATED: {contextLine([s?.label, s && `sim ${s.sim_version}`, s?.split, s && `${s.runs} runs`])}; before = checkpoint healthy thresholds with 1 window, then 2 windows, then the validation thresholds of D101 (committed, D106).</p>
          <p>Episode = a run of consecutive windows in the same state. Bound = exact one-sided Poisson 95% upper. ALARM on carOBD is 0 by construction (no classifier witness), so it is not shown as a result.</p>
          {real?.status !== 'loaded' && <p>column_c_detail.json: {real?.message ?? 'loading'}</p>}
          {sim?.status !== 'loaded' && <p>soak_l3_long.json: {sim?.message ?? 'loading'}</p>}
        </>
      )}
      table={table}
    >
      <div className="rs-multiples">
        {real?.status === 'loaded'
          ? <Card kind="real" column="C" title={`carOBD held-out · ${fmtSig(c?.overall?.hours)} h`} rows={realRows}
              changed="What changed: Unclassified now needs 2 consecutive anomalous windows instead of 1 (D100). Same model, same thresholds, same files." />
          : <div className="rs-multiple rs-card real"><p className="rs-mtitle"><SourceTag column="C" /> <strong>carOBD held-out</strong></p><p className="panel-note">{real ? real.message : 'Loading…'}</p></div>}
        {sim?.status === 'loaded'
          ? <Card kind="sim" column="A" title={`long soak · ${fmtSig(s?.configs?.after?.hours)} engine-h`} rows={simRows}
              changed="What changed: the 2-window rule (D100), then healthy thresholds set per RPM band from validation engines (D101). Same recorded runs replayed under each setting." />
          : <div className="rs-multiple rs-card sim"><p className="rs-mtitle"><SourceTag column="A" /> <strong>long soak</strong></p><p className="panel-note">{sim ? sim.message : 'Loading…'}</p></div>}
      </div>
    </ChartFrame>
  );
}
