// Mock backend messages for schema 1.3 (docs/DATA_CONTRACT.md): `state`, `obd` and `signal`.
// Every number here is made up so the UI has something to draw. None of it is a result.
// The mock transport (src/api/transport.js) calls these builders on a timer.

import { FIRING_ORDER } from '../lib/geometry.js';

// The mock speaks 1.5 (= 1.4, i.e. 1.3 + state.injection, plus a nullable anomaly.score it never uses), so the
// injection console can be demonstrated; the dashboard accepts 1.3, 1.4 and 1.5.
export const SCHEMA_VERSION = '1.5';

const UNITS = { t: 's', sent_at: 'unix s', rpm: 'rev/min', load_pct: '%', latency_ms: 'ms', severity: '0-1', crank_deg: 'deg' };
const FIRE_DEG = [0, 180, 360, 540]; // crank angle where each cylinder in firing order fires
const THRESHOLD = 0.6;

function wrapDist(a, b) {
  const d = Math.abs(a - b);
  return Math.min(d, 720 - d);
}

const jit = (v, amount) => v + (Math.random() - 0.5) * 2 * amount;
const clamp01 = (v) => Math.max(0, Math.min(1, v));
const round = (v, d = 2) => +v.toFixed(d);
const now = () => Date.now() / 1000;

// ---------- signal shapes (one 720° cycle), reused from the v1 mock ----------
// `sig` is the physical picture: { shape, loc }, shape = healthy | misfire | knock | valve |
// injector | advisory, loc = the affected cylinder (ignored for healthy).

// index of a cylinder in the firing order (its firing slot)
const slot = (cyl) => FIRING_ORDER.indexOf(cyl);

// relative combustion strength per cylinder for each shape
function combustion({ shape, loc }, cyl) {
  if (cyl !== loc) return 0.7;
  if (shape === 'misfire') return 0.03;
  if (shape === 'valve') return 0.6;
  if (shape === 'injector') return 0.55;
  if (shape === 'advisory') return 0.45;
  return 0.7;
}

function signatureFor(sig) {
  const { shape, loc } = sig;
  const fireAt = FIRE_DEG[slot(loc)] ?? 0; // where the affected cylinder fires
  const crank = [];
  const amp = [];
  const base = [];
  for (let th = 0; th < 720; th += 2) {
    let healthy = 0.25;
    let cur = 0.25;
    FIRE_DEG.forEach((deg, i) => {
      const cyl = FIRING_ORDER[i];
      const g = Math.exp(-((wrapDist(th, deg) / 32) ** 2));
      healthy += 0.7 * g;
      cur += combustion(sig, cyl) * g;
      if (shape === 'valve') {
        const gv = Math.exp(-((wrapDist(th, (deg + 120) % 720) / 8) ** 2));
        cur += (cyl === loc ? 0.34 : 0.1) * gv;
      }
    });
    // knock: ringing just after the affected cylinder fires
    const sinceFire = (th - fireAt - 2 + 720) % 720;
    if (shape === 'knock' && sinceFire > 0 && sinceFire < 148) {
      cur += 0.17 * Math.sin(sinceFire * 0.6) * Math.exp(-sinceFire / 50);
    }
    if (shape === 'misfire') cur -= 0.13 * Math.exp(-((wrapDist(th, fireAt) / 22) ** 2));
    crank.push(th);
    amp.push(+cur.toFixed(4));
    base.push(+healthy.toFixed(4));
  }
  return { crank_deg: crank, amplitude: amp, baseline_amplitude: base };
}

// Signal is generated from a running crank angle at the scenario's rpm, so consecutive
// frames join up seamlessly. Each combustion event gets a small random strength
// (real engines vary cycle to cycle) and the noise is fresh every sample.

// deterministic 0..1 hash so a given cycle/cylinder always gets the same variation
function hash01(a, b) {
  let h = (a * 374761393 + b * 668265263) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}
const cycleGain = (cycle, cyl) => 1 + 0.16 * (hash01(cycle, cyl) - 0.5);

