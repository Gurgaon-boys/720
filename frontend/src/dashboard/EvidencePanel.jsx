import { useEffect, useState } from 'react';
import { loadEval } from '../api/evalSource.js';
import { checkResult, COLUMN_B_ALLOWED, COLUMNS, projectionShown } from '../lib/evidence.js';
import { fmtSig } from '../lib/format.js';
import './dashboard.css';

// Evidence panel (task 8): Diljit's eval.json, read once (docs/EVAL_JSON.md, PROPOSED).
// Order per the plan: false positives on real ECU data first (carOBD held-out files), then the
// audio bound, then simulated / injected / real results in separate tables, never mixed.
// Every row keeps its column and split next to it; a result missing its counts is "incomplete"
// and shows no number at all. Grey throughout (ISA-101): evidence is not an alarm.

const FP_LEVELS = [
  ['anomaly_flags', 'Anomaly flags'],
  ['advisories', 'Watches (advisories)'],
  ['classifier_false_faults', 'Classifier false faults'],
  ['alarms', 'Alarms'],
];

// the number itself, with its own uncertainty
function valueText(r) {
  if (r.kind === 'rate') return `${fmtSig(r.per_hour)} /h · 95% upper ${fmtSig(r.upper_95_per_hour)} /h`;
  if (r.kind === 'proportion') return `${fmtSig(r.value)} · 95% CI ${fmtSig(r.ci_95[0])}–${fmtSig(r.ci_95[1])}`;
  const unit = r.unit ? ` ${r.unit}` : '';
  const ci = Array.isArray(r.ci_95) ? ` · 95% CI ${fmtSig(r.ci_95[0])}–${fmtSig(r.ci_95[1])}` : '';
  return `${fmtSig(r.value)}${unit}${ci}`;
}

// what the number is based on
function basisText(r) {
  if (r.kind === 'rate') return `${r.events} events in ${fmtSig(r.exposure_hours)} h${r.n_files != null ? ` · ${r.n_files} files` : ''}`;
  if (r.kind === 'proportion') return `${r.successes} / ${r.n}`;
  return `n = ${r.n}`;
}

// A level that cannot occur on this data (ALARM on carOBD: no classifier witness; on audio-only
// data: no second witness) shows the file's own reason, never a 0 /h that reads as measured.
function Row({ label, r, extra, flag }) {
  const { ok, missing } = checkResult(r);
  if (r?.structurally_limited) {
    return (
      <tr>
        <th scope="row">{label}</th>
        <td colSpan={2} className="mono">cannot occur here · {r.note ?? 'structurally limited'}</td>
        <td className="mono ev-ctx">{COLUMNS[r.column] ?? '—'}</td>
        <td className="mono ev-ctx">{r.split || '—'}</td>
      </tr>
    );
  }
  return (
    <tr className={ok ? '' : 'ev-incomplete'}>
      <th scope="row">
        {label}
        {flag && <span className="ev-flag">{flag}</span>}
      </th>
      {ok ? (
        <>
          <td className="mono">{valueText(r)}{extra}</td>
          <td className="mono">{basisText(r)}</td>
        </>
      ) : (
        <td colSpan={2} className="mono">{r == null ? 'incomplete · not in eval.json' : `incomplete · missing ${missing.join(', ')}`}</td>
      )}
      <td className="mono ev-ctx">{COLUMNS[r?.column] ?? '—'}</td>
      <td className="mono ev-ctx">{r?.split || '—'}</td>
    </tr>
  );
}

