import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { apiUrl, createTransport, usingMock } from './api/transport.js';
import ScrollIntro from './intro/ScrollIntro.jsx';
import Dashboard from './dashboard/Dashboard.jsx';
import PanelBoundary from './dashboard/PanelBoundary.jsx';
import ViewTabs, { panelId, tabId, useViewTab } from './dashboard/ViewTabs.jsx';
import ResultsView from './dashboard/ResultsView.jsx';
import { audioContext, createAlarmTone, createEnginePlayer } from './lib/audio.js';
import { DEMO_SOURCE, isDemoMode } from './lib/demoMode.js';

const REPLAY_SOURCE = 'carobd_replay';

const DEMO = isDemoMode();

const OPERATOR = 'operator';
const OBD_KEEP = 400; // samples per channel (~400 s at 1 Hz; a REAL replay at 30x fills it faster)
const WINDOWS_KEEP = 120; // OBD branch windows kept for the trim trend (REAL replay)
const OFFLINE_MS = 3000; // UI watchdog: no `state` for 3 s → diagnostics offline (contract rule 4)
const WATCHDOG_TICK_MS = 250;
const LIST_RETRY_MS = 5000; // scenario list: retry interval after a failed load

// 3D engine view: its own chunk (three.js), fetched the first time its tab opens
const Engine3D = lazy(() => import('./dashboard/Engine3D.jsx'));

// obd messages carry new samples; keep a short history per channel
function appendObd(prev, msg) {
  const next = { ...prev };
  Object.entries(msg.channels).forEach(([name, ch]) => {
    const old = prev[name];
    next[name] = {
      unit: ch.unit,
      source: ch.source,
      source_label: ch.source_label, // 1.6: latest message's label for this channel
      t: [...(old?.t ?? []), ...ch.t].slice(-OBD_KEEP),
      v: [...(old?.v ?? []), ...ch.v].slice(-OBD_KEEP),
    };
  });
  return next;
}

// signal frames: latest frame per channel; SignalStrip keeps its own scrolling history
function appendSignal(prev, frame) {
  return { ...prev, [frame.channel]: frame };
}

// whole seconds since each data stream (obd, mic, accel, stft) last delivered; a stream that
// hasn't delivered anything since the run started has no entry
function arrivalAges(arrived, now) {
  return Object.fromEntries(Object.entries(arrived).map(([k, t]) => [k, Math.floor((now - t) / 1000)]));
}
const sameAges = (a, b) => Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((k) => a[k] === b[k]);