// one sample at absolute crank angle `deg` (0 → ∞); `u` below is in the old 600-steps-per-cycle units
function waveSample(sig, deg, gain) {
  const { shape, loc } = sig;
  const cycle = Math.floor(deg / 720);
  const u = (deg % 720) / 1.2;
  let y = 0;
  for (let j = 0; j < 4; j++) {
    const d = u - (j * 150 + 20);
    if (d >= 0 && d < 100) {
      const a = (combustion(sig, FIRING_ORDER[j]) / 0.7) * 0.55 * cycleGain(cycle, j);
      y += a * Math.exp(-d / 22) * Math.sin(d * 0.35);
    }
  }
  if (shape === 'knock') {
    const d = u - (slot(loc) * 150 + 22);
    // knock is intermittent: not every cycle
    if (d >= 0 && d < 70 && hash01(cycle, 99) < 0.75) y += 0.42 * cycleGain(cycle, 7) * Math.exp(-d / 18) * Math.sin(d * 2.2);
  }
  if (shape === 'valve') {
    for (let q = 0; q < 4; q++) {
      const d = u - (q * 150 + 95);
      if (d >= 0 && d < 14) y += (q === slot(loc) ? 0.4 : 0.16) * Math.exp(-d / 4) * Math.sin(d * 1.8);
    }
  }
  y += (Math.random() - 0.5) * 0.1;
  return +(y * gain).toFixed(4);
}

const SIGNAL_FS = 4000; // display stream, server-downsampled (mock value)
const FRAME_S = 0.2; // one signal frame per 200 ms (matches the mock transport)
const STFT_DT = 0.025;
const STFT_BINS = 48;
const STFT_FREQS = Array.from({ length: STFT_BINS }, (_, i) => Math.round((i / (STFT_BINS - 1)) * 10000));

const degAt = (rpm, t) => t * (rpm / 60) * 360; // absolute crank angle at time t

function waveFrame(sig, rpm, t0, gain) {
  const n = Math.round(FRAME_S * SIGNAL_FS);
  return Array.from({ length: n }, (_, i) => waveSample(sig, degAt(rpm, t0 + i / SIGNAL_FS), gain));
}

// STFT analysis window: longer than the hop and tapered (Hann), like a real STFT, so energy
// is smooth between firings and only a real change (e.g. a missing firing) shows as ripple.
const STFT_WIN = 0.064;

function stftColumn(sig, rpm, t) {
  const { shape, loc } = sig;
  const degPerS = (rpm / 60) * 360;
  const firingsPerS = (degPerS / 720) * 4;
  const tc = t + STFT_DT / 2;
  const tA = tc - STFT_WIN / 2;
  const tB = tc + STFT_WIN / 2;
  let burn = 0;
  let knock = 0;
  let click = 0;
  FIRE_DEG.forEach((deg, j) => {
    const cyl = FIRING_ORDER[j];
    for (let k = Math.ceil((degAt(rpm, tA) - deg) / 720); (deg + 720 * k) / degPerS < tB; k++) {
      const te = (deg + 720 * k) / degPerS;
      const w = 0.5 - 0.5 * Math.cos((2 * Math.PI * (te - tA)) / STFT_WIN); // Hann weight
      burn += w * (combustion(sig, cyl) / 0.7) * cycleGain(k, j);
      if (shape === 'knock' && cyl === loc && hash01(k, 99) < 0.75) knock += w;
      if (shape === 'valve') click += w * (cyl === loc ? 0.35 : 0.12);
    }
  });
  // normalise: a healthy engine averages ~1 regardless of rpm
  const norm = (firingsPerS * STFT_WIN) / 2;
  burn /= norm;
  const knockLevel = Math.min(1, knock / Math.max(0.5, norm / 4));
  click /= Math.max(0.5, norm / 4);
  return STFT_FREQS.map((hz, f) => {
    let e = 0.1 + Math.random() * 0.07;
    if (hz < 1200) e += 0.3 * (1 - hz / 1200);
    e += 0.12 * Math.exp(-hz / 3000);
    e += 0.16 * burn * (1 - f / STFT_BINS);
    if (hz >= 5800 && hz <= 7600) e += knockLevel * (0.45 + Math.random() * 0.2);
    e += click * 0.5 * (0.8 + Math.random() * 0.4);
    return +(-90 + Math.min(1, e) * 90).toFixed(1);
  });
}

