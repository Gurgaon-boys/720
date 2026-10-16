import { fmtSig } from '../../lib/format.js';
import ChartFrame, { columnName, contextLine } from './ChartFrame.jsx';

// Section 5: models on identical grouped splits, and what a random split would have claimed.
// Bar = mean macro-F1 over seeds; whisker = ± 1 sd across seeds (spread between training runs, not
// a confidence interval). Two panels: test engines, and an RPM region never seen in training.

const MODELS = [
  ['Baseline', 'Baseline'],
  ['SVM', 'SVM'],
  ['ANN', 'ANN'],
  ['CNN', 'CNN (40 epochs)'],
  ['CNN_6_epochs', 'CNN (6 epochs)'],
];
const PANELS = [
  ['models', 'Test engines', 'test_windows'],
  ['heldout_rpm', 'Held-out RPM region', 'heldout_rpm_windows'],
];
const pct = (v) => `${Math.max(0, Math.min(100, v * 100))}%`;

function ModelRow({ name, m }) {
  if (!m) return null;
  const { mean, sd } = m.macro_f1;
  const seeds = m.per_seed?.length ?? 0;
  return (
    <div className="rs-row">
      <span className="rs-row-label">{name}</span>
      <span className="rs-track" aria-hidden="true">
        <span className="rs-bar" style={{ width: pct(mean) }} />
        {sd > 0 && <span className="rs-whisker-sym" style={{ left: pct(mean - sd), width: pct(2 * sd) }} />}
      </span>
      <span className="rs-row-val mono">
        {fmtSig(mean)} <span className="rs-dim">± {fmtSig(sd)} sd · {seeds} seed{seeds === 1 ? '' : 's'} · n {m.n.toLocaleString()}</span>
      </span>
    </div>
  );
}

export default function Models({ comparison, columnA }) {
  const mc = comparison?.data;
  const gap = columnA?.data?.column_a?.split_gap;

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">model</th>{PANELS.map(([k, t]) => <th key={k} scope="col">{t}: macro-F1 (sd)</th>)}<th scope="col">seeds</th></tr></thead>
      <tbody>
        {MODELS.map(([k, name]) => (
          <tr key={k}>
            <th scope="row">{name}</th>
            {PANELS.map(([p]) => { const m = mc?.[p]?.[k]; return <td key={p}>{m ? `${fmtSig(m.macro_f1.mean)} (${fmtSig(m.macro_f1.sd)})` : '—'}</td>; })}
            <td>{mc?.models?.[k]?.per_seed?.length ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <>
      <ChartFrame
        title="Models on identical grouped splits"
        lede="Every model trained and tested on the same split, grouped by engine so no engine appears in both. The second panel is an RPM range never seen in training."
        file="model_comparison.json"
        loaded={comparison}
        context={contextLine([mc && columnName(mc.column), mc?.split, mc && `sim ${mc.sim_version}`, 'bar = mean macro-F1, whisker = ± 1 sd across seeds (not a CI)'])}
        table={table}
      >
        <div className="rs-multiples">
          {PANELS.map(([key, title, nKey]) => (
            <div key={key} className="rs-multiple">
              <p className="rs-mtitle"><strong>{title}</strong> <span className="rs-dim mono">{mc?.[nKey]?.toLocaleString()} windows</span></p>
              {MODELS.map(([k, name]) => <ModelRow key={k} name={name} m={mc?.[key]?.[k]} />)}
            </div>
          ))}
        </div>
        {mc?.cnn_note && <p className="rs-note">CNN: {mc.cnn_note}</p>}
      </ChartFrame>

      <ChartFrame
        title="Why the split matters: random vs grouped"
        lede="The same SVM scored on a random split by window (neighbouring windows of one engine end up on both sides) and on the honest split grouped by engine."
        file="column_a.json"
        loaded={columnA}
        context={contextLine([gap?.grouped && columnName(gap.grouped.column), gap?.grouped?.unit, gap?.grouped && `n ${gap.grouped.n.toLocaleString()} windows`])}
      >
        <div className="rs-multiple">
          {[['random', 'Random split'], ['grouped', 'Grouped split']].map(([k, name]) => {
            const r = gap?.[k];
            return r ? (
              <div key={k} className="rs-row">
                <span className="rs-row-label">{name} <span className="rs-dim mono">{r.split}</span></span>
                <span className="rs-track" aria-hidden="true"><span className="rs-bar" style={{ width: pct(r.value) }} /></span>
                <span className="rs-row-val mono">{fmtSig(r.value)} <span className="rs-dim">· {r.note}</span></span>
              </div>
            ) : null;
          })}
          {gap?.random && gap?.grouped && (
            <p className="rs-note">Difference: {fmtSig(gap.random.value - gap.grouped.value)} macro-F1. A random split would have overstated the score by this much.</p>
          )}
        </div>
      </ChartFrame>
    </>
  );
}
