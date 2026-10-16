// Sabotage Lab: HTTP client for the backend's sensor controls (D140) and the Resilience Lab reads (D141).
//   GET  /sensors               which sensors the backend has switched off (+ last health)
//   POST /sensor/{ch}/off|on    switch one off / back on (409 off the simulator, 422 unknown channel)
//   POST /sensors/restore       switch every sensor back on
//   GET  /events?after=N        incident record (requested / accepted / refused / confirmed), backend clock
//   GET  /investigate           Reliability Agent: read-only tools, deterministic answers
//   GET  /industrial            OPC UA server facts (endpoint, last publish, last event, last command)
//   GET  /report                printable incident report (HTML)
//   GET  /report.pdf            the same report as a PDF download (D143)
// A reply only says a command was ACCEPTED. Whether a sensor is actually offline or live again is read
// from state.health.sensors (the backend watchdog), never from this reply.

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
    events: (after = 0) => call('GET', `/events?after=${after}`),
    investigate: (trigger) => call('GET', `/investigate${trigger ? `?trigger=${encodeURIComponent(trigger)}` : ''}`),
    industrial: () => call('GET', '/industrial'),
    reportUrl: `${http}/report`,
    pdfUrl: `${http}/report.pdf`,
  };
}