// ---------- audio (PROVISIONAL `mic_audio` channel, see buildSignal) ----------
// A simple sound model consistent with the spectrogram: a low thump per firing (missing on a
// misfire), 6.6 kHz ringing after the knocking cylinder (~5.8–7.6 kHz band), sharp clicks for
// valve tick, over a hum at the firing frequency. Mock only: it is not a recording of anything.
const AUDIO_FS = 16000; // ≥ 2 × the 7.6 kHz knock band, so knock is audible

function audioSample(sig, rpm, t) {
  const { shape, loc } = sig;
  const degPerS = (rpm / 60) * 360;
  const D = t * degPerS;
  const fFire = (degPerS / 720) * 4;
  let y = 0.05 * Math.sin(2 * Math.PI * fFire * t) + 0.025 * Math.sin(4 * Math.PI * fFire * t);
  FIRE_DEG.forEach((deg, j) => {
    const cyl = FIRING_ORDER[j];
    const k = Math.floor((D - deg) / 720); // cycle of this cylinder's latest firing
    const tau = (D - (deg + 720 * k)) / degPerS; // seconds since that firing
    const a = (combustion(sig, cyl) / 0.7) * cycleGain(k, j);
    if (tau < 0.06) {
      const env = Math.exp(-tau / 0.01);
      y += 0.32 * a * env * (0.7 * Math.sin(2 * Math.PI * 95 * tau) + 0.3 * (Math.random() - 0.5));
    }
    if (shape === 'knock' && cyl === loc && hash01(k, 99) < 0.75) {
      const tk = tau - 0.0015;
      if (tk > 0 && tk < 0.03) y += 0.22 * Math.exp(-tk / 0.004) * Math.sin(2 * Math.PI * 6600 * tk);
    }
    if (shape === 'valve') {
      const kv = Math.floor((D - deg - 120) / 720);
      const tv = (D - (deg + 120 + 720 * kv)) / degPerS;
      if (tv < 0.004) y += (cyl === loc ? 0.45 : 0.15) * Math.exp(-tv / 0.0006) * (Math.random() - 0.5) * 2;
    }
  });
  y += 0.012 * (Math.random() - 0.5);
  return Math.max(-1, Math.min(1, y));
}

function audioFrame(sig, rpm, t0) {
  const n = Math.round(FRAME_S * AUDIO_FS);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = audioSample(sig, rpm, t0 + i / AUDIO_FS);
  return out;
}

// ---------- scenarios ----------

// Physics rules for the simulated scenarios. Contract rule 8: rule_basis is `crank_angle` for
// simulated/injected data (`firing_event` is for real audio, which has no crank reference).
const RULES = ['misfire', 'knock', 'injector_clog', 'valve_tick'];
const rules = (fired = {}) =>
  RULES.map((rule) => ({
    rule,
    fired: rule in fired,
    location: fired[rule] ?? null,
    rule_basis: 'crank_angle',
  }));

const models = (rows) => rows.map(([model, label, p]) => ({ model, label, p }));

const NORMAL_DECISION = { state: 'NORMAL', fault: null, location: null, severity: null, witnesses: [], consecutive_windows: 0, alarm: false };
const SENSORS_OK = { mic: 'NORMAL', accel: 'NORMAL', obd: 'NORMAL' };

