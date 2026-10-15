// The one thing the UI talks to. Same interface for the mock and (task 5) the real WebSocket:
//   subscribe(channel, cb) → unsubscribe     channel: 'state' | 'obd' | 'signal'
//   sendAck(alarm_id, operator)               sends the `ack` message; the UI waits for the broadcast
//   listScenarios() / setScenario(id)         mock only: which demo scenario is streaming
//   close()
// The UI never clears an alarm itself: acked/latched only change when a new `state` arrives.

import { buildAck, buildObd, buildSignal, buildState, DEFAULT_SCENARIO, scenario, scenarioRaisesAlarm, SCENARIOS } from '../data/mockEngine.js';

const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

export const usingMock = !API;

export function createTransport() {
  if (!usingMock) {
    throw new Error(`WebSocket transport not implemented yet (task 5). VITE_API_URL is set to ${API}; unset it to use mock data.`);
  }
  return createMockTransport();
}

const CHANNELS = ['state', 'obd', 'signal'];
const STATE_MS = 100; // ~10 Hz
const OBD_MS = 1000; // ~1 Hz
const SIGNAL_MS = 200;
const ACK_ROUND_TRIP_MS = 300; // pretend network + rule layer

function createMockTransport() {
  const subs = Object.fromEntries(CHANNELS.map((c) => [c, new Set()]));
  let scenarioId = DEFAULT_SCENARIO;
  let startedAt = performance.now();
  let alarm = null;
  let alarmCount = 0;
  let signalTick = 0;
  const timers = [];

  const elapsed = () => (performance.now() - startedAt) / 1000;
  const emit = (channel, msg) => subs[channel].forEach((cb) => cb(msg));

  // what the rule layer would latch when this scenario starts
  const freshAlarm = () => {
    if (!scenarioRaisesAlarm(scenarioId)) return null;
    alarmCount += 1;
    return { alarm_id: `a_${String(alarmCount).padStart(4, '0')}`, latched: true, acked: false, acked_by: null, acked_at: null };
  };

  const sendState = () => emit('state', buildState(scenarioId, elapsed(), alarm));
  const sendObd = () => emit('obd', buildObd(scenarioId, elapsed()));
  const sendSignal = () => {
    buildSignal(scenarioId, signalTick++).forEach((f) => emit('signal', f));
  };

  alarm = freshAlarm();
  timers.push(setInterval(sendState, STATE_MS), setInterval(sendObd, OBD_MS), setInterval(sendSignal, SIGNAL_MS));

  return {
    mock: true,

    subscribe(channel, cb) {
      if (!subs[channel]) throw new Error(`Unknown channel "${channel}"`);
      subs[channel].add(cb);
      return () => subs[channel].delete(cb);
    },

    // Mock rule layer: an ack for the active, un-acked alarm clears the latch and is broadcast.
    // The fault condition is still present, so decision.state stays ALARM.
    sendAck(alarm_id, operator) {
      const msg = buildAck(alarm_id, operator);
      setTimeout(() => {
        if (!alarm || alarm.alarm_id !== msg.alarm_id || alarm.acked) {
          console.warn('[mock rule layer] ack ignored: no matching un-acked alarm', msg);
          return;
        }
        alarm = { ...alarm, latched: false, acked: true, acked_by: msg.operator, acked_at: msg.sent_at };
        sendState();
      }, ACK_ROUND_TRIP_MS);
      return msg;
    },

    listScenarios: () => SCENARIOS,

    setScenario(id) {
      scenario(id); // throws on an unknown id
      scenarioId = id;
      startedAt = performance.now();
      signalTick = 0;
      alarm = freshAlarm();
      sendState();
      sendObd();
      sendSignal();
    },

    get scenarioId() {
      return scenarioId;
    },

    close() {
      timers.forEach(clearInterval);
      CHANNELS.forEach((c) => subs[c].clear());
    },
  };
}