export default function App() {
  const transportRef = useRef(null);
  const [scenarios, setScenarios] = useState([]);
  const [scenarioId, setScenarioId] = useState(null);
  const [state, setState] = useState(null);
  const [obd, setObd] = useState({});
  const [obdWindows, setObdWindows] = useState([]); // REAL replay: one point per OBD branch window
  const [signal, setSignal] = useState({});
  const [error, setError] = useState(null);
  const [scenarioError, setScenarioError] = useState(null);
  const [ackError, setAckError] = useState(null);
  const [links, setLinks] = useState(null); // backend only: per-socket connection status
  const arrivedRef = useRef({}); // last arrival (performance.now) per data stream, this run only
  const [ages, setAges] = useState({});
  const [offline, setOffline] = useState(false);
  const [lastStateAt, setLastStateAt] = useState(null);
  const lastStateRef = useRef(performance.now()); // counts from mount, so a dead backend also trips it
  const runIdRef = useRef(null);
  const [sound, setSound] = useState({ engine: false, alarm: false });
  // what "Listen" can play: 'full' = the stream's mic_audio channel; 'display' = no mic_audio, so
  // the 4 kHz mic waveform the plots use (firing rhythm audible, nothing above 2 kHz); null = nothing
  const [audioKind, setAudioKind] = useState(null);
  const audioKindRef = useRef(null);
  const sourceRef = useRef(null);
  const setAudio = (kind) => { audioKindRef.current = kind; setAudioKind(kind); };
  const playerRef = useRef(null);
  const toneRef = useRef(null);
  const dashRef = useRef(null);
  const [view, setView] = useViewTab();
  const [opened3d, setOpened3d] = useState(view === '3d'); // keep it mounted (paused) once opened
  useEffect(() => { if (view === '3d') setOpened3d(true); }, [view]);
  const [openedResults, setOpenedResults] = useState(view === 'results'); // load the files once
  useEffect(() => { if (view === 'results') setOpenedResults(true); }, [view]);
  if (!playerRef.current) playerRef.current = createEnginePlayer();
  if (!toneRef.current) toneRef.current = createAlarmTone();

  useEffect(() => {
    let transport;
    try {
      transport = createTransport();
    } catch (e) {
      setError(e.message);
      return undefined;
    }
    transportRef.current = transport;
    if (transport.mock) {
      setScenarios(transport.listScenarios());
      setScenarioId(transport.scenarioId);
    }
    // backend L0 fake scenarios; if the list can't load, say so (the stream itself may still work)
    // and retry while state is arriving, i.e. once the backend is reachable (at most every 5 s)
    let listRetryAt = null; // null: loaded or loading
    const loadScenarios = () => transport.listScenarios()
      .then((list) => {
        setScenarios(list);
        setScenarioId(transport.scenarioId);
        setScenarioError(null);
        // demo mode: start on the live simulator (once; a backend already on it, or on the REAL replay,
        // is left alone: the replay is picked in the source picker, which demo mode keeps)
        if (DEMO && transport.scenarioId !== DEMO_SOURCE && transport.scenarioId !== REPLAY_SOURCE
            && list.some((x) => x.id === DEMO_SOURCE)) {
          return transport.setScenario(DEMO_SOURCE).then(() => setScenarioId(DEMO_SOURCE));
        }
        return undefined;
      })
      .catch((e) => {
        console.error('[720] scenario list unavailable', e);
        setScenarioError(`Scenario list unavailable: ${e.message}`);
        listRetryAt = performance.now() + LIST_RETRY_MS;
      });
    if (!transport.mock) loadScenarios();
    const offs = [
      transport.subscribe('state', (msg) => {
        lastStateRef.current = performance.now();
        if (listRetryAt != null && performance.now() >= listRetryAt) {
          listRetryAt = null;
          loadScenarios();
        }
        // a new run: drop OBD/signal buffers, including frames from the old run that arrived late
        if (msg.run_id !== runIdRef.current) {
          if (runIdRef.current != null) {
            setObd({});
            setSignal({});
            setObdWindows([]);
            arrivedRef.current = {};
            setAudio(null);
          }
          runIdRef.current = msg.run_id;
        }
        // REAL replay: a new OBD branch window (features_summary.window_t0_s changes) adds a trend point
        const fs = msg.replay ? msg.features_summary : null;
        if (fs && fs.window_t0_s != null && msg.anomaly.score != null) {
          setObdWindows((w) => (w.length && w[w.length - 1].t0 === fs.window_t0_s ? w : [...w, {
            t0: fs.window_t0_s, t: fs.window_t0_s + 30, trim: fs.total_trim_pct, trimMax: fs.trim_rule_max_pct, score: msg.anomaly.score,
          }].slice(-WINDOWS_KEEP)));
        }
        sourceRef.current = msg.source;
        setState(msg);
      }),
      transport.subscribe('ack_error', (msg) => {
        console.warn('[720] ACK rejected', msg);
        setAckError({ ...msg, receivedAt: Date.now() });
      }),
      transport.subscribe('obd', (m) => {
        arrivedRef.current.obd = performance.now();
        setObd((p) => appendObd(p, m));
      }),
      transport.subscribe('signal', (f) => {
        // audio goes straight to the player, not through React state
        if (f.channel === 'mic_audio') {
          if (audioKindRef.current !== 'full') setAudio('full');
          playerRef.current.push(f);
          return;
        }
        // no mic_audio on this stream: play the display mic instead. Never for REAL: until the
        // recording itself is streamed, sound under a REAL badge would not be that recording
        if (f.channel === 'mic' && audioKindRef.current !== 'full' && sourceRef.current && sourceRef.current !== 'real') {
          if (audioKindRef.current !== 'display') setAudio('display');
          playerRef.current.push(f);
        }
        arrivedRef.current[f.channel] = performance.now();
        setSignal((p) => appendSignal(p, f));
      }),
      transport.subscribe('status', setLinks),
    ];
    return () => {
      offs.forEach((off) => off());
      transport.close();
      transportRef.current = null;
    };
  }, []);

  // UI watchdog. Sensor-health layer: it never touches the decision, it only says the decision is stale.
  useEffect(() => {
    const id = setInterval(() => {
      const now = performance.now();
      const stale = now - lastStateRef.current > OFFLINE_MS;
      setOffline(stale);
      setLastStateAt(lastStateRef.current);
      const next = arrivalAges(arrivedRef.current, now);
      setAges((prev) => (sameAges(prev, next) ? prev : next));
    }, WATCHDOG_TICK_MS);
    return () => clearInterval(id);
  }, []);

  const selectScenario = useCallback((id) => {
    // clear buffers so the new run doesn't mix with the old one
    setObd({});
    setSignal({});
    setObdWindows([]);
    setAudio(null);
    setAckError(null);
    arrivedRef.current = {};
    // .then() so a synchronous throw (mock) lands in .catch like an async one (backend)
    Promise.resolve()
      .then(() => transportRef.current?.setScenario(id))
      .then(() => { setScenarioId(id); setScenarioError(null); })
      .catch((e) => {
        console.error('[720] scenario switch failed', e);
        setScenarioError(`Scenario switch to "${id}" failed: ${e.message}`);
      });
  }, []);

  const sendAck = useCallback((alarmId) => {
    transportRef.current?.sendAck(alarmId, OPERATOR);
  }, []);

  // injection console: the promise only says whether the request was accepted; what is injected
  // is read from state.injection
  const startInjection = useCallback((req) => (
    transportRef.current ? transportRef.current.startInjection(req) : Promise.reject(new Error('no transport'))
  ), []);
  const stopInjection = useCallback(() => (
    transportRef.current ? transportRef.current.stopInjection() : Promise.reject(new Error('no transport'))
  ), []);

  // engine audio: stop when switched off or when the stream has nothing to play
  const listening = sound.engine && audioKind != null;
  useEffect(() => {
    playerRef.current.setEnabled(listening);
  }, [listening]);

  // alarm tone: only while the broadcast says an alarm is unacknowledged
  const d = state?.decision;
  const alarmUnacked = sound.alarm && d?.state === 'ALARM' && d.alarm_id != null && !d.acked;
  useEffect(() => {
    toneRef.current.set(alarmUnacked);
  }, [alarmUnacked]);
  useEffect(() => () => {
    playerRef.current.setEnabled(false);
    toneRef.current.set(false);
  }, []);

  // called from a click, so the browser lets the AudioContext start
  const toggleSound = useCallback((name) => {
    audioContext();
    setSound((s) => ({ ...s, [name]: !s[name] }));
  }, []);

  const goToDashboard = () => dashRef.current?.scrollIntoView({ behavior: 'smooth' });

  return (
    <>
      {/* an intro crash must never block the dashboard below it */}
      <PanelBoundary name="Intro">
        <ScrollIntro state={state} onSkip={goToDashboard} />
      </PanelBoundary>
      <div ref={dashRef}>
        <ViewTabs view={view} onChange={setView} />
        {/* the 2D dashboard stays mounted while the 3D tab is open, so its buffers keep filling */}
        <div role="tabpanel" id={panelId('2d')} aria-labelledby={tabId('2d')} hidden={view !== '2d'}>
        {/* last resort: anything outside the per-panel boundaries (e.g. the header text) */}
        <PanelBoundary name="Dashboard" resetKey={state?.run_id}>
          <Dashboard
            state={state}
            obd={obd}
            obdWindows={obdWindows}
            transport={usingMock ? null : transportRef.current}
            signal={signal}
            error={error}
            scenarioError={scenarioError}
            links={links}
            apiUrl={apiUrl}
            ages={ages}
            scenarios={DEMO ? [] : scenarios /* demo mode: no fake-scenario row */}
            scenarioId={scenarioId}
            onSelectScenario={selectScenario}
            onAck={sendAck}
            onStartInjection={startInjection}
            onStopInjection={stopInjection}
            ackError={ackError}
            offline={offline}
            lastStateAt={lastStateAt}
            sound={sound}
            onToggleSound={toggleSound}
            audioKind={audioKind}
            mock={usingMock}
          />
        </PanelBoundary>
        </div>
        {opened3d && (
          <div role="tabpanel" id={panelId('3d')} aria-labelledby={tabId('3d')} hidden={view !== '3d'} className="view-3d">
            {/* its own boundary: a WebGL or chunk-load failure stays in this tab */}
            <PanelBoundary name="3D engine view">
              <Suspense fallback={<div className="view-loading">Loading the 3D view…</div>}>
                <Engine3D state={state} offline={offline} active={view === '3d'} />
              </Suspense>
            </PanelBoundary>
          </div>
        )}
        {openedResults && (
          <div role="tabpanel" id={panelId('results')} aria-labelledby={tabId('results')} hidden={view !== 'results'}>
            <PanelBoundary name="Results">
              <ResultsView />
            </PanelBoundary>
          </div>
        )}
      </div>
    </>
  );
}