// obd: baseline values per channel; `stft_drift` is added to fuel trim (injector clog ≈ clog/4).
const SCENARIO_DEFS = [
  {
    id: 'real_healthy', name: 'Real · healthy (P3)', shape: 'healthy',
    source: 'real', mode: 'replay', audioset_pool: 'P3', engine_tag: 'vetted_car', localisation: 'none',
    op: { rpm: 780, load_pct: 18, regime: 'idle' },
    features: { half_order_ratio: 1.1, knock_band_ratio: 0.9, cam_env_peak: 0.12 },
    anomaly: { score: 0.21, per_location: [] },
    models: models([['Baseline', 'healthy', 0.81], ['SVM', 'healthy', 0.88], ['ANN', 'healthy', 0.9], ['CNN', 'healthy', 0.92]]),
    physics_rules: [],
    decision: NORMAL_DECISION,
    // AudioSet clips are audio only: no accelerometer on real data
    // schema 1.3: sensors absent on this recording are left out (no accelerometer on AudioSet clips)
    health: { asset_ne107: 'NORMAL', sensors: { mic: 'NORMAL', obd: 'NORMAL' } },
    obd: { source: 'real_carobd', rpm: 780, load_pct: 18, coolant_c: 88, stft_pct: 0.8, transitions: true },
  },
  {
    id: 'sim_healthy', name: 'Simulated · healthy', shape: 'healthy',
    source: 'simulated', mode: 'live', localisation: 'cylinder',
    op: { rpm: 2200, load_pct: 34, regime: 'cruise' },
    features: { half_order_ratio: 1.0, knock_band_ratio: 1.0, cam_env_peak: 0.14 },
    anomaly: { score: 0.18, per_location: [0.12, 0.1, 0.14, 0.11] },
    models: models([['Baseline', 'healthy', 0.84], ['SVM', 'healthy', 0.94], ['ANN', 'healthy', 0.95], ['CNN', 'healthy', 0.96]]),
    physics_rules: rules(),
    decision: NORMAL_DECISION,
    health: { asset_ne107: 'NORMAL', sensors: SENSORS_OK },
    obd: { source: 'simulator', rpm: 2200, load_pct: 34, coolant_c: 91, stft_pct: 1.1 },
  },
  {
    id: 'misfire', name: 'Misfire', shape: 'misfire', loc: 3,
    source: 'simulated', mode: 'live', localisation: 'cylinder',
    op: { rpm: 1620, load_pct: 28, regime: 'cruise' },
    features: { half_order_ratio: 3.1, knock_band_ratio: 1.0, cam_env_peak: 0.2 },
    anomaly: { score: 0.82, per_location: [0.12, 0.1, 0.91, 0.15] },
    models: models([['Baseline', 'misfire', 0.7], ['SVM', 'misfire', 0.86], ['ANN', 'misfire', 0.88], ['CNN', 'misfire', 0.91]]),
    physics_rules: rules({ misfire: 3 }),
    decision: { state: 'ALARM', fault: 'misfire', location: 3, severity: 0.12, witnesses: ['physics', 'classifier'], consecutive_windows: 2, alarm: true },
    health: { asset_ne107: 'OFF_SPEC', sensors: SENSORS_OK },
    obd: { source: 'simulator', rpm: 1620, load_pct: 28, coolant_c: 89, stft_pct: 1.4 },
  },
  {
    id: 'knock', name: 'Knock', shape: 'knock', loc: 3,
    source: 'simulated', mode: 'live', localisation: 'cylinder',
    op: { rpm: 3240, load_pct: 72, regime: 'high_load' },
    features: { half_order_ratio: 1.2, knock_band_ratio: 4.1, cam_env_peak: 0.18 },
    anomaly: { score: 0.77, per_location: [0.14, 0.12, 0.84, 0.18] },
    models: models([['Baseline', 'knock', 0.62], ['SVM', 'knock', 0.79], ['ANN', 'knock', 0.83], ['CNN', 'knock', 0.87]]),
    physics_rules: rules({ knock: 3 }),
    decision: { state: 'ALARM', fault: 'knock', location: 3, severity: 0.35, witnesses: ['physics', 'classifier'], consecutive_windows: 3, alarm: true },
    health: { asset_ne107: 'OFF_SPEC', sensors: SENSORS_OK },
    obd: { source: 'simulator', rpm: 3240, load_pct: 72, coolant_c: 96, stft_pct: 1.6 },
  },
  {
    id: 'injector', name: 'Injector clog', shape: 'injector', loc: 2,
    source: 'simulated', mode: 'live', localisation: 'cylinder',
    op: { rpm: 2000, load_pct: 30, regime: 'cruise' },
    features: { half_order_ratio: 1.6, knock_band_ratio: 1.0, cam_env_peak: 0.15 },
    anomaly: { score: 0.68, per_location: [0.13, 0.71, 0.12, 0.16] },
    models: models([['Baseline', 'injector_clog', 0.55], ['SVM', 'injector_clog', 0.72], ['ANN', 'injector_clog', 0.76], ['CNN', 'injector_clog', 0.79]]),
    physics_rules: rules({ injector_clog: 2 }),
    decision: { state: 'ALARM', fault: 'injector_clog', location: 2, severity: 0.2, witnesses: ['physics', 'classifier'], consecutive_windows: 4, alarm: true },
    health: { asset_ne107: 'MAINTENANCE_REQUIRED', sensors: SENSORS_OK },
    // clog severity 0.20 → fuel trim drift ≈ 0.20 / 4 = +5 %
    obd: { source: 'simulator', rpm: 2000, load_pct: 30, coolant_c: 90, stft_pct: 1.0, stft_drift: 5.0 },
  },
  {
    id: 'valve', name: 'Valve tick', shape: 'valve', loc: 2,
    source: 'simulated', mode: 'live', localisation: 'cylinder',
    op: { rpm: 2400, load_pct: 41, regime: 'cruise' },
    features: { half_order_ratio: 1.3, knock_band_ratio: 1.4, cam_env_peak: 0.61 },
    anomaly: { score: 0.74, per_location: [0.22, 0.79, 0.2, 0.24] },
    // valve is held out of ML training: the classifiers can't name it and disagree
    models: models([['Baseline', 'healthy', 0.48], ['SVM', 'knock', 0.52], ['ANN', 'healthy', 0.44], ['CNN', 'knock', 0.47]]),
    physics_rules: rules({ valve_tick: 2 }),
    decision: { state: 'UNCLASSIFIED', fault: null, location: null, severity: null, witnesses: ['physics'], consecutive_windows: 3, alarm: false },
    health: { asset_ne107: 'OFF_SPEC', sensors: SENSORS_OK },
    obd: { source: 'simulator', rpm: 2400, load_pct: 41, coolant_c: 92, stft_pct: 0.8 },
  },
  {
    id: 'advisory', name: 'Advisory (watch)', shape: 'advisory', loc: 2,
    source: 'simulated', mode: 'live', localisation: 'cylinder',
    op: { rpm: 1800, load_pct: 26, regime: 'cruise' },
    features: { half_order_ratio: 1.9, knock_band_ratio: 1.0, cam_env_peak: 0.15 },
    anomaly: { score: 0.63, per_location: [0.12, 0.64, 0.11, 0.13] },
    models: models([['Baseline', 'healthy', 0.58], ['SVM', 'healthy', 0.61], ['ANN', 'healthy', 0.55], ['CNN', 'misfire', 0.41]]),
    physics_rules: rules({ misfire: 2 }),
    decision: { state: 'ADVISORY', fault: 'misfire', location: 2, severity: 0.05, witnesses: ['physics'], consecutive_windows: 1, alarm: false },
    health: { asset_ne107: 'NORMAL', sensors: SENSORS_OK },
    obd: { source: 'simulator', rpm: 1800, load_pct: 26, coolant_c: 90, stft_pct: 1.2 },
  },
];

