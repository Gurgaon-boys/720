import { useEffect, useRef, useState } from 'react';
import { STAGES, VIDEO } from './stages.js';
import { SignatureFrame } from './SignatureFrame.jsx';
import { cylLabel, decisionColor, decisionText, traceColor } from '../lib/faults.js';
import { FIRING_ORDER, hasSignature, polarPoint } from '../lib/geometry.js';
import { reducedMotion } from '../lib/motion.js';
import { isDemoMode } from '../lib/demoMode.js';
import { createFrameDecoder } from './frames.js';
import { VIDEO_SHARE, autoplayProgress, autoplayStart, autoplayTimeline, keyToEvent, playbackStep } from './playback.js';
import './intro.css';

// Scroll easing: the shown progress closes 1 − e^(−dt/τ) of the gap to the scroll position each
// frame, so a mouse-wheel notch glides instead of jumping. Off with prefers-reduced-motion.
const SCROLL_TAU_MS = 60;
// Autoplay (▶ / Space): the 120 frames at 24 fps, then the signature, ~9 s. Scrolling takes over.
const TIMELINE = autoplayTimeline(VIDEO.frameCount);
// ?demo=1: the Play button also sits big in the middle of the first frame
const DEMO = isDemoMode();

const smooth = (a, b, x) => {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};
// 0 → 1 → 0 window with soft edges, for overlays tied to a stretch of the video
const windowed = (t, from, to, edge = 0.25) => Math.min(smooth(from - edge, from + edge, t), 1 - smooth(to - edge, to + edge, t));

