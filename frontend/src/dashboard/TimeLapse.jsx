import { useState } from 'react';

// Simulator time-lapse (state.time_lapse, contract 1.6 optional; pending T1). Stream time runs `speed` x wall
// time, and every decision is made in stream time, so a time-lapse shows the same decisions sooner. Auto runs at
// 10x only while warming up, recovering after a cleared fault, or while a slow fault (fuel trims) builds up.
// The badge always says when the clock is not real time.
const WHY = {
  warming_up: 'warming up',
  recovering: 'recovering after the fault',
  building: 'fuel trims building up',
  manual: 'set by the operator',
};

export function TimeLapseBadge({ timeLapse }) {
  if (!timeLapse || timeLapse.speed === 1) return null;
  return (
    <span className="tl-badge mono" role="status" aria-label={`Time-lapse ${timeLapse.speed} times real time: ${WHY[timeLapse.reason] ?? ''}`}>
      <b>⏩ TIME-LAPSE {timeLapse.speed}×</b>
      {WHY[timeLapse.reason] && <span> · {WHY[timeLapse.reason]}</span>}
    </span>
  );
}

const CHOICES = [['auto', 'Auto'], [1, '1×'], [5, '5×'], [10, '10×']];

export default function TimeLapse({ state, transport, offline }) {
  const tl = state?.time_lapse;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (!tl || !transport?.setTimeLapse) return <TimeLapseBadge timeLapse={tl} />;
  const current = tl.mode === 'auto' ? 'auto' : tl.speed;
  const choose = (c) => {
    setBusy(true);
    setError(null);
    transport.setTimeLapse(c === 'auto' ? { mode: 'auto' } : { mode: 'manual', speed: c })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  };
  return (
    <div className="dash-runs tl-row">
      <span className="dash-runs-label" title="Auto: 10× only while warming up, recovering, or while fuel trims build up; 1× whenever there is something to read">Time-lapse</span>
      {CHOICES.map(([c, label]) => (
        <button key={c} type="button" className={`run-pill${current === c ? ' on' : ''}`} aria-pressed={current === c}
          disabled={busy || offline} onClick={() => choose(c)}>{label}</button>
      ))}
      <TimeLapseBadge timeLapse={tl} />
      {error && <span className="scenario-error" role="alert">{error}</span>}
    </div>
  );
}