const BY_ID = Object.fromEntries(SCENARIO_DEFS.map((s) => [s.id, s]));

export const SCENARIOS = SCENARIO_DEFS.map(({ id, name, source }) => ({ id, name, source }));
export const DEFAULT_SCENARIO = 'misfire';

export function scenario(id) {
  const s = BY_ID[id];
  if (!s) throw new Error(`Unknown mock scenario "${id}"`);
  return s;
}

export function scenarioRaisesAlarm(id) {
  return scenario(id).decision.alarm;
}

// ---------- injection (mock rule layer; PROPOSED schema 1.4) ----------
// An injected fault borrows the matching fault scenario's picture, moved to the chosen cylinder.
// `inj` (from the mock transport) = { record, fault, location, live, decided }:
//   live     the fault is being injected now (signals, anomaly, OBD show it)
//   decided  the rule layer has seen enough windows: the decision shows it (also after a stop,
//            while the alarm is still latched)
//   record   what goes into state.injection (null after a stop)
// The requested severity only drives the injector-clog fuel-trim drift (≈ severity / 4); the mock
// waveforms do not scale with it, and decision.severity stays null (the mock has no estimator).
const INJECT_TEMPLATE = { misfire: 'misfire', knock: 'knock', injector_clog: 'injector', valve_tick: 'valve' };
export const INJECTABLE_FAULTS = Object.keys(INJECT_TEMPLATE);
export const INJECTION_BASE_SCENARIO = 'sim_healthy'; // mock limitation: injects onto a healthy engine only

