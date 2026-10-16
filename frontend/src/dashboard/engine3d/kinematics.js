// Pure geometry for the 3D engine view: no three.js, no React, so it can be checked on its own.
// Inline-4, firing order 1-3-4-2 (lib/geometry.js). Crank axis = x, cylinders stand along +y.
// Crank angle θ runs over the 720° cycle; a cylinder is at top dead centre when it fires.

import { FIRING_ORDER, fireDeg } from '../../lib/geometry.js';

export const N_CYL = FIRING_ORDER.length;
export const PITCH = 1.2; // bore centre spacing
export const BORE_R = 0.42;
export const CRANK_R = 0.36; // crank throw (half the stroke)
export const ROD_L = 1.25; // con-rod length, pin to pin
export const PISTON_H = 0.5;
export const PIN_OFFSET = 0.16; // wrist pin sits this far below the piston centre
export const DECK_Y = CRANK_R + ROD_L + PISTON_H / 2 + PIN_OFFSET + 0.08; // top of the bores
export const LINER_BOTTOM = ROD_L - CRANK_R - 0.05;

const RAD = Math.PI / 180;

// x of a cylinder's bore centre (cyl 1..4, left to right)
export const cylX = (cyl) => (cyl - (N_CYL + 1) / 2) * PITCH;

// Crank throw phase: 1 & 4 share a throw, 2 & 3 share the opposite one (fireDeg mod 360).
export const throwDeg = (cyl) => fireDeg(cyl) % 360;

// Slider-crank at crank angle θ (deg) for one cylinder:
//   pin  = crank-pin centre {y, z}   wrist = wrist-pin height y   rod = rod tilt about x (rad)
export function slider(thetaDeg, cyl) {
  const a = (thetaDeg - throwDeg(cyl)) * RAD;
  const pinY = CRANK_R * Math.cos(a);
  const pinZ = CRANK_R * Math.sin(a);
  const wrist = pinY + Math.sqrt(ROD_L * ROD_L - pinZ * pinZ);
  // rod is a box along local +y: rotating (0,1,0) by β about x gives (0, cos β, sin β)
  const rod = Math.atan2(-pinZ, wrist - pinY);
  return { pinY, pinZ, wrist, rod };
}

// Crank degrees since this cylinder last fired, 0..720.
export const sinceFire = (thetaDeg, cyl) => (((thetaDeg - fireDeg(cyl)) % 720) + 720) % 720;

// Combustion flash 0..1: fades over `spanDeg` of crank after firing.
export const flashLevel = (thetaDeg, cyl, spanDeg) => Math.max(0, 1 - sinceFire(thetaDeg, cyl) / spanDeg);

// Glow 0..1 for one cylinder's anomaly score: none at or under the threshold (ISA-101: no glow on
// healthy), then rising to full at score 1. A missing score never glows.
export function glowLevel(score, threshold) {
  if (typeof score !== 'number' || typeof threshold !== 'number' || score <= threshold) return 0;
  const span = Math.max(1e-6, 1 - threshold);
  return 0.35 + 0.65 * Math.min(1, (score - threshold) / span);
}

// Advance the crank. rpm → crank °/s is rpm/60·360; `slow` divides it so motion is readable.
// dt is clamped so a resumed tab doesn't make the crank jump a random amount.
export const MAX_DT_S = 0.1;
export function advance(thetaDeg, dtS, rpm, slow) {
  if (!rpm || !(slow > 0)) return thetaDeg;
  const dt = Math.min(Math.max(dtS, 0), MAX_DT_S);
  return (thetaDeg + (dt * rpm * 6) / slow) % 720;
}
