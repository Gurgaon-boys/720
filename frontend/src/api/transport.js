// The one thing the UI talks to. Same interface for the mock and (task 5) the real WebSocket:
//   subscribe(channel, cb) → unsubscribe     channel: 'state' | 'obd' | 'signal' | 'ack_error' | 'status'
//   sendAck(alarm_id, operator)               sends the `ack` message; the UI waits for the broadcast
//   listScenarios() / setScenario(id)         which demo scenario is streaming (mock: sync; backend L0: async)
//   startInjection({fault, location, severity}) / stopInjection()
//                                             injection console (PROPOSED 1.4); resolve = request accepted,
//                                             the UI still waits for state.injection
//   close()
// The UI never clears an alarm itself: acked/latched only change when a new `state` arrives.

import { createWsTransport } from './wsTransport.js';
import {
  buildAck, buildObd, buildSignal, buildState, DEFAULT_SCENARIO, INJECTABLE_FAULTS, INJECTION_BASE_SCENARIO,
  injectionRaisesAlarm, scenario, scenarioRaisesAlarm, SCENARIOS,
} from '../data/mockEngine.js';

const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

export const usingMock = !API;
export const apiUrl = API || null; // the configured backend address, for messages

export function createTransport() {
  return usingMock ? createMockTransport() : createWsTransport(API);
}

const CHANNELS = ['state', 'obd', 'signal', 'ack_error', 'status'];
const STATE_MS = 100; // ~10 Hz
const OBD_MS = 1000; // ~1 Hz
const SIGNAL_MS = 200;
const ACK_ROUND_TRIP_MS = 300; // pretend network + rule layer
const DECIDE_MS = 1000; // mock rule layer: an injected fault reaches the decision after ~2 windows
const CLEAR_MS = 1000; // mock rule layer: an acked alarm whose condition is gone clears after ~2 healthy windows

