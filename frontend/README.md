# frontend/

**Owner:** Livana

React dashboard for 720 in ISA-101 style: grey by default, colour only where attention is needed (plan section 12). The UI never computes a diagnosis and never clears an alarm locally; it shows what the rule layer sends.

Runs on a built-in mock stream (schema 1.3) out of the box, so UI work doesn't wait for the backend. All mock values are made up and the dashboard says so.

## Run it

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm run build      # static build in dist/
```

Node 18+ required. React 18 + Vite 5, plain CSS, no UI library.

## Data source

- `VITE_API_URL` empty (default): **mock transport** emits `state` ~10 Hz, `obd` ~1 Hz and `signal` frames, and answers `ack` round trips like the rule layer would. Scenario pills switch between REAL healthy (P3), SIMULATED healthy, misfire, knock, injector clog, valve tick (UNCLASSIFIED) and an ADVISORY case.
- `VITE_API_URL` set (e.g. `http://localhost:8000`): the **WebSocket client** (`api/wsTransport.js`) opens `/ws/state`, `/ws/obd` and `/ws/signal`, reconnects with backoff, sends `ack` on `/ws/state` and shows `ack_error` replies in the banner. Binary frames are decoded per `contract/signal_frame.md` (`api/signalFrame.js`) into the same shapes the mock emits. The pills switch the backend's L0 fake scenarios (`GET /scenarios`, `POST /scenario/{name}`).
- **UI watchdog:** no `state` for 3 s → "DIAGNOSTICS OFFLINE" banner, dashboard greyed and frozen, ACK disabled. It never changes the decision.

```bash
python -m uvicorn backend.main:app --port 8000                     # repo root
VITE_API_URL=http://localhost:8000 npm run dev                     # frontend/ (PowerShell: $env:VITE_API_URL="http://localhost:8000"; npm run dev)
```

The 16 kHz `mic_audio` channel ("Listen") exists only in the mock; the backend doesn't send it yet.

## Layout

```
src/
  App.jsx                 opens the transport, holds latest state / OBD history / signal frames, sound
  api/transport.js        subscribe(channel) / sendAck / setScenario; picks mock or WebSocket
  api/wsTransport.js      the three backend WebSockets behind the same interface
  api/signalFrame.js      binary signal frame decoder (contract/signal_frame.md)
  data/mockEngine.js      schema-1.3 state, obd and signal messages for the demo scenarios
  data/mockEval.js        PLACEHOLDER eval.json (all values null) for the evidence panel (task 8)
  lib/faults.js           fault, decision-state, NE 107 and source colours/labels (single source)
  lib/geometry.js         firing order, crank helpers, the animation slow factor, polar paths
  lib/PolarAxes.jsx       crank-angle axes shared by the fingerprint and the intro HUD
  lib/audio.js            Web Audio: engine "Listen" player and the ISA-18.2 alarm tone
  intro/                  scroll-scrubbed intro (120 WebP frames) ending in the fingerprint HUD
  dashboard/
    Dashboard.jsx         header (source badge, NE 107 chips, sound), scenario pills, layout
    AlarmBanner.jsx       NORMAL / WATCH / ALARM / INSPECT banner; the only place to ACK
    OfflineBanner.jsx     UI watchdog banner (no state for 3 s)
    CylinderBank.jsx      pistons driven by rpm (slowed, labelled) + per-cylinder anomaly bars
    CrankSignature.jsx    720° polar fingerprint vs baseline (simulator/injected only)
    Verdict.jsx           read-only decision details + model row
    StatePanel.jsx        anomaly summary, physics rules, features, stream metadata
    ObdPanel.jsx          latest OBD values with per-channel source badges
    SignalStrip.jsx       mic + accelerometer traces and STFT, scrolling canvas plots
    SourceBadge.jsx, HealthChips.jsx, SoundControls.jsx
docs/DATA_CONTRACT.md     UI copy of the schema-1.3 contract (contract/ is authoritative)
```

## Replacing the intro video

The intro scrubs through still frames as you scroll (smoother than seeking a `<video>`):

```bash
rm public/intro-frames/*.webp
ffmpeg -i your-clip.mp4 -an -vf "fps=12,unsharp=5:5:0.7:5:5:0.0" -c:v libwebp -quality 86 public/intro-frames/f%03d.webp
```

Then set `frameCount` and `duration` in `src/intro/stages.js` and adjust each stage's `from`/`to` seconds.
The current clip is AI-generated (Gemini). Its watermark was removed during extraction, so the intro shows a visible credit.

## Working with Claude Code

`CLAUDE.md` mirrors the locked plan v1.4 (schema 1.3) and the ordered UI task list. Done so far: tasks 1–4 (schema 1.3 migration, ISA-101 restyle + badges + health chips, alarm banner + ACK, rpm-driven animation + per-cylinder bars), plus sound.
