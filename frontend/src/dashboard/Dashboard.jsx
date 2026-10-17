import { decisionColor } from '../lib/faults.js';
import { FIRING_ORDER } from '../lib/geometry.js';
import CylinderBank from './CylinderBank.jsx';
import CrankSignature from './CrankSignature.jsx';
import Verdict from './Verdict.jsx';
import StatePanel from './StatePanel.jsx';
import ObdPanel from './ObdPanel.jsx';
import SignalStrip from './SignalStrip.jsx';
import SourceBadge from './SourceBadge.jsx';
import TimeLapse from './TimeLapse.jsx';
import HealthChips from './HealthChips.jsx';
import AlarmBanner from './AlarmBanner.jsx';
import EventTimeline from './EventTimeline.jsx';
import SoundControls from './SoundControls.jsx';
import OfflineBanner from './OfflineBanner.jsx';
import PanelBoundary from './PanelBoundary.jsx';
import InjectionConsole from './InjectionConsole.jsx';
import EvidencePanel from './EvidencePanel.jsx';
import SourcePicker from './SourcePicker.jsx';
import TrimTrend from './TrimTrend.jsx';
import './dashboard.css';

const STALE_S = 3; // a data stream silent this long gets a panel notice (same limit as the UI watchdog)
// OBD charts shown by default on a live stream (the rest behind "Show all"): speed and load set the
// operating point; the two fuel trims are what the injector-clog rule reads
const OBD_PRIMARY = ['rpm', 'load_pct', 'stft_pct', 'ltft_pct'];

