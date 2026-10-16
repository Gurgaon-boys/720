import { useState } from 'react';
import { fmtNum } from '../lib/format.js';

// OBD small multiples (task 6): one small line chart per channel, each on its own y-scale
// (never a shared axis across units). Every sample is drawn at its own timestamp, at the rate it
// arrived; the line breaks where samples are missing instead of joining across the gap.
// Grey by ISA-101: OBD values carry no alarm meaning here. Hover shows a sample in place.

const SOURCE = { real_carobd: 'REAL · carOBD', real_upload: 'REAL · UPLOAD', simulator: 'SIMULATOR' };
// 1.6: a channel an injection modified (lean drift on a REAL replay: STFT, LTFT) says so; the rest stay REAL
const injected = (ch) => ch.source_label?.endsWith('_injected');
const tag = (ch) => (injected(ch) ? 'REAL + INJECTED' : SOURCE[ch.source] ?? ch.source);
const WINDOW_S = 60; // default; a REAL replay passes a longer window (file time)
const W = 200;
const H = 56;
const PAD = { t: 6, b: 6, l: 2, r: 6 };
const GAP_FACTOR = 1.5; // a step longer than 1.5 × the usual spacing is a gap
const MIN_SPAN_FRAC = 0.1; // y-axis spans at least 10 % of the channel's typical value

function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

// samples inside the window, the usual spacing, and summary numbers for one channel
function analyse(ch, WINDOW_S) {
  const tEnd = ch.t[ch.t.length - 1];
  const idx = ch.t.map((t, i) => i).filter((i) => ch.t[i] >= tEnd - WINDOW_S);
  const t = idx.map((i) => ch.t[i]);
  const v = idx.map((i) => ch.v[i]);
  const dt = median(t.slice(1).map((x, i) => x - t[i]));
  return { t, v, tEnd, dt, min: Math.min(...v), max: Math.max(...v), last: v[v.length - 1] };
}

function Spark({ a, unit, hover, setHover, WINDOW_S }) {
  const { t, v, tEnd, dt } = a;
  // y-range: the data's min–max, but never narrower than 10 % of the typical value, so ±1 %
  // sensor noise on a steady channel (rpm, coolant) draws as a near-flat line, not as big swings
  const mid = (a.min + a.max) / 2;
  const span = Math.max(a.max - a.min, Math.abs(mid) * MIN_SPAN_FRAC, 1e-6);
  let lo = mid - span / 2;
  let hi = mid + span / 2;
  const pad = span * 0.1;
  lo -= pad;
  hi += pad;
  const x = (ti) => PAD.l + ((ti - (tEnd - WINDOW_S)) / WINDOW_S) * (W - PAD.l - PAD.r);
  const y = (vi) => PAD.t + (1 - (vi - lo) / (hi - lo)) * (H - PAD.t - PAD.b);

  let d = '';
  t.forEach((ti, i) => {
    const gap = i === 0 || (dt && ti - t[i - 1] > GAP_FACTOR * dt);
    d += `${gap ? 'M' : 'L'}${x(ti).toFixed(1)},${y(v[i]).toFixed(1)} `;
  });

  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const tAt = tEnd - WINDOW_S + ((e.clientX - r.left) / r.width) * WINDOW_S;
    let best = 0;
    t.forEach((ti, i) => { if (Math.abs(ti - tAt) < Math.abs(t[best] - tAt)) best = i; });
    setHover(best);
  };
  const at = hover ?? t.length - 1;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="spark" role="img"
      aria-label={`Last ${WINDOW_S} s: min ${fmtNum(a.min)}, max ${fmtNum(a.max)}, latest ${fmtNum(a.last)} ${unit}`}
      onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      <line x1={PAD.l} x2={W - PAD.r} y1={y(a.max)} y2={y(a.max)} className="spark-ref" />
      <line x1={PAD.l} x2={W - PAD.r} y1={y(a.min)} y2={y(a.min)} className="spark-ref" />
      <path d={d} className="spark-line" />
      {hover != null && <line x1={x(t[at])} x2={x(t[at])} y1={0} y2={H} className="spark-cross" />}
      <circle cx={x(t[at])} cy={y(v[at])} r="3.5" className="spark-dot" />
    </svg>
  );
}

