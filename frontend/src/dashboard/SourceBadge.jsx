import { SOURCE_LABELS, sourceInfo } from '../lib/faults.js';

// Where the data comes from (state.source, or state.source_label when the backend sends it, 1.6).
// Large in the header, small as a tag on each panel.
// REAL is solid, SIMULATED dashed, INJECTED double-bordered, so it reads without colour too.
const DETAIL = {
  real: (pool) => (pool ? `AudioSet · pool ${pool}` : 'recorded data'),
  simulated: () => 'engine simulator',
  injected: () => 'injected fault',
};

export default function SourceBadge({ source, label, pool, size = 'small' }) {
  const l = label ? SOURCE_LABELS[label] : null;
  const s = l ?? sourceInfo(source);
  if (size === 'small') {
    return <span className={`src-tag src-${s.style}`} aria-label={`Source: ${s.name}`}>{s.name}</span>;
  }
  return (
    <div className={`src-badge src-${s.style}`} role="status" aria-label={`Data source: ${s.name}`}>
      <strong>{s.name}</strong>
      <span>{l ? l.detail : DETAIL[source](pool)}</span>
    </div>
  );
}
