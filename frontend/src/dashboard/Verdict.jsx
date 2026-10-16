import { cylLabel, decisionColor, decisionText, fault, locatedCyl } from '../lib/faults.js';
import SourceBadge from './SourceBadge.jsx';

const BASIS = { crank_angle: 'crank angle', firing_event: 'firing event', obd: 'OBD trims' };
const clock = (unixS) => (unixS == null ? '—' : new Date(unixS * 1000).toLocaleTimeString());

// Read-only decision details. Acknowledging happens in the alarm banner.
export default function Verdict({ state }) {
  const d = state.decision;
  const color = decisionColor(d);
  const { panelTitle: title, sub } = decisionText(d, state.localisation);
  const hints = state.physics_rules.filter((r) => r.fired);

  const rows = [
    ['State', d.state],
    ['Alarm id', d.alarm_id ?? '—'],
    ['Severity', d.severity == null ? '—' : d.severity.toFixed(2)],
    ['Witnesses', d.witnesses.length ? d.witnesses.join(' + ') : '—'],
    ['Consecutive windows', d.consecutive_windows],
    ['Latched', d.latched ? 'yes' : 'no'],
    ['Acknowledged', d.acked ? `yes · ${d.acked_by} · ${clock(d.acked_at)}` : 'no'],
  ];

  return (
    <section className="panel verdict" style={{ borderColor: d.state === 'NORMAL' ? undefined : color }}>
      <div className="panel-head">
        <h2 className="panel-label">DECISION · RULE LAYER</h2>
        <SourceBadge source={state.source} />
      </div>
      <div>
        <div className="verdict-title" style={{ color }}>{title}</div>
        <div className="verdict-sub">{sub}</div>
        {d.state === 'UNCLASSIFIED' && hints.length > 0 && (
          <div className="verdict-sub">
            Physics hint: {hints.map((r) => `${r.rule}${locatedCyl(r.location, state.localisation) ? ` · ${cylLabel(r.location)}` : ''} (${BASIS[r.rule_basis] ?? r.rule_basis})`).join(', ')}
          </div>
        )}
      </div>

      <div className="verdict-block votes-block">
        <div className="eyebrow">Models</div>
        <div className="votes">
          {state.models.map((m) => (
            <div key={m.model} className="vote">
              <span className="mono">{m.model}</span>
              <strong>{fault(m.label).name}</strong>
              <span className="mono vote-conf">p {m.p.toFixed(2)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* the alarm fields repeat the banner's; kept one click away for the record */}
      <details className="verdict-details">
        <summary className="eyebrow">Decision details</summary>
        <dl className="kv">
          {rows.map(([k, v]) => (
            <div key={k}><dt>{k}</dt><dd className="mono">{v}</dd></div>
          ))}
        </dl>
      </details>
    </section>
  );
}
