import { fmtSig } from '../../lib/format.js';
import ChartFrame, { SourceTag, contextLine } from './ChartFrame.jsx';

// Section 2: what one operator watching N healthy engines would get, per hour. A PROJECTION from
// the healthy-data rates in the same file (not an observed fleet). Two cards, never on one axis:
//   SIMULATED: notifications = ALARM + Unclassified episodes, shown against the alarm-rate guidance
//   the file names (EEMUA 191 / ANSI/ISA-18.2); the "maximum manageable" line is amber.
//   REAL carOBD: Unclassified episodes only (ALARM cannot occur on carOBD: no classifier witness),
//   so it is labelled as projected Unclassified episodes and drawn WITHOUT the alarm guidance.
// Counts behind each rate are in the hover and the table view; assumptions under "About".

const SIM = ['sim_20h', 'sim_2h'];
const REAL = ['real_carobd'];
const pct = (v, max) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;

function Rows({ keys, by, max, kind, guides = [], unit }) {
  return keys.map((k) => {
    const s = by[k];
    const e = s.per_engine_operator_notifications;
    return (
      <div key={k} className="rs-row rs-row-proj" title={`${s.label} · ${s.split} · ${e?.events} episodes in ${fmtSig(s.hours)} engine-h`}>
        <span className="rs-row-label">{s.label} <span className="rs-dim mono">{e?.events} in {fmtSig(s.hours)} h</span></span>
        <span className="rs-track" aria-hidden="true">
          <span className={`rs-bar ${kind}`} style={{ width: pct(s.fleet_per_hour, max) }} />
          <span className="rs-whisker" style={{ left: pct(s.fleet_per_hour, max), width: `calc(${pct(s.fleet_upper_95_per_hour, max)} - ${pct(s.fleet_per_hour, max)})` }} />
          {guides.map(([name, v], i) => <span key={name} className={`rs-guide-line${i === guides.length - 1 ? ' limit' : ''}`} style={{ left: pct(v, max) }} />)}
        </span>
        <span className="rs-row-val mono">{fmtSig(s.fleet_per_hour)}<span className="rs-dim"> {unit} · ≤ {fmtSig(s.fleet_upper_95_per_hour)}</span></span>
      </div>
    );
  });
}

export default function Projection({ loaded }) {
  const p = loaded?.data?.operator_projection;
  const by = p?.by_source ?? {};
  const sim = SIM.filter((k) => by[k]);
  const real = REAL.filter((k) => by[k]);
  // the higher guide is the limit (amber), the lower one is the comfortable level (grey)
  const guides = Object.entries(p?.guidance_per_operator_hour ?? {}).sort((a, b) => a[1] - b[1]);
  const simMax = Math.max(...sim.map((k) => by[k].fleet_upper_95_per_hour ?? 0), ...guides.map(([, v]) => v), 1) * 1.05;
  const realMax = Math.max(...real.map((k) => by[k].fleet_upper_95_per_hour ?? 0), 1) * 1.05;

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">source</th><th scope="col">counts</th><th scope="col">split</th><th scope="col">hours</th><th scope="col">events</th><th scope="col">per engine /h</th><th scope="col">{p?.engines} engines /h</th><th scope="col">95% upper</th></tr></thead>
      <tbody>
        {[...real, ...sim].map((k) => {
          const s = by[k];
          const e = s.per_engine_operator_notifications;
          return (
            <tr key={k}>
              <th scope="row">{s.label}</th><td>{REAL.includes(k) ? 'Unclassified episodes' : 'ALARM + Unclassified episodes'}</td>
              <td>{s.split}</td><td>{fmtSig(s.hours)}</td><td>{e?.events}</td>
              <td>{fmtSig(e?.per_hour)}</td><td>{fmtSig(s.fleet_per_hour)}</td><td>{fmtSig(s.fleet_upper_95_per_hour)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );

  const about = (
    <>
      <p>{contextLine([`${p?.engines ?? '?'} engines`, 'whisker = exact Poisson 95% upper bound', 'a projection: per-engine healthy rates × engines'])}</p>
      {Array.isArray(p?.assumptions) && <ul>{p.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>}
      {[...real, ...sim].filter((k) => by[k].note).map((k) => <p key={k}>{by[k].label}: {by[k].note}</p>)}
    </>
  );

  return (
    <ChartFrame
      title={`Projection: ${p?.engines ?? '?'} healthy engines, one operator`}
      lede="A projection, not an observed fleet: healthy-data rates scaled to a fleet. Simulated and real count different things, so they are shown separately."
      file="alarm_budget.json"
      loaded={loaded}
      about={about}
      table={table}
    >
      <div className="rs-multiples">
        {real.length > 0 && (
          <div className="rs-multiple rs-card real">
            <p className="rs-mtitle"><SourceTag column="C" /> <strong>Projected Unclassified episodes per hour</strong></p>
            <p className="rs-note">Unclassified episodes only: ALARM cannot occur on carOBD (no classifier witness). Not an alarm rate, so it is not compared with alarm-management guidance.</p>
            <Rows keys={real} by={by} max={realMax} kind="real" unit="episodes/h" />
          </div>
        )}
        {sim.length > 0 && (
          <div className="rs-multiple rs-card sim">
            <p className="rs-mtitle"><SourceTag column="A" /> <strong>Projected notifications per hour (ALARM + Unclassified)</strong></p>
            <div className="rs-guides" aria-hidden="true">
              {guides.map(([name, v], i) => (
                <span key={name} className={`rs-guide${i === guides.length - 1 ? ' limit' : ''}`} style={{ left: pct(v, simMax) }}>
                  <span className="rs-guide-label mono" style={{ bottom: `${2 + (guides.length - 1 - i) * 16}px` }}>{fmtSig(v)}/h {name.replace(/\s*\(.*\)/, '')}</span>
                </span>
              ))}
            </div>
            <Rows keys={sim} by={by} max={simMax} kind="sim" guides={guides} unit="/h" />
            <p className="rs-note">Guidance lines: average alarm rate per operator (EEMUA 191 / ISA-18.2).</p>
          </div>
        )}
      </div>
    </ChartFrame>
  );
}
