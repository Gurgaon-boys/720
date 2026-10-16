import SourceBadge from './SourceBadge.jsx';

// Plain readout of the rest of the schema-1.3 `state` message: anomaly, physics rules, features
// and stream metadata. Health chips live in the header. Later tasks restyle or absorb these pieces.

const BASIS = { crank_angle: 'crank angle', firing_event: 'firing event' };
const clock = (unixS) => new Date(unixS * 1000).toLocaleTimeString();

export default function StatePanel({ state }) {
  const { anomaly, localisation } = state;
  const meta = [
    ['schema', state.schema_version],
    ['run_id', state.run_id],
    ['source', state.source],
    ['audioset_pool', state.audioset_pool ?? '—'],
    ['engine_tag', state.engine_tag ?? '—'],
    ['mode', state.mode],
    ['localisation', localisation],
    ['t', `${state.t.toFixed(1)} s`],
    ['sent_at', clock(state.sent_at)],
    ['latency', `${state.latency_ms} ms`],
  ];

  return (
    <section className="panel state-panel">
      <div className="panel-head">
        <h2 className="panel-label">STATE</h2>
        <SourceBadge source={state.source} />
      </div>

      <div className="verdict-block">
        <div className="eyebrow">
          {anomaly.score === null
            ? 'Anomaly · no healthy model yet (score null, schema 1.5)'
            : `Anomaly · score ${anomaly.score.toFixed(2)} / threshold ${anomaly.threshold.toFixed(2)}`}
        </div>
        {localisation === 'cylinder' && <p className="panel-note">Per-cylinder scores are shown under each cylinder in the bank.</p>}
      </div>

      <div className="verdict-block">
        <div className="eyebrow">Physics rules</div>
        {state.physics_rules.length === 0 ? (
          <p className="panel-note">No crank-angle rules on real clips.</p>
        ) : (
          <ul className="rules">
            {state.physics_rules.map((r) => (
              <li key={r.rule} className={r.fired ? 'fired' : undefined}>
                <span className="mono">{r.fired ? 'FIRED' : 'quiet'}</span>
                {r.rule}{r.location ? ` · cyl ${r.location}` : ''}
                <small>{BASIS[r.rule_basis] ?? r.rule_basis}</small>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="verdict-block">
        <div className="eyebrow">Features</div>
        <dl className="kv">
          {Object.entries(state.features_summary).map(([k, v]) => (
            <div key={k}><dt>{k}</dt><dd className="mono">{v}</dd></div>
          ))}
        </dl>
      </div>

      <details className="verdict-block">
        <summary className="eyebrow">Stream · units</summary>
        <dl className="kv">
          {meta.map(([k, v]) => (
            <div key={k}><dt>{k}</dt><dd className="mono">{v}</dd></div>
          ))}
          {Object.entries(state.units).map(([k, v]) => (
            <div key={`u-${k}`}><dt>unit · {k}</dt><dd className="mono">{v}</dd></div>
          ))}
        </dl>
      </details>
    </section>
  );
}
