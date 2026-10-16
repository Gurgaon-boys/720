// Decodes the intro frames ahead of time so drawing one never waits on a JPEG/WebP decode (the
// stutter on the first play). Each frame is fetched and turned into an ImageBitmap off the main
// thread: the first `eager` frames at once, the rest one or two at a time in idle callbacks, nearest
// the playhead first. While the intro plays, or when the frame it wants isn't ready, decoding stops
// waiting for idle time.
//
// Memory: a decoded 1280×720 frame is ~3.7 MB, so all 120 hold ~440 MB until dispose() closes them
// (on unmount). Only the bitmaps are kept; fetched blobs are dropped once decoded.

const URGENT_IN_FLIGHT = 4;
const IDLE_IN_FLIGHT = 2;

const PENDING = 0;
const LOADING = 1;
const READY = 2;
const FAILED = 3;

export function createFrameDecoder({ count, src, eager = 20, onReady }) {
  const frames = new Array(count).fill(null);
  const status = new Uint8Array(count);
  const abort = new AbortController();
  const ric = window.requestIdleCallback?.bind(window) ?? ((cb) => setTimeout(cb, 50));
  const cic = window.cancelIdleCallback?.bind(window) ?? clearTimeout;
  let inFlight = 0;
  let head = 0; // frame the intro wants now: decode from here forward first
  let playing = false;
  let idleId = 0;
  let disposed = false;

  const decode = async (i) => {
    const res = await fetch(src(i), { signal: abort.signal });
    if (!res.ok) throw new Error(`intro frame ${i + 1}: HTTP ${res.status}`);
    const blob = await res.blob();
    if (typeof createImageBitmap === 'function') return createImageBitmap(blob);
    // old browsers: a decoded <img> works with drawImage too
    const url = URL.createObjectURL(blob);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      return img;
    } finally {
      URL.revokeObjectURL(url);
    }
  };

  const nextPending = () => {
    for (let i = head; i < count; i++) if (status[i] === PENDING) return i;
    for (let i = 0; i < head; i++) if (status[i] === PENDING) return i;
    return -1;
  };
  const pump = (limit) => {
    while (!disposed && inFlight < limit) {
      const i = nextPending();
      if (i < 0) return;
      start(i);
    }
  };
  // no idle waiting while the intro plays, or while the frame it wants is still missing
  const urgent = () => playing || status[head] !== READY;
  const schedule = () => {
    if (disposed) return;
    if (urgent()) pump(URGENT_IN_FLIGHT);
    else if (!idleId && inFlight < IDLE_IN_FLIGHT) {
      idleId = ric(() => { idleId = 0; pump(IDLE_IN_FLIGHT); }, { timeout: 1000 });
    }
  };
  function start(i) {
    status[i] = LOADING;
    inFlight += 1;
    decode(i)
      .then((bmp) => {
        if (disposed) { bmp.close?.(); return; }
        frames[i] = bmp;
        status[i] = READY;
        onReady(i);
      }, () => { if (!disposed) status[i] = FAILED; })
      .finally(() => { inFlight -= 1; schedule(); });
  }

  for (let i = 0; i < Math.min(eager, count); i++) start(i);

  return {
    get: (i) => frames[i],
    ready: (i) => status[i] === READY,
    // where the intro is now, and whether it is playing
    focus(i, isPlaying) {
      head = Math.max(0, Math.min(count - 1, i));
      playing = isPlaying;
      schedule();
    },
    dispose() {
      disposed = true;
      abort.abort();
      if (idleId) cic(idleId);
      frames.forEach((f, i) => { f?.close?.(); frames[i] = null; });
    },
  };
}
