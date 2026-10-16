import { fmtSig } from '../../lib/format.js';
import ChartFrame, { contextLine } from './ChartFrame.jsx';

// Section 2: what one operator watching N healthy engines would get, per hour. A PROJECTION from
// the healthy-data rates in the same file (not an observed fleet), against the alarm-rate guidance
// the file names (EEMUA 191 / ANSI/ISA-18.2). Bars coloured by source; the "maximum manageable"
// line is amber because crossing it is the point of the chart. Assumptions under "About".

const ORDER = ['real_carobd', 'sim_20h', 'sim_2h'];
const pct = (v, max) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;
const kindOf = (label) => (/REAL/i.test(label) ? 'real' : 'sim');

export default function Projection({ loaded }) {
  const p = loaded?.data?.operator_projection;
  const by = p?.by_source ?? {};
  const keys = ORDER.filter((k) => by[k]);
  // the higher guide is the limit (amber), the lower one is the comfortable level (grey)
  const guides = Object.entries(p?.guidance_per_operator_hour ?? {}).sort((a, b) => a[1] - b[1]);
  const max = Math.max(...keys.map((k) => by[k].fleet_upper_95_per_hour ?? 0), ...guides.map(([, v]) => v), 1) * 1.05;

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">source</th><th scope="col">split</th><th scope="col">hours</th><th scope="col">events</th><th scope="col">per engine /h</th><th scope="col">{p?.engines} engines /h</th><th scope="col">95% upper</th></tr></thead>
      <tbody>
        {keys.map((k) => {
          const s = by[k];
          const e = s.per_engine_operator_notifications;
          return (
            <tr key={k}>
              <th scope="row">{s.label}</th><td>{s.split}</td><td>{fmtSig(s.hours)}</td><td>{e?.events}</td>
              <td>{fmtSig(e?.per_hour)}</td><td>{fmtSig(s.fleet_per_hour)}</td><td>{fmtSig(s.fleet_upper_95_per_hour)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );

  const about = (
    <>
      <p>{contextLine(['notifications = ALARM + Unclassified episodes', 'whisker = 95% upper bound', `${p?.engines ?? '?'} engines`])}</p>
      {Array.isArray(p?.assumptions) && <ul>{p.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>}
      {keys.filter((k) => by[k].note).map((k) => <p key={k}>{by[k].label}: {by[k].note}</p>)}
    </>
  );

  return (
    <ChartFrame
      title={`Projection: one operator, ${p?.engines ?? '?'} healthy engines`}
      lede="A projection, not an observed fleet: healthy-data alerts scaled to a fleet, against industry guidance for operator alarm load."
      file="alarm_budget.json"
      loaded={loaded}
      about={about}
      table={table}
    >
      <div className="rs-multiple">
        <div className="rs-guides" aria-hidden="true">
          {guides.map(([name, v], i) => (
            <span key={name} className={`rs-guide${i === guides.length - 1 ? ' limit' : ''}`} style={{ left: pct(v, max) }}>
              <span className="rs-guide-label mono" style={{ bottom: `${2 + (guides.length - 1 - i) * 16}px` }}>{fmtSig(v)}/h {name.replace(/\s*\(.*\)/, '')}</span>
            </span>
          ))}
        </div>
        {keys.map((k) => {
          const s = by[k];
          return (
            <div key={k} className="rs-row rs-row-proj" title={`${s.label} · ${s.split} · ${fmtSig(s.hours)} h`}>
              <span className="rs-row-label">{s.label} <span className="rs-dim mono">{fmtSig(s.hours)} h</span></span>
              <span className="rs-track" aria-hidden="true">
                <span className={`rs-bar ${kindOf(s.label)}`} style={{ width: pct(s.fleet_per_hour, max) }} />
                <span className="rs-whisker" style={{ left: pct(s.fleet_per_hour, max), width: `calc(${pct(s.fleet_upper_95_per_hour, max)} - ${pct(s.fleet_per_hour, max)})` }} />
                {guides.map(([name, v], i) => <span key={name} className={`rs-guide-line${i === guides.length - 1 ? ' limit' : ''}`} style={{ left: pct(v, max) }} />)}
              </span>
              <span className="rs-row-val mono">{fmtSig(s.fleet_per_hour)}<span className="rs-dim"> /h · ≤ {fmtSig(s.fleet_upper_95_per_hour)}</span></span>
            </div>
          );
        })}
      </div>
    </ChartFrame>
  );
}
