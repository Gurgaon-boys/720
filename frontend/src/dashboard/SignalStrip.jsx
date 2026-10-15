import { useEffect, useRef } from 'react';
import SourceBadge from './SourceBadge.jsx';

// Instrument-style plots for the `signal` channel: mic + accelerometer traces and the mic STFT.
// Frames arrive in chunks (~5 per second). Between chunks each plot slides at the measured
// arrival rate, so it scrolls continuously instead of jumping. When data stops, the slide
// stops at the newest sample: nothing moves unless the data does.

const WAVE_WINDOW_S = 1.0;
const SPEC_WINDOW_S = 2.4;
const WAVE_PAD = { l: 46, r: 10, t: 8, b: 20 };
const SPEC_PAD = { l: 46, r: 58, t: 8, b: 20 };
const C = { grid: '#1e2836', zero: '#2c3a4e', text: '#8e9aab', trace: '#c3cbd6', bg: '#0a0e15' };
const FONT = '11px "JetBrains Mono", ui-monospace, monospace';

// grey colormap, dark → light (ISA-101: no rainbow, colour is reserved for alarms)
const LUT = Array.from({ length: 256 }, (_, i) => {
  const v = (i / 255) ** 1.15;
  const lo = [10, 14, 21];
  const hi = [232, 237, 244];
  return lo.map((a, k) => Math.round(a + (hi[k] - a) * v));
});
// Spectrogram levels arrive in dB, clipped to [-90, 0]. We show a 60 dB window whose top follows
// the strongest recent level (in 10 dB steps), so contrast fits the data; the colour bar always
// shows the actual dB values, so nothing is hidden or rescaled silently.
const DB_MIN = -90;
const DB_MAX = 0;
const DB_SPAN = 60;

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

// ---- arrival timing shared by all plots ----
function arrive(st) {
  const t = performance.now();
  if (st.lastAt) st.period = st.period * 0.8 + (t - st.lastAt) * 0.2;
  st.lastAt = t;
  st.dirty = true;
}
// 0 just after a chunk lands → 1 when the next one is due (capped: freezes if data stops)
function progress(st, still) {
  if (still || !st.lastAt) return 1;
  return Math.min(1, (performance.now() - st.lastAt) / st.period);
}

// match the canvas bitmap to its CSS size × devicePixelRatio
function fit(cv) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = cv.clientWidth;
  const h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

function timeAxis(ctx, x0, pw, y, windowS, step) {
  ctx.fillStyle = C.text;
  ctx.font = FONT;
  ctx.textBaseline = 'top';
  for (let s = 0; s <= windowS + 1e-9; s += step) {
    const x = x0 + pw - (s / windowS) * pw;
    ctx.textAlign = s === 0 ? 'right' : 'center';
    ctx.fillText(s === 0 ? 'now' : `−${s.toFixed(1)} s`, x, y);
  }
}

