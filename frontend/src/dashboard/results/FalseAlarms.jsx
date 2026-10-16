import { fmtSig } from '../../lib/format.js';
import ChartFrame, { SourceTag, columnName, contextLine } from './ChartFrame.jsx';

// Section 1: notifications per hour on HEALTHY data, at every level of the decision chain.
// One panel per source (never mixed), all on one x-scale. Bar = rate, whisker = the file's exact
// Poisson 95 % upper bound. Bars are coloured by source (teal real, blue simulated); the counts
// behind each rate are in the hover, the table view and the panel header (hours).

const LEVELS = [
  ['physics_alone', 'Physics rule alone'],
  ['classifier_alone', 'Classifier alone'],
  ['alarm', 'Two-witness ALARM'],
  ['unclassified', 'Unclassified'],
  ['advisory', 'Watch (advisory)'],
];
const SOURCES = ['real', 'sim', 'sim_2h']; // real data first: it is the headline

const pct = (v, max) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;
const kindOf = (col) => (String(col).startsWith('C') ? 'real' : 'sim');

function Row({ label, r, max, kind }) {
  if (!r) {
    return (
      <div className="rs-row">
        <span className="rs-row-label">{label}</span>
        <span className="rs-row-na">not measured</span>
      </div>
    );
  }
  return (
    <div className="rs-row" title={`${r.events} events in ${fmtSig(r.exposure_hours)} h · ${fmtSig(r.per_hour)} /h · 95% upper ${fmtSig(r.upper_95_per_hour)} /h`}>
      <span className="rs-row-label">{label}</span>
      <span className="rs-track" aria-hidden="true">
        <span className={`rs-bar ${kind}`} style={{ width: pct(r.per_hour, max) }} />
        <span className="rs-whisker" style={{ left: pct(r.per_hour, max), width: `calc(${pct(r.upper_95_per_hour, max)} - ${pct(r.per_hour, max)})` }} />
      </span>
      <span className="rs-row-val mono">{fmtSig(r.per_hour)}<span className="rs-dim"> /h · ≤ {fmtSig(r.upper_95_per_hour)}</span></span>
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

  const about = (
    <>
      <p>{contextLine([d && `sim ${d.sim_version}`, d && `built ${d.created_at?.slice(0, 10)}`, 'episodes per engine-hour', 'whisker = exact Poisson 95% upper bound'])}</p>
      <ul>
        {sources.map((k) => (
          <li key={k}>{healthy[k].label}: {columnName(healthy[k].column)} · {healthy[k].split}{healthy[k].note ? ` · ${healthy[k].note}` : ''}</li>
        ))}
      </ul>
      <p>Only the two-witness ALARM and Unclassified reach the operator; watches are logged.</p>
    </>
  );

  return (
    <ChartFrame
      title="False alarms on healthy engines"
      lede="How often each witness fires when nothing is wrong. Lower is better; the whisker is the worst case we can't rule out (95%)."
      file="alarm_budget.json"
      loaded={loaded}
      about={about}
      table={table}
    >
      <div className="rs-multiples">
        {sources.map((k) => {
          const s = healthy[k];
          const kind = kindOf(s.column);
          return (
            <div key={k} className={`rs-multiple rs-card ${kind}`}>
              <p className="rs-mtitle">
                <SourceTag column={s.column} /> <strong>{s.label}</strong>
                <span className="rs-dim mono"> {fmtSig(s.hours)} h{s.files ? ` · ${s.files} files` : ''}</span>
              </p>
              {LEVELS.map(([lv, name]) => <Row key={lv} label={name} r={s.levels?.[lv]} max={max} kind={kind} />)}
            </div>
          );
        })}
      </div>
    </ChartFrame>
  );
}
