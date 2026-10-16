# CLAUDE.md — 720 frontend (HackHive 2026, Honeywell PS2)

You are working on the **frontend** of a 48-hour hackathon project called **720**. The frontend is owned by **Livana**. This file mirrors the team's locked **720 Final Plan v1.4** (contract schema 1.3). Read it fully before changing anything. If something here conflicts with what Livana tells you, ask her.

## 1. Project in one paragraph

720 is a condition monitor for combustion engines that **names the fault, says when it doesn't know, and leaves the alarm decision to a deterministic rule layer and a human**. It folds every sensor onto the engine's 720° four-stroke cycle. It is trained and stress-tested on a physics-informed engine simulator and judged on real recordings with honest splits, false alarms per hour, and a properly tested "unknown" output. Judges are Honeywell **industrial automation** engineers: they reward trust, evidence and operator-friendly design, and distrust spectacle and 99% accuracy claims. The car engine is pitched as a test bed for gas-engine compressors, gensets and engine fleets.

**Thesis:** real recordings are the evidence · the simulator is the test bench · the deterministic layer decides.

## 2. Team and interfaces

| Person | Owns | UI gets from them |
|---|---|---|
| Dipen | Simulator, signal processing, physics rules, model design | Simulated/injected streams via the backend |
| Diljit | Data (AudioSet pools, carOBD), ML evaluation | `eval.json` + **draft charts** (Livana restyles) |
| Nishant | FastAPI backend, rule layer, OPC UA, deck narrative text | `state` / `obd` / `signal` WebSocket channels; receives `ack` |
| **Livana** | **This React app, the UI side of the contract (WebSocket client, UI watchdog, ACK sending), deck visuals, backup demo recording** | — |

**The UI never computes diagnoses, and never clears an alarm locally.** The rule layer is the single source of truth for alarm and ACK state.

## 3. Data rules that affect the UI

- **No real car, no new recordings.** Real data = AudioSet clips (vetted passenger-car idle, held-out pool **P3**) and carOBD (real ECU, ~1 Hz). Faults shown in the demo are **simulated**.
- Every view shows its source badge: **REAL / SIMULATED / INJECTED** (from `state.source`). The REAL → SIMULATED switch in the demo must be large and obvious.
- **On real AudioSet clips: no 720° fingerprint and no localisation at all** (the firing-slot idea is retired). `signature` arrives empty and `localisation` is `none`. Show the spectral view instead with a short note ("engine geometry unknown on real clips").
- Simulator and injected data: cylinder numbers (`localisation: "cylinder"`). Injected (column B) covers knock and valve tick only.
- **carOBD is the headline real evidence** (held-out files, real ECU, ~1 Hz, includes real RPM/load transitions).
- OBD: show at true rates; badge per channel from `obd.channels.*.source` (`real_carobd` or `simulator`). Simulator-internal fast values are badged as such.
- Real carOBD has **no O2 voltage** (O2 ripple is simulator-only).

## 4. Current state of the code

React 18 + Vite 5, plain CSS, no UI library. `npm install && npm run dev` → http://localhost:5173; `npm run build` → `dist/` (relative paths).

```
src/
  App.jsx                 loads runs + analysis; renders <ScrollIntro/> then <Dashboard/>
  api/client.js           listRuns() / getAnalysis(); mock when VITE_API_URL is empty (REST, v1 shape)
  data/mockEngine.js      mock AnalysisResult (v1 shape) for healthy / misfire / knock / valve
  lib/faults.js           fault names + colours (single source of truth)
  lib/geometry.js         polar fingerprint path, looped waveform path, polar label points
  styles/global.css       dark tokens, keyframes, reduced-motion rule
  intro/                  scroll-scrubbed AI video (120 frames) ending in the fingerprint HUD
  dashboard/
    Dashboard.jsx         header + run pills + layout
    CylinderBank.jsx      4 animated cylinders (FIXED 2 s cycle — must become RPM-driven) + 4 tiles
    CrankSignature.jsx    720° polar fingerprint vs baseline (KEEP — hero visual)
    Verdict.jsx           confidence ring + evidence + model votes (to be reworked)
    SignalStrip.jsx       scrolling waveform + canvas spectrogram
docs/DATA_CONTRACT.md     **schema 1.3 contract (authoritative copy of plan v1.4 section 12)**
```

