import { useEffect, useState } from 'react';
import { FAULTS } from '../lib/faults.js';
import SourceBadge from './SourceBadge.jsx';

// Injection console (task 7, PROPOSED schema 1.4). Asks the backend to inject a simulated fault;
// it never shows an injection itself. "Injecting…" appears only once `state.injection` says so,
// like ACK. Enabled only for a live simulated stream whose backend sends `injection`.
// Severity is the REQUESTED value sent to the simulator, not a measurement or a confidence.
// For misfire the simulator reads it as the probability that the cylinder misfires on a cycle.

const FAULT_IDS = ['misfire', 'knock', 'injector_clog', 'valve_tick'];
// REAL carOBD replay (contract 1.6): only a bank-wide fuel-trim ramp can be added to a real log
const REPLAY_FAULT_IDS = ['lean_drift'];
const LEAN_DEFAULT = 0.5; // steady added trim s/(4-s) = 14 %, about twice the trim rule's limit
const CYLINDERS = [1, 2, 3, 4];
const CONFIRM_MS = 3000; // accepted request but no matching state.injection by then → error
const DEFAULT_REQUEST = 1.0; // demo default: a misfire on every cycle (lower values stay selectable)

// what the requested value means for this fault, in words
const requestLabel = (f) => (f === 'misfire' ? 'Requested misfire probability' : 'Requested severity');
const requestValue = (f, v) => (f === 'misfire' ? `${v.toFixed(2)} (${Math.round(v * 100)}% of cycles)` : v.toFixed(2));

// why the console can't be used right now, or null
function unavailableReason(state, offline, mock) {
  if (offline) return 'Diagnostics offline: the console needs a live connection to the rule layer.';
  if (!mock && !('injection' in state)) {
    return `Backend injection unavailable: this backend sends schema ${state.schema_version} without state.injection. The console needs the proposed 1.4 field so it can confirm what the simulator is injecting.`;
  }
  if (state.mode === 'replay' && state.replay) return null; // lean drift onto the REAL replay
  if (state.mode !== 'live' || state.source !== 'simulated') {
    return `Injection runs only on the live simulator. This stream is ${state.source.toUpperCase()} · ${state.mode}.`;
  }
  return null;
}

export default function InjectionConsole({ state, offline, mock, onStart, onStop }) {
  const onReplay = state.mode === 'replay' && state.replay != null;
  const faultIds = onReplay ? REPLAY_FAULT_IDS : FAULT_IDS;
  const [fault, setFault] = useState(onReplay ? 'lean_drift' : 'misfire');
  const [cyl, setCyl] = useState(onReplay ? null : 1);
  const [severity, setSeverity] = useState(onReplay ? LEAN_DEFAULT : DEFAULT_REQUEST);
  // pending: { kind: 'start' | 'stop', fault?, cylinder?, acceptedAt? } until the state confirms
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);

  const inj = state.injection ?? null;
  const reason = unavailableReason(state, offline, mock);

  // confirmation comes only from the broadcast state
  useEffect(() => {
    if (!pending) return;
    const confirmed = pending.kind === 'start'
      ? inj && inj.fault === pending.fault && inj.cylinder === pending.cylinder
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
  const start = () => send({ kind: 'start', fault, cylinder: cyl }, () => onStart({ fault, cylinder: cyl, severity }));
  const stop = () => send({ kind: 'stop' }, () => onStop());

  const locked = reason != null || pending != null || inj != null; // inputs: only before a new start
  const runningFor = inj ? Math.max(0, Date.now() / 1000 - inj.started_at) : 0;

  return (
    <section className="panel inject" aria-label="Injection console">
      <div className="inject-head">
        <div className="panel-title">
          <h2 className="panel-label">INJECTION CONSOLE</h2>
          {onReplay ? <SourceBadge source="injected" label="real_replay_injected" /> : <SourceBadge source="simulated" />}
        </div>
        <span className="mono inject-sub">
          {onReplay ? 'Adds a fuel-trim ramp to the real log; the rule layer decides what it is' : 'Asks the simulator to add a fault; the rule layer decides what it is'}
        </span>
      </div>

      {reason && <p className="panel-note inject-reason">{reason}</p>}

      <div className={`inject-form${reason ? ' disabled' : ''}`}>
        <fieldset className="inject-group" disabled={locked}>
          <legend className="eyebrow">Fault</legend>
          {faultIds.map((f) => (
            <button key={f} type="button" className={`run-pill${fault === f ? ' on' : ''}`} aria-pressed={fault === f} onClick={() => setFault(f)}>
              {FAULTS[f].name}
            </button>
          ))}
        </fieldset>
        {!onReplay && <fieldset className="inject-group" disabled={locked}>
          <legend className="eyebrow">Cylinder</legend>
          {CYLINDERS.map((c) => (
            <button key={c} type="button" className={`run-pill${cyl === c ? ' on' : ''}`} aria-pressed={cyl === c} onClick={() => setCyl(c)}>
              {c}
            </button>
          ))}
        </fieldset>}
        <fieldset className="inject-group" disabled={locked}>
          <legend className="eyebrow">{requestLabel(fault)}</legend>
          <input
            type="range" min="0.05" max="1" step="0.05" value={severity}
            onChange={(e) => setSeverity(Number(e.target.value))}
            aria-label={`${requestLabel(fault)}, 0 to 1`}
            aria-valuetext={requestValue(fault, severity)}
          />
          <span className="mono inject-sev">{requestValue(fault, severity)}</span>
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

      {!inj && onReplay && (
        <p className="panel-note">
          Bank-wide, no cylinder. The added total trim ramps over 60 s of log time to s/(4−s) (the simulator&apos;s
          injector-clog relation, {Math.round(1000 * severity / (4 - severity)) / 10} % here); the ECU&apos;s STFT carries it first and LTFT learns it.
          The stream is labelled REAL + INJECTED while it runs.
        </p>
      )}
      {!inj && !onReplay && (
        <p className="panel-note">
          {fault === 'misfire'
            ? 'The value is what we ask the simulator for: the chance that the cylinder misfires on each cycle. It is not a measured fault severity or a confidence; the rule layer decides what it sees.'
            : 'The value is what we ask the simulator for, not a measured fault severity or a confidence.'}
        </p>
      )}
      {fault === 'valve_tick' && !inj && !reason && (
        <p className="panel-note">Valve tick is held out of ML training: expect the rule layer to report it as unclassified, not by name.</p>
      )}

      {inj && (
        <p className="inject-status mono" role="status">
          <strong>INJECTING</strong> {FAULTS[inj.fault]?.name ?? inj.fault}{inj.cylinder != null ? ` · cyl ${inj.cylinder}` : ' · bank-wide'} · {requestLabel(inj.fault).toLowerCase()} {requestValue(inj.fault, inj.severity)} · for {runningFor.toFixed(0)} s
        </p>
      )}
      {error && <p className="inject-error" role="alert">{error}</p>}

      {mock && (
        <p className="bank-note mono">
          Mock: the waveforms are illustrative and do not scale with the requested value (it only sets the injector-clog fuel-trim drift, ≈ value ÷ 4). Injects onto Simulated · healthy only.
        </p>
      )}
    </section>
  );
}