// ---------- waveform ----------
function drawWave(cv, st, still) {
  const { ctx, w, h } = fit(cv);
  const p = WAVE_PAD;
  const pw = w - p.l - p.r;
  const ph = h - p.t - p.b;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = C.bg;
  ctx.fillRect(p.l, p.t, pw, ph);

  const yOf = (v) => p.t + ph / 2 - (v / 1.1) * (ph / 2);
  // grid: amplitude ±0.5, ±1 and time every 0.25 s
  ctx.lineWidth = 1;
  [-1, -0.5, 0.5, 1].forEach((v) => {
    ctx.strokeStyle = C.grid;
    ctx.beginPath(); ctx.moveTo(p.l, Math.round(yOf(v)) + 0.5); ctx.lineTo(p.l + pw, Math.round(yOf(v)) + 0.5); ctx.stroke();
  });
  for (let s = 0.25; s < WAVE_WINDOW_S; s += 0.25) {
    const x = Math.round(p.l + pw - (s / WAVE_WINDOW_S) * pw) + 0.5;
    ctx.strokeStyle = C.grid;
    ctx.beginPath(); ctx.moveTo(x, p.t); ctx.lineTo(x, p.t + ph); ctx.stroke();
  }
  ctx.strokeStyle = C.zero;
  ctx.beginPath(); ctx.moveTo(p.l, Math.round(yOf(0)) + 0.5); ctx.lineTo(p.l + pw, Math.round(yOf(0)) + 0.5); ctx.stroke();

  ctx.fillStyle = C.text;
  ctx.font = FONT;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  [-1, 0, 1].forEach((v) => ctx.fillText(v.toFixed(1), p.l - 6, yOf(v)));
  timeAxis(ctx, p.l, pw, p.t + ph + 5, WAVE_WINDOW_S, 0.5);

  // trace: min/max per pixel column, like a scope
  const n = Math.round(WAVE_WINDOW_S * st.fs);
  const end = st.buf.length - st.chunk + progress(st, still) * st.chunk;
  const start = end - n;
  ctx.fillStyle = C.trace;
  for (let x = 0; x < pw; x++) {
    const i0 = Math.floor(start + (x / pw) * n);
    const i1 = Math.max(i0 + 1, Math.floor(start + ((x + 1) / pw) * n));
    if (i1 <= 0) continue;
    let lo = Infinity;
    let hi = -Infinity;
    for (let i = Math.max(0, i0); i < Math.min(i1, st.buf.length); i++) {
      const v = st.buf[i];
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    if (lo === Infinity) continue;
    const yTop = yOf(hi);
    ctx.fillRect(p.l + x, yTop, 1, Math.max(1, yOf(lo) - yTop));
  }
}

function pushWave(st, frame) {
  st.fs = frame.sample_rate_hz;
  st.chunk = frame.samples.length;
  const keep = Math.round(WAVE_WINDOW_S * st.fs) + 2 * st.chunk;
  st.buf = st.buf.concat(frame.samples).slice(-keep);
  arrive(st);
}

// ---------- spectrogram ----------
// paint `cols` (dB arrays) into the history canvas starting at column x0, using the current window
function paintCols(st, cols, x0) {
  if (!cols.length) return;
  const F = st.freqs.length;
  const k = cols.length;
  const img = st.hist.getContext('2d').createImageData(k, F);
  const lo = st.top - DB_SPAN;
  cols.forEach((col, x) => {
    for (let fi = 0; fi < F; fi++) {
      const v = Math.max(0, Math.min(1, (col[fi] - lo) / DB_SPAN));
      const [r, g, b] = LUT[Math.round(v * 255)];
      const o = ((F - 1 - fi) * k + x) * 4; // low frequencies at the bottom
      img.data[o] = r; img.data[o + 1] = g; img.data[o + 2] = b; img.data[o + 3] = 255;
    }
  });
  st.hist.getContext('2d').putImageData(img, x0, 0);
}

function pushSpec(st, frame) {
  const k = frame.columns.length;
  const F = frame.freqs_hz.length;
  if (!st.hist || !st.cols) {
    st.dt = frame.dt_s;
    st.freqs = frame.freqs_hz;
    st.chunk = k;
    st.width = Math.round(SPEC_WINDOW_S / st.dt) + 2 * k;
    st.hist = document.createElement('canvas');
    st.hist.width = st.width;
    st.hist.height = F;
    st.cols = [];
    st.top = null;
  }
  st.cols = st.cols.concat(frame.columns).slice(-st.width);

  // window top = strongest level on screen, rounded up to 10 dB
  let peak = DB_MIN;
  for (const col of st.cols) for (const v of col) if (v > peak) peak = v;
  const top = Math.min(DB_MAX, Math.max(DB_MIN + DB_SPAN, Math.ceil(peak / 10) * 10));

  const hctx = st.hist.getContext('2d');
  if (top !== st.top) {
    // window moved: repaint the whole history so old and new columns share one scale
    st.top = top;
    hctx.fillStyle = `rgb(${LUT[0].join(',')})`;
    hctx.fillRect(0, 0, st.width, F);
    paintCols(st, st.cols, st.width - st.cols.length);
  } else {
    // shift history left by k columns, then paint the new columns on the right
    hctx.drawImage(st.hist, -k, 0);
    paintCols(st, frame.columns, st.width - k);
  }
  arrive(st);
}

// frequency tick step that gives ~4–6 labels for the range on screen
const freqStep = (maxHz) => (maxHz <= 2500 ? 500 : maxHz <= 6000 ? 1000 : 2000);

function drawSpec(cv, st, still) {
  const { ctx, w, h } = fit(cv);
  const p = SPEC_PAD;
  const pw = w - p.l - p.r;
  const ph = h - p.t - p.b;
  ctx.clearRect(0, 0, w, h);

  const W = Math.round(SPEC_WINDOW_S / st.dt);
  const sx = st.width - st.chunk + progress(st, still) * st.chunk - W;
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(st.hist, sx, 0, W, st.hist.height, p.l, p.t, pw, ph);

  // frequency axis (kHz)
  const maxHz = st.freqs[st.freqs.length - 1];
  ctx.font = FONT;
  ctx.fillStyle = C.text;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  const step = freqStep(maxHz);
  for (let hz = 0; hz <= maxHz + 1e-6; hz += step) {
    const y = p.t + ph - (hz / maxHz) * ph;
    ctx.fillText(`${+(hz / 1000).toFixed(1)}`, p.l - 6, y);
    ctx.strokeStyle = 'rgba(30, 40, 54, 0.6)';
    ctx.beginPath(); ctx.moveTo(p.l, Math.round(y) + 0.5); ctx.lineTo(p.l + 4, Math.round(y) + 0.5); ctx.stroke();
  }
  timeAxis(ctx, p.l, pw, p.t + ph + 5, SPEC_WINDOW_S, 1);

  // colour bar (dB)
  const bx = p.l + pw + 12;
  const bw = 10;
  for (let y = 0; y < ph; y++) {
    const [r, g, b] = LUT[Math.round((1 - y / ph) * 255)];
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fillRect(bx, p.t + y, bw, 1);
  }
  ctx.strokeStyle = C.grid;
  ctx.strokeRect(bx + 0.5, p.t + 0.5, bw - 1, ph - 1);
  ctx.fillStyle = C.text;
  ctx.textAlign = 'left';
  [st.top, st.top - DB_SPAN / 2, st.top - DB_SPAN].forEach((db, i) => {
    ctx.fillText(`${db}`, bx + bw + 4, p.t + (i / 2) * ph);
  });
}

export default function SignalStrip({ signal, source }) {
  const { mic, accel, stft } = signal;
  const micRef = useRef(null);
  const accelRef = useRef(null);
  const specRef = useRef(null);
  const streams = useRef({
    mic: { buf: [], fs: 1, chunk: 0, period: 200, lastAt: 0 },
    accel: { buf: [], fs: 1, chunk: 0, period: 200, lastAt: 0 },
    stft: { hist: null, cols: [], top: null, period: 200, lastAt: 0 },
  });

  useEffect(() => { if (mic) pushWave(streams.current.mic, mic); }, [mic]);
  useEffect(() => { if (accel) pushWave(streams.current.accel, accel); }, [accel]);
  useEffect(() => { if (stft) pushSpec(streams.current.stft, stft); }, [stft]);

  // one animation loop for all three plots; with reduced motion, redraw only when data lands
  useEffect(() => {
    const still = reducedMotion();
    let raf = 0;
    const frame = () => {
      const s = streams.current;
      [[micRef, s.mic, drawWave], [accelRef, s.accel, drawWave], [specRef, s.stft, drawSpec]].forEach(([ref, st, draw]) => {
        const ready = st.hist || st.buf?.length;
        if (!ref.current || !ready) return;
        if (still && !st.dirty) return;
        draw(ref.current, st, still);
        st.dirty = false;
      });
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <section className="panel strip">
      <div className="strip-head">
        <div className="panel-title">
          <h2 className="panel-label">SIGNAL · SPECTRAL VIEW</h2>
          <SourceBadge source={source} />
        </div>
        {mic && <span className="mono strip-note">display rate {mic.sample_rate_hz / 1000} kHz (server-downsampled)</span>}
      </div>
      {!mic && !stft && <p className="panel-note">Waiting for signal…</p>}
      {mic && (
        <figure className="plot">
          <figcaption className="eyebrow">Mic · amplitude (a.u.)</figcaption>
          <canvas ref={micRef} className="plot-wave" role="img" aria-label="Microphone waveform, last second" />
        </figure>
      )}
      {mic && (accel ? (
        <figure className="plot">
          <figcaption className="eyebrow">Accelerometer · amplitude (a.u.)</figcaption>
          <canvas ref={accelRef} className="plot-wave" role="img" aria-label="Accelerometer waveform, last second" />
        </figure>
      ) : (
        <p className="panel-note">No accelerometer on this recording (audio only).</p>
      ))}
      {stft && (
        <figure className="plot">
          <figcaption className="eyebrow">Mic STFT · frequency (kHz) · level (dB) · hop {stft.dt_s * 1000} ms</figcaption>
          <canvas ref={specRef} className="plot-spec" role="img" aria-label="Mic spectrogram, last 2.4 seconds" />
        </figure>
      )}
    </section>
  );
}
