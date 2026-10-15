// Real transport: the backend's three WebSocket channels behind the same interface as the mock.
//   /ws/state   state JSON (~10 Hz); also carries our `ack` upstream and `ack_error` replies
//   /ws/obd     obd JSON (~1 Hz)
//   /ws/signal  binary frames (contract/signal_frame.md), adapted to SignalStrip's frame shape
// Each socket reconnects with backoff. The transport never decides anything about alarms: it
// forwards what the rule layer broadcasts and sends ACKs.

import { decodeFrame, toUiFrame } from './signalFrame.js';

// 1.3 = the agreed contract; 1.4 = 1.3 + state.injection (PROPOSED, docs/DATA_CONTRACT.md)
const SUPPORTED_VERSIONS = ['1.3', '1.4'];
const CHANNELS = ['state', 'obd', 'signal', 'ack_error', 'status'];
const BACKOFF_MS = [500, 1000, 2000, 4000];

export function createWsTransport(apiUrl) {
  const http = apiUrl.replace(/\/$/, '');
  const ws = http.replace(/^http/, 'ws');
  const subs = Object.fromEntries(CHANNELS.map((c) => [c, new Set()]));
  const sockets = {};
  const status = { state: 'connecting', obd: 'connecting', signal: 'connecting' };
  const warned = new Set();
  let closed = false;
  let scenarioId = null;
  let backendVersion = '1.3'; // what the backend's state messages say; ACKs are sent in the same version

  const emit = (channel, msg) => subs[channel].forEach((cb) => cb(msg));
  const warnOnce = (key, ...args) => {
    if (!warned.has(key)) {
      warned.add(key);
      console.warn('[720 transport]', ...args);
    }
  };
  const setStatus = (name, s) => {
    status[name] = s;
    emit('status', { ...status });
  };
  // supported versions only; anything else is dropped rather than half-rendered
  const checkVersion = (name, msg) => {
    if (SUPPORTED_VERSIONS.includes(msg.schema_version)) return true;
    warnOnce(`ver-${name}`, `${name} message has schema_version ${msg.schema_version}, expected one of ${SUPPORTED_VERSIONS.join(', ')}; dropped`);
    return false;
  };

  // POST helper for the HTTP routes; a refusal becomes an Error with the backend's `detail`
  async function post(path, body) {
    const res = await fetch(`${http}${path}`, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = await res.json();
        if (j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
      } catch {
        // body wasn't JSON; keep the status text
      }
      throw new Error(`POST ${path}: ${res.status} ${detail}`);
    }
  }

  const handlers = {
    state(ev) {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'ack_error') {
        emit('ack_error', msg);
        return;
      }
      if (checkVersion('state', msg)) {
        backendVersion = msg.schema_version;
        emit('state', msg);
      }
    },
    obd(ev) {
      const msg = JSON.parse(ev.data);
      if (checkVersion('obd', msg)) emit('obd', msg);
    },
    signal(ev) {
      try {
        emit('signal', toUiFrame(decodeFrame(ev.data)));
      } catch (e) {
        warnOnce(`sig-${e.message}`, 'bad signal frame:', e.message);
      }
    },
  };

  function open(name, attempt = 0) {
    if (closed) return;
    const sock = new WebSocket(`${ws}/ws/${name}`);
    if (name === 'signal') sock.binaryType = 'arraybuffer';
    sockets[name] = sock;
    sock.onopen = () => {
      attempt = 0;
      setStatus(name, 'open');
    };
    sock.onmessage = (ev) => {
      try {
        handlers[name](ev);
      } catch (e) {
        warnOnce(`msg-${name}`, `bad ${name} message:`, e.message);
      }
    };
    sock.onclose = () => {
      if (closed) return;
      setStatus(name, 'reconnecting');
      setTimeout(() => open(name, attempt + 1), BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]);
    };
  }

  ['state', 'obd', 'signal'].forEach((name) => open(name));

  return {
    mock: false,

    subscribe(channel, cb) {
      if (!subs[channel]) throw new Error(`Unknown channel "${channel}"`);
      subs[channel].add(cb);
      return () => subs[channel].delete(cb);
    },

    // Sends the contract's `ack`. The banner waits for the rule layer's next `state` broadcast;
    // a rejection comes back as `ack_error`.
    sendAck(alarm_id, operator) {
      const msg = { schema_version: backendVersion, type: 'ack', alarm_id, operator, sent_at: Date.now() / 1000 };
      const sock = sockets.state;
      if (sock?.readyState === WebSocket.OPEN) {
        sock.send(JSON.stringify(msg));
      } else {
        emit('ack_error', { type: 'ack_error', alarm_id, reason: 'not_connected', detail: 'state channel is not connected; ACK not sent', sent_at: msg.sent_at });
      }
      return msg;
    },

    // L0 only: the backend's fake scenarios (GET /scenarios, POST /scenario/{name}).
    async listScenarios() {
      const res = await fetch(`${http}/scenarios`);
      if (!res.ok) throw new Error(`GET /scenarios: ${res.status}`);
      const body = await res.json();
      scenarioId = body.current;
      return body.scenarios.map((s) => ({ id: s.name, name: s.name, description: s.description }));
    },

    async setScenario(id) {
      const res = await fetch(`${http}/scenario/${encodeURIComponent(id)}`, { method: 'POST' });
      if (!res.ok) throw new Error(`POST /scenario/${id}: ${res.status}`);
      scenarioId = id;
    },

    // Injection console (proposed schema 1.4). Resolves when the backend accepts the request;
    // the UI still waits for state.injection before showing anything.
    startInjection({ fault, location, severity }) {
      return post('/inject', { fault, location, severity });
    },
    stopInjection() {
      return post('/inject/stop');
    },

    get scenarioId() {
      return scenarioId;
    },

    close() {
      closed = true;
      Object.values(sockets).forEach((s) => s.close());
      CHANNELS.forEach((c) => subs[c].clear());
    },
  };
}
