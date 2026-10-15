import { DECISION_STATES, decisionColor, fault } from '../lib/faults.js';
import SourceBadge from './SourceBadge.jsx';

const BASIS = { crank_angle: 'crank angle', firing_event: 'firing event' };
const clock = (unixS) => (unixS == null ? '—' : new Date(unixS * 1000).toLocaleTimeString());

function headline(d) {
  const f = fault(d.fault);
  if (d.state === 'ALARM') return { title: f.title(d.location), sub: f.sub };
  if (d.state === 'ADVISORY') return { title: `Watch · ${f.title(d.location)}`, sub: 'One witness only. Not an alarm.' };
  if (d.state === 'UNCLASSIFIED') return { title: DECISION_STATES.UNCLASSIFIED.name, sub: 'The classifiers cannot name this pattern.' };
  return { title: DECISION_STATES.NORMAL.name, sub: 'No rule or classifier witness.' };
}

// Read-only decision details. Acknowledging happens in the alarm banner.
export default function Verdict({ state }) {
  const d = state.decision;
  const color = decisionColor(d);
  const { title, sub } = headline(d);
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
            Physics hint: {hints.map((r) => `${r.rule}${r.location ? ` · cyl ${r.location}` : ''} (${BASIS[r.rule_basis] ?? r.rule_basis})`).join(', ')}
          </div>
        )}
      </div>

      <dl className="kv">
        {rows.map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd className="mono">{v}</dd></div>
        ))}
      </dl>

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
    </section>
  );
}
