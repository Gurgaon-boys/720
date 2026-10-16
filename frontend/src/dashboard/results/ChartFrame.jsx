// One Results chart: a title, one short sentence, a coloured data-source badge, the chart, and
// the full context (split, source file, simulator version, notes) one click away under
// "About this data", next to a table view of the same numbers. A file that didn't load shows its
// reason instead of a chart.
//
// Colour means the same thing everywhere in this tab: teal = real data (column C), blue =
// simulated (column A), yellow = watch, red = ALARM. Palette checked with the dataviz validator
// (dark surface, colour-blind separation, contrast).

const COL = { A: 'A · simulated', B: 'B · injected', C: 'C · real' };
export const columnName = (c) => COL[c] ?? c;

export function contextLine(parts) {
  return parts.filter(Boolean).join(' · ');
}

// column → badge: REAL (C) teal, SIMULATED (A) blue, INJECTED (B) neutral
export function SourceTag({ column, children }) {
  const c = String(column ?? '').trim().charAt(0).toUpperCase();
  const kind = c === 'C' ? 'real' : c === 'A' ? 'sim' : 'other';
  const name = c === 'C' ? 'REAL' : c === 'A' ? 'SIMULATED' : c === 'B' ? 'INJECTED' : String(column ?? '');
  return <span className={`rs-tag ${kind}`}>{name}{children ? <span className="rs-tag-sub"> · {children}</span> : null}</span>;
}

export default function ChartFrame({ title, lede, file, loaded, column, tagNote, about, table, children }) {
  const ok = loaded?.status === 'loaded';
  return (
    <section className="panel rs-frame" aria-label={title}>
      <div className="rs-frame-head">
        <h3 className="rs-title">{title}</h3>
        {ok && column && <SourceTag column={column}>{tagNote}</SourceTag>}
      </div>
      {lede && <p className="rs-lede">{lede}</p>}
      {ok ? (
        <>
          {children}
          <div className="rs-folds">
            <details className="rs-about">
              <summary className="eyebrow">About this data</summary>
              <div className="rs-about-body">
                {about}
                <p className="rs-src mono">source: eval/results/{file}</p>
              </div>
            </details>
            {table && (
              <details className="rs-table">
                <summary className="eyebrow">Table view</summary>
                {table}
              </details>
            )}
          </div>
        </>
      ) : (
        <p className={loaded?.status === 'error' ? 'inject-error' : 'panel-note'} role={loaded?.status === 'error' ? 'alert' : undefined}>
          {loaded ? loaded.message : 'Loading…'}
        </p>
      )}
    </section>
  );
}
