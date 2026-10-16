import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FAULTS, decisionText, fault as faultInfo, state3d } from '../lib/faults.js';
import {
  LAB_SENSORS, LEVEL_TEXT, SENSOR_INFO, STATUS_TEXT,
  classifierWitness, evidenceLevel, outcome, physicsWitness, sensorStatus,
} from './evidence.js';
import { createSensorApi } from './sensorApi.js';
import { useBackendEvents } from './useBackend.js';
import LabScene from './LabScene.jsx';
import Outcome from './Outcome.jsx';
import AgentPanel from './AgentPanel.jsx';
import IndustrialLink, { useIndustrial } from './IndustrialLink.jsx';
import Incident from './Incident.jsx';
import LabSignals from './LabSignals.jsx';
import Guide, { currentStep } from './Guide.jsx';
import Details from './Details.jsx';
import ExportButton from './ExportButton.jsx';
import './sabotage.css';
import './resilience.css';

// SABOTAGE LAB = 720 INDUSTRIAL RESILIENCE LAB (D140, D141): a judge switches simulator sensors off and on
// and watches what 720 can still prove across the whole chain: sensors, evidence, rule layer, OPC UA. Everything shown comes from the backend: sensor state from GET /sensors and the watchdog's
// state.health.sensors, evidence from state.models / state.physics_rules, the decision from the rule
// layer. This view never derives a fault, a confidence or a decision, and never clears an alarm.

const POLL_MS = 2000;
const INJECT_FAULTS = ['misfire', 'knock', 'injector_clog', 'valve_tick'];


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
  const { events, error: evError } = useBackendEvents(api, active);
  const { data: industrial, error: indError } = useIndustrial(api, active);

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

  const off = useMemo(() => new Set(LAB_SENSORS.filter((ch) => sensors[ch].commanded || sensors[ch].health === 'FAILURE')), [sensors]);
  const everActive = useRef(false);
  useEffect(() => { everActive.current = false; }, [runId]);
  const classifier = classifierWitness(state, off, everActive.current);
  if (classifier.status === 'ACTIVE') everActive.current = true;
  const physics = physicsWitness(state, off);
  const level = evidenceLevel(classifier, off);

  if (!state) {
    return (
      <section className="lab rl lab-empty">
        <LabHeader scripted={false} api={null} />
        <p className="panel-note">Waiting for the first state message…</p>
      </section>
    );
  }
  const result = outcome({ state, sensors, classifier, level });

  return (
    <section className={`lab rl${offline ? ' lab-offline' : ''}`} aria-label="720 Industrial Resilience Lab">
      <LabHeader scripted={String(state.run_id).startsWith('fake_')} api={api} />
      <Guide step={currentStep(state, sensors)} />

      <div className="rl-grid">
        <aside className="lab-col rl-left" aria-label="Controls">
          <InjectPanel state={state} enabled={Boolean(api) && isSim && ctl.controllable === true && !offline} onStart={onStartInjection} onStop={onStopInjection} />
          <SensorPanel
            sensors={sensors} canControl={canControl} ctl={ctl} onCommand={(kind, ch) => ctl.command(kind, ch)}
            why={controlBlocker({ api, isSim, ctl, offline, mock })}
            onSwitchToSim={api && !offline && (!isSim || ctl.controllable === false) && onSelectScenario
              ? () => { onSelectScenario('sim'); setTimeout(ctl.refresh, 1500); }
              : null}
          />
        </aside>

        <div className="rl-center">
          <DecisionCard decision={state.decision} onAck={onAck} ackError={ackError} level={level} />
          <LabScene
            state={state} sensors={sensors} classifier={classifier} physics={physics}
            industrial={industrial} offline={offline} active={active}
          />
        </div>

      </div>

      <Outcome result={result} state={state} sensors={sensors} level={level} />

      <Details
        tabs={[
          { id: 'agent', label: 'Reliability Agent', body: <AgentPanel api={api} events={events} active={active} /> },
          {
            id: 'timeline', label: 'Incident timeline', badge: events.length ? String(events.filter((e) => e.kind !== 'opcua_event').length) : null,
            body: <Incident events={events} error={evError} api={api} showOpc={false} />,
          },
          {
            id: 'opcua', label: 'OPC UA link', badge: industrial ? (industrial.running ? 'UP' : 'DOWN') : null, badgeTone: industrial?.running ? 'ok' : 'red',
            body: <IndustrialLink data={industrial} error={indError} />,
          },
          { id: 'signals', label: 'Live signals', body: <LabSignals state={state} signal={signal} ages={ages} sensors={sensors} offline={offline} /> },
        ]}
      />
      <p className="rl-closing">Most systems tell you when an engine fails. 720 also tells you when its own diagnosis can no longer be trusted.</p>
    </section>
  );
}

// ------------------------------------------------------------ header

