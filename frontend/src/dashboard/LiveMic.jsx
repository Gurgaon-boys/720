import { useEffect, useRef, useState } from 'react';
import { fmtNum } from '../lib/format.js';
import './livemic.css';

// Live microphone tab (optional: only with ?mic=1). REAL audio from this laptop's microphone, streamed to the
// backend's /ws/mic in 0.5 s chunks of 48 kHz mono float32 and scored there with the existing real-audio path
// (the AudioSet P3 healthy model and the knock-band score, backend/live_mic.py). The spectrogram is drawn here
// from the browser's own analyser. METERS ONLY, NO VERDICT: on the vetted clips the knock-band score did not
// separate knock from healthy engines (eval/audio_p3.json: 1/19 vs 1/32 over threshold). Isolated from the main
// stream: nothing here reaches the rule layer, the alarm banner or OPC UA.

const SR = 48000;
const CHUNK = SR / 2; // 0.5 s
const KNOCK_LO = 5000;
const KNOCK_HI = 15000;
const SPEC_H = 160;

// AudioWorklet that forwards raw input blocks to the page (inline module, no extra file to serve)
const WORKLET = `class Tap extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor('mic-tap', Tap);`;

function Meter({ label, value, limit, unit, note }) {
  // bar scaled so the limit sits at 2/3 of the track; the number is always printed beside it
  const frac = value == null ? 0 : Math.min(1, value / (1.5 * limit));
  const over = value != null && value > limit;
  return (
    <div className="mic-meter">
      <div className="mic-meter-head">
        <span className="eyebrow">{label}</span>
        <span className={`mono${over ? ' mic-over' : ''}`}>{value == null ? '—' : `${fmtNum(value)}${unit}`} · limit {fmtNum(limit)}{unit}</span>
      </div>
      <div className="mic-track" role="meter" aria-label={label} aria-valuenow={value ?? 0} aria-valuemin={0} aria-valuemax={1.5 * limit}>
        <div className={`mic-fill${over ? ' over' : ''}`} style={{ width: `${100 * frac}%` }} />
        <div className="mic-limit" style={{ left: `${100 / 1.5}%` }} />
      </div>
      {note && <p className="bank-note mono">{note}</p>}
    </div>
  );
}

