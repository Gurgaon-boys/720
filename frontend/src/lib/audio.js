// Web Audio (built into the browser, no library). Two independent sounds:
//  - engine player: plays `mic_audio` signal frames back to back, so you hear what the plots show
//  - alarm tone: a quiet two-note beep every 2 s while an alarm is unacknowledged (ISA-18.2)
// Browsers only allow audio after a user click, so both are switched on from a button.

let ctx = null;

// Create/resume the shared AudioContext. Call from inside a click handler the first time.
export function audioContext() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) throw new Error('Web Audio is not supported in this browser');
    ctx = new AC();
  }
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

const LEAD_S = 0.15; // jitter buffer: start this far ahead so late frames don't cause gaps
const MAX_AHEAD_S = 1.0; // if we're this far ahead (frames arriving too fast), drop frames

export function createEnginePlayer() {
  let out = null;
  let nextAt = 0;
  return {
    setEnabled(on) {
      if (on && !out) {
        const c = audioContext();
        out = c.createGain();
        out.gain.value = 0.8;
        out.connect(c.destination);
        nextAt = 0;
      }
      if (!on && out) {
        out.disconnect(); // cuts anything already scheduled
        out = null;
      }
    },
    push(frame) {
      if (!out) return;
      const c = audioContext();
      // first frame, or data stalled and we ran dry: re-buffer
      if (nextAt < c.currentTime + 0.02) nextAt = c.currentTime + LEAD_S;
      if (nextAt > c.currentTime + MAX_AHEAD_S) return;
      const buf = c.createBuffer(1, frame.samples.length, frame.sample_rate_hz);
      buf.copyToChannel(Float32Array.from(frame.samples), 0);
      const src = c.createBufferSource();
      src.buffer = buf;
      src.connect(out);
      src.start(nextAt);
      nextAt += buf.duration;
    },
  };
}

function beep(c) {
  const t = c.currentTime;
  [[880, 0], [660, 0.2]].forEach(([hz, dt]) => {
    const osc = c.createOscillator();
    const g = c.createGain();
    osc.type = 'triangle';
    osc.frequency.value = hz;
    // short ramps so the tone doesn't click
    g.gain.setValueAtTime(0, t + dt);
    g.gain.linearRampToValueAtTime(0.12, t + dt + 0.01);
    g.gain.setValueAtTime(0.12, t + dt + 0.14);
    g.gain.linearRampToValueAtTime(0, t + dt + 0.16);
    osc.connect(g).connect(c.destination);
    osc.start(t + dt);
    osc.stop(t + dt + 0.17);
  });
}

export function createAlarmTone() {
  let timer = null;
  return {
    // active = an alarm is latched and not yet acknowledged (from the broadcast, never local)
    set(active) {
      if (active && !timer) {
        const c = audioContext();
        beep(c);
        timer = setInterval(() => beep(c), 2000);
      }
      if (!active && timer) {
        clearInterval(timer);
        timer = null;
      }
    },
  };
}
