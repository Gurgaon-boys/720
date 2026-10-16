// Plain-language "What happened / What it means / What to do now" (D142), for people who don't know the
// system. It only rephrases backend facts (sensor health and the backend's switched-off list, the rule
// layer's decision, witnesses and guidance text) and the fixed sensor dependency table. It never names a
// fault the rule layer did not name, and never gives a confidence.

import { LAB_SENSORS, SENSOR_INFO, offlineSet } from './evidence.js';

export const PLAIN = {
  mic: {
    name: 'microphone', what: 'listens to the engine', check: 'Check the microphone and its cable', rank: 4,
    impact: 'Only the sound picture on screen is affected. Fault detection does not rely on the microphone.',
  },
  accel: {
    name: 'vibration sensor (accelerometer)', what: 'feels the engine block vibrate',
    check: 'Check the accelerometer mounting and connector on the engine block', rank: 2,
    impact: 'Knock can no longer be detected, and the AI second opinion stops: it needs vibration data.',
  },
  crank: {
    name: 'crankshaft sensor', what: 'times every cylinder firing',
    check: 'Check the crankshaft sensor and its connector near the flywheel', rank: 1,
    impact: 'Misfire can no longer be detected or pinned to a cylinder, and the AI second opinion stops: it needs crank data.',
  },
  cam: {
    name: 'camshaft sensor', what: 'tells which stroke each cylinder is on',
    check: 'Check the camshaft sensor on the cylinder head', rank: 3,
    impact: 'Little changes for now: 720 keeps the cycle timing from the last cam signal.',
  },
};

const FAULT_PLAIN = {
  misfire: 'a misfire (a cylinder that is not burning fuel)',
  knock: 'engine knock (fuel igniting too early)',
  injector_clog: 'a clogged fuel injector (a cylinder running lean)',
  valve_tick: 'valve-train wear',
};
export const faultPlain = (f) => FAULT_PLAIN[f] ?? (f ? f.replace('_', ' ') : 'a fault');
const join = (xs) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);
const WITNESS = { physics: 'the physics check', classifier: 'the AI check' };

export function explain({ state, sensors, level }) {
  const d = state?.decision ?? {};
  const off = offlineSet(sensors);
  const removed = LAB_SENSORS.filter((ch) => off.has(ch)).sort((a, b) => PLAIN[a].rank - PLAIN[b].rank);
  const where = d.location ? ` on cylinder ${d.location}` : '';
  const alarm = d.state === 'ALARM';
  const unacked = alarm && !d.acked;
  const canConfirm = level === 'FULL';
  const guidance = typeof d.guidance === 'string' && d.guidance.trim() ? d.guidance.trim() : null;
  const happened = [];
  const means = [];
  const todo = [];

  if (removed.length) {
    removed.forEach((ch) => happened.push({
      tone: 'red',
      text: `The ${PLAIN[ch].name} stopped sending data`
        + `${sensors[ch].health === 'FAILURE' ? ' (confirmed: no data for 3 seconds)' : ' (waiting for the 3-second confirmation)'}. `
        + `It ${PLAIN[ch].what}.`,
    }));
    removed.forEach((ch) => means.push({ tone: ch === 'mic' || ch === 'cam' ? 'neutral' : 'amber', text: PLAIN[ch].impact }));
    means.push(canConfirm
      ? { tone: 'ok', text: 'Both independent checks still have their data, so new faults can still be confirmed.' }
      : { tone: 'amber', text: 'Any new fault can now reach WATCH at most, never ALARM: an alarm needs two independent checks to agree, and one of them is gone.' });
    if (alarm) {
      means.push({
        tone: 'red',
        text: unacked
          ? `The alarm for ${faultPlain(d.fault)}${where} (${d.alarm_id}) stays on. Losing a sensor never hides a problem 720 already confirmed.`
          : `The alarm for ${faultPlain(d.fault)}${where} (${d.alarm_id}) was acknowledged; it clears once its evidence is gone.`,
      });
    } else if (d.state === 'ADVISORY' && d.fault) {
      means.push({ tone: 'amber', text: `720 still sees signs of ${faultPlain(d.fault)}${where}, from one check only, so it shows WATCH, not ALARM.` });
    }
    removed.forEach((ch) => todo.push({ kind: 'sensor', urgent: PLAIN[ch].rank <= 2, text: `${PLAIN[ch].check}, then press Enable ${SENSOR_INFO[ch].short}.` }));
    if (unacked) todo.push({ kind: 'ack', text: `Acknowledge alarm ${d.alarm_id} so the record shows an operator saw it.` });
    if (guidance && d.state !== 'NORMAL') todo.push({ kind: 'engine', text: `For the engine itself: ${guidance}` });
    const names = join(removed.map((ch) => SENSOR_INFO[ch].short));
    const headline = `${names} ${removed.length > 1 ? 'are' : 'is'} offline. `
      + (canConfirm ? 'Diagnosis is unaffected.' : alarm ? 'The alarm stays on, but 720 can no longer confirm new faults.' : '720 can no longer confirm new faults.');
    return { headline, tone: canConfirm ? 'neutral' : alarm ? 'red' : 'amber', happened, means, todo, removed };
  }

  if (alarm) {
    happened.push({ tone: 'red', text: `720 detected ${faultPlain(d.fault)}${where}.` });
    means.push({ tone: 'red', text: `Two independent checks agreed (${join((d.witnesses ?? []).map((w) => WITNESS[w] ?? w))}), so the rule layer raised a confirmed ALARM.` });
    if (unacked) todo.push({ kind: 'ack', text: `Acknowledge alarm ${d.alarm_id}.` });
    if (guidance) todo.push({ kind: 'engine', text: guidance });
    todo.push({ kind: 'demo', text: 'Now take a sensor away to see what 720 does when it loses evidence.' });
    return { headline: `ALARM: ${faultPlain(d.fault)}${where}`, tone: 'red', happened, means, todo, removed };
  }
  if (d.state === 'ADVISORY') {
    happened.push({ tone: 'amber', text: d.fault ? `720 sees early signs of ${faultPlain(d.fault)}${where}.` : 'One check sees something unusual.' });
    means.push({ tone: 'amber', text: 'Only one check sees it, so it is a WATCH, not an alarm. The rule layer waits for a second, matching check.' });
    if (guidance) todo.push({ kind: 'engine', text: guidance });
    return { headline: `WATCH${d.fault ? `: ${faultPlain(d.fault)}${where}` : ''}`, tone: 'amber', happened, means, todo, removed };
  }
  if (d.state === 'UNCLASSIFIED') {
    happened.push({ tone: 'amber', text: 'The engine is behaving differently from normal, in a way 720 was not trained to name.' });
    means.push({ tone: 'amber', text: 'Instead of guessing a fault, 720 says it does not know.' });
    todo.push({ kind: 'engine', text: guidance ?? 'Inspect the engine; do not clear the alarm.' });
    return { headline: 'Abnormal, but 720 cannot name it', tone: 'amber', happened, means, todo, removed };
  }
  happened.push({ tone: 'ok', text: 'The engine is healthy and all four sensors are sending data.' });
  means.push({ tone: 'ok', text: 'Both independent checks are working, so any fault can be confirmed.' });
  todo.push({ kind: 'demo', text: 'Press Inject misfire to simulate a fault.' });
  return { headline: 'All sensors live: full evidence', tone: 'neutral', happened, means, todo, removed };
}