The code still uses the old **v1 shape** (`prediction`, `telemetry`, `waveform`, `spectrogram`). Task 1 migrates it to schema 1.3.

## 5. Design direction (decided)

- **ISA-101:** grey by default; colour **only** where attention is needed. No glows/pulses on healthy states. Fault colours (in `lib/faults.js`) are semantic only.
- Keep fonts (Chakra Petch display, IBM Plex Sans body, JetBrains Mono numbers) and the dark theme, desaturated.
- **Rename the product to "720"** everywhere.
- **Intro:** keep it to about 10 s of scrolling, no on-screen credit (Livana removed it; the footage is AI-generated with Gemini and the watermark was removed from the frames, noted in README), keep "Skip to diagnostics". Whether it appears in the judged demo is Livana's call.
- Evidence panel: never show a number without n, source column (A/B/C) and split. **Lead with false positives at every level on carOBD** (anomaly flags, advisories, classifier false faults, alarms per hour, each with a 95% upper bound); show the audio bound as secondary; mark alarms on audio-only data as "structurally limited"; include the operator-scaling line ("an operator watching 50 engines would see N per hour"). Never invent numbers; placeholder evidence must be visibly marked "PLACEHOLDER".
- Terminology follows ISA-18.2: "alarm" only for `decision.state === "ALARM"`; ADVISORY is a yellow "watch", not an alarm.
- Accessibility: real buttons, visible focus, `prefers-reduced-motion` respected.

## 6. Contract (schema 1.3) — what the UI must handle

Full schema in `docs/DATA_CONTRACT.md`. Key UI behaviour:

| Field / event | UI behaviour |
|---|---|
| `sent_at` | **UI watchdog:** no message on any channel for 3 s → show "Diagnostics offline" (sensor-health layer, separate from engine health). Freeze animations. |
| `decision.state` | NORMAL (grey), ADVISORY (yellow watch, one witness), ALARM (latched banner + ACK), UNCLASSIFIED ("Abnormal, unclassified: inspect" + physics hint if any) |
| `decision.latched / acked / acked_by / acked_at` | Banner shows latched/acknowledged; ACK button sends the `ack` message with `alarm_id`; button shows "waiting…" until the broadcast confirms |
| `decision.witnesses`, `consecutive_windows` | Show both witnesses (physics + classifier) and the window count in the banner |
| `physics_rules[].rule_basis` | Show "crank angle" or "firing event" next to the rule |
| `health.asset_ne107` / `health.sensors.*` | Two separate chips: asset health, sensor health. Expected mapping: misfire/knock alarm → OFF_SPEC, injector clog → MAINTENANCE_REQUIRED, unclassified → OFF_SPEC, injection active → CHECK_FUNCTION |
| `anomaly.per_location` + `threshold` | Per-cylinder bars with threshold line; hide when `localisation === "none"` |
| `models[]` (incl. Baseline) | Compact model row: label + p for Baseline, SVM, ANN, CNN |
| `latency_ms` | Small readout in the header or banner |
| `mode` | Injection console enabled **only** in `live` mode with `source: simulated`; disabled with an explanation otherwise. While active, asset health shows CHECK_FUNCTION (backend sets it). |
| `signature` empty | No fingerprint; show spectral view + note |
| `obd` channel | Small multiples at true rate, unit + source badge per channel |

## 7. Livana's build plan (hours from H0 = when the team starts building; Nishant maps H0 to the real clock)

| Hours | Work |
|---|---|
| 0–1 | ISA-101 grey layout against Nishant's WebSocket stub; confirm the binary `signal` frame layout with him |
| 1–6 | **WebSocket client** (3 channels in, `ack` out, reconnect with backoff); **UI watchdog** on `sent_at`; waveforms, STFT, OBD panel with source badges; **deck skeleton (visual template)** |
| 6–12 | **Alarm banner + ACK sending**; per-location bars; healthy-vs-observed overlay; 720° fingerprint (simulator/injected only) |
| 12–18 | **Evidence panel** from `eval.json`; **injection console** marked SIMULATED; **3D slot 14–18 only if the 2D dashboard and WebSocket client are done by hour 14** |
| 18–24 | **3D hard cut-off at hour 20 → 2D fallback, no debate**; restyle Diljit's draft charts; fill deck visuals; empty, error and offline states |
| 24–30 | **Freeze.** Backup demo recording + click-through script; rehearse on the demo laptop |

