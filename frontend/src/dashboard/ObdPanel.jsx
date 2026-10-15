// Latest OBD value per channel with its unit and source badge.
// Small-multiples charts at true rate come in task 6.

const SOURCE = { real_carobd: 'REAL · carOBD', simulator: 'SIMULATOR' };

export default function ObdPanel({ obd, source }) {
  const channels = Object.entries(obd);
  const separateRecording = source === 'real' && channels.some(([, ch]) => ch.source === 'real_carobd');

  return (
    <section className="panel obd">
      <h2 className="panel-label">OBD · ~1 Hz</h2>
      {separateRecording && (
        <p className="panel-note">Separate carOBD recording, not the same vehicle as the audio clip.</p>
      )}
      {channels.length === 0 ? (
        <p className="panel-note">Waiting for OBD…</p>
      ) : (
        <div className="obd-grid">
          {channels.map(([name, ch]) => (
            <div key={name} className="obd-cell">
              <div className="eyebrow">{name}</div>
              <div className="tile-val mono">{ch.v[ch.v.length - 1]}<span> {ch.unit}</span></div>
              <span className={`obd-src ${ch.source}`}>{SOURCE[ch.source] ?? ch.source}</span>
              <span className="mono obd-n">{ch.v.length} samples</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
