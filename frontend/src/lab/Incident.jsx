// LIVE INCIDENT TIMELINE (D138): the backend's incident record (GET /events), newest first, with the three
// phases kept apart: REQUESTED (a command arrived), ACCEPTED (the backend applied it), CONFIRMED (the
// broadcast shows the change). Times are the backend clock. A confirmation shows how long after its
// command it arrived, measured on that clock.

const SHOWN = new Set(['inject', 'clear', 'sensor_off', 'sensor_on', 'sensor_restore', 'ack', 'sensor_health',
  'classifier', 'physics', 'decision', 'source', 'opcua_event']);
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });

function toneOf(e) {
  if (e.phase === 'refused') return 'red';
  if (e.kind === 'sensor_health') return e.data.status === 'FAILURE' ? 'red' : 'ok';
  if (e.kind === 'classifier') return e.data.available ? 'ok' : 'amber';
  if (e.kind === 'decision') return e.data.state === 'ALARM' ? 'red' : e.data.state === 'NORMAL' ? 'neutral' : 'amber';
  if (e.kind === 'opcua_event') return 'ind';
  return 'neutral';
}

// the command a confirmation answers: the latest accepted command before it on the same sensor (or any)
function causeOf(e, list) {
  if (e.phase !== 'confirmed') return null;
  const want = e.kind === 'sensor_health' ? (x) => ['sensor_off', 'sensor_on', 'sensor_restore'].includes(x.kind)
    && (x.data.sensor === e.data.sensor || x.kind === 'sensor_restore')
    : e.kind === 'classifier' ? (x) => ['sensor_off', 'sensor_on', 'sensor_restore'].includes(x.kind)
      : e.kind === 'decision' && e.data.state !== 'NORMAL' ? (x) => ['inject', 'ack', 'sensor_off', 'sensor_on', 'sensor_restore'].includes(x.kind)
        : null;
  if (!want) return null;
  for (let i = list.indexOf(e) - 1; i >= 0; i -= 1) {
    const x = list[i];
    if (x.phase === 'accepted' && want(x)) return e.t - x.t < 60 ? x : null;
  }
  return null;
}

export default function Incident({ events, error, reportUrl, showOpc }) {
  const list = events.filter((e) => SHOWN.has(e.kind) && (showOpc || e.kind !== 'opcua_event'));
  const rows = list.slice(-80).reverse();
  return (
    <section className="lab-card incident" aria-label="Live incident timeline">
      <div className="lab-card-head">
        <h3>Live incident timeline</h3>
        {reportUrl
          ? <a className="lab-btn primary" href={reportUrl} target="_blank" rel="noreferrer">Export incident report</a>
          : <span className="lab-tag mono">BACKEND ONLY</span>}
      </div>
      <div className="inc-legend mono"><span className="ph requested">requested</span><span className="ph accepted">accepted</span><span className="ph confirmed">confirmed</span></div>
      {error && <p className="lab-note err">{error}</p>}
      {rows.length ? (
        <ol className="inc-log">
          {rows.map((e) => {
            const cause = causeOf(e, list);
            return (
              <li key={e.seq} className={`tone-${toneOf(e)}`}>
                <span className="inc-t mono">{clock(e.t)}</span>
                <span className={`ph ${e.phase}`}>{e.phase}</span>
                <span className="inc-text">{e.text}{e.via ? <em> · via {e.via}</em> : null}</span>
                {cause && <span className="inc-dt mono" title={`after: ${cause.text}`}>+{(e.t - cause.t).toFixed(2)} s</span>}
              </li>
            );
          })}
        </ol>
      ) : <p className="lab-note faint">Nothing recorded yet.</p>}
    </section>
  );
}
