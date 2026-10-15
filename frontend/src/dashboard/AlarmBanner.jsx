import { useState } from 'react';
import { DECISION_STATES, decisionColor, fault } from '../lib/faults.js';

// Full-width decision banner (ISA-18.2 wording: only ALARM is an alarm; ADVISORY is a watch).
// Always present so the layout never jumps. ACK lives here and only here: the button sends the
// `ack` message and shows "Waiting…" until the rule layer's broadcast says acked.

const BASIS = { crank_angle: 'crank angle', firing_event: 'firing event' };
const clock = (unixS) => new Date(unixS * 1000).toLocaleTimeString();

function Witnesses({ list }) {
  return (
    <span className="witnesses">
      {['physics', 'classifier'].map((w) => {
        const has = list.includes(w);
        return <span key={w} className={has ? 'yes' : 'no'}>{w} {has ? '✓' : '✗'}</span>;
      })}
    </span>
  );
}

export default function AlarmBanner({ state, onAck }) {
  const d = state.decision;
  const [sentFor, setSentFor] = useState(null);
  const waiting = d.alarm_id != null && sentFor === d.alarm_id && !d.acked;

  if (d.state === 'NORMAL') {
    return (
      <div className="banner banner-normal" role="status">
        <span className="banner-kind">NORMAL</span>
        <span>No active alarms</span>
      </div>
    );
  }

  const color = decisionColor(d);
  const f = fault(d.fault);
  const hints = state.physics_rules.filter((r) => r.fired);
  const unacked = d.state === 'ALARM' && d.alarm_id && !d.acked;

  let kind;
  let title;
  let note;
  if (d.state === 'ALARM') {
    kind = 'ALARM';
    title = f.title(d.location);
    note = d.acked ? `Acknowledged by ${d.acked_by} at ${clock(d.acked_at)} · condition still active` : 'Unacknowledged';
  } else if (d.state === 'ADVISORY') {
    kind = 'WATCH';
    title = f.title(d.location);
    note = 'One witness only · not an alarm';
  } else {
    kind = 'INSPECT';
    title = DECISION_STATES.UNCLASSIFIED.name;
    note = hints.length
      ? `Physics hint: ${hints.map((r) => `${r.rule}${r.location ? ` · cyl ${r.location}` : ''} (${BASIS[r.rule_basis] ?? r.rule_basis})`).join(', ')}`
      : 'No physics hint';
  }

  const ack = () => {
    setSentFor(d.alarm_id);
    onAck(d.alarm_id);
  };

  return (
    <section className={`banner banner-active${unacked ? ' unacked' : ''}`} style={{ '--c': color }} aria-label="Active decision">
      <div className="banner-main" role="status">
        <span className="banner-kind">{kind}</span>
        <strong className="banner-title">{title}</strong>
        <span className="banner-note">{note}</span>
      </div>
      <div className="banner-meta mono">
        <Witnesses list={d.witnesses} />
        <span>{d.consecutive_windows} consecutive window{d.consecutive_windows === 1 ? '' : 's'}</span>
        {d.alarm_id && <span>{d.alarm_id}{d.latched ? ' · latched' : ''}</span>}
        <span>latency {state.latency_ms} ms</span>
      </div>
      {d.alarm_id && !d.acked && (
        <button type="button" className="ack-btn" onClick={ack} disabled={waiting}>
          {waiting ? 'Waiting…' : 'Acknowledge'}
        </button>
      )}
    </section>
  );
}