function Table({ caption, children }) {
  return (
    <table className="ev-table">
      <caption className="eyebrow">{caption}</caption>
      <thead>
        <tr><th scope="col">Result</th><th scope="col">Value</th><th scope="col">Based on</th><th scope="col">Column</th><th scope="col">Split</th></tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}

function FalsePositives({ fp, caption }) {
  return (
    <Table caption={`${caption} · ${fp?.dataset ?? 'dataset not named'}`}>
      {FP_LEVELS.map(([key, label]) => (
        <Row key={key} label={label} r={fp?.[key]} />
      ))}
    </Table>
  );
}

export default function EvidencePanel() {
  const [ev, setEv] = useState(null);
  useEffect(() => {
    let live = true;
    loadEval().then((r) => {
      if (r.status === 'error') console.error('[720]', r.message);
      if (live) setEv(r);
    });
    return () => { live = false; };
  }, []);

  if (!ev) return <section className="panel evidence"><p className="panel-note">Loading evidence…</p></section>;
  const d = ev.data;
  const placeholder = ev.status !== 'loaded' || d.placeholder === true;
  const a = d.column_a ?? {};
  const extraB = Object.keys(d.column_b ?? {}).filter((k) => !COLUMN_B_ALLOWED.includes(k));
  const proj = d.operator_projection;

  return (
    <section className="panel evidence" aria-label="Evidence">
      <div className="ev-head">
        <h2 className="panel-label">EVIDENCE</h2>
        {placeholder && <span className="ev-placeholder">PLACEHOLDER · NOT RESULTS</span>}
        <span className="mono ev-src">
          {ev.status === 'loaded'
            ? `eval.json · ${d.eval_version ?? 'version ?'} · generated ${d.generated_at ?? '?'} · commit ${d.source_commit ?? '?'}`
            : 'placeholder structure (docs/EVAL_JSON.md, proposed)'}
        </span>
      </div>
      {ev.status === 'missing' && <p className="panel-note">{ev.message}.</p>}
      {ev.status === 'error' && <p className="inject-error" role="alert">{ev.message}. Showing the placeholder structure instead.</p>}

      <h3 className="ev-h">False positives on real data, at every level</h3>
      <FalsePositives fp={d.false_positives?.headline} caption="Headline · real ECU" />
      <FalsePositives fp={d.false_positives?.secondary} caption="Secondary · real audio" />

      <h3 className="ev-h">Simulated, injected and real results, kept apart</h3>
      {/* the operator projection in eval.json is simulated (column A): shown here, not under the real data */}
      {projectionShown(proj) && (
        <p className="ev-projection">
          <strong>{proj.column === 'A' ? 'SIMULATED PROJECTION' : 'PROJECTION'}, not an observed fleet result:</strong> an operator watching {proj.engines} engines would see{' '}
          <span className="mono">{fmtSig(proj.value)} {proj.unit}</span>.
          {' '}Assumptions: {proj.assumptions}.{proj.basis ? ` Basis: ${proj.basis}.` : ''}
          <span className="mono ev-ctx"> · {COLUMNS[proj.column] ?? '—'} · {proj.split || '—'}</span>
        </p>
      )}
      <Table caption={`${COLUMNS.A} · test bench`}>
        <Row label="Split gap · random" r={a.split_gap?.random} />
        <Row label="Split gap · grouped" r={a.split_gap?.grouped} />
        {(a.model_comparison ?? []).map((m) => (
          <Row key={m.model} label={`Model · ${m.model}`} r={m.result}
            extra={typeof m.sd_across_seeds === 'number' ? ` · sd across seeds ${fmtSig(m.sd_across_seeds)}` : ''} />
        ))}
        {Object.entries(a.held_out_class ?? {}).flatMap(([cls, v]) => [
          <Row key={`${cls}-auroc`} label={`Held-out ${cls.replace('_', ' ')} · AUROC`} r={v.auroc} />,
          <Row key={`${cls}-pauc`} label={`Held-out ${cls.replace('_', ' ')} · pAUC`} r={v.pauc} />,
        ])}
        <Row label="Smallest injector clog at fixed false-alarm rate" r={a.injector_detectability?.smallest_clog_at_fixed_far} />
        <Row label="Window-to-alarm latency" r={a.latency_ms} />
      </Table>

      <Table caption={`${COLUMNS.B} onto real audio · knock and valve tick only`}>
        {COLUMN_B_ALLOWED.map((k) => <Row key={k} label={`Detection · ${k.replace('_', ' ')}`} r={d.column_b?.[k]} />)}
      </Table>
      {extraB.length > 0 && (
        <p className="inject-error" role="alert">
          eval.json lists {extraB.join(', ')} under column B; only knock and valve tick can be injected onto real audio. Not shown.
        </p>
      )}

      <Table caption={`${COLUMNS.C} · held-out`}>
        <Row label="OBD branch" r={d.column_c?.obd_branch} />
      </Table>
    </section>
  );
}
