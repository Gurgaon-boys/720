import { ne107 } from '../lib/faults.js';

// Two separate NE 107 chips: asset health (the engine) and sensor health (the measurement).
// Sensor health shows the worst sensor; every sensor is listed underneath.
const RANK = ['NORMAL', 'MAINTENANCE_REQUIRED', 'OFF_SPEC', 'CHECK_FUNCTION', 'FAILURE'];

function Chip({ label, status, detail }) {
  const s = ne107(status);
  return (
    <div className={`health-chip${s.color ? ' attn' : ''}`} style={s.color ? { '--attn': s.color } : undefined}>
      <span className="health-label">{label}</span>
      <strong>{s.name}</strong>
      {detail && <span className="health-detail mono">{detail}</span>}
    </div>
  );
}

export default function HealthChips({ health }) {
  const sensors = Object.entries(health.sensors);
  const present = sensors.filter(([, v]) => v != null);
  const worst = present.reduce((w, [, v]) => (RANK.indexOf(v) > RANK.indexOf(w) ? v : w), 'NORMAL');
  const detail = sensors.map(([name, v]) => `${name} ${v == null ? 'n/a' : ne107(v).name.toLowerCase()}`).join(' · ');

  return (
    <div className="health" aria-label="Health (NE 107)">
      <Chip label="Asset health · NE 107" status={health.asset_ne107} />
      <Chip label="Sensor health" status={worst} detail={detail} />
    </div>
  );
}
