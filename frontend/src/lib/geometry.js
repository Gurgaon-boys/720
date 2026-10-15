// Turns contract arrays into SVG paths.

// The 4-cylinder test engine (CLAUDE.md section 9). Not part of schema 1.3, and only
// meaningful for simulator / injected data; real clips have no engine geometry.
export const FIRING_ORDER = [1, 3, 4, 2];

export const hasSignature = (sig) => Boolean(sig?.crank_deg?.length);

// Animation speed. A real 720° cycle takes 120 / rpm seconds (74 ms at 1,620 rpm): too fast to see.
// Every animation is slowed by ONE constant factor, so on-screen speed stays proportional to rpm
// (idle visibly slower than high load). The factor is computed so the fastest engine speed we
// expect is still watchable. Used by CylinderBank and (later) the 3D view, always with its label.
const MAX_RPM = 6000;
const MIN_SCREEN_CYCLE_S = 0.6;
export const SLOW_FACTOR = Math.ceil((MIN_SCREEN_CYCLE_S * MAX_RPM) / 120); // = 30
export const realCycleMs = (rpm) => 120000 / rpm;
// firing angle (crank °) of each cylinder number, from the firing order
export const fireDeg = (cyl) => FIRING_ORDER.indexOf(cyl) * (720 / FIRING_ORDER.length);

// Polar "fingerprint": 720° of crank angle mapped to one turn of the circle.
export function polarPath(crankDeg, amplitude, { cx, cy, inner, scale }) {
  let d = '';
  for (let i = 0; i < crankDeg.length; i++) {
    const ph = ((crankDeg[i] / 2) * Math.PI) / 180;
    const r = inner + amplitude[i] * scale;
    d += `${i ? ' L' : 'M'}${(cx + r * Math.sin(ph)).toFixed(1)},${(cy - r * Math.cos(ph)).toFixed(1)}`;
  }
  return d + ' Z';
}

// Position of a label around the polar plot for the cylinder that fires at `deg`.
export function polarPoint(deg, radius, cx, cy) {
  const ph = ((deg / 2) * Math.PI) / 180;
  return { x: cx + radius * Math.sin(ph), y: cy - radius * Math.cos(ph) };
}