export function injectionRaisesAlarm(fault) {
  return scenario(INJECT_TEMPLATE[fault]).decision.alarm;
}

function effective(id, inj) {
  const base = scenario(id);
  if (!inj) return base;
  const tpl = scenario(INJECT_TEMPLATE[inj.fault]);
  const loc = inj.location;
  const s = { ...base };
  if (inj.live) {
    s.shape = tpl.shape;
    s.loc = loc;
    s.features = tpl.features;
    s.models = tpl.models;
    s.physics_rules = rules({ [inj.fault]: loc });
    const peak = Math.max(...tpl.anomaly.per_location);
    s.anomaly = { score: tpl.anomaly.score, per_location: base.anomaly.per_location.map((v, i) => (i === loc - 1 ? peak : v)) };
    if (inj.fault === 'injector_clog') s.obd = { ...base.obd, stft_drift: round((inj.severity / 4) * 100, 1) };
  }
  if (inj.decided) {
    s.decision = { ...tpl.decision, location: tpl.decision.location == null ? null : loc, severity: null };
  }
  // while the console is active the asset is in test (NE 107 CHECK_FUNCTION); after a stop, a
  // still-latched alarm shows the fault's own asset health until it clears
  s.health = { ...base.health, asset_ne107: inj.live ? 'CHECK_FUNCTION' : inj.decided ? tpl.health.asset_ne107 : base.health.asset_ne107 };
  return s;
}

// cached per picture so the UI gets the same array object every tick
const cache = {};
function signatureOf(s) {
  const key = `${s.id}:${s.shape}:${s.loc ?? ''}`;
  if (!cache[key]) {
    // real clips: signature arrives empty (no engine geometry)
    cache[key] = s.localisation === 'cylinder'
      ? signatureFor({ shape: s.shape, loc: s.loc })
      : { crank_deg: [], amplitude: [], baseline_amplitude: [] };
  }
  return cache[key];
}

// ---------- message builders ----------

// `alarm` is the transport's alarm record { alarm_id, latched, acked, acked_by, acked_at } or null.
// `inj` is the transport's injection view (see above) or null.
export function buildState(id, t, alarm, inj = null) {
  const s = effective(id, inj);
  const { alarm: _raises, ...decision } = s.decision;
  return {
    schema_version: SCHEMA_VERSION,
    sent_at: now(),
    units: UNITS,
    run_id: `mock_${s.id}`,
    source: s.source,
    audioset_pool: s.audioset_pool ?? null,
    engine_tag: s.engine_tag ?? null,
    mode: s.mode,
    t: round(t),
    operating_point: { rpm: Math.round(jit(s.op.rpm, s.op.rpm * 0.01)), load_pct: Math.round(jit(s.op.load_pct, 1)), regime: s.op.regime },
    localisation: s.localisation,
    features_summary: Object.fromEntries(Object.entries(s.features).map(([k, v]) => [k, round(jit(v, v * 0.04))])),
    anomaly: {
      score: round(clamp01(jit(s.anomaly.score, 0.02))),
      threshold: THRESHOLD,
      per_location: s.anomaly.per_location.map((v) => round(clamp01(jit(v, 0.02)))),
    },
    models: s.models.map((m) => ({ ...m, p: round(clamp01(jit(m.p, 0.01))) })),
    physics_rules: s.physics_rules,
    decision: {
      ...decision,
      alarm_id: alarm?.alarm_id ?? null,
      latched: alarm?.latched ?? false,
      acked: alarm?.acked ?? false,
      acked_by: alarm?.acked_by ?? null,
      acked_at: alarm?.acked_at ?? null,
    },
    health: s.health,
    latency_ms: Math.round(jit(41, 6)),
    signature: signatureOf(s),
    injection: inj?.record ?? null,
  };
}

