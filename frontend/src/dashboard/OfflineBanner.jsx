import { useEffect, useState } from 'react';

// UI watchdog banner (contract rule 4): no `state` for 3 s means the decision on screen is stale.
// This is measurement health, not engine health, so it sits above the alarm banner instead of
// replacing it, and it never changes the decision. Animations freeze while it shows (.dash.offline).

export default function OfflineBanner({ lastStateAt }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 500);
    return () => clearInterval(id);
  }, []);
  const ago = lastStateAt ? Math.max(0, (performance.now() - lastStateAt) / 1000) : null;

  return (
    <div className="banner banner-offline" role="alert">
      <span className="banner-kind">DIAGNOSTICS OFFLINE</span>
      <span>No state from the rule layer {ago != null ? `for ${ago.toFixed(0)} s` : 'yet'} · values below are stale · engine health unknown</span>
    </div>
  );
}
