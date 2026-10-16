// Sabotage Lab: evidence AVAILABILITY from the broadcast, never a diagnosis (D140).
// Everything here reads fields the backend already sends (state.models, state.physics_rules,
// state.decision, state.health.sensors) plus which sensors the backend says are switched off
// (GET /sensors). It never derives a fault label, a confidence or a decision state.

export const LAB_SENSORS = ['mic', 'accel', 'crank', 'cam'];

// What each sensor feeds in the backend (backend/sources/sim_source.py, dsp/features.py,
// models/features.py). The classifier's feature vector has crank and accelerometer features.
export const SENSOR_INFO = {
  mic: {
    short: 'MIC', name: 'Microphone', noun: 'microphone',
    feeds: 'Acoustic signature display',
    note: 'Not a rule or classifier input',
  },
  accel: {
    short: 'ACCEL', name: 'Accelerometer', noun: 'accelerometer',
    feeds: 'Knock rule · classifier',
    note: 'Knock-band energy and vibration sectors',
  },
  crank: {
    short: 'CRANK', name: 'Crank position', noun: 'crank sensor',
    feeds: 'Misfire rule · cylinder localisation · classifier',
    note: 'Tooth timing gives per-cylinder speed',
  },
  cam: {
    short: 'CAM', name: 'Cam position', noun: 'cam sensor',
    feeds: '720° cycle phase',
    note: 'Phase held from the last cam pulse while lost',
  },
};

// sensors whose loss removes the classifier witness (its inputs)
export const CLASSIFIER_INPUTS = ['crank', 'accel'];
// physics rule -> the sensor it reads (injector clog reads OBD fuel trims, not switchable here)
export const RULE_SENSOR = { misfire: 'crank', knock: 'accel', injector_clog: 'obd' };
export const RULE_NAME = { misfire: 'Misfire', knock: 'Knock', injector_clog: 'Injector clog' };
export const RULE_BASIS = { misfire: 'crank half-order energy', knock: 'accel knock-band energy', injector_clog: 'OBD fuel-trim drift' };

/**
 * One sensor's displayed status, from backend facts only.
 *   commanded  true when GET/POST /sensors lists it as disabled
 *   health     state.health.sensors[ch] (backend watchdog: FAILURE after 3 s without data)
 *   inflight   'off' | 'on' while a command is awaiting its HTTP reply
 *   restoring  true after an enable command until the watchdog reports NORMAL
 */
export function sensorStatus({ commanded, health, inflight = null, restoring = false }) {
  if (inflight) return 'SENDING';
  if (commanded) return health === 'FAILURE' ? 'OFFLINE' : 'DISABLING';
  if (health === 'FAILURE') return restoring ? 'RECOVERING' : 'FAILURE';
  if (health == null) return 'UNKNOWN';
  return 'LIVE';
}

export const STATUS_TEXT = {
  LIVE: 'LIVE',
  SENDING: 'SENDING…',
  DISABLING: 'CUTTING · AWAITING WATCHDOG',
  OFFLINE: 'SENSOR OFFLINE',
  RECOVERING: 'RECOVERING',
  FAILURE: 'SENSOR FAILURE',
  UNKNOWN: 'NO DATA',
};

// a sensor counts as unavailable for evidence as soon as the backend blanks it (commanded off) or
// the watchdog reports it failed
export function offlineSet(sensors) {
  return new Set(Object.entries(sensors).filter(([, s]) => s.commanded || s.health === 'FAILURE').map(([ch]) => ch));
}

const joinNouns = (chs) => chs.map((c) => SENSOR_INFO[c]?.noun ?? c).join(' and ');

/**
 * The classifier witness as the backend reported it this window.
 *   ACTIVE       state.models has an entry: shows its label and p exactly as sent
 *   UNAVAILABLE  no entry and a classifier input sensor is offline
 *   NO_OUTPUT    no entry, inputs back, but the classifier has spoken before in this run
 *                (the window still holds blanked samples)
 *   NOT_LOADED   no entry and it never had one in this run (no checkpoint on this backend)
 */
export function classifierWitness(state, offline, everActive = false) {
  const models = state?.models ?? [];
  if (models.length) {
    // the deployed witness is the SVM (fast_v1); other rows (scripted scenarios) only if it is absent
    const top = models.find((m) => m.model === 'SVM') ?? models.reduce((a, b) => ((b.p ?? 0) > (a.p ?? 0) ? b : a));
    const counted = (state.decision?.witnesses ?? []).includes('classifier');
    const cyl = state.features_summary?.classifier_cylinder ?? null;
    return { status: 'ACTIVE', model: top.model, label: top.label, p: top.p, cylinder: cyl, counted };
  }
  const missing = CLASSIFIER_INPUTS.filter((c) => offline.has(c));
  if (missing.length) {
    return {
      status: 'UNAVAILABLE', missing,
      reason: `Classifier witness unavailable: ${joinNouns(missing)} offline. New confirmed alarms require two matching witnesses.`,
    };
  }
  if (everActive) return { status: 'NO_OUTPUT', reason: 'No classifier output this window: waiting for a complete window of valid data.' };
  return { status: 'NOT_LOADED', reason: 'No classifier on this backend (checkpoint not loaded): physics witness only, ADVISORY at most.' };
}

