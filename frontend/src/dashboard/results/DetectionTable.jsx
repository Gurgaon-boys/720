import { fmtSig } from '../../lib/format.js';
import ChartFrame, { columnName, contextLine } from './ChartFrame.jsx';

// Section 3: injected-fault runs on unseen simulated engines. Each cell: detected / runs, the
// rate with its Wilson 95 % interval, and the median latency. Held-out cells say why they are n/a.
// This is already a table, so it has no separate table view.

const LEVELS = [
  ['physics_alone', 'Physics rule alone'],
  ['classifier_alone', 'Classifier alone'],
  ['alarm', 'Two-witness ALARM'],
  ['unclassified', 'Unclassified'],
];

function Cell({ r }) {
  if (!r) return <td className="rs-na">—</td>;
  if (r.applicable === false) return <td className="rs-na" title={r.reason}>n/a · held out</td>;
  return (
    <td>
      <strong>{r.successes}/{r.n}</strong> <span className="rs-dim">{fmtSig(r.value)} [{fmtSig(r.ci_95?.[0])}–{fmtSig(r.ci_95?.[1])}]</span>
      {typeof r.latency_s_median === 'number' && <span className="rs-lat">median {fmtSig(r.latency_s_median)} s</span>}
    </td>
  );
}

export default function DetectionTable({ loaded }) {
  const fr = loaded?.data?.fault_runs;
  const rows = Object.entries(fr?.detection ?? {});
  return (
    <ChartFrame
      title="Fault detection on unseen simulated engines"
      lede="Each fault injected for a fixed time; how many runs each witness caught, and how fast. Valve tick is held out of training on purpose, so only Unclassified can catch it."
      file="alarm_budget.json"
      loaded={loaded}
      context={contextLine([fr && `${fr.label}`, fr && columnName(fr.column), fr?.split, 'rate [Wilson 95%]', 'latency = median'])}
    >
      <div className="rs-scroll">
        <table className="rs-det mono">
          <thead>
            <tr><th scope="col">fault · severity</th>{LEVELS.map(([k, n]) => <th key={k} scope="col">{n}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map(([fault, cells]) => (
              <tr key={fault}>
                <th scope="row">{fault}</th>
                {LEVELS.map(([k]) => <Cell key={k} r={cells[k]} />)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartFrame>
  );
}
