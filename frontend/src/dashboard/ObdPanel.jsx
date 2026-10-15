// Latest OBD value per channel with its unit and source badge.
// Small-multiples charts at true rate come in task 6.

import { fmtNum } from '../lib/format.js';

const SOURCE = { real_carobd: 'REAL · carOBD', simulator: 'SIMULATOR' };

// hasObd: the state's health.sensors lists an OBD sensor; if not, this recording has no OBD at all
export default function ObdPanel({ obd, source, hasObd }) {
  const channels = Object.entries(obd);
  const separateRecording = source === 'real' && channels.some(([, ch]) => ch.source === 'real_carobd');

  return (
    <section className="panel obd">
      <h2 className="panel-label">OBD · ~1 Hz</h2>
      {separateRecording && (
        <p className="panel-note">Separate carOBD recording, not the same vehicle as the audio clip.</p>
      )}
      {channels.length === 0 ? (
        <p className="panel-note">{hasObd ? 'Waiting for OBD…' : 'No OBD on this recording.'}</p>
      ) : (
        <div className="obd-grid">
          {channels.map(([name, ch]) => (
            <div key={name} className="obd-cell">
              <div className="eyebrow">{name}</div>
              <div className="tile-val mono">{fmtNum(ch.v[ch.v.length - 1])}<span> {ch.unit}</span></div>
              <span className={`obd-src ${ch.source}`}>{SOURCE[ch.source] ?? ch.source}</span>
              <span className="mono obd-n">{ch.v.length} samples</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