/** The physics rules exactly as sent, each with whether the sensor it reads is available. */
export function physicsWitness(state, offline) {
  return (state?.physics_rules ?? []).map((r) => {
    const sensor = RULE_SENSOR[r.rule] ?? null;
    const available = !(sensor && offline.has(sensor));
    return { rule: r.rule, fired: Boolean(r.fired) && available, location: r.location ?? null, sensor, available };
  });
}

/**
 * Evidence availability (not a decision):
 *   FULL          both witnesses can speak: classifier output present, crank and accelerometer live
 *   INSUFFICIENT  crank and accelerometer both offline: no cylinder-level evidence at all
 *   PARTIAL       anything in between
 */
export function evidenceLevel(classifier, offline) {
  const bothGone = CLASSIFIER_INPUTS.every((c) => offline.has(c));
  if (bothGone) return 'INSUFFICIENT';
  if (classifier.status === 'ACTIVE' && !CLASSIFIER_INPUTS.some((c) => offline.has(c))) return 'FULL';
  return 'PARTIAL';
}

export const LEVEL_TEXT = {
  FULL: { title: 'FULL EVIDENCE', sub: 'Both witnesses available. A new ALARM can be confirmed.' },
  PARTIAL: { title: 'PARTIAL EVIDENCE', sub: 'One witness available. New confirmed alarms require two matching witnesses.' },
  INSUFFICIENT: { title: 'INSUFFICIENT EVIDENCE', sub: 'Crank and accelerometer offline: no cylinder-level evidence.' },
};

/** Integrity notes for the current moment, in display order. */
export function integrityNotes(state, level, classifier) {
  const notes = [];
  if (classifier.status === 'UNAVAILABLE') notes.push({ tone: 'amber', text: classifier.reason });
  if (state?.decision?.state === 'ALARM' && level !== 'FULL') {
    notes.push({ tone: 'red', text: 'Previously confirmed alarm remains latched. Current diagnostic evidence is degraded.' });
  }
  return notes;
}

// ------------------------------------------------------------ outcome: what removing sensors did (D141)
// Static facts about what each sensor feeds (same table as backend/investigator.py), combined with the
// backend's broadcast. It says what was lost and what still works; the verdict lines quote the rule layer.
export const CAPABILITIES = [
  { id: 'misfire_rule', name: 'Misfire rule', needs: ['crank'] },
  { id: 'knock_rule', name: 'Knock rule', needs: ['accel'] },
  { id: 'clog_rule', name: 'Injector-clog rule', needs: [] }, // OBD fuel trims: not switchable here
  { id: 'classifier', name: 'ML classifier witness', needs: ['crank', 'accel'] },
  { id: 'localisation', name: 'Cylinder localisation', needs: ['crank'] },
  { id: 'signature', name: 'Acoustic signature display', needs: ['mic'] },
  { id: 'phase', name: '720° cycle phase', needs: ['cam'], soft: true }, // held from the last cam pulse
];

export function outcome({ state, sensors, classifier, level }) {
  const off = offlineSet(sensors);
  const removed = LAB_SENSORS.filter((ch) => off.has(ch));
  const lost = CAPABILITIES.filter((c) => c.needs.some((n) => off.has(n)) && !c.soft)
    .map((c) => ({ ...c, because: c.needs.filter((n) => off.has(n)).map((n) => SENSOR_INFO[n].short) }));
  const held = CAPABILITIES.filter((c) => c.soft && c.needs.some((n) => off.has(n)));
  const kept = CAPABILITIES.filter((c) => !c.needs.some((n) => off.has(n)));
  const d = state?.decision ?? {};
  const verdicts = [];
  const newAlarm = level === 'FULL';
  if (removed.length) {
    verdicts.push({ tone: newAlarm ? 'ok' : 'amber', big: newAlarm ? 'New alarms can still be confirmed' : 'No new alarm can be confirmed', sub: newAlarm ? 'Both witnesses keep their inputs.' : 'The rule layer needs two matching witnesses; one is gone.' });
  }
  if (d.state === 'ALARM') {
    verdicts.push(d.acked
      ? { tone: 'red', big: `${d.alarm_id} acknowledged`, sub: 'It resets once its own two-witness match is gone (D124).' }
      : { tone: 'red', big: `${d.alarm_id} stays latched`, sub: 'Losing a witness never clears an alarm: it waits for the operator ACK.' });
  }
  const witnesses = (d.witnesses ?? []).join(' + ') || 'no witness';
  verdicts.push({
    tone: d.state === 'ALARM' ? 'red' : d.state === 'NORMAL' ? 'neutral' : 'amber',
    big: `Rule layer now: ${d.state ?? '—'}${d.fault ? ` · ${d.fault.replace('_', ' ')}` : ''}${d.location ? ` · cyl ${d.location}` : ''}`,
    sub: d.state === 'ALARM' ? `Confirmed by ${witnesses}.` : `Current witnesses: ${witnesses}.`,
  });
  return { removed, lost, held, kept, verdicts, classifierGone: classifier?.status !== 'ACTIVE' && removed.length > 0 };
}
