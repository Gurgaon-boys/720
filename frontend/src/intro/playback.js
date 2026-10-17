// Intro autoplay: the timeline and the play / skip / Esc rules, kept free of React and the DOM so
// node --test can check them (playback.test.js).
//
// Everything on screen is a function of one progress value p in [0, 1] (the scroll position of the
// track). Autoplay drives p from a clock instead of the scroll bar: the 120 frames play at 24 fps,
// then the signature dims in and traces itself, then a short hold. About 9 s in all.

// Share of the progress range spent scrubbing the video; the rest crossfades into the signature.
export const VIDEO_SHARE = 0.75;

export const AUTOPLAY_FPS = 24;
export function autoplayTimeline(frameCount) {
  const video = ((frameCount - 1) / AUTOPLAY_FPS) * 1000; // frame 0 → last frame at exactly 24 fps
  const total = 9000;
  return { video, total, rest: total - video };
}

// Progress p for `ms` into autoplay: linear through the video, then linear through the signature
// stretch (dim, trace, hold) over the remaining time.
export function autoplayProgress(ms, tl) {
  if (ms <= 0) return 0;
  if (ms < tl.video) return (ms / tl.video) * VIDEO_SHARE;
  if (ms < tl.total) return VIDEO_SHARE + ((ms - tl.video) / tl.rest) * (1 - VIDEO_SHARE);
  return 1;
}

// Inverse of autoplayProgress, so Play resumes from wherever scrolling left the intro. At the end
// (or past it) Play starts over.
export function autoplayStart(p, tl) {
  if (p >= 0.999) return 0;
  if (p <= VIDEO_SHARE) return (Math.max(0, p) / VIDEO_SHARE) * tl.video;
  return tl.video + ((p - VIDEO_SHARE) / (1 - VIDEO_SHARE)) * tl.rest;
}

// Playback state machine. Modes: 'idle' (scroll drives the intro) and 'playing' (the clock does).
// Events:
//   toggle  ▶ button or Space: play from here, or stop where it is
//   scroll  the user scrolled (wheel, touch, scrollbar, scroll keys), or the tab was hidden: scrolling takes over
//   end     the timeline finished: hand off to the dashboard, as Skip does
//   skip    the Skip button; escape  the Esc key: stop and jump to the dashboard
// Returns the next mode and whether to jump to the dashboard.
export function playbackStep(mode, event) {
  switch (event) {
    case 'toggle': return { mode: mode === 'playing' ? 'idle' : 'playing', skip: false };
    case 'scroll': return { mode: 'idle', skip: false };
    case 'end':
    case 'skip':
    case 'escape': return { mode: 'idle', skip: true };
    default: return { mode, skip: false };
  }
}

const SCROLL_KEYS = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End']);
const OWN_SPACE = new Set(['BUTTON', 'INPUT', 'TEXTAREA', 'SELECT', 'A', 'SUMMARY']);

// Maps a keydown to a playback event, or null to leave the key alone.
//   key        KeyboardEvent.key
//   tag        tagName of the focused target ('BODY' when nothing is focused)
//   editable   the target is contenteditable
//   modified   ctrl / meta / alt held
//   inView     the intro covers at least half the viewport
//   playing    autoplay is running
// `prevent` says whether to cancel the browser default (Space would otherwise scroll a page).
export function keyToEvent({ key, tag = 'BODY', editable = false, modified = false, inView, playing }) {
  if (!inView || modified || editable) return null;
  if (key === 'Escape') return { event: 'escape', prevent: false };
  // a focused button already turns Space into a click; inputs want the space character
  if (key === ' ' || key === 'Spacebar') return OWN_SPACE.has(tag) ? null : { event: 'toggle', prevent: true };
  if (playing && SCROLL_KEYS.has(key)) return { event: 'scroll', prevent: false };
  return null;
}
