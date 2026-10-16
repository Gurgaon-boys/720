import { decisionColor } from '../lib/faults.js';
import { FIRING_ORDER } from '../lib/geometry.js';
import CylinderBank from './CylinderBank.jsx';
import CrankSignature from './CrankSignature.jsx';
import Verdict from './Verdict.jsx';
import StatePanel from './StatePanel.jsx';
import ObdPanel from './ObdPanel.jsx';
import SignalStrip from './SignalStrip.jsx';
import SourceBadge from './SourceBadge.jsx';
import HealthChips from './HealthChips.jsx';
import AlarmBanner from './AlarmBanner.jsx';
import SoundControls from './SoundControls.jsx';
import OfflineBanner from './OfflineBanner.jsx';
import PanelBoundary from './PanelBoundary.jsx';
import InjectionConsole from './InjectionConsole.jsx';
import EvidencePanel from './EvidencePanel.jsx';
import SourcePicker from './SourcePicker.jsx';
import TrimTrend from './TrimTrend.jsx';
import './dashboard.css';

const STALE_S = 3; // a data stream silent this long gets a panel notice (same limit as the UI watchdog)

export default function Dashboard({ state, obd, obdWindows, transport, signal, error, scenarioError, links, apiUrl, ages, scenarios, scenarioId, onSelectScenario, onAck, onStartInjection, onStopInjection, ackError, offline, lastStateAt, sound, onToggleSound, audioAvailable, mock }) {
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

  return (
    <section className={`dash${offline ? ' offline' : ''}`} style={{ '--accent': color }} aria-label="Diagnostics">
      <header className="dash-head">
        <div className="dash-title">
          <div className="dash-dot"><i /></div>
          <div>
            <h1>720</h1>
            {state && (
              <div className="dash-meta mono">
                {state.run_id} · {state.mode.toUpperCase()}
                {state.localisation === 'cylinder' ? ` · 4-cyl, firing order ${FIRING_ORDER.join('-')}` : ' · engine geometry unknown'}
                {' · '}latency {state.latency_ms} ms
              </div>
            )}
          </div>
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
              <SoundControls sound={sound} onToggle={onToggleSound} audioAvailable={audioAvailable} source={state.source} />
            </PanelBoundary>
          )}
        </div>
        {scenarios.length > 0 && (
          <div className="dash-runs">
            <span className="dash-runs-label">{mock ? 'Demo scenario' : 'Backend scenario (L0, fake)'}</span>
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
        )}
        {transport?.listReplayFiles && (
          <PanelBoundary name="Source picker" variant="inline">
            <SourcePicker state={state} transport={transport} onSelectSim={onSelectScenario} />
          </PanelBoundary>
        )}
        {scenarioError && <p className="scenario-error" role="alert">{scenarioError}</p>}
      </header>

      {offline && <OfflineBanner lastStateAt={lastStateAt} everConnected={state != null} apiUrl={apiUrl} links={links} />}
      {state && (
        <PanelBoundary name="Alarm banner" variant="banner" resetKey={run}>
          <AlarmBanner key={state.run_id} state={state} onAck={onAck} ackError={ackError} offline={offline} />
        </PanelBoundary>
      )}

      {error && <div className="dash-error" role="alert">{error}</div>}
      {!state && !error && !offline && <div className="dash-loading">Waiting for the first state message…</div>}

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
          <PanelBoundary name="Injection console" className="inject" resetKey={run}>
            <InjectionConsole key={state.run_id} state={state} offline={offline} mock={mock} onStart={onStartInjection} onStop={onStopInjection} />
          </PanelBoundary>
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
          <div className="dash-grid">
            <PanelBoundary name="State" className="state-panel" resetKey={run}>
              <StatePanel state={state} />
            </PanelBoundary>
            <PanelBoundary name="OBD" className="obd" resetKey={run}>
              <ObdPanel obd={obd} source={state.source} hasObd={'obd' in sensors} silentFor={obdSilent} />
            </PanelBoundary>
          </div>
          <PanelBoundary name="Injection console" className="inject" resetKey={run}>
            <InjectionConsole key={state.run_id} state={state} offline={offline} mock={mock} onStart={onStartInjection} onStop={onStopInjection} />
          </PanelBoundary>
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
