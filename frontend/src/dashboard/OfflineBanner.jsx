import { useEffect, useState } from 'react';

// UI watchdog banner (contract rule 4): no `state` for 3 s means the decision on screen is stale.
// This is measurement health, not engine health, so it sits above the alarm banner instead of
// replacing it, and it never changes the decision. Animations freeze while it shows (.dash.offline).
//
// Two cases: no `state` has ever arrived (backend not reachable yet: nothing on screen to trust),
// or the stream stopped after working (values below are stale).
// links: per-socket status from the WebSocket transport ({state, obd, signal}); null in mock mode.

function linkText(links) {
  if (!links) return null;
  const down = Object.entries(links).filter(([, s]) => s !== 'open');
  return down.length ? down.map(([name, s]) => `${name} ${s}`).join(' · ') : 'sockets open, but no state messages';
}

export default function OfflineBanner({ lastStateAt, everConnected, apiUrl, links }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 500);
    return () => clearInterval(id);
  }, []);
  const ago = lastStateAt ? Math.max(0, (performance.now() - lastStateAt) / 1000).toFixed(0) : null;
  const where = apiUrl ? `the backend at ${apiUrl}` : 'the mock transport';
  const link = linkText(links);

  return (
    <div className="banner banner-offline" role="alert">
      <span className="banner-kind">DIAGNOSTICS OFFLINE</span>
      {everConnected ? (
        <span>No state from the rule layer for {ago} s · values below are stale · engine health unknown</span>
      ) : (
        <span>No state received yet from {where} ({ago} s) · retrying · engine health unknown</span>
      )}
      {link && <span className="banner-meta mono">{link}</span>}
    </div>
  );
}