export default function Dashboard({ state, obd, obdWindows, transport, signal, error, scenarioError, links, apiUrl, ages, scenarios, scenarioId, onSelectScenario, onAck, onStartInjection, onStopInjection, onResetEngine, ackError, events, offline, lastStateAt, sound, onToggleSound, audioKind, mock }) {
  const color = decisionColor(state?.decision);
  // REAL log replay (contract 1.6): no crank or audio, so the OBD panels lead and the cylinder,
  // fingerprint and signal panels are hidden
  const replay = state?.mode === 'replay' && state?.replay != null;
  const run = state?.run_id; // a new run clears any panel error

  // Panel data notices: a stream that delivered at least once this run and has now been silent
  // for STALE_S. Only for sensors this recording has, and not while the whole dashboard is
  // already marked offline. This is a UI arrival check, separate from the backend's sensor health.
  const sensors = state?.health.sensors ?? {};
  const silentFor = (stream, present) => (!offline && present && ages[stream] >= STALE_S ? ages[stream] : null);
  const obdSilent = silentFor('obd', 'obd' in sensors);
  const signalSilent = {
    mic: silentFor('mic', 'mic' in sensors),
    stft: silentFor('stft', 'mic' in sensors),
    accel: silentFor('accel', 'accel' in sensors),
  };

  const injection = state && (
    <PanelBoundary name="Injection console" className="inject" resetKey={run}>
      <InjectionConsole key={state.run_id} state={state} offline={offline} mock={mock} onStart={onStartInjection} onStop={onStopInjection} onReset={onResetEngine} />
    </PanelBoundary>
  );

  return (
    <section className={`dash${offline ? ' offline' : ''}`} style={{ '--accent': color }} aria-label="Diagnostics">
      {/* header: what is streaming and how healthy the asset and its sensors are; the scenario list
          and stream metadata sit behind disclosures so the live sequence starts near the top */}
      <header className="dash-head">
        <div className="dash-title">
          <div className="dash-dot"><i /></div>
          <h1>720</h1>
          {state && (
            <PanelBoundary name="Source badge" variant="inline" resetKey={run}>
              <SourceBadge source={state.source} label={state.source_label} pool={state.audioset_pool} size="large" />
            </PanelBoundary>
          )}
          {state && (
            <PanelBoundary name="Health" variant="inline" resetKey={run}>
              <HealthChips health={state.health} />
            </PanelBoundary>
          )}
          {state && (
            <PanelBoundary name="Sound" variant="inline" resetKey={run}>
              <SoundControls sound={sound} onToggle={onToggleSound} audioKind={audioKind} source={state.source} />
            </PanelBoundary>
          )}
        </div>
        {state?.time_lapse && (
          <PanelBoundary name="Time-lapse" variant="inline">
            <TimeLapse state={state} transport={transport} offline={offline} />
          </PanelBoundary>
        )}
        {transport?.listReplayFiles && (
          <PanelBoundary name="Source picker" variant="inline">
            <SourcePicker state={state} transport={transport} onSelectSim={onSelectScenario} />
          </PanelBoundary>
        )}
        {(scenarios.length > 0 || state) && (
          <div className="dash-more">
            {scenarios.length > 0 && (
              // the mock has no other source control, so its scenarios start open
              <details className="dash-disclosure" open={mock || undefined}>
                <summary>{mock ? 'Demo scenarios' : 'Demo scenarios · backend L0, fake'} ({scenarios.length})</summary>
                <div className="dash-runs">
                  {scenarios.map((s) => {
                    const on = s.id === scenarioId;
                    return (
                      <button
                        key={s.id}
                        type="button"
                        className={`run-pill${on ? ' on' : ''}`}
                        aria-pressed={on}
                        title={s.description}
                        onClick={() => onSelectScenario(s.id)}
                      >
                        {s.name}
                      </button>
                    );
                  })}
                </div>
              </details>
            )}
            {state && (
              <details className="dash-disclosure">
                <summary>Technical details</summary>
                <div className="dash-meta mono">
                  {state.run_id} · {state.mode.toUpperCase()}
                  {state.localisation === 'cylinder' ? ` · 4-cyl, firing order ${FIRING_ORDER.join('-')}` : ' · engine geometry unknown'}
                  {' · '}latency {state.latency_ms} ms · schema {state.schema_version}
                </div>
              </details>
            )}
          </div>
        )}
        {scenarioError && <p className="scenario-error" role="alert">{scenarioError}</p>}
      </header>

      {offline && <OfflineBanner lastStateAt={lastStateAt} everConnected={state != null} apiUrl={apiUrl} links={links} />}
      {state && (
        <PanelBoundary name="Alarm banner" variant="banner" resetKey={run}>
          <AlarmBanner key={state.run_id} state={state} onAck={onAck} ackError={ackError} offline={offline} />
        </PanelBoundary>
      )}
      {injection}
      {/* no resetKey: the session's events outlive a run change */}
      {state && (
        <PanelBoundary name="Event timeline" variant="banner">
          <EventTimeline events={events} offline={offline} />
        </PanelBoundary>
      )}

      {error && <div className="dash-error" role="alert">{error}</div>}
      {!state && !error && !offline && <div className="dash-loading">Waiting for the first state message…</div>}

      {/* REAL log replay: the OBD recording is the evidence, so every channel shows by default */}
      {state && replay && (
        <>
          {state.replay.note && <p className="replay-note mono" role="note">{state.replay.note}</p>}
          <div className="dash-grid">
            <PanelBoundary name="OBD" className="obd obd-wide" resetKey={run}>
              <ObdPanel obd={obd} source={state.source} hasObd={'obd' in sensors} silentFor={obdSilent} windowS={300} />
            </PanelBoundary>
          </div>
          <div className="dash-grid">
            <PanelBoundary name="Trim trend" className="trend-panel" resetKey={run}>
              <TrimTrend windows={obdWindows} state={state} />
            </PanelBoundary>
            <PanelBoundary name="Decision" className="verdict" resetKey={run}>
              <Verdict state={state} />
            </PanelBoundary>
          </div>
          <div className="dash-grid">
            <PanelBoundary name="State" className="state-panel" resetKey={run}>
              <StatePanel state={state} />
            </PanelBoundary>
          </div>
          <p className="panel-note">No crank signal in this data: the cylinder bank, 720° fingerprint and audio panels are hidden.</p>
        </>
      )}

      {state && !replay && (
        <>
          <div className="dash-grid">
            <PanelBoundary name="Cylinder bank" className="bank" resetKey={run}>
              <CylinderBank state={state} offline={offline} />
            </PanelBoundary>
            <PanelBoundary name="720° crank signature" className="sig" resetKey={run}>
              <CrankSignature state={state} />
            </PanelBoundary>
            <PanelBoundary name="Decision" className="verdict" resetKey={run}>
              <Verdict state={state} />
            </PanelBoundary>
          </div>
          {/* top-aligned: the State panel keeps its own height instead of stretching to the OBD column */}
          <div className="dash-grid dash-grid-top">
            <PanelBoundary name="OBD" className="obd obd-main" resetKey={run}>
              <ObdPanel obd={obd} source={state.source} hasObd={'obd' in sensors} silentFor={obdSilent} primary={OBD_PRIMARY} />
            </PanelBoundary>
            <PanelBoundary name="State" className="state-panel" resetKey={run}>
              <StatePanel state={state} />
            </PanelBoundary>
          </div>
          <PanelBoundary name="Signal" className="strip" resetKey={run}>
            <SignalStrip key={state.run_id} signal={signal} source={state.source} sensors={sensors} silentFor={signalSilent} />
          </PanelBoundary>
        </>
      )}

      {/* evidence is read once from eval.json; it doesn't depend on the live stream */}
      <PanelBoundary name="Evidence" className="evidence">
        <EvidencePanel />
      </PanelBoundary>

      {mock && <p className="dash-foot">Mock data · values are made up · set VITE_API_URL to connect the backend</p>}
    </section>
  );
}