export default function LiveMic({ apiUrl }) {
  const [status, setStatus] = useState('idle'); // idle | starting | live | error
  const [error, setError] = useState(null);
  const [last, setLast] = useState(null); // latest mic_window from the backend
  const [count, setCount] = useState(0);
  const run = useRef(null); // { ctx, stream, ws, node, analyser, raf }
  const canvasRef = useRef(null);

  const stop = () => {
    const r = run.current;
    run.current = null;
    if (!r) return;
    cancelAnimationFrame(r.raf);
    r.node?.port.close();
    r.stream?.getTracks().forEach((t) => t.stop());
    try { r.ws?.close(); } catch { /* already closed */ }
    r.ctx?.close();
  };
  useEffect(() => stop, []);

  const fail = (msg) => {
    stop();
    setError(msg);
    setStatus('error');
  };

  const start = async () => {
    if (!apiUrl) { fail('The live mic needs the backend (VITE_API_URL); the mock has no /ws/mic.'); return; }
    setError(null);
    setStatus('starting');
    setLast(null);
    setCount(0);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, sampleRate: SR, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
      const ctx = new AudioContext({ sampleRate: SR });
      if (ctx.sampleRate !== SR) throw new Error(`the browser opened the audio at ${ctx.sampleRate} Hz, not ${SR} Hz`);
      const url = URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' }));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      const src = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, 'mic-tap');
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0;
      src.connect(node);
      src.connect(analyser);
      const ws = new WebSocket(`${apiUrl.replace(/\/$/, '').replace(/^http/, 'ws')}/ws/mic`);
      ws.binaryType = 'arraybuffer';
      const r = { ctx, stream, ws, node, analyser, raf: 0 };
      run.current = r;

      // 0.5 s chunks of float32, sent only once the backend has accepted the hello
      let buf = new Float32Array(CHUNK);
      let fill = 0;
      let ready = false;
      node.port.onmessage = (e) => {
        if (!ready || ws.readyState !== WebSocket.OPEN) return;
        let x = e.data;
        while (x.length) {
          const n = Math.min(CHUNK - fill, x.length);
          buf.set(x.subarray(0, n), fill);
          fill += n;
          x = x.subarray(n);
          if (fill === CHUNK) { ws.send(buf.buffer); buf = new Float32Array(CHUNK); fill = 0; }
        }
      };
      ws.onopen = () => ws.send(JSON.stringify({ type: 'mic_hello', sample_rate: SR, channels: 1 }));
      ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === 'mic_ready') { ready = true; setStatus('live'); }
        else if (m.type === 'mic_window') { setLast(m); setCount((c) => c + 1); }
        else if (m.type === 'mic_error') fail(`Backend refused the audio: ${m.reason} (${m.detail})`);
      };
      ws.onerror = () => fail('Could not reach /ws/mic on the backend.');
      ws.onclose = () => { if (run.current === r) fail('The backend closed the mic socket.'); };

      // scrolling spectrogram 0-24 kHz from the browser's analyser; the knock band is marked
      const bins = new Uint8Array(analyser.frequencyBinCount);
      const draw = () => {
        const c = canvasRef.current;
        if (c) {
          const g = c.getContext('2d');
          g.drawImage(c, -2, 0);
          analyser.getByteFrequencyData(bins);
          for (let y = 0; y < SPEC_H; y += 1) {
            const v = bins[Math.floor(((SPEC_H - 1 - y) / SPEC_H) * bins.length)];
            g.fillStyle = `rgb(${v * 0.75},${v * 0.8},${v})`;
            g.fillRect(c.width - 2, y, 2, 1);
          }
        }
        r.raf = requestAnimationFrame(draw);
      };
      r.raf = requestAnimationFrame(draw);
    } catch (e) {
      fail(e.name === 'NotAllowedError' ? 'Microphone permission was denied.' : e.message);
    }
  };

  const yOf = (hz) => SPEC_H * (1 - hz / (SR / 2));
  const a = last?.anomaly;
  const k = last?.knock_band;

  return (
    <section className="panel mic-panel" aria-label="Live microphone">
      <div className="panel-head">
        <div className="panel-title">
          <h2 className="panel-label">LIVE MIC</h2>
        </div>
        <div className="src-badge src-solid mic-badge" role="status" aria-label="Data source: REAL, live microphone">
          <strong>REAL · LIVE MIC</strong>
          <span>audio only: no cylinder, ADVISORY at most</span>
        </div>
      </div>

      <div className="mic-controls">
        {status === 'live' || status === 'starting' ? (
          <button type="button" className="ack-btn" onClick={() => { stop(); setStatus('idle'); }}>Stop microphone</button>
        ) : (
          <button type="button" className="ack-btn" onClick={start}>Start microphone</button>
        )}
        <span className="mono bank-note">
          {status === 'live' ? `streaming 48 kHz mono · ${count} windows scored` : status === 'starting' ? 'asking for the microphone…' : 'off'}
        </span>
      </div>
      {error && <p className="inject-error" role="alert">{error}</p>}

      <div className="mic-spec">
        <canvas ref={canvasRef} width={720} height={SPEC_H} aria-label="Live spectrogram, 0 to 24 kHz" />
        <div className="mic-band" style={{ top: yOf(KNOCK_HI), height: yOf(KNOCK_LO) - yOf(KNOCK_HI) }} aria-hidden="true">
          <span className="mono">knock band 5–15 kHz</span>
        </div>
        <p className="bank-note mono">Spectrogram 0–24 kHz (browser analyser), newest on the right.</p>
      </div>

      <div className="mic-meters">
        <Meter label="Anomaly score (healthy-engine model, 0–4 kHz)" value={a ? a.ratio : null} limit={1} unit=""
          note={a ? `Mahalanobis ${fmtNum(a.distance)} vs ${fmtNum(a.threshold)} (AudioSet P3 healthy model)` : last?.silent ? 'silence: not scored' : null} />
        <Meter label="Knock-band impulsiveness (5–15 kHz envelope kurtosis)" value={k ? k.envelope_kurtosis : null} limit={k ? k.threshold : 15.45} unit=""
          note={k ? `band energy ${fmtNum(k.energy_db_rel)} dB relative to the whole window` : null} />
        <p className="mono bank-note">Level {last ? `${fmtNum(last.rms_dbfs)} dBFS` : '—'}</p>
      </div>

      <p className="panel-note mic-noverdict">
        No verdict here, meters only. On vetted real recordings the knock-band score did not separate knocking engines from
        healthy ones (1 of 19 knock clips vs 1 of 32 healthy clips over the limit), so a line like &ldquo;knock detected&rdquo;
        would not be honest. Nothing on this tab reaches the rule layer, the alarm banner or OPC UA.
      </p>
    </section>
  );
}
