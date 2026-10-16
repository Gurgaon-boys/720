import { fmtSig } from '../../lib/format.js';
import ChartFrame, { columnName, contextLine } from './ChartFrame.jsx';

// Section 4: models on identical grouped splits, and what a random split would have claimed.
// Bar = mean macro-F1 over seeds (blue: simulated); whisker = ± 1 sd across seeds (spread between
// training runs, not a confidence interval). In the split comparison the misleading random split is
// grey and the honest grouped split blue.

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
    <div className="rs-row" title={`${name}: macro-F1 ${fmtSig(mean)} ± ${fmtSig(sd)} sd over ${seeds} seed${seeds === 1 ? '' : 's'} · n ${m.n.toLocaleString()} windows`}>
      <span className="rs-row-label">{name}</span>
      <span className="rs-track" aria-hidden="true">
        <span className="rs-bar sim" style={{ width: pct(mean) }} />
        {sd > 0 && <span className="rs-whisker-sym" style={{ left: pct(mean - sd), width: pct(2 * sd) }} />}
      </span>
      <span className="rs-row-val mono">{fmtSig(mean)}<span className="rs-dim"> ± {fmtSig(sd)}</span></span>
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
        title="Models on the same honest split"
        lede="Macro-F1 (1 = perfect). Every model trained and tested on the same split, grouped by engine. The second panel is an RPM range never seen in training."
        file="model_comparison.json"
        loaded={comparison}
        column={mc?.column}
        tagNote={mc ? `sim ${mc.sim_version}` : null}
        about={(
          <>
            <p>{contextLine([mc && columnName(mc.column), mc?.split, mc && `sim ${mc.sim_version}`, 'bar = mean macro-F1, whisker = ± 1 sd across seeds (not a CI)'])}</p>
            {mc?.cnn_note && <p>CNN: {mc.cnn_note}</p>}
          </>
        )}
        table={table}
      >
        <div className="rs-multiples">
          {PANELS.map(([key, title, nKey]) => (
            <div key={key} className="rs-multiple rs-card sim">
              <p className="rs-mtitle"><strong>{title}</strong> <span className="rs-dim mono">{mc?.[nKey]?.toLocaleString()} windows</span></p>
              {MODELS.map(([k, name]) => <ModelRow key={k} name={name} m={mc?.[key]?.[k]} />)}
            </div>
          ))}
        </div>
      </ChartFrame>

      <ChartFrame
        title="Why the split matters"
        lede="The same SVM, scored two ways. A random split lets windows from one engine land on both sides, so it looks better than it is."
        file="column_a.json"
        loaded={columnA}
        column={gap?.grouped?.column}
        tagNote={gap?.grouped ? `n ${gap.grouped.n.toLocaleString()} windows` : null}
        about={<p>{contextLine([gap?.grouped?.unit, gap?.random && `random: ${gap.random.split}, ${gap.random.note}`, gap?.grouped && `grouped: ${gap.grouped.split}, ${gap.grouped.note}`])}</p>}
      >
        <div className="rs-multiple">
          {[['random', 'Random split (leaky)', 'muted'], ['grouped', 'Grouped by engine (honest)', 'sim']].map(([k, name, kind]) => {
            const r = gap?.[k];
            return r ? (
              <div key={k} className="rs-row" title={`${r.split} · ${r.note}`}>
                <span className="rs-row-label">{name}</span>
                <span className="rs-track" aria-hidden="true"><span className={`rs-bar ${kind}`} style={{ width: pct(r.value) }} /></span>
                <span className="rs-row-val mono">{fmtSig(r.value)}</span>
              </div>
            ) : null;
          })}
          {gap?.random && gap?.grouped && (
            <p className="rs-callout">A random split would have overstated the score by <strong>{fmtSig(gap.random.value - gap.grouped.value)}</strong> macro-F1.</p>
          )}
        </div>
      </ChartFrame>
    </>
  );
}
