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
import './dashboard.css';

export default function Dashboard({ state, obd, signal, error, scenarios, scenarioId, onSelectScenario, onAck, ackError, offline, lastStateAt, sound, onToggleSound, audioAvailable, mock }) {
  const color = decisionColor(state?.decision);

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
          {state && <SourceBadge source={state.source} pool={state.audioset_pool} size="large" />}
          {state && <HealthChips health={state.health} />}
          {state && <SoundControls sound={sound} onToggle={onToggleSound} audioAvailable={audioAvailable} source={state.source} />}
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
      </header>

      {offline && <OfflineBanner lastStateAt={lastStateAt} />}
      {state && <AlarmBanner key={state.run_id} state={state} onAck={onAck} ackError={ackError} offline={offline} />}

      {error && <div className="dash-error" role="alert">{error}</div>}
      {!state && !error && !offline && <div className="dash-loading">Waiting for the first state message…</div>}

      {state && (
        <>
          <div className="dash-grid">
            <CylinderBank state={state} offline={offline} />
            <CrankSignature state={state} />
            <Verdict state={state} />
          </div>
          <div className="dash-grid">
            <StatePanel state={state} />
            <ObdPanel obd={obd} source={state.source} hasObd={'obd' in state.health.sensors} />
          </div>
          <SignalStrip key={state.run_id} signal={signal} source={state.source} />
        </>
      )}

      {mock && <p className="dash-foot">Mock data · values are made up · set VITE_API_URL to connect the backend</p>}
    </section>
  );
}
