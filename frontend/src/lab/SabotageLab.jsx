import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FAULTS, decisionText, fault as faultInfo, state3d } from '../lib/faults.js';
import {
  LAB_SENSORS, LEVEL_TEXT, RULE_BASIS, RULE_NAME, SENSOR_INFO, STATUS_TEXT,
  classifierWitness, evidenceLevel, integrityNotes, offlineSet, physicsWitness, sensorStatus,
} from './evidence.js';
import { createSensorApi } from './sensorApi.js';
import LabStage from './LabStage.jsx';
import LabSignals from './LabSignals.jsx';
import './sabotage.css';

// SABOTAGE LAB (D137): a judge switches simulator sensors off and on and watches what 720 can still
// prove. Everything shown comes from the backend: sensor state from GET /sensors and the watchdog's
// state.health.sensors, evidence from state.models / state.physics_rules, the decision from the rule
// layer. This view never derives a fault, a confidence or a decision, and never clears an alarm.

const POLL_MS = 2000;
const LOG_KEEP = 40;
const INJECT_FAULTS = ['misfire', 'knock', 'injector_clog', 'valve_tick'];
const KEY_LINE = 'You took away a sensor, so 720 stopped claiming what it could no longer prove.';

const clock = () => new Date().toLocaleTimeString([], { hour12: false });
const secs = (ms) => `${(ms / 1000).toFixed(1)} s`;

// ------------------------------------------------------------ sensor commands + backend state

