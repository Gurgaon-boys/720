import { useEffect, useState } from 'react';
import { cylLabel, decisionColor, decisionText, locatedCyl } from '../lib/faults.js';

// Full-width decision banner (ISA-18.2 wording: only ALARM is an alarm; ADVISORY is a watch).
// Always present so the layout never jumps. ACK lives here and only here: the button sends the
// `ack` message and shows "Waiting…" until the rule layer's broadcast says acked. A rejected ACK
// (`ack_error` from the rule layer) re-enables the button and says why; it never changes the alarm.
// The operator action beside ACK is the rule layer's own `decision.guidance` (contract 1.5,
// optional, D105: one fixed line per fault and NE 107 state). The UI never writes advice: when no
// guidance arrives (the mock, or an older backend) it says so.

const BASIS = { crank_angle: 'crank angle', firing_event: 'firing event', obd: 'OBD trims' };
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

const ACK_ERROR_SHOW_MS = 8000;

function AckError({ err }) {
  return (
    <p className="ack-error mono" role="alert">
      ACK rejected · {err.reason}{err.alarm_id ? ` · ${err.alarm_id}` : ''} · {err.detail}
    </p>
  );
}

export default function AlarmBanner({ state, onAck, ackError, offline }) {
  const d = state.decision;
  const [sentFor, setSentFor] = useState(null);
  const waiting = d.alarm_id != null && sentFor === d.alarm_id && !d.acked;
  const [shownError, setShownError] = useState(null);

  // once no alarm is latched, forget the ACK we sent, so a later alarm starts with a live button
  useEffect(() => {
    if (d.alarm_id == null) setSentFor(null);
  }, [d.alarm_id]);

  // a rejection for the ACK we're waiting on ends the wait; show it for a few seconds
  useEffect(() => {
    if (!ackError) return undefined;
    if (ackError.alarm_id === sentFor) setSentFor(null);
    setShownError(ackError);
    const id = setTimeout(() => setShownError(null), ACK_ERROR_SHOW_MS);
    return () => clearTimeout(id);
  }, [ackError]); // eslint-disable-line react-hooks/exhaustive-deps

  if (d.state === 'NORMAL') {
    return (
      <div className="banner banner-normal" role="status">
        <span className="banner-kind banner-enter">NORMAL</span>
        <span className="banner-enter">No active alarms</span>
        {shownError && <AckError err={shownError} />}
      </div>
    );
  }

  const color = decisionColor(d);
  const text = decisionText(d, state.localisation);
  const hints = state.physics_rules.filter((r) => r.fired);
  const unacked = d.state === 'ALARM' && d.alarm_id && !d.acked;

  // wording shared with the decision panel and the intro (lib/faults.js decisionText)
  const { kind, title } = text;
  let note = text.sub;
  if (d.state === 'ALARM') {
    note = d.acked ? `Acknowledged by ${d.acked_by} at ${clock(d.acked_at)} · condition still active` : 'Unacknowledged';
  } else if (d.state === 'UNCLASSIFIED') {
    note = hints.length
      ? `Physics hint: ${hints.map((r) => `${r.rule}${locatedCyl(r.location, state.localisation) ? ` · ${cylLabel(r.location)}` : ''} (${BASIS[r.rule_basis] ?? r.rule_basis})`).join(', ')}`
      : 'No physics hint';
  }

  const guidance = typeof d.guidance === 'string' && d.guidance.trim() ? d.guidance.trim() : null;

  const ack = () => {
    setSentFor(d.alarm_id);
    onAck(d.alarm_id);
  };

  return (
    <section className={`banner banner-active${unacked ? ' unacked' : ''}`} style={{ '--c': color }} aria-label="Active decision">
      {/* keyed on the decision: a new state, alarm or ACK replays the short slide-in (motion = a change happened) */}
      <div className="banner-main banner-enter" role="status" key={`${d.state}-${d.alarm_id ?? ''}-${d.acked}`}>
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
      <p className={`banner-guidance${guidance ? '' : ' none'}`}>
        <span className="eyebrow">What to do</span>
        {guidance ?? 'No operator guidance sent by the rule layer'}
      </p>
      {shownError && <AckError err={shownError} />}
      {d.alarm_id && !d.acked && (
        <button type="button" className="ack-btn banner-enter" onClick={ack} disabled={waiting || offline} title={offline ? 'Diagnostics offline: ACK cannot reach the rule layer' : undefined}>
          {waiting ? 'Waiting…' : 'Acknowledge'}
        </button>
      )}
    </section>
  );
}
