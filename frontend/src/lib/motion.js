// Motion helpers. One rule: nothing moves unless the data does (CLAUDE.md §9); motion only makes
// a change readable instead of a jump. Display smoothing is short (≈0.1–0.3 s) so the screen never
// lags the data noticeably, and it switches off with prefers-reduced-motion.

import { createElement, useEffect, useLayoutEffect, useRef } from 'react';

export const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

const EPS = 1e-4;

// Eases a number, or an array of numbers, toward `target` with an exponential approach that is
// frame-rate independent: each frame closes 1 − e^(−dt/τ) of the gap. Calls onFrame(current) every
// animation frame while moving, writes the exact target when settled, and then stops the loop.
// onFrame writes to the DOM directly (a ref), so a 10 Hz stream doesn't re-render React at 60 fps.
// A change of shape (array length) or a reduced-motion preference snaps straight to the target.
export function useSmoothed(target, { tau = 150, onFrame }) {
  const s = useRef({ cur: null, target: null, raf: 0, last: 0 });
  const cb = useRef(onFrame);
  cb.current = onFrame;

  useLayoutEffect(() => {
    const st = s.current;
    st.target = target;
    const isArr = Array.isArray(target);
    const sameShape = st.cur != null && Array.isArray(st.cur) === isArr && (!isArr || st.cur.length === target.length);
    if (target == null || !sameShape || reducedMotion()) {
      cancelAnimationFrame(st.raf);
      st.raf = 0;
      st.cur = isArr ? target.slice() : target;
      cb.current(st.cur);
      return;
    }
    if (st.raf) return; // already easing; it picks up the new target next frame
    st.last = performance.now();
    const step = (now) => {
      const k = 1 - Math.exp(-Math.max(0, now - st.last) / tau);
      st.last = now;
      const t = st.target;
      let moving = false;
      if (Array.isArray(st.cur)) {
        if (!Array.isArray(t) || t.length !== st.cur.length) { st.cur = t.slice(); st.raf = 0; cb.current(st.cur); return; }
        for (let i = 0; i < t.length; i++) {
          const d = t[i] - st.cur[i];
          if (Math.abs(d) > EPS) { st.cur[i] += d * k; moving = true; } else st.cur[i] = t[i];
        }
      } else {
        const d = t - st.cur;
        if (Math.abs(d) > EPS * Math.max(1, Math.abs(t))) { st.cur += d * k; moving = true; } else st.cur = t;
      }
      cb.current(st.cur);
      st.raf = moving ? requestAnimationFrame(step) : 0;
    };
    st.raf = requestAnimationFrame(step);
  }, [target, tau]);

  useEffect(() => () => cancelAnimationFrame(s.current.raf), []);
}

// A number that eases between readings instead of flickering at the stream rate. The text is set
// through a ref (the span has no React children), so re-renders never flash the raw target.
export function SmoothNumber({ value, format = (v) => String(v), tau = 250, className }) {
  const ref = useRef(null);
  const fmt = useRef(format);
  fmt.current = format;
  useSmoothed(value, {
    tau,
    onFrame: (v) => { if (ref.current) ref.current.textContent = fmt.current(v); },
  });
  return createElement('span', { ref, className });
}