// One new sample per channel, sent ~1 Hz.
export function buildObd(id, t, inj = null) {
  const s = effective(id, inj);
  const o = s.obd;
  // carOBD replay includes real RPM/load transitions; mock them as a slow swing
  const swing = o.transitions ? 0.5 - 0.5 * Math.cos((t * 2 * Math.PI) / 20) : 0;
  const ch = (v, unit, source = o.source) => ({ t: [round(t, 1)], v: [v], unit, source });
  const channels = {
    rpm: ch(Math.round(jit(o.rpm + swing * 1500, o.rpm * 0.01)), 'rev/min'),
    load_pct: ch(Math.round(jit(o.load_pct + swing * 30, 1)), '%'),
    coolant_c: ch(round(jit(o.coolant_c, 0.3), 1), '°C'),
    stft_pct: ch(round(jit(o.stft_pct + (o.stft_drift ?? 0), 0.4), 1), '%'),
  };
  // real carOBD has no O2 voltage; O2 ripple is simulator-only
  if (o.source === 'simulator') channels.o2_v = ch(round(0.45 + 0.4 * Math.sin(t * 2.7) + jit(0, 0.03), 2), 'V');
  return { schema_version: SCHEMA_VERSION, sent_at: now(), channels };
}

// PROVISIONAL `signal` frames. The binary layout is not agreed with Nishant yet (CLAUDE.md §11);
// these are plain objects, one per channel, each covering the next FRAME_S seconds.
//   { channel: 'mic' | 'accel', t0, sample_rate_hz, samples }
//   { channel: 'stft', t0, freqs_hz, dt_s, columns }
//   { channel: 'mic_audio', t0, sample_rate_hz, samples }   16 kHz mic for listening (Float32Array)
export function buildSignal(id, tick, inj = null) {
  const s = effective(id, inj);
  const sig = { shape: s.shape, loc: s.loc };
  const t0 = tick * FRAME_S;
  const head = { schema_version: SCHEMA_VERSION, sent_at: now(), t0: round(t0) };
  const frames = [{ ...head, channel: 'mic', sample_rate_hz: SIGNAL_FS, samples: waveFrame(sig, s.op.rpm, t0, 1) }];
  // AudioSet clips are audio only: no accelerometer frames on real data
  if (s.source !== 'real') {
    frames.push({ ...head, channel: 'accel', sample_rate_hz: SIGNAL_FS, samples: waveFrame(sig, s.op.rpm, t0, 0.8) });
  }
  const cols = Math.round(FRAME_S / STFT_DT);
  const columns = Array.from({ length: cols }, (_, i) => stftColumn(sig, s.op.rpm, t0 + i * STFT_DT));
  frames.push({ ...head, channel: 'stft', freqs_hz: STFT_FREQS, dt_s: STFT_DT, columns });
  // no audio for REAL clips in mock: we don't have the recording, and synthetic sound under a
  // REAL badge would misrepresent it
  if (s.source !== 'real') {
    frames.push({ ...head, channel: 'mic_audio', sample_rate_hz: AUDIO_FS, samples: audioFrame(sig, s.op.rpm, t0) });
  }
  return frames;
}

export function buildAck(alarm_id, operator) {
  return { schema_version: SCHEMA_VERSION, type: 'ack', alarm_id, operator, sent_at: now() };
}
