import { fmtSig } from '../../lib/format.js';
import ChartFrame, { columnName, contextLine } from './ChartFrame.jsx';

// Section 3: injected-fault runs on unseen simulated engines. Each cell: detected / runs with a
// small bar, and the median latency. The bar colour follows the dashboard: blue for a single
// witness, red for ALARM, magenta for Unclassified. The Wilson 95 % interval is in the hover and
// the table view. Held-out cells say why they are n/a.

const LEVELS = [
  ['physics_alone', 'Physics rule', 'sim'],
  ['classifier_alone', 'Classifier', 'sim'],
  ['alarm', 'ALARM (both)', 'alarm'],
  ['unclassified', 'Unclassified', 'unclass'],
];

function Cell({ r, kind }) {
  if (!r) return <td className="rs-na">—</td>;
  if (r.applicable === false) return <td className="rs-na" title={r.reason}>held out</td>;
  return (
    <td title={`${r.successes}/${r.n} runs · rate ${fmtSig(r.value)} · Wilson 95% ${fmtSig(r.ci_95?.[0])}–${fmtSig(r.ci_95?.[1])}${typeof r.latency_s_median === 'number' ? ` · median latency ${fmtSig(r.latency_s_median)} s` : ''}`}>
      <span className="rs-cell-top"><strong>{r.successes}/{r.n}</strong></span>
      <span className="rs-mini-track" aria-hidden="true"><span className={`rs-mini-bar ${kind}`} style={{ width: `${Math.round(r.value * 100)}%` }} /></span>
      {typeof r.latency_s_median === 'number' && <span className="rs-lat">{fmtSig(r.latency_s_median)} s</span>}
    </td>
  );
}

export default function DetectionTable({ loaded }) {
  const fr = loaded?.data?.fault_runs;
  const rows = Object.entries(fr?.detection ?? {});

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">fault</th>{LEVELS.map(([k, n]) => <th key={k} scope="col">{n}: rate [Wilson 95%] · latency</th>)}</tr></thead>
      <tbody>
        {rows.map(([fault, cells]) => (
          <tr key={fault}>
            <th scope="row">{fault}</th>
            {LEVELS.map(([k]) => {
              const r = cells[k];
              if (!r || r.applicable === false) return <td key={k}>{r?.reason ?? '—'}</td>;
              return <td key={k}>{r.successes}/{r.n} · {fmtSig(r.value)} [{fmtSig(r.ci_95?.[0])}–{fmtSig(r.ci_95?.[1])}] · {fmtSig(r.latency_s_median)} s</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title="Fault detection on unseen simulated engines"
      lede="Runs caught out of 20, and the median time to catch it. Valve tick is held out of training, so only Unclassified can catch it."
      file="alarm_budget.json"
      loaded={loaded}
      column={fr?.column}
      tagNote={fr ? `${rows.length} fault settings` : null}
      about={<p>{contextLine([fr?.label, fr && columnName(fr.column), fr?.split, 'latency = median', 'hover a cell for its Wilson 95% interval'])}</p>}
      table={table}
    >
      <div className="rs-scroll">
        <table className="rs-det mono">
          <thead>
            <tr><th scope="col">fault · severity</th>{LEVELS.map(([k, n, kind]) => <th key={k} scope="col"><span className={`rs-dot ${kind}`} />{n}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map(([fault, cells]) => (
              <tr key={fault}>
                <th scope="row">{fault.replace('_', ' ')}</th>
                {LEVELS.map(([k, , kind]) => <Cell key={k} r={cells[k]} kind={kind} />)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartFrame>
  );
}