function useSensorControl(api, active, runId) {
  const [commanded, setCommanded] = useState(() => new Set());
  const [controllable, setControllable] = useState(null); // null = not known yet
  const [inflight, setInflight] = useState({}); // ch -> 'off' | 'on'
  const [restoring, setRestoring] = useState(() => new Set());
  const [error, setError] = useState(null);
  const commandedRef = useRef(commanded);
  commandedRef.current = commanded;

  const apply = useCallback((reply) => {
    if (!reply) return;
    setControllable(Boolean(reply.controllable));
    setCommanded(new Set(reply.disabled ?? []));
  }, []);

  const refresh = useCallback(() => {
    if (!api) return Promise.resolve();
    return api.status().then(apply).catch((e) => setError(`Sensor status unavailable: ${e.message}`));
  }, [api, apply]);

  // keep in step with the backend while the tab is open (another client, OPC UA or a source switch can
  // change it); a new run starts with every sensor on, so re-read on run_id changes too
  useEffect(() => {
    if (!active || !api) return undefined;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [active, api, refresh, runId]);

  const command = useCallback((kind, ch) => {
    if (!api) return Promise.resolve(null);
    setError(null);
    const key = ch ?? '*';
    setInflight((m) => ({ ...m, [key]: kind }));
    const call = kind === 'off' ? api.off(ch) : kind === 'on' ? api.on(ch) : api.restore();
    return call
      .then((reply) => {
        apply(reply);
        if (kind === 'on') setRestoring((s) => new Set([...s, ch]));
        if (kind === 'restore') setRestoring((s) => new Set([...s, ...commandedRef.current]));
        return reply;
      })
      .catch((e) => {
        setError(e.message);
        return null;
      })
      .finally(() => setInflight((m) => {
        const next = { ...m };
        delete next[key];
        return next;
      }));
  }, [api, apply]);

  const settle = useCallback((ch) => setRestoring((s) => {
    if (!s.has(ch)) return s;
    const next = new Set(s);
    next.delete(ch);
    return next;
  }), []);

  return { commanded, controllable, inflight, restoring, error, command, settle, refresh };
}

// ------------------------------------------------------------ the view

export default function SabotageLab({
  state, signal, ages, offline, active, apiUrl, mock, onAck, onStartInjection, onStopInjection, ackError, onSelectScenario,
}) {
  const api = useMemo(() => (mock ? null : createSensorApi(apiUrl)), [apiUrl, mock]);
  const runId = state?.run_id ?? null;
  const ctl = useSensorControl(api, active, runId);
  const health = state?.health?.sensors ?? {};
  const isSim = state?.source === 'simulated' && state?.mode === 'live';
  const canControl = Boolean(api) && isSim && ctl.controllable !== false && !offline;

  // per-sensor view model
  const sensors = useMemo(() => Object.fromEntries(LAB_SENSORS.map((ch) => {
    const commanded = ctl.commanded.has(ch);
    const status = sensorStatus({
      commanded, health: health[ch], inflight: ctl.inflight[ch] ?? (ctl.inflight['*'] && commanded ? 'on' : null),
      restoring: ctl.restoring.has(ch),
    });
    return [ch, { commanded, health: health[ch], status }];
  })), [ctl.commanded, ctl.inflight, ctl.restoring, health]);

  // a re-enabled sensor stops "recovering" once the watchdog says NORMAL
  useEffect(() => {
    LAB_SENSORS.forEach((ch) => { if (health[ch] === 'NORMAL' && !ctl.commanded.has(ch)) ctl.settle(ch); });
  }, [health, ctl]);

  const off = useMemo(() => offlineSet(sensors), [sensors]);
  const everActive = useRef(false);
  useEffect(() => { everActive.current = false; }, [runId]);
  const classifier = classifierWitness(state, off, everActive.current);
  if (classifier.status === 'ACTIVE') everActive.current = true;
  const physics = physicsWitness(state, off);
  const level = evidenceLevel(classifier, off);
  const notes = integrityNotes(state, level, classifier);

  const log = useLabLog({ state, sensors, classifier, runId });

  const sendCommand = (kind, ch) => {
    log.command(kind, ch);
    return ctl.command(kind, ch);
  };

  if (!state) {
    return (
      <section className="lab lab-empty">
        <LabHeader level={null} scripted={false} />
        <p className="panel-note">Waiting for the first state message…</p>
      </section>
    );
  }

  const anyCut = LAB_SENSORS.some((ch) => sensors[ch].commanded || sensors[ch].status === 'RECOVERING');
  const explanation = explain({ state, level, classifier, sensors, anyCut });

  return (
    <section className={`lab${offline ? ' lab-offline' : ''}`} aria-label="Sabotage Lab">
      <LabHeader level={level} scripted={String(state.run_id).startsWith('fake_')} />

      <div className="lab-grid">
        <aside className="lab-col lab-left" aria-label="Controls">
          <SensorPanel
            sensors={sensors} canControl={canControl} ctl={ctl} onCommand={sendCommand}
            why={controlBlocker({ api, isSim, ctl, offline, mock })}
            onSwitchToSim={api && !offline && (!isSim || ctl.controllable === false) && onSelectScenario
              ? () => { log.note('Switch source to the live simulator'); onSelectScenario('sim'); setTimeout(ctl.refresh, 1500); }
              : null}
          />
          <InjectPanel state={state} enabled={Boolean(api) && isSim && ctl.controllable === true && !offline} onStart={onStartInjection} onStop={onStopInjection} log={log} />
        </aside>

        <div className="lab-center">
          <LabStage state={state} sensors={sensors} offline={offline} active={active} />
          <p className={`lab-explain tone-${explanation.tone}`} role="status" aria-live="polite">
            {explanation.text}
          </p>
        </div>

        <aside className="lab-col lab-right" aria-label="Evidence">
          <DecisionCard decision={state.decision} onAck={onAck} ackError={ackError} />
          <EvidenceCard level={level} notes={notes} />
          <PhysicsCard rules={physics} sensors={sensors} />
          <ClassifierCard classifier={classifier} />
        </aside>
      </div>

      <div className="lab-bottom">
        <LabSignals state={state} signal={signal} ages={ages} sensors={sensors} offline={offline} />
        <Timeline title="Sensor & evidence timeline" items={log.sensorLog} empty="Cut a sensor to start the record." />
        <Timeline title="Diagnostic transitions" items={log.decisionLog} empty="No transitions yet." />
      </div>
    </section>
  );
}

function controlBlocker({ api, isSim, ctl, offline, mock }) {
  if (mock || !api) return 'Sensor controls need the live backend (VITE_API_URL).';
  if (offline) return 'Diagnostics offline: controls paused until the stream returns.';
  if (!isSim || ctl.controllable === false) return 'The backend is not on the live simulator, so sensor cuts and fault injection are off. Switch to it here (starts a fresh run).';
  return null;
}

// one line under the engine saying what is going on, from backend facts only
function explain({ state, level, classifier, sensors, anyCut }) {
  const d = state.decision;
  const cut = LAB_SENSORS.filter((ch) => sensors[ch].commanded).map((ch) => SENSOR_INFO[ch].short);
  const recovering = LAB_SENSORS.filter((ch) => sensors[ch].status === 'RECOVERING').map((ch) => SENSOR_INFO[ch].short);
  if (recovering.length) return { tone: 'amber', text: `${recovering.join(', ')} re-enabled: waiting for valid data before showing it live.` };
  if (level !== 'FULL' && classifier.status === 'UNAVAILABLE') {
    if (d.state === 'ALARM') return { tone: 'red', text: `${cut.join(', ')} offline. The confirmed alarm stays latched until acknowledged and cleared by the rule layer; no new alarm can be confirmed.` };
    return { tone: 'amber', text: KEY_LINE };
  }
  if (anyCut && level === 'FULL') {
    return { tone: 'neutral', text: `${cut.join(', ')} offline. Both diagnostic witnesses still have their inputs, so evidence is unaffected.` };
  }
  if (d.state === 'ALARM') return { tone: 'red', text: 'Two independent witnesses agreed: the rule layer confirmed an alarm. Now take a sensor away.' };
  if (d.state === 'ADVISORY') return { tone: 'amber', text: 'One witness sees it. The rule layer waits for a second, matching witness before it alarms.' };
  if (d.state === 'UNCLASSIFIED') return { tone: 'amber', text: 'Something is abnormal, and 720 says it cannot name it.' };
  return { tone: 'neutral', text: 'Healthy engine, every sensor live. Inject a fault, then take a sensor away.' };
}

// ------------------------------------------------------------ header

function LabHeader({ level, scripted }) {
  const lv = level ? LEVEL_TEXT[level] : null;
  return (
    <header className="lab-head">
      <div className="lab-title">
        <span className="lab-kicker mono">720 · RELIABILITY TEST</span>
        <h2>SABOTAGE LAB</h2>
      </div>
      {scripted ? (
        <span className="lab-simbadge mono scripted" title="A fixed L0 test script, not the engine simulator">SCRIPTED L0 SCENARIO · NOT THE SIMULATOR</span>
      ) : (
        <span className="lab-simbadge mono" title="Everything in this tab runs on the engine simulator">SIMULATED TEST ENVIRONMENT</span>
      )}
      {lv && (
        <div className={`lab-level lv-${level.toLowerCase()}`} role="status" aria-live="polite">
          <span className="lab-level-title">{lv.title}</span>
          <span className="lab-level-sub">{lv.sub}</span>
        </div>
      )}
    </header>
  );
}

// ------------------------------------------------------------ left: controls

function SensorPanel({ sensors, canControl, ctl, onCommand, why, onSwitchToSim }) {
  const anyOff = LAB_SENSORS.some((ch) => sensors[ch].commanded);
  return (
    <section className="lab-card" aria-label="Sensor control">
      <div className="lab-card-head">
        <h3>Sensors</h3>
        <button
          type="button" className="lab-btn ghost" disabled={!canControl || !anyOff || Boolean(ctl.inflight['*'])}
          onClick={() => onCommand('restore')}
        >
          Restore all
        </button>
      </div>
      <ul className="lab-sensors">
        {LAB_SENSORS.map((ch) => {
          const s = sensors[ch];
          const info = SENSOR_INFO[ch];
          const busy = Boolean(ctl.inflight[ch] || ctl.inflight['*']);
          return (
            <li key={ch} className={`lab-sensor st-${s.status.toLowerCase()}`}>
              <div className="lab-sensor-id">
                <span className="lab-sensor-name">{info.short}</span>
                <span className="lab-sensor-feeds">{info.feeds}</span>
              </div>
              <span className="lab-chip mono" aria-live="polite">{STATUS_TEXT[s.status]}</span>
              <button
                type="button"
                className={`lab-btn ${s.commanded ? 'restore' : 'cut'}`}
                disabled={!canControl || busy}
                aria-pressed={s.commanded}
                aria-label={`${s.commanded ? 'Enable' : 'Disable'} ${info.name}`}
                onClick={() => onCommand(s.commanded ? 'on' : 'off', ch)}
              >
                {s.commanded ? 'Enable' : 'Disable'}
              </button>
            </li>
          );
        })}
      </ul>
      {why && <p className="lab-note">{why}</p>}
      {onSwitchToSim && (
        <button type="button" className="lab-btn primary" onClick={onSwitchToSim}>Switch to live simulator</button>
      )}
      {ctl.error && <p className="lab-note err" role="alert">{ctl.error}</p>}
      <p className="lab-note faint">Disable blanks the sensor inside the simulator. The watchdog confirms it after 3 s without data; nothing here changes until the backend says so.</p>
    </section>
  );
}

function InjectPanel({ state, enabled, onStart, onStop, log }) {
  const [fault, setFault] = useState('misfire');
  const [cyl, setCyl] = useState(3);
  const [sev, setSev] = useState(1.0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const inj = state.injection?.active ? state.injection : null;
  const run = (p, what) => {
    setBusy(true);
    setErr(null);
    log.note(what);
    Promise.resolve().then(p).catch((e) => setErr(e.message)).finally(() => setBusy(false));
  };
  return (
    <section className="lab-card" aria-label="Fault injection">
      <div className="lab-card-head"><h3>Fault injection</h3><span className="lab-tag mono">SIMULATED</span></div>
      <div className="lab-seg" role="group" aria-label="Fault">
        {INJECT_FAULTS.map((f) => (
          <button key={f} type="button" className={`lab-seg-btn${f === fault ? ' on' : ''}`} aria-pressed={f === fault} onClick={() => setFault(f)} disabled={!enabled}>
            {FAULTS[f].name}
          </button>
        ))}
      </div>
      <div className="lab-row">
        <span className="lab-row-label">Cylinder</span>
        <div className="lab-seg small" role="group" aria-label="Cylinder">
          {[1, 2, 3, 4].map((c) => (
            <button key={c} type="button" className={`lab-seg-btn${c === cyl ? ' on' : ''}`} aria-pressed={c === cyl} onClick={() => setCyl(c)} disabled={!enabled}>{c}</button>
          ))}
        </div>
      </div>
      <label className="lab-row">
        <span className="lab-row-label">{fault === 'misfire' ? 'Misfire prob.' : 'Severity'}</span>
        <input type="range" min="0.05" max="1" step="0.05" value={sev} disabled={!enabled} onChange={(e) => setSev(Number(e.target.value))} />
        <span className="mono lab-sev">{sev.toFixed(2)}</span>
      </label>
      <div className="lab-actions">
        <button
          type="button" className="lab-btn primary" disabled={!enabled || busy}
          onClick={() => run(() => onStart({ fault, cylinder: cyl, severity: sev }), `Inject ${FAULTS[fault].name.toLowerCase()} · cyl ${cyl} · ${sev.toFixed(2)}`)}
        >
          Inject
        </button>
        <button type="button" className="lab-btn ghost" disabled={!enabled || busy || !inj} onClick={() => run(onStop, 'Clear faults')}>
          Clear fault
        </button>
      </div>
      <p className="lab-note mono" aria-live="polite">
        {inj ? `ACTIVE · ${faultInfo(inj.fault).name}${inj.cylinder != null ? ` · cyl ${inj.cylinder}` : ''} · ${inj.severity.toFixed(2)}` : 'No injection active (healthy engine)'}
      </p>
      {err && <p className="lab-note err" role="alert">{err}</p>}
      <p className="lab-note faint">Clearing a fault never re-enables a sensor.</p>
    </section>
  );
}

// ------------------------------------------------------------ right: evidence

function DecisionCard({ decision: d, onAck, ackError }) {
  const s = state3d(d.state);
  const text = decisionText(d);
  const [waiting, setWaiting] = useState(null);
  useEffect(() => { if (d.acked || d.alarm_id !== waiting) setWaiting(null); }, [d.acked, d.alarm_id, waiting]);
  const canAck = d.state === 'ALARM' && d.alarm_id && !d.acked;
  const guidance = typeof d.guidance === 'string' && d.guidance.trim() ? d.guidance.trim() : null;
  return (
    <section className={`lab-card lab-decision ds-${d.state.toLowerCase()}`} style={{ '--state': s.color }} aria-label="Rule layer decision">
      <div className="lab-card-head">
        <h3>Rule layer decision</h3>
        <span className="lab-tag mono">AUTHORITATIVE</span>
      </div>
      <div className="lab-decision-state" role="status" aria-live="polite">
        <span className="lab-decision-name">{s.label}</span>
        <span className="lab-decision-title">{text.title}</span>
      </div>
      <dl className="lab-facts mono">
        {/* on ALARM the rule layer reports the witnesses that confirmed it, not the current window's */}
        <div><dt>{d.state === 'ALARM' ? 'Confirmed by' : 'Witnesses'}</dt><dd>{d.witnesses?.length ? d.witnesses.join(' + ') : '—'}</dd></div>
        <div><dt>Windows</dt><dd>{d.consecutive_windows ?? '—'}</dd></div>
        {d.alarm_id && <div><dt>Alarm</dt><dd>{d.alarm_id} · {d.latched ? 'latched' : 'not latched'} · {d.acked ? `ACK ${d.acked_by ?? ''}` : 'unacknowledged'}</dd></div>}
      </dl>
      {canAck && (
        <button
          type="button" className="lab-btn ack" disabled={waiting === d.alarm_id}
          onClick={() => { setWaiting(d.alarm_id); onAck(d.alarm_id); }}
        >
          {waiting === d.alarm_id ? 'Waiting for the rule layer…' : 'Acknowledge alarm'}
        </button>
      )}
      {ackError && <p className="lab-note err">ACK refused: {ackError.reason}</p>}
      {guidance && <p className="lab-guidance"><span className="eyebrow">Operator guidance</span>{guidance}</p>}
    </section>
  );
}

function EvidenceCard({ level, notes }) {
  return (
    <section className={`lab-card lab-evidence lv-${level.toLowerCase()}`} aria-label="Evidence integrity">
      <div className="lab-card-head"><h3>Evidence integrity</h3></div>
      <p className="lab-confirm mono">
        New confirmed alarm possible: <strong>{level === 'FULL' ? 'YES' : 'NO'}</strong>
      </p>
      {notes.map((n) => <p key={n.text} className={`lab-integrity tone-${n.tone}`}>{n.text}</p>)}
      {!notes.length && <p className="lab-note faint">Describes which evidence is available. It is not a diagnosis; the rule layer decides.</p>}
    </section>
  );
}

function PhysicsCard({ rules, sensors }) {
  const firing = rules.filter((r) => r.fired);
  return (
    <section className="lab-card" aria-label="Physics witness">
      <div className="lab-card-head">
        <h3>Witness 1 · Physics</h3>
        <span className={`lab-tag mono${firing.length ? ' hot' : ''}`}>{firing.length ? 'RULE FIRING' : 'QUIET'}</span>
      </div>
      <ul className="lab-rules">
        {rules.map((r) => {
          const sensorDown = r.sensor && !r.available;
          return (
            <li key={r.rule} className={`${r.fired ? 'fired' : ''}${sensorDown ? ' down' : ''}`}>
              <span className="lab-rule-name">{RULE_NAME[r.rule] ?? r.rule}</span>
              <span className="lab-rule-state mono">
                {sensorDown ? `NO INPUT · ${SENSOR_INFO[r.sensor].short} offline` : r.fired ? `FIRED${r.location ? ` · cyl ${r.location}` : ''}` : 'quiet'}
              </span>
              <span className="lab-rule-basis">{RULE_BASIS[r.rule]}{r.sensor === 'obd' ? ' (always on)' : ''}</span>
            </li>
          );
        })}
      </ul>
      {sensors.cam.commanded && <p className="lab-note faint">CAM offline: the cycle phase is held from the last cam pulse while the crank keeps counting.</p>}
    </section>
  );
}

function ClassifierCard({ classifier: c }) {
  const active = c.status === 'ACTIVE';
  return (
    <section className={`lab-card lab-classifier cs-${c.status.toLowerCase()}`} aria-label="Classifier witness">
      <div className="lab-card-head">
        <h3>Witness 2 · ML classifier</h3>
        <span className={`lab-tag mono${active ? '' : ' warn'}`}>{active ? 'AVAILABLE' : c.status === 'NOT_LOADED' ? 'NOT LOADED' : 'UNAVAILABLE'}</span>
      </div>
      {active ? (
        <div className="lab-model">
          <span className={`lab-model-label${!c.counted && c.label !== 'healthy' ? ' muted' : ''}`}>{faultInfo(c.label).name}{c.cylinder ? ` · cyl ${c.cylinder}` : ''}</span>
          <span className="lab-model-p mono">{c.model} p = {Number(c.p).toFixed(2)}</span>
          <span className="lab-note faint">
            {c.counted ? 'Counted by the rule layer as the second witness.'
              : c.label === 'healthy' ? 'Output as sent by the backend this window.'
                : 'Not counted as a witness by the rule layer this window.'}
          </span>
        </div>
      ) : (
        <p className="lab-model-off">{c.reason}</p>
      )}
      <p className="lab-note faint">Inputs: crank and accelerometer features plus OBD trims (SVM, fast_v1).</p>
    </section>
  );
}

// ------------------------------------------------------------ bottom: timelines

function Timeline({ title, items, empty }) {
  return (
    <section className="lab-card lab-timeline" aria-label={title}>
      <div className="lab-card-head"><h3>{title}</h3></div>
      {items.length ? (
        <ol className="lab-log">
          {items.map((e) => (
            <li key={e.id} className={`tone-${e.tone}`}>
              <span className="lab-log-t mono">{e.clock}</span>
              <span className="lab-log-text">{e.text}</span>
              {e.dt != null && <span className="lab-log-dt mono">+{secs(e.dt)}</span>}
            </li>
          ))}
        </ol>
      ) : <p className="lab-note faint">{empty}</p>}
    </section>
  );
}

// Records what happened and measures how long the backend took to confirm it. Times are measured in
// this browser from the command click to the first broadcast that shows the change.
function useLabLog({ state, sensors, classifier, runId }) {
  const [sensorLog, setSensorLog] = useState([]);
  const [decisionLog, setDecisionLog] = useState([]);
  const idRef = useRef(0);
  const lastCmd = useRef({}); // ch -> { kind, at }
  const lastAnyCmd = useRef(null);
  const prev = useRef({ health: {}, cls: null, dec: null });

  const push = useCallback((setter, entry) => {
    idRef.current += 1;
    setter((l) => [{ id: idRef.current, clock: clock(), ...entry }, ...l].slice(0, LOG_KEEP));
  }, []);

  const command = useCallback((kind, ch) => {
    const at = performance.now();
    if (ch) lastCmd.current[ch] = { kind, at };
    else LAB_SENSORS.forEach((c) => { lastCmd.current[c] = { kind: 'on', at }; });
    lastAnyCmd.current = { kind, ch, at };
    const text = kind === 'restore' ? 'Command: restore all sensors' : `Command: ${kind === 'off' ? 'disable' : 'enable'} ${SENSOR_INFO[ch].short}`;
    push(setSensorLog, { text, tone: kind === 'off' ? 'amber' : 'neutral' });
  }, [push]);

  const note = useCallback((text) => push(setDecisionLog, { text: `Command: ${text}`, tone: 'neutral' }), [push]);

  // a new run (source switch): start the comparison over, keep the record
  useEffect(() => { prev.current = { health: {}, cls: null, dec: null }; }, [runId]);

  useEffect(() => {
    if (!state) return;
    const now = performance.now();
    // sensor health transitions (backend watchdog)
    LAB_SENSORS.forEach((ch) => {
      const h = sensors[ch].health;
      const before = prev.current.health[ch];
      if (before && h && before !== h) {
        const cmd = lastCmd.current[ch];
        const dt = cmd ? now - cmd.at : null;
        if (h === 'FAILURE') push(setSensorLog, { text: `${SENSOR_INFO[ch].short} offline: confirmed by the backend watchdog`, tone: 'red', dt: cmd?.kind === 'off' ? dt : null });
        else push(setSensorLog, { text: `${SENSOR_INFO[ch].short} live again: valid data confirmed`, tone: 'ok', dt: cmd?.kind !== 'off' ? dt : null });
      }
      prev.current.health[ch] = h;
    });
    // classifier witness availability
    const cls = classifier.status === 'ACTIVE' ? 'ACTIVE' : classifier.status;
    if (prev.current.cls && prev.current.cls !== cls) {
      const cmd = lastAnyCmd.current;
      const dt = cmd ? now - cmd.at : null;
      if (cls === 'ACTIVE') push(setSensorLog, { text: 'Classifier witness resumed: model inference back', tone: 'ok', dt });
      else if (cls === 'UNAVAILABLE') push(setSensorLog, { text: `Classifier witness withdrawn (${classifier.missing.map((c) => SENSOR_INFO[c].short).join(', ')} offline)`, tone: 'amber', dt });
    }
    prev.current.cls = cls;
    // decision transitions (rule layer)
    const d = state.decision;
    const key = `${d.state}|${d.fault}|${d.location}|${d.alarm_id}|${d.acked}`;
    if (prev.current.dec && prev.current.dec.key !== key) {
      const t = decisionText(d);
      const inj = state.injection;
      const sinceInj = inj?.active && inj.started_at && (d.state === 'ALARM' || d.state === 'ADVISORY') && prev.current.dec.state === 'NORMAL'
        ? (state.sent_at - inj.started_at) * 1000 : null;
      const ackText = d.state === 'ALARM' && d.acked && !prev.current.dec.acked ? ` · acknowledged by ${d.acked_by ?? 'operator'}` : '';
      push(setDecisionLog, {
        text: `${state3d(d.state).label} · ${t.title}${d.witnesses?.length ? ` · ${d.witnesses.join(' + ')}` : ''}${ackText}`,
        tone: d.state === 'ALARM' ? 'red' : d.state === 'NORMAL' ? 'neutral' : 'amber',
        dt: sinceInj != null && sinceInj >= 0 ? sinceInj : null,
      });
    }
    prev.current.dec = { key, state: d.state, acked: d.acked };
  }, [state, sensors, classifier, push]);

  return { sensorLog, decisionLog, command, note };
}

