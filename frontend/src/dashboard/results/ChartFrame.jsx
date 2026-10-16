// One Results chart: title, the context every number needs (column, split, source file, simulator
// version, date), the chart itself, and a table view of the same numbers. A file that didn't load
// shows its reason instead of a chart.

const COL = { A: 'A · simulated', B: 'B · injected', C: 'C · real' };

export function contextLine(parts) {
  return parts.filter(Boolean).join(' · ');
}

export const columnName = (c) => COL[c] ?? c;

export default function ChartFrame({ title, lede, file, loaded, context, table, children }) {
  return (
    <section className="panel rs-frame" aria-label={title}>
      <h3 className="rs-title">{title}</h3>
      {lede && <p className="rs-lede">{lede}</p>}
      {loaded?.status === 'loaded' ? (
        <>
          <p className="rs-context mono">{context}</p>
          {children}
          {table && (
            <details className="rs-table">
              <summary className="eyebrow">Table view</summary>
              {table}
            </details>
          )}
          <p className="rs-src mono">source: eval/results/{file}</p>
        </>
      ) : (
        <p className={loaded?.status === 'error' ? 'inject-error' : 'panel-note'} role={loaded?.status === 'error' ? 'alert' : undefined}>
          {loaded ? loaded.message : 'Loading…'}
        </p>
      )}
    </section>
  );
}