function Cell({ name, ch, windowS }) {
  const [hover, setHover] = useState(null);
  const a = analyse(ch, windowS);
  const shown = hover != null ? a.v[hover] : a.last;
  const when = hover != null ? `${fmtNum(a.t[hover] - a.tEnd)} s` : 'latest';
  return (
    <div className="obd-cell">
      <div className="obd-cell-head">
        <span className="eyebrow">{name}</span>
        <span className={`obd-src ${ch.source}${injected(ch) ? ' injected' : ''}`}>{tag(ch)}</span>
      </div>
      <div className="tile-val mono">{fmtNum(shown)}<span> {ch.unit}</span></div>
      <Spark a={a} unit={ch.unit} hover={hover} setHover={setHover} WINDOW_S={windowS} />
      <div className="obd-meta mono">
        <span>{when}</span>
        <span>{fmtNum(a.min)}–{fmtNum(a.max)}</span>
        <span>{a.dt ? `${fmtNum(1 / a.dt)} Hz` : '—'}</span>
      </div>
    </div>
  );
}

// hasObd: the state's health.sensors lists an OBD sensor; if not, this recording has no OBD at all
// silentFor: seconds since the last obd message, once it passes the stale limit (else null)
// primary: channel names drawn by default, the rest behind "Show all"; without it (a REAL replay,
// where the OBD log is the evidence) every channel is drawn. The table always lists every channel.
export default function ObdPanel({ obd, source, hasObd, silentFor, windowS = WINDOW_S, primary = null }) {
  const [showAll, setShowAll] = useState(false);
  const channels = Object.entries(obd);
  // an AudioSet clip with carOBD beside it; on a carOBD replay the OBD is the recording itself
  const separateRecording = source === 'real' && windowS === WINDOW_S && channels.some(([, ch]) => ch.source === 'real_carobd');
  const main = primary ? channels.filter(([name]) => primary.includes(name)) : channels;
  // if none of the primary channels is in this stream, draw everything rather than nothing
  const collapsible = primary != null && main.length > 0 && main.length < channels.length;
  const drawn = collapsible && !showAll ? main : channels;

  return (
    <section className={`panel obd${silentFor != null ? ' stale' : ''}`}>
      <div className="panel-head">
        <h2 className="panel-label">OBD · TRUE RATE</h2>
        {collapsible && (
          <button type="button" className="toggle-btn" aria-expanded={showAll} onClick={() => setShowAll((v) => !v)}>
            {showAll ? `Show ${main.length} main channels` : `Show all ${channels.length} channels`}
          </button>
        )}
      </div>
      {silentFor != null && <p className="stale-note mono" role="status">No OBD data for {silentFor} s · showing last values</p>}
      {separateRecording && (
        <p className="panel-note">Separate carOBD recording, not the same vehicle as the audio clip.</p>
      )}
      {channels.length === 0 ? (
        <p className="panel-note">{hasObd ? 'Waiting for OBD…' : 'No OBD on this recording.'}</p>
      ) : (
        <>
          <div className="obd-grid">
            {drawn.map(([name, ch]) => <Cell key={name} name={name} ch={ch} windowS={windowS} />)}
          </div>
          <p className="bank-note mono">Last {windowS} s per channel{windowS !== WINDOW_S ? ' (file time)' : ''}, own scale each · line breaks where samples are missing · hover for a sample</p>
          <details className="obd-table">
            <summary className="eyebrow">Table · all {channels.length} channels</summary>
            <table className="mono">
              <thead>
                <tr><th scope="col">channel</th><th scope="col">latest</th><th scope="col">min</th><th scope="col">max</th><th scope="col">unit</th><th scope="col">rate</th><th scope="col">n</th><th scope="col">source</th></tr>
              </thead>
              <tbody>
                {channels.map(([name, ch]) => {
                  const a = analyse(ch, windowS);
                  return (
                    <tr key={name}>
                      <th scope="row">{name}</th><td>{fmtNum(a.last)}</td><td>{fmtNum(a.min)}</td><td>{fmtNum(a.max)}</td>
                      <td>{ch.unit}</td><td>{a.dt ? `${fmtNum(1 / a.dt)} Hz` : '—'}</td><td>{a.t.length}</td><td>{tag(ch)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </details>
        </>
      )}
    </section>
  );
}
