import { fmtSig } from '../../lib/format.js';
import ChartFrame, { columnName, contextLine } from './ChartFrame.jsx';

// Section 1: notifications per hour on HEALTHY data, at every level of the decision chain.
// One panel per source (never mixed), all on one x-scale so they compare. Bar = rate, whisker =
// the file's exact Poisson 95 % upper bound; the counts behind each rate are printed beside it.

const LEVELS = [
  ['physics_alone', 'Physics rule alone'],
  ['classifier_alone', 'Classifier alone'],
  ['alarm', 'Two-witness ALARM'],
  ['unclassified', 'Unclassified (abnormal, no name)'],
  ['advisory', 'Advisory (watch)'],
];
const SOURCES = ['real', 'sim', 'sim_2h']; // real data first: it is the headline

const pct = (v, max) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;

function Row({ label, r, max }) {
  if (!r) {
    return (
      <div className="rs-row">
        <span className="rs-row-label">{label}</span>
        <span className="rs-row-na mono">not measured</span>
      </div>
    );
  }
  return (
    <div className="rs-row">
      <span className="rs-row-label">{label}</span>
      <span className="rs-track" aria-hidden="true">
        <span className="rs-bar" style={{ width: pct(r.per_hour, max) }} />
        <span className="rs-whisker" style={{ left: pct(r.per_hour, max), width: `calc(${pct(r.upper_95_per_hour, max)} - ${pct(r.per_hour, max)})` }} />
      </span>
      <span className="rs-row-val mono">
        {fmtSig(r.per_hour)} /h <span className="rs-dim">(≤ {fmtSig(r.upper_95_per_hour)})</span>
        <span className="rs-dim"> · {r.events} in {fmtSig(r.exposure_hours)} h</span>
      </span>
    </div>
  );
}

export default function FalseAlarms({ loaded }) {
  const d = loaded?.data;
  const healthy = d?.healthy ?? {};
  const sources = SOURCES.filter((k) => healthy[k]);
  const max = Math.max(1, ...sources.flatMap((k) => LEVELS.map(([lv]) => healthy[k].levels?.[lv]?.upper_95_per_hour ?? 0))) * 1.05;

  const table = (
    <table className="mono">
      <thead>
        <tr><th scope="col">source</th><th scope="col">level</th><th scope="col">events</th><th scope="col">hours</th><th scope="col">per h</th><th scope="col">95% upper</th><th scope="col">column</th></tr>
      </thead>
      <tbody>
        {sources.flatMap((k) => LEVELS.map(([lv, name]) => {
          const r = healthy[k].levels?.[lv];
          return (
            <tr key={`${k}-${lv}`}>
              <th scope="row">{healthy[k].label} ({fmtSig(healthy[k].hours)} h)</th><td>{name}</td>
              {r ? (<><td>{r.events}</td><td>{fmtSig(r.exposure_hours)}</td><td>{fmtSig(r.per_hour)}</td><td>{fmtSig(r.upper_95_per_hour)}</td></>) : <td colSpan={4}>not measured</td>}
              <td>{healthy[k].column}</td>
            </tr>
          );
        }))}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title="False alarms on healthy engines, at every level"
      lede="How often each witness fires on engines with no fault. Only the two-witness ALARM and Unclassified reach the operator; advisories are logged."
      file="alarm_budget.json"
      loaded={loaded}
      context={contextLine([d && `sim ${d.sim_version}`, d && `built ${d.created_at?.slice(0, 10)}`, 'episodes per engine-hour', 'whisker = exact Poisson 95% upper bound'])}
      table={table}
    >
      <div className="rs-multiples">
        {sources.map((k) => {
          const s = healthy[k];
          return (
            <div key={k} className="rs-multiple">
              <p className="rs-mtitle">
                <strong>{s.label}</strong> <span className="rs-dim mono">{columnName(s.column)} · {s.split} · {fmtSig(s.hours)} h{s.files ? ` · ${s.files} files` : ''}</span>
              </p>
              {LEVELS.map(([lv, name]) => <Row key={lv} label={name} r={s.levels?.[lv]} max={max} />)}
              {s.note && <p className="rs-note">{s.note}</p>}
            </div>
          );
        })}
      </div>
    </ChartFrame>
  );
}
