import { useCallback, useEffect, useRef, useState } from 'react';
import { createTransport, usingMock } from './api/transport.js';
import ScrollIntro from './intro/ScrollIntro.jsx';
import Dashboard from './dashboard/Dashboard.jsx';
import PanelBoundary from './dashboard/PanelBoundary.jsx';
import { audioContext, createAlarmTone, createEnginePlayer } from './lib/audio.js';

const OPERATOR = 'operator';
const OBD_KEEP = 60; // samples per channel (~60 s at 1 Hz)
const OFFLINE_MS = 3000; // UI watchdog: no `state` for 3 s → diagnostics offline (contract rule 4)
const WATCHDOG_TICK_MS = 250;

// obd messages carry new samples; keep a short history per channel
function appendObd(prev, msg) {
  const next = { ...prev };
  Object.entries(msg.channels).forEach(([name, ch]) => {
    const old = prev[name];
    next[name] = {
      unit: ch.unit,
      source: ch.source,
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

export default function App() {
  const transportRef = useRef(null);
  const [scenarios, setScenarios] = useState([]);
  const [scenarioId, setScenarioId] = useState(null);
  const [state, setState] = useState(null);
  const [obd, setObd] = useState({});
  const [signal, setSignal] = useState({});
  const [error, setError] = useState(null);
  const [ackError, setAckError] = useState(null);
  const [offline, setOffline] = useState(false);
  const [lastStateAt, setLastStateAt] = useState(null);
  const lastStateRef = useRef(performance.now()); // counts from mount, so a dead backend also trips it
  const runIdRef = useRef(null);
  const [sound, setSound] = useState({ engine: false, alarm: false });
  const [audioAvailable, setAudioAvailable] = useState(false);
  const playerRef = useRef(null);
  const toneRef = useRef(null);
  const dashRef = useRef(null);
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
    } else {
      // backend L0 fake scenarios; harmless if the backend doesn't offer them
      transport.listScenarios()
        .then((list) => { setScenarios(list); setScenarioId(transport.scenarioId); })
        .catch(() => {});
    }
    const offs = [
      transport.subscribe('state', (msg) => {
        lastStateRef.current = performance.now();
        // a new run: drop OBD/signal buffers, including frames from the old run that arrived late
        if (msg.run_id !== runIdRef.current) {
          if (runIdRef.current != null) {
            setObd({});
            setSignal({});
          }
          runIdRef.current = msg.run_id;
        }
        setState(msg);
      }),
      transport.subscribe('ack_error', (msg) => {
        console.warn('[720] ACK rejected', msg);
        setAckError({ ...msg, receivedAt: Date.now() });
      }),
      transport.subscribe('obd', (m) => setObd((p) => appendObd(p, m))),
      transport.subscribe('signal', (f) => {
        // audio goes straight to the player, not through React state
        if (f.channel === 'mic_audio') {
          setAudioAvailable(true);
          playerRef.current.push(f);
          return;
        }
        setSignal((p) => appendSignal(p, f));
      }),
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
      const stale = performance.now() - lastStateRef.current > OFFLINE_MS;
      setOffline(stale);
      setLastStateAt(lastStateRef.current);
    }, WATCHDOG_TICK_MS);
    return () => clearInterval(id);
  }, []);

  const selectScenario = useCallback((id) => {
    // clear buffers so the new run doesn't mix with the old one
    setObd({});
    setSignal({});
    setAudioAvailable(false);
    setAckError(null);
    setScenarioId(id);
    Promise.resolve(transportRef.current?.setScenario(id)).catch((e) => setError(e.message));
  }, []);

  const sendAck = useCallback((alarmId) => {
    transportRef.current?.sendAck(alarmId, OPERATOR);
  }, []);

  // engine audio: stop when switched off or when the stream has no audio channel
  const listening = sound.engine && audioAvailable;
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
        {/* last resort: anything outside the per-panel boundaries (e.g. the header text) */}
        <PanelBoundary name="Dashboard" resetKey={state?.run_id}>
          <Dashboard
            state={state}
            obd={obd}
            signal={signal}
            error={error}
            scenarios={scenarios}
            scenarioId={scenarioId}
            onSelectScenario={selectScenario}
            onAck={sendAck}
            ackError={ackError}
            offline={offline}
            lastStateAt={lastStateAt}
            sound={sound}
            onToggleSound={toggleSound}
            audioAvailable={audioAvailable}
            mock={usingMock}
          />
        </PanelBoundary>
      </div>
    </>
  );
}
