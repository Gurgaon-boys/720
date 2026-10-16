import { decisionColor, decisionText } from './faults.js';

// Alarm events (Raised → Acknowledged → Cleared) from consecutive state broadcasts. Called once per
// received message in the transport subscriber, not from a render: React can merge two quick
// renders and skip a message (an ACK broadcast followed at once by NORMAL), but the subscriber sees
// every message. Nothing is inferred: an event is recorded when a broadcast shows the change.
// Time = the backend's acked_at for ACK, otherwise the sent_at of the message that showed it.
//
// prev: what the last message showed ({ run_id, alarm_id, acked }) or null before the first one.
// Returns { events, next }.
export function observeDecision(prev, msg) {
  const d = msg.decision;
  const at = msg.sent_at;
  const alarmId = d.alarm_id ?? null;
  const label = alarmId ? `${decisionText(d, msg.localisation).title} · ${alarmId}` : null;
  const color = alarmId ? decisionColor(d) : undefined;
  const events = [];

  if (!prev) {
    // first message this session: an alarm already latched was raised (and maybe acked) unseen
    if (alarmId) events.push({ kind: 'Already active', text: `${label}${d.acked ? ' · already acknowledged' : ''}`, at, color, raised: true });
  } else {
    if (prev.run_id !== msg.run_id) events.push({ kind: 'New run', text: msg.run_id, at });
    if (prev.alarm_id && prev.alarm_id !== alarmId) events.push({ kind: 'Cleared', text: prev.alarm_id, at });
    if (alarmId && prev.alarm_id !== alarmId) events.push({ kind: 'Raised', text: label, at, color, raised: true });
    if (alarmId && d.acked && !(prev.alarm_id === alarmId && prev.acked)) {
      events.push({ kind: 'Acknowledged', text: `${alarmId}${d.acked_by ? ` by ${d.acked_by}` : ''}`, at: d.acked_at ?? at });
    }
  }
  return { events, next: { run_id: msg.run_id, alarm_id: alarmId, acked: Boolean(d.acked) } };
}
