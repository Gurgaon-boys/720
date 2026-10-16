import { useCallback } from 'react';
import { usePoll } from './useBackend.js';

// INDUSTRIAL LINK (D141): the OPC UA server as it actually runs, from GET /industrial. No client count:
// the backend does not expose OPC UA sessions, so none is shown.

const ago = (t, now) => (t == null ? 'never' : `${Math.max(0, now - t).toFixed(1)} s ago`);
const NE = ['NORMAL', 'FAILURE', 'CHECK_FUNCTION', 'OFF_SPEC', 'MAINTENANCE_REQUIRED'];

export function useIndustrial(api, active) {
  const fn = useCallback(() => api.industrial(), [api]);
  return usePoll(api ? fn : null, active, 1500);
}

export default function IndustrialLink({ data, error }) {
  const now = data?.now ?? 0;
  const fresh = data?.last_publish != null && now - data.last_publish < 3;
  const health = data?.sensor_health ?? {};
  return (
    <section className="lab-card link" aria-label="Industrial link">
      <div className="lab-card-head">
        <h3>Industrial link · OPC UA</h3>
        <span className={`lab-tag mono${data?.running ? '' : ' warn'}`}>{data ? (data.running ? 'SERVER UP' : 'SERVER DOWN') : '…'}</span>
      </div>
      {error && <p className="lab-note err">{error}</p>}
      <dl className="link-facts mono">
        <div><dt>Endpoint</dt><dd>{data?.endpoint ?? 'unavailable'}</dd></div>
        <div><dt>Namespace</dt><dd>{data?.namespace ?? '—'} · security {data?.security ?? '—'}</dd></div>
        <div><dt>Publishing</dt><dd className={fresh ? '' : 'warn'}>{data ? (fresh ? `live · last write ${ago(data.last_publish, now)}` : `stale · last write ${ago(data.last_publish, now)}`) : '—'}</dd></div>
        <div><dt>Last command</dt><dd>{data?.last_command ? `${data.last_command.text} (${data.last_command.phase}, ${ago(data.last_command.t, now)})` : 'none from OPC UA yet'}</dd></div>
        <div><dt>Last event</dt><dd>{data?.last_event ? `${data.last_event.message} · sev ${data.last_event.severity} · ${ago(data.last_event.t, now)}` : 'none yet'}</dd></div>
        <div><dt>Events emitted</dt><dd>{data?.events_emitted ?? 0}</dd></div>
      </dl>
      <div className="link-health">
        {Object.entries(health).map(([k, v]) => (
          <span key={k} className={`link-chip${v === 'FAILURE' ? ' bad' : ''}`}>
            <code>SensorHealth.{k}</code> = {NE.indexOf(v)} <small>{v}</small>
          </span>
        ))}
      </div>
      <p className="lab-note faint">UaExpert: call <code>EngineAsset.DisableSensor("accel")</code> and this whole page follows the same backend state.</p>
    </section>
  );
}
