// Live mic tab: OFF unless the URL has ?mic=1. Without the flag nothing changes (no tab, no getUserMedia).
export function isMicMode(search = typeof window !== 'undefined' ? window.location.search : '') {
  try {
    return new URLSearchParams(search).get('mic') === '1';
  } catch {
    return false;
  }
}
