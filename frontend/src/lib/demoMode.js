// Demo mode (?demo=1 in the URL, or VITE_DEMO=1 at build/dev time): the judged demo shows only the live
// simulator: a backend that runs a fake scenario is switched to "sim" once at start. The fake scenarios stay
// available under the collapsed "Demo scenarios" row. Injection console, 2D/3D tabs and the evidence panel are unchanged.
// Without the flag nothing changes.
export const DEMO_SOURCE = 'sim';

export function isDemoMode(search = typeof window !== 'undefined' ? window.location.search : '', env = import.meta.env) {
  if (env?.VITE_DEMO === '1') return true;
  try {
    return new URLSearchParams(search).get('demo') === '1';
  } catch {
    return false;
  }
}