// title on the left, the report export on the right; a scripted (fake L0) source still says so
function LabHeader({ scripted, api }) {
  return (
    <header className="lab-head rl-head">
      <div className="lab-title">
        <h2>Industrial Resilience Lab</h2>
        <span className="rl-sub">Challenge the sensors. Test the evidence. Trust the decision.</span>
      </div>
      <div className="rl-head-right">
        {scripted && (
          <span className="lab-simbadge mono scripted" title="A fixed L0 test script, not the engine simulator">SCRIPTED L0 SCENARIO · NOT THE SIMULATOR</span>
        )}
        <ExportButton api={api} className="lab-btn primary rl-export" />
      </div>
    </header>
  );
}

// ------------------------------------------------------------ left: controls

function controlBlocker({ api, isSim, ctl, offline, mock }) {
  if (mock || !api) return 'Sensor controls need the live backend (VITE_API_URL).';
  if (offline) return 'Diagnostics offline: controls paused until the stream returns.';
  if (!isSim || ctl.controllable === false) return 'The backend is not on the live simulator, so sensor cuts and fault injection are off. Switch to it here (starts a fresh run).';
  return null;
}

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
      <p className="lab-note faint">A cut is confirmed by the backend watchdog after 3 s without data.</p>
    </section>
  );
}

function InjectPanel({ state, enabled, onStart, onStop }) {
  const [fault, setFault] = useState('misfire');
  const [cyl, setCyl] = useState(3);
  const [sev, setSev] = useState(1.0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const inj = state.injection?.active ? state.injection : null;
  const run = (p) => {
    setBusy(true);
    setErr(null);
    Promise.resolve().then(p).catch((e) => setErr(e.message)).finally(() => setBusy(false));
  };
  return (
    <section className="lab-card" aria-label="Fault injection">
      <div className="lab-card-head"><h3>Fault</h3><span className="lab-tag mono">SIMULATED</span></div>
      <div className="lab-actions inject-main">
        <button
          type="button" className="lab-btn primary big" disabled={!enabled || busy}
          onClick={() => run(() => onStart({ fault, cylinder: cyl, severity: sev }))}
        >
          Inject {FAULTS[fault].name.toLowerCase()} · cylinder {cyl}
        </button>
        <button type="button" className="lab-btn ghost" disabled={!enabled || busy || !inj} onClick={() => run(onStop)}>
          Clear
        </button>
      </div>
      <p className="lab-note mono" aria-live="polite">
        {inj ? `ACTIVE · ${faultInfo(inj.fault).name}${inj.cylinder != null ? ` · cyl ${inj.cylinder}` : ''} · ${inj.severity.toFixed(2)}` : 'Healthy engine: nothing injected'}
      </p>
      {err && <p className="lab-note err" role="alert">{err}</p>}
      <details className="inject-more">
        <summary>Change fault</summary>
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
      <p className="lab-note faint">Clearing a fault never re-enables a sensor.</p>
      </details>
    </section>
  );
}

// ------------------------------------------------------------ decision

function DecisionCard({ decision: d, onAck, ackError, level }) {
  const s = state3d(d.state);
  const text = decisionText(d);
  const [waiting, setWaiting] = useState(null);
  useEffect(() => { if (d.acked || d.alarm_id !== waiting) setWaiting(null); }, [d.acked, d.alarm_id, waiting]);
  const canAck = d.state === 'ALARM' && d.alarm_id && !d.acked;
  const guidance = typeof d.guidance === 'string' && d.guidance.trim() ? d.guidance.trim() : null;
  const lv = level ? LEVEL_TEXT[level] : null;
  return (
    <section className={`dbar ds-${d.state.toLowerCase()}`} style={{ '--state': s.color }} aria-label="Rule layer decision">
      <div className="dbar-state" role="status" aria-live="polite">
        <span className="dbar-kicker">RULE LAYER DECISION</span>
        <span className="dbar-name">{s.label}</span>
        <span className="dbar-title">{text.title}</span>
      </div>
      <dl className="dbar-facts mono">
        <div><dt>{d.state === 'ALARM' ? 'Confirmed by' : 'Witnesses'}</dt><dd>{d.witnesses?.length ? d.witnesses.join(' + ') : '—'}</dd></div>
        {d.alarm_id && <div><dt>Alarm</dt><dd>{d.alarm_id} · {d.acked ? `ACK ${d.acked_by ?? ''}` : 'unacknowledged'}</dd></div>}
        {lv && <div><dt>Evidence</dt><dd className={`lv-${level.toLowerCase()}`}>{level === 'FULL' ? 'full · new alarms possible' : `${level.toLowerCase()} · no new alarm`}</dd></div>}
      </dl>
      <div className="dbar-guidance">
        <span className="dbar-kicker">OPERATOR GUIDANCE</span>
        <span>{guidance ?? 'No action needed.'}</span>
        {ackError && <span className="lab-note err">ACK refused: {ackError.reason}</span>}
      </div>
      {canAck ? (
        <button
          type="button" className="lab-btn ack dbar-ack" disabled={waiting === d.alarm_id}
          onClick={() => { setWaiting(d.alarm_id); onAck(d.alarm_id); }}
        >
          {waiting === d.alarm_id ? 'Waiting…' : 'Acknowledge'}
        </button>
      ) : <span className="dbar-ack-gap" aria-hidden="true" />}
    </section>
  );
}
