import { useEffect, useState } from 'react';
import { FAULTS } from '../lib/faults.js';
import SourceBadge from './SourceBadge.jsx';

// Injection console (task 7, PROPOSED schema 1.4). Asks the backend to inject a simulated fault;
// it never shows an injection itself. "Injecting…" appears only once `state.injection` says so,
// like ACK. Enabled only for a live simulated stream whose backend sends `injection`.
// Severity is the REQUESTED value sent to the simulator, not a measurement.

const FAULT_IDS = ['misfire', 'knock', 'injector_clog', 'valve_tick'];
const CYLINDERS = [1, 2, 3, 4];
const CONFIRM_MS = 3000; // accepted request but no matching state.injection by then → error

// why the console can't be used right now, or null
function unavailableReason(state, offline, mock) {
  if (offline) return 'Diagnostics offline: the console needs a live connection to the rule layer.';
  if (!mock && !('injection' in state)) {
    return `Backend injection unavailable: this backend sends schema ${state.schema_version} without state.injection. It needs the proposed 1.4 contract and POST /inject.`;
  }
  if (state.mode !== 'live' || state.source !== 'simulated') {
    return `Injection runs only on the live simulator. This stream is ${state.source.toUpperCase()} · ${state.mode}.`;
  }
  return null;
}

export default function InjectionConsole({ state, offline, mock, onStart, onStop }) {
  const [fault, setFault] = useState('misfire');
  const [cyl, setCyl] = useState(1);
  const [severity, setSeverity] = useState(0.3);
  // pending: { kind: 'start' | 'stop', fault?, location?, acceptedAt? } until the state confirms
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);

  const inj = state.injection ?? null;
  const reason = unavailableReason(state, offline, mock);

  // confirmation comes only from the broadcast state
  useEffect(() => {
    if (!pending) return;
    const confirmed = pending.kind === 'start'
      ? inj && inj.fault === pending.fault && inj.location === pending.location
      : inj == null;
    if (confirmed) setPending(null);
  }, [inj, pending]);

  // a refusal is about the situation at the time; once the injection or the alarm changes, it's stale
  const alarmId = state.decision.alarm_id;
  useEffect(() => {
    if (!pending) setError(null);
  }, [inj?.started_at, alarmId]); // eslint-disable-line react-hooks/exhaustive-deps

  // accepted but never confirmed → say so instead of waiting forever
  useEffect(() => {
    if (!pending?.acceptedAt) return undefined;
    const id = setTimeout(() => {
      setPending(null);
      setError(`The backend accepted the request, but no state confirmed it within ${CONFIRM_MS / 1000} s (state.injection did not change).`);
    }, CONFIRM_MS);
    return () => clearTimeout(id);
  }, [pending?.acceptedAt]);

  const send = (req, call) => {
    setError(null);
    setPending(req);
    call()
      .then(() => setPending((p) => (p === req ? { ...req, acceptedAt: Date.now() } : p)))
      .catch((e) => {
        console.error('[720] injection request failed', e);
        setPending(null);
        setError(e.message);
      });
  };
  const start = () => send({ kind: 'start', fault, location: cyl }, () => onStart({ fault, location: cyl, severity }));
  const stop = () => send({ kind: 'stop' }, () => onStop());

  const locked = reason != null || pending != null || inj != null; // inputs: only before a new start
  const runningFor = inj ? Math.max(0, Date.now() / 1000 - inj.started_at) : 0;

  return (
    <section className="panel inject" aria-label="Injection console">
      <div className="inject-head">
        <div className="panel-title">
          <h2 className="panel-label">INJECTION CONSOLE</h2>
          <SourceBadge source="simulated" />
        </div>
        <span className="mono inject-sub">Asks the simulator to add a fault; the rule layer decides what it is</span>
      </div>

      {reason && <p className="panel-note inject-reason">{reason}</p>}

      <div className={`inject-form${reason ? ' disabled' : ''}`}>
        <fieldset className="inject-group" disabled={locked}>
          <legend className="eyebrow">Fault</legend>
          {FAULT_IDS.map((f) => (
            <button key={f} type="button" className={`run-pill${fault === f ? ' on' : ''}`} aria-pressed={fault === f} onClick={() => setFault(f)}>
              {FAULTS[f].name}
            </button>
          ))}
        </fieldset>
        <fieldset className="inject-group" disabled={locked}>
          <legend className="eyebrow">Cylinder</legend>
          {CYLINDERS.map((c) => (
            <button key={c} type="button" className={`run-pill${cyl === c ? ' on' : ''}`} aria-pressed={cyl === c} onClick={() => setCyl(c)}>
              {c}
            </button>
          ))}
        </fieldset>
        <fieldset className="inject-group" disabled={locked}>
          <legend className="eyebrow">Requested severity</legend>
          <input
            type="range" min="0.05" max="1" step="0.05" value={severity}
            onChange={(e) => setSeverity(Number(e.target.value))}
            aria-label="Requested severity, 0 to 1"
          />
          <span className="mono inject-sev">{severity.toFixed(2)}</span>
        </fieldset>
        <div className="inject-actions">
          {inj ? (
            <button type="button" className="ack-btn" onClick={stop} disabled={reason != null || pending != null}>
              {pending?.kind === 'stop' ? 'Waiting for the backend…' : 'Stop injection'}
            </button>
          ) : (
            <button type="button" className="ack-btn" onClick={start} disabled={reason != null || pending != null}>
              {pending?.kind === 'start' ? 'Waiting for the backend…' : 'Inject'}
            </button>
          )}
        </div>
      </div>

      {fault === 'valve_tick' && !inj && !reason && (
        <p className="panel-note">Valve tick is held out of ML training: expect the rule layer to report it as unclassified, not by name.</p>
      )}

      {inj && (
        <p className="inject-status mono" role="status">
          <strong>INJECTING</strong> {FAULTS[inj.fault]?.name ?? inj.fault} · cyl {inj.location} · requested severity {inj.severity.toFixed(2)} · for {runningFor.toFixed(0)} s
        </p>
      )}
      {error && <p className="inject-error" role="alert">{error}</p>}

      {mock && (
        <p className="bank-note mono">
          Mock: the waveforms are illustrative and do not scale with the requested severity (it only sets the injector-clog fuel-trim drift). Injects onto Simulated · healthy only.
        </p>
      )}
    </section>
  );
}