## 8. Task list for Claude Code (in order; each must leave the app runnable)

1. **Migrate to contract schema 1.3.** Rewrite `mockEngine.js` to emit schema-1.3 `state` and `obd` messages (plus mock waveform/spectrogram frames for `signal`) for scenarios: REAL healthy (P3, `signature` empty, `localisation: none`), SIMULATED healthy, misfire (ALARM), knock (ALARM), injector clog (ALARM, small trim drift ≈ clog/4), valve tick (UNCLASSIFIED + physics hint), an ADVISORY case. Add a **mock transport** that emits these over time like the real WebSocket (state ~10 Hz, obd ~1 Hz) and handles `ack` round trips. Update components to read schema 1.3. Rename to **720**. Add the intro credit. Add `src/data/mockEval.js` marked PLACEHOLDER.
   *Done when:* every schema-1.3 field renders somewhere on mock data and `npm run build` passes.
2. **ISA-101 restyle + badges + health chips.** *Done when:* a healthy run is nearly colourless and REAL/SIMULATED/INJECTED is obvious.
3. **Alarm banner + ACK + UNCLASSIFIED/ADVISORY states.** ACK goes through the transport; the UI updates only from the broadcast.
4. **RPM-driven animation + per-location bars.** CylinderBank speed from `operating_point.rpm`, slowed with a computed factor and label ("slowed N×"). Bars hidden when `localisation === "none"`.
5. **WebSocket client + UI watchdog.** Real transport behind the same interface as the mock; `VITE_API_URL` switches. Kill the stream → "Diagnostics offline" within 3 s.
6. **Signals + OBD panels.** Mic + accelerometer waveforms and STFT from `signal`; OBD small multiples from `obd`.
7. **Injection console** (live + simulated only), labelled SIMULATED.
8. **Evidence panel** from `eval.json`: A/B/C columns, n, split, bounds; carOBD false positives at every level as the headline; column B limited to knock and valve tick.
9. **Empty / error / offline states.**
10. **3D explain view** — only if 1–9 are done by hour 14; cut-off hour 20 (section 9).

## 9. 3D explain view spec

- `npm i three @react-three/fiber @react-three/drei` (only new dependencies allowed without asking).
- One lazy-loaded component `src/dashboard/Engine3D.jsx`; props from the `state` message: `rpm`, firing order 1-3-4-2, `per_location`, `threshold`, `decision`, `source`, `offline`.
- Simple primitives only (translucent block, 4 bores, pistons, rods, crank line). No imported models.
- Motion at real RPM, slowed with the same factor/label as CylinderBank; freezes when offline.
- Grey by default; a cylinder turns amber/red only above threshold. Misfire = no combustion flash; knock = sharp flash.
- Only shown for simulator data (no cylinder geometry on real clips).
- Linked hover with the 720° fingerprint (lift hover state to Dashboard).
- In the demo it gets a 10-second beat during the misfire moment. **Nothing moves unless the data says so.**

## 10. Working rules

- Mock mode must work at all times (it's the demo fallback).
- Keep the app runnable after every task; run `npm run build` before calling a task done.
- No new libraries beyond section 9 without asking Livana.
- Don't invent evidence numbers; don't remove source badges; don't show cylinder numbers or the fingerprint on real clips; don't clear alarms locally.
- Fault names/colours only in `lib/faults.js`. One panel per file in `src/dashboard/`.
- Short imperative commit messages.

## 11. Open items (ask Livana; don't decide alone)

- Binary `signal` frame layout (agree with Nishant).
- `eval.json` exact shape (agree with Diljit).
- Whether the scroll intro is in the judged demo.
- Deck tool/format for the visuals.
