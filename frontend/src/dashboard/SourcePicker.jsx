import { useEffect, useRef, useState } from 'react';

// Source picker (backend only, contract 1.6): the live simulator, a REAL carOBD held-out log replayed
// at 1× / 10× / 30×, or an uploaded carOBD-format log (REAL, unverified). The badge in the header says
// which one is streaming; this control only asks the backend to switch.

const TYPE_ORDER = ['drive', 'idle', 'live', 'campus', 'long', 'upload'];
const TYPE_NAME = { drive: 'Drive (high-speed roads)', idle: 'Idle (parked, engine on)', live: 'Live (fixed commute)', campus: 'Campus (low speed)', long: 'Long trips', upload: 'Uploaded (unverified)' };
const DEFAULT_FILE = 'heldout:drive2.csv';
const SIM = 'sim';

const fmtMin = (s) => `${Math.round(s / 60)} min`;

export default function SourcePicker({ state, transport, onSelectSim }) {
  const [files, setFiles] = useState([]);
  const [speeds, setSpeeds] = useState([1, 10, 30]);
  const [file, setFile] = useState(DEFAULT_FILE);
  const [speed, setSpeed] = useState(10);
  const [busy, setBusy] = useState(null); // what we're waiting for, in words
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  const replay = state?.replay ?? null;
  const mode = replay ? (replay.uploaded ? 'upload' : 'replay') : (state?.source === 'simulated' ? 'sim' : null);

  const loadFiles = () => transport.listReplayFiles()
    .then((r) => {
      setFiles(r.files);
      setSpeeds(r.speeds);
      if (r.current) {
        setFile(r.current.file);
        setSpeed(r.current.speed);
      }
    })
    .catch((e) => setError(`Replay file list unavailable: ${e.message}`));
  useEffect(() => { loadFiles(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // the stream's own speed wins (another tab may have changed it)
  useEffect(() => { if (replay) setSpeed(replay.speed); }, [replay?.speed]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = (what, p) => {
    setBusy(what);
    setError(null);
    return p.catch((e) => setError(e.message)).finally(() => setBusy(null));
  };
  const play = (f, s = speed) => run('Starting the replay…', transport.startReplay(f, s).then(() => setFile(f)));
  const changeSpeed = (s) => {
    setSpeed(s);
    if (replay) run('Changing speed…', transport.setReplaySpeed(s));
  };
  const upload = (ev) => {
    const f = ev.target.files?.[0];
    ev.target.value = '';
    if (!f) return;
    run(`Uploading ${f.name}…`, transport.uploadObd(f).then((r) => {
      loadFiles();
      return transport.startReplay(r.file, speed).then(() => setFile(r.file));
    }));
  };

  const groups = TYPE_ORDER.map((t) => [t, files.filter((f) => f.type === t)]).filter(([, fs]) => fs.length);

  return (
    <div className="src-picker" role="group" aria-label="Data source">
      <div className="dash-runs">
        <span className="dash-runs-label">Source</span>
        <button type="button" className={`run-pill${mode === 'sim' ? ' on' : ''}`} aria-pressed={mode === 'sim'} disabled={busy != null}
          onClick={() => onSelectSim(SIM)} title="Physics-informed engine simulator, live">Simulator</button>
        <button type="button" className={`run-pill${mode === 'replay' ? ' on' : ''}`} aria-pressed={mode === 'replay'} disabled={busy != null}
          onClick={() => play(file.startsWith('heldout:') ? file : DEFAULT_FILE)} title="A held-out carOBD log from a real car (2014 Toyota Etios), replayed">Real car replay</button>
        <button type="button" className={`run-pill${mode === 'upload' ? ' on' : ''}`} aria-pressed={mode === 'upload'} disabled={busy != null}
          onClick={() => inputRef.current?.click()} title="Upload a carOBD-format CSV (max 5 MB); it replays as REAL, unverified">Upload log</button>
        <input ref={inputRef} type="file" accept=".csv,text/csv" hidden onChange={upload} aria-label="carOBD CSV to upload" />
      </div>
      {(mode === 'replay' || mode === 'upload') && (
        <div className="dash-runs">
          <label className="dash-runs-label" htmlFor="replay-file">File</label>
          <select id="replay-file" className="src-select mono" value={file} disabled={busy != null} onChange={(e) => play(e.target.value)}>
            {groups.map(([t, fs]) => (
              <optgroup key={t} label={TYPE_NAME[t]}>
                {fs.map((f) => <option key={f.id} value={f.id}>{f.name} · {fmtMin(f.duration_s)}</option>)}
              </optgroup>
            ))}
          </select>
          <span className="dash-runs-label">Speed</span>
          {speeds.map((s) => (
            <button key={s} type="button" className={`run-pill${speed === s ? ' on' : ''}`} aria-pressed={speed === s} disabled={busy != null}
              onClick={() => changeSpeed(s)}>{s}×</button>
          ))}
          {replay && (
            <span className="mono src-pos">
              {fmtMin(replay.position_s)} / {fmtMin(replay.duration_s)}{replay.loop > 0 ? ` · pass ${replay.loop + 1}` : ''}
            </span>
          )}
        </div>
      )}
      {busy && <p className="panel-note" role="status">{busy}</p>}
      {error && <p className="scenario-error" role="alert">{error}</p>}
    </div>
  );
}