function createMockTransport() {
  const subs = Object.fromEntries(CHANNELS.map((c) => [c, new Set()]));
  let scenarioId = DEFAULT_SCENARIO;
  let startedAt = performance.now();
  let alarm = null;
  let alarmCount = 0;
  let signalTick = 0;
  // injection: { fault, location, severity, started_at (unix s), at (performance.now) } while active
  let injection = null;
  // held: { fault, location } after a stop while the injected fault's alarm is still latched
  let held = null;
  let clearAt = null;
  const timers = [];

  const elapsed = () => (performance.now() - startedAt) / 1000;
  const emit = (channel, msg) => subs[channel].forEach((cb) => cb(msg));

  const newAlarm = () => {
    alarmCount += 1;
    return { alarm_id: `a_${String(alarmCount).padStart(4, '0')}`, latched: true, acked: false, acked_by: null, acked_at: null };
  };
  // what the rule layer would latch when this scenario starts
  const freshAlarm = () => (scenarioRaisesAlarm(scenarioId) ? newAlarm() : null);

  // the injection as the mock rule layer sees it right now (see mockEngine `effective`)
  const injView = () => {
    if (injection) {
      const decided = performance.now() - injection.at >= DECIDE_MS;
      const { fault, location, severity, started_at } = injection;
      return { record: { active: true, fault, location, severity, started_at }, fault, location, severity, live: true, decided };
    }
    if (held) return { record: null, ...held, live: false, decided: true };
    return null;
  };

  // mock rule layer bookkeeping, once per state tick
  const ruleLayer = () => {
    const v = injView();
    if (v?.live && v.decided && !alarm && injectionRaisesAlarm(v.fault)) alarm = newAlarm();
    if (held && clearAt != null && performance.now() >= clearAt) {
      held = null;
      alarm = null;
      clearAt = null;
    }
  };

  const sendState = () => {
    ruleLayer();
    emit('state', buildState(scenarioId, elapsed(), alarm, injView()));
  };
  const sendObd = () => emit('obd', buildObd(scenarioId, elapsed(), injView()));
  const sendSignal = () => {
    buildSignal(scenarioId, signalTick++, injView()).forEach((f) => emit('signal', f));
  };

  // a request answered after a short delay, like an HTTP round trip; `fn` throws to refuse
  const request = (fn) => new Promise((resolve, reject) => {
    setTimeout(() => {
      try {
        fn();
        resolve();
      } catch (e) {
        reject(e);
      }
    }, ACK_ROUND_TRIP_MS);
  });

  alarm = freshAlarm();
  timers.push(setInterval(sendState, STATE_MS), setInterval(sendObd, OBD_MS), setInterval(sendSignal, SIGNAL_MS));

  return {
    mock: true,

    subscribe(channel, cb) {
      if (!subs[channel]) throw new Error(`Unknown channel "${channel}"`);
      subs[channel].add(cb);
      return () => subs[channel].delete(cb);
    },

    // Mock rule layer: an ack for the active, un-acked alarm is marked acknowledged and broadcast.
    // ACK never clears the latch while the fault is present, so decision.state stays ALARM.
    // If the condition is already gone (injection stopped), the alarm clears ~2 windows later.
    // Anything else is rejected with an `ack_error`, like the backend does.
    sendAck(alarm_id, operator) {
      const msg = buildAck(alarm_id, operator);
      setTimeout(() => {
        if (!alarm || alarm.alarm_id !== msg.alarm_id || alarm.acked) {
          const reason = alarm?.alarm_id === msg.alarm_id ? 'duplicate_ack' : 'unknown_alarm_id';
          emit('ack_error', { type: 'ack_error', schema_version: msg.schema_version, sent_at: msg.sent_at, alarm_id: msg.alarm_id, reason, detail: 'mock rule layer: no matching un-acked alarm' });
          return;
        }
        alarm = { ...alarm, latched: true, acked: true, acked_by: msg.operator, acked_at: msg.sent_at };
        if (held) clearAt = performance.now() + CLEAR_MS;
        sendState();
      }, ACK_ROUND_TRIP_MS);
      return msg;
    },

    // Mock of POST /inject: same refusals the backend is asked to give (409/422 in the proposal).
    startInjection({ fault, location, severity }) {
      return request(() => {
        const s = scenario(scenarioId);
        if (s.mode !== 'live' || s.source !== 'simulated') throw new Error('mock 409: injection runs only in live mode on simulated data');
        if (scenarioId !== INJECTION_BASE_SCENARIO) throw new Error(`mock 409: the mock injects only onto "${scenario(INJECTION_BASE_SCENARIO).name}"; switch to it first`);
        if (injection) throw new Error('mock 409: already injecting; stop first');
        if (held) throw new Error('mock 409: the previous injected alarm is still latched; acknowledge it first');
        if (!INJECTABLE_FAULTS.includes(fault)) throw new Error(`mock 422: unknown fault "${fault}"`);
        if (![1, 2, 3, 4].includes(location)) throw new Error(`mock 422: cylinder must be 1–4, got ${location}`);
        if (!(severity >= 0 && severity <= 1)) throw new Error(`mock 422: severity must be 0–1, got ${severity}`);
        injection = { fault, location, severity, started_at: Date.now() / 1000, at: performance.now() };
        sendState();
      });
    },

    // Mock of POST /inject/stop. Stopping removes the fault but never clears a latched alarm:
    // an un-acked alarm stays until ACK, an acked one clears ~2 windows later.
    stopInjection() {
      return request(() => {
        if (!injection) throw new Error('mock 409: nothing is being injected');
        const decided = performance.now() - injection.at >= DECIDE_MS;
        if (alarm && decided) {
          held = { fault: injection.fault, location: injection.location, severity: injection.severity };
          if (alarm.acked) clearAt = performance.now() + CLEAR_MS;
        }
        injection = null;
        sendState();
      });
    },

    listScenarios: () => SCENARIOS,

    setScenario(id) {
      scenario(id); // throws on an unknown id
      scenarioId = id;
      startedAt = performance.now();
      signalTick = 0;
      injection = null;
      held = null;
      clearAt = null;
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