export default function ScrollIntro({ state, onSkip }) {
  const trackRef = useRef(null);
  const canvasRef = useRef(null);
  const videoLayerRef = useRef(null);
  const sigLayerRef = useRef(null);
  const overlayRefs = useRef({});
  const onSkipRef = useRef(onSkip);
  onSkipRef.current = onSkip;
  const dispatchRef = useRef(() => {});
  const [stage, setStage] = useState(0);
  const [loaded, setLoaded] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [phase, setPhase] = useState('start'); // where the intro is: start | mid | end

  const d = state?.decision;
  const geometry = hasSignature(state?.signature);
  const raised = d && (d.state === 'ALARM' || d.state === 'ADVISORY');
  const color = decisionColor(d);
  const hints = (state?.physics_rules ?? [])
    .filter((r) => r.fired)
    .map((r) => `Physics rule "${r.rule}" fired${r.location ? ` on cylinder ${r.location}` : ''} (${{ crank_angle: 'crank angle', firing_event: 'firing event', obd: 'OBD trims' }[r.rule_basis] ?? r.rule_basis})`);
  const summary = {
    label: raised ? d.fault : 'healthy',
    cylinder: geometry && raised ? d.location : null,
    hint: hints[0],
    abnormal: d ? d.state !== 'NORMAL' : false,
    geometry,
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    let wanted = 0; // fractional frame index: neighbouring frames are blended
    let drawnKey = null;
    let stageNow = 0;
    let phaseNow = 'start';
    let count = 0;
    let shown = null; // eased progress on screen
    let target = 0; // progress the scroll position asks for
    let raf = 0;
    let last = 0;
    let mode = 'idle'; // 'playing': the autoplay clock drives the progress instead of the scroll bar
    let playT0 = 0; // performance.now() at autoplay time 0
    let geo = null; // track position, while autoplay moves the page along with the intro
    let expectedY = null; // scroll position autoplay last set; any other is the user scrolling

    // frames are decoded to ImageBitmaps ahead of the playhead (frames.js)
    const decoder = createFrameDecoder({
      count: VIDEO.frameCount,
      src: VIDEO.src,
      onReady: (i) => {
        count += 1;
        if (count % 10 === 0 || count === VIDEO.frameCount) setLoaded(count);
        if (drawnKey === null || Math.abs(i - wanted) <= 1.5) { drawnKey = null; draw(); }
      },
    });

    const sizeCanvas = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(canvas.clientWidth * dpr);
      canvas.height = Math.round(canvas.clientHeight * dpr);
      drawnKey = null;
    };

    const paint = (img, alpha) => {
      const cw = canvas.width;
      const ch = canvas.height;
      const iw = img.naturalWidth ?? img.width;
      const ih = img.naturalHeight ?? img.height;
      const s = Math.max(cw / iw, ch / ih); // cover
      const w = iw * s;
      const h = ih * s;
      ctx.globalAlpha = alpha;
      ctx.drawImage(img, (cw - w) / 2, (ch - h) / 2, w, h);
      ctx.globalAlpha = 1;
    };
    const nearestReady = (k) => {
      if (decoder.ready(k)) return k;
      for (let d = 1; d < VIDEO.frameCount; d++) {
        if (k - d >= 0 && decoder.ready(k - d)) return k - d;
        if (k + d < VIDEO.frameCount && decoder.ready(k + d)) return k + d;
      }
      return -1;
    };

    // draw the wanted frame (or the nearest decoded one), blended with the next frame by the
    // fractional part, so 120 stills scrub like continuous video
    function draw() {
      const k0 = Math.floor(wanted);
      const a = nearestReady(k0);
      if (a < 0) return;
      const k1 = Math.min(k0 + 1, VIDEO.frameCount - 1);
      const frac = wanted - k0;
      const blend = a === k0 && k1 !== k0 && decoder.ready(k1) && frac > 0.02 ? Math.round(frac * 50) / 50 : 0;
      const key = blend ? `${a}:${k1}:${blend}` : `${a}`;
      if (key === drawnKey) return;
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      paint(decoder.get(a), 1);
      if (blend) paint(decoder.get(k1), blend);
      drawnKey = key;
    }

    const scrollProgress = () => {
      const track = trackRef.current;
      if (!track) return 0;
      const total = track.offsetHeight - window.innerHeight;
      return total > 0 ? Math.max(0, Math.min(1, -track.getBoundingClientRect().top / total)) : 0;
    };

    const render = (p) => {
      const vp = Math.min(1, p / VIDEO_SHARE);
      const t = vp * VIDEO.duration;
      wanted = vp * (VIDEO.frameCount - 1);
      decoder.focus(Math.floor(wanted), mode === 'playing');
      draw();

      // hand-off: the last combustion frame stays, dims, and the fingerprint traces itself over it
      const dim = smooth(VIDEO_SHARE - 0.02, VIDEO_SHARE + 0.06, p);
      const traced = smooth(VIDEO_SHARE + 0.02, 0.95, p);
      videoLayerRef.current.style.filter = dim > 0.001
        ? `brightness(${(1 - dim * 0.45).toFixed(3)}) saturate(${(1 - dim * 0.2).toFixed(3)})`
        : 'none';
      videoLayerRef.current.style.transform = `scale(${(1 + dim * 0.06).toFixed(3)})`;
      const sig = sigLayerRef.current;
      sig.style.opacity = dim.toFixed(3);
      sig.style.visibility = dim < 0.01 ? 'hidden' : 'visible';
      sig.style.setProperty('--draw', traced.toFixed(4));

      // overlays tied to stretches of the video (video seconds, so they follow the footage at any speed)
      Object.entries(overlayRefs.current).forEach(([key, el]) => {
        if (!el) return;
        const [from, to] = key.split('-').map(Number);
        const o = p >= VIDEO_SHARE ? 0 : windowed(t, from, to);
        el.style.opacity = o.toFixed(3);
        el.style.visibility = o < 0.01 ? 'hidden' : 'visible';
      });

      let s = STAGES.length - 1;
      if (p < (VIDEO_SHARE + 0.97) / 2) {
        s = STAGES.findIndex((st) => st.to != null && t < st.to);
        if (s === -1) s = STAGES.length - 2;
      }
      if (s !== stageNow) {
        stageNow = s;
        setStage(s);
      }
      const ph = p < 0.01 ? 'start' : p >= 0.999 ? 'end' : 'mid';
      if (ph !== phaseNow) {
        phaseNow = ph;
        setPhase(ph);
      }
    };

    // autoplay moves the page with the intro, so scrolling (or Skip) carries on from where it is
    const trackGeo = () => {
      const track = trackRef.current;
      return { top: track.getBoundingClientRect().top + window.scrollY, total: track.offsetHeight - window.innerHeight };
    };
    const scrollToProgress = (p) => {
      window.scrollTo({ top: geo.top + p * geo.total, behavior: 'instant' });
      expectedY = window.scrollY;
    };

    // ease the shown progress toward the scroll position; the loop stops once it has arrived.
    // During autoplay the clock sets the progress instead, and the loop runs to the end.
    const step = (now) => {
      if (mode === 'playing') {
        const ms = now - playT0;
        shown = autoplayProgress(ms, TIMELINE);
        target = shown;
        scrollToProgress(shown);
        render(shown);
        if (ms >= TIMELINE.total) {
          raf = 0;
          dispatch('end');
        } else raf = requestAnimationFrame(step);
        return;
      }
      const k = 1 - Math.exp(-Math.max(0, now - last) / SCROLL_TAU_MS);
      last = now;
      shown += (target - shown) * k;
      if (Math.abs(target - shown) < 0.0002) shown = target;
      render(shown);
      raf = shown === target ? 0 : requestAnimationFrame(step);
    };

    const startPlay = () => {
      mode = 'playing';
      setPlaying(true);
      geo = trackGeo();
      playT0 = performance.now() - autoplayStart(shown ?? 0, TIMELINE);
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(step);
    };
    const stopPlay = () => {
      mode = 'idle';
      setPlaying(false);
      expectedY = null;
      last = performance.now();
      target = scrollProgress(); // the page is already there; scrolling eases on from here
    };
    // play / stop / skip, by the rules in playback.js (tested there)
    function dispatch(event) {
      const next = playbackStep(mode, event);
      if (next.mode !== mode) {
        if (next.mode === 'playing') startPlay();
        else stopPlay();
      }
      if (next.skip) onSkipRef.current?.();
    }
    dispatchRef.current = dispatch;

    const inView = () => {
      const r = trackRef.current?.getBoundingClientRect();
      return !!r && r.top < window.innerHeight / 2 && r.bottom > window.innerHeight / 2;
    };
    const onKey = (e) => {
      if (e.defaultPrevented) return;
      const el = e.target;
      const r = keyToEvent({
        key: e.key,
        tag: el?.tagName ?? 'BODY',
        editable: !!el?.isContentEditable,
        modified: e.ctrlKey || e.metaKey || e.altKey,
        inView: inView(),
        playing: mode === 'playing',
      });
      if (!r) return;
      if (r.prevent) e.preventDefault();
      if (!e.repeat) dispatch(r.event);
    };
    // wheel and touch take over at once; a scrollbar drag shows up as a scroll autoplay didn't make
    const onUserScroll = () => { if (mode === 'playing') dispatch('scroll'); };
    // a hidden page pauses animation frames: stop rather than leap ahead on return
    const onVisibility = () => { if (document.hidden && mode === 'playing') dispatch('end'); };

    const onScroll = () => {
      if (mode === 'playing') {
        if (expectedY === null || Math.abs(window.scrollY - expectedY) <= 2) return;
        dispatch('scroll');
      }
      target = scrollProgress();
      if (shown === null || reducedMotion()) {
        shown = target;
        render(shown);
        return;
      }
      if (!raf) {
        last = performance.now();
        raf = requestAnimationFrame(step);
      }
    };
    const update = () => {
      target = scrollProgress();
      shown = target;
      render(shown);
    };
    const onResize = () => {
      sizeCanvas();
      if (mode === 'playing') geo = trackGeo();
      else update();
    };

    sizeCanvas();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onResize);
    window.addEventListener('keydown', onKey);
    window.addEventListener('wheel', onUserScroll, { passive: true });
    window.addEventListener('touchmove', onUserScroll, { passive: true });
    document.addEventListener('visibilitychange', onVisibility);
    update();
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('wheel', onUserScroll);
      window.removeEventListener('touchmove', onUserScroll);
      document.removeEventListener('visibilitychange', onVisibility);
      dispatchRef.current = () => {};
      decoder.dispose();
    };
  }, []);

  const cap = STAGES[stage].caption(summary);
  const ov = (from, to) => (el) => { overlayRefs.current[`${from}-${to}`] = el; };
  const play = () => dispatchRef.current('toggle');

  return (
    <div className="intro-track" ref={trackRef}>
      <div className="intro-stage">
        <div className="intro-video" ref={videoLayerRef}>
          <canvas ref={canvasRef} aria-hidden="true" />
          {/* frame-anchored: sensor markers sit on the engine during the x-ray shot */}
          <div className="intro-frame" aria-hidden="true">
            <div ref={ov(1.6, 2.2)} className="ov-layer">
              <span className="ov-sensor" style={{ left: '30%', top: '47%' }}><i />ACCEL · block, Z-axis</span>
              <span className="ov-sensor" style={{ left: '26%', top: '60%' }}><i />MIC · intake side</span>
            </div>
          </div>
        </div>

        <div className="intro-sig" ref={sigLayerRef}>
          <div className="intro-frame">
            <SignatureFrame signature={state?.signature} trace={traceColor(d)} />
            {geometry && <CylLabels state={state} color={color} />}
            {state && !geometry && <div className="ov-nogeo">Engine geometry unknown on real clips</div>}
          </div>
        </div>

        {/* viewport-anchored data cards, wired to the rule-layer decision */}
        {state && d.state !== 'NORMAL' && (
          <div ref={ov(3.0, 4.5)} className="ov-card ov-corner" style={{ borderColor: color }}>
            <strong style={{ color }}>
              {decisionText(d, state.localisation).tag}
              {summary.cylinder ? ` · ${cylLabel(summary.cylinder)}` : ''}
            </strong>
            <span className="mono">{state.operating_point.rpm == null ? 'rpm unknown' : `${state.operating_point.rpm.toLocaleString()} rpm`} · load {state.operating_point.load_pct == null ? '—' : `${state.operating_point.load_pct}%`} · {state.source.toUpperCase()}</span>
          </div>
        )}
        {state && hints.length > 0 && (
          <div ref={ov(6.4, 9.9)} className="ov-text ov-corner">
            <strong style={{ color }}>{decisionText(d, state.localisation).title}</strong>
            {hints.slice(0, 2).map((h) => <span key={h}>{h}</span>)}
          </div>
        )}

        <div className="intro-brand">720</div>
        <div className="intro-controls">
          <button type="button" className="intro-play" aria-pressed={playing} onClick={play}>
            <span aria-hidden="true">{playing ? '❚❚' : phase === 'end' ? '↺' : '▶'}</span>
            {playing ? 'Pause' : phase === 'end' ? 'Replay' : 'Play'}
          </button>
          <button type="button" className="intro-skip" onClick={() => dispatchRef.current('skip')}>Skip to diagnostics</button>
        </div>
        {DEMO && !playing && phase === 'start' && (
          <button type="button" className="intro-play-hero" onClick={play}>
            <span aria-hidden="true">▶</span>Play intro
          </button>
        )}

        <nav className="intro-rail" aria-hidden="true">
          {STAGES.map((st, k) => (
            <span key={st.id} className={k === stage ? 'on' : ''}><em>{st.rail}</em><i /></span>
          ))}
        </nav>

        <div className="intro-caption" aria-live="polite" key={stage}>
          <div className="eyebrow intro-eyebrow">Stage {stage + 1} of {STAGES.length} · {STAGES[stage].rail}</div>
          <h2>{cap.title}</h2>
          <p>{cap.body}</p>
        </div>

        {loaded < VIDEO.frameCount && (
          <div className="intro-loading mono">Loading {Math.round((loaded / VIDEO.frameCount) * 100)}%</div>
        )}
        <div className="intro-hint">SCROLL ↓ · SPACE ▶</div>
      </div>
    </div>
  );
}

function CylLabels({ state, color }) {
  const d = state.decision;
  const hitCyl = d.state === 'ALARM' || d.state === 'ADVISORY' ? d.location : null;
  return FIRING_ORDER.map((cyl, i) => {
    const pt = polarPoint((720 / FIRING_ORDER.length) * i, 352, 800, 450);
    return (
      <span key={cyl} className="ov-cyl" style={{ left: `${(pt.x / 1600) * 100}%`, top: `${(pt.y / 900) * 100}%`, color: hitCyl === cyl ? color : undefined }}>
        C{cyl}<small>{(720 / FIRING_ORDER.length) * i}°</small>
      </span>
    );
  });
}
