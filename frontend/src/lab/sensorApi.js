// Sabotage Lab: HTTP client for the backend's sensor controls (D137).
//   GET  /sensors               which sensors the backend has switched off (+ last health)
//   POST /sensor/{ch}/off|on    switch one off / back on (409 off the simulator, 422 unknown channel)
//   POST /sensors/restore       switch every sensor back on
// A reply only says the command was ACCEPTED. Whether the sensor is actually offline or live again is
// read from state.health.sensors (the backend watchdog), never from this reply.

export function createSensorApi(apiUrl) {
  if (!apiUrl) return null; // mock transport: no backend to command
  const http = apiUrl.replace(/\/$/, '');
  async function call(method, path) {
    const res = await fetch(`${http}${path}`, { method, cache: 'no-store' });
    let body = null;
    try {
      body = await res.json();
    } catch {
      // not JSON
    }
    if (!res.ok) {
      const detail = typeof body?.detail === 'string' ? body.detail : res.statusText;
      throw new Error(detail || `HTTP ${res.status}`);
    }
    return body;
  }
  return {
    status: () => call('GET', '/sensors'),
    off: (ch) => call('POST', `/sensor/${encodeURIComponent(ch)}/off`),
    on: (ch) => call('POST', `/sensor/${encodeURIComponent(ch)}/on`),
    restore: () => call('POST', '/sensors/restore'),
  };
}
