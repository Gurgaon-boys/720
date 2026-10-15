# backend/

**Owner:** Nishant

- **FastAPI**, with replay mode by default and a live mode on the same code path after the input stage
- **WebSocket** with 3 server→UI channels (`signal`, `state`, `obd`) plus `ack` from the UI; server-side downsampling. See `contract/`.
- **Deterministic rule layer:** two witnesses (physics + classifier), 2 consecutive windows, hysteresis, latch + operator ACK, NE 107 mapping. It is the single source of truth for alarm and ACK state.
- **Backend watchdog:** a stream stalled for 3 s sets that sensor to `FAILURE` under `health.sensors`
- **OPC UA** (`asyncua`): EngineAsset object, asset and sensor health, fault events with severity, inject/ACK methods; tested in UaExpert

## Status: L1 (live simulator), L0 scenarios kept

**L1:** source `sim` runs the physics-informed simulator live: `EngineSim` → 4-cycle windows → crank-angle features → the misfire physics rule → this rule layer → the same messages and sockets as L0 (`sources/sim_source.py`).

```bash
S720_SCENARIO=sim python -m uvicorn backend.main:app --port 8000
curl -X POST 127.0.0.1:8000/inject -H "Content-Type: application/json" -d '{"fault":"misfire","cylinder":3,"severity":1.0}'
curl -X POST 127.0.0.1:8000/clear_faults
```

- **One witness only.** There is no classifier yet, so `models` is `[]` and a misfire is **ADVISORY, never ALARM**. Nothing fakes a second witness.
- **No healthy model yet:** `anomaly.score` is a placeholder `0.0`. `anomaly.per_location` is the calibrated crank roughness (0–1, limit 0.5 = `anomaly.threshold`) for the per-cylinder bars. (With your precedence, a real anomaly score over threshold and no classifier label would make this UNCLASSIFIED, not ADVISORY; worth agreeing before Diljit's healthy model lands.)
- **Windows vs ticks:** a 4-cycle window with 50% overlap finishes every ~300 ms at idle, while `state` goes out at 10 Hz. The rule layer is stepped **once per finished window** (`Window.fresh`); ticks in between resend the last decision. Windows finishing in the same tick are stepped in order (`Window.backlog`).
- `obd` carries the simulator's own rpm, MAP and MAP-based load (no ECU/OBD loops in sim v1). `signal` carries `mic` (4 kHz, display gain ×8) and `mic_stft`; there is no accelerometer in sim v1.
- Source switches (`POST /scenario/{name}`) build the new source in a worker thread and swap it in on the event loop, so a tick never steps the new simulator with the old stream's tick index.
- `S720_SIM_SEED` (run seed) and `S720_SIM_ENGINE` (randomised engine seed) pick the simulated engine.
- Test with Windows clients on `127.0.0.1`: `localhost` tries IPv6 first and costs ~2 s per request.

## L0 (fake scenarios)

Everything after the input stage is real: the rule layer, the contract messages, the binary frames, the WebSockets and the watchdog. The input is `scenarios.py`, scripted fake evidence that the simulator, DSP and models replace later. **No number it produces is a result.**

Run from the repo root:

```bash
pip install -r requirements.txt
python -m uvicorn backend.main:app --port 8000      # starts on the "misfire" scenario
python -m backend.probe --seconds 15 --ack --bad-ack
python -m pytest backend/tests -q
```

Pick a scenario with `S720_SCENARIO=<name>` before starting, or switch while running with `POST /scenario/<name>` (resets the rule layer). `S720_VALIDATE=1` checks every outgoing message against `contract/schemas` and logs violations.

| Scenario | What happens |
|---|---|
| `healthy` | NORMAL throughout |
| `misfire` | Healthy 2 s → misfire cyl 3, both witnesses → ALARM on the 2nd window, latched until ACK + 2 healthy windows |
| `knock` | Same, knock on cyl 2 |
| `injector_clog` | Same, cyl 1; asset health MAINTENANCE_REQUIRED |
| `advisory` | Classifier says knock, physics rule quiet → ADVISORY, never ALARM |
| `conflict` | Classifier says misfire, knock rule fires → ADVISORY with no fault, never ALARM |
| `unclassified` | Valve tick (held out): anomaly high, no confident label, physics hint → UNCLASSIFIED, OFF_SPEC |
| `real_p3` | `source: real`: no localisation, empty fingerprint, `firing_event` rules, no OBD |
| `offline` | Healthy 4 s, then **all output stops** for 5 s so the UI's 3 s watchdog trips; loops |
| `mic_dropout` | Mic frames stop for 8 s; the backend watchdog sets `health.sensors.mic` to FAILURE after 3 s |

## Endpoints

| Endpoint | Direction | Content |
|---|---|---|
| `ws://…/ws/state` | server → UI, ~10 Hz | `state` JSON (one per window) |
| | UI → server | `ack` JSON. A rejected ACK gets an `ack_error` reply on this socket only (see below) |
| `ws://…/ws/obd` | server → UI, ~1 Hz | `obd` JSON, one sample per channel, `source: simulator` |
| `ws://…/ws/signal` | server → UI, ~20 Hz per channel | binary frames per `contract/signal_frame.md`: `mic`, `accel` (4 kHz waveforms, 200 samples) and `mic_stft` (one 101-bin column to 2 kHz) |
| `GET /health`, `GET /scenarios`, `POST /scenario/{name}` | HTTP | pick the source: a fake scenario or `sim` |
| `POST /inject` `{fault, cylinder, severity}`, `POST /clear_faults` | HTTP | simulator only (409 otherwise); 501 for faults not in sim v1, 422 for bad values |

**`ack_error`** is not a contract 1.3 message. The UI tells it apart from `state` because it has a `type` field:

```json
{ "type": "ack_error", "schema_version": "1.3", "sent_at": 1760551240.2,
  "alarm_id": "a_9999", "reason": "unknown_alarm_id", "detail": "a_9999 is not an alarm; active alarm: a_0001" }
```

`reason` is `invalid_message` (not JSON, or fails `ack.schema.json`), `unknown_alarm_id` or `duplicate_ack` (already acknowledged, or acknowledged and cleared). A rejected ACK never changes alarm state.

## Rule layer (`rule_layer.py`)

Each window is assessed on its own (match / conflict / unclassified / advisory / healthy, in that order), then fed through the latch:

- **ALARM** after 2 consecutive windows where the classifier majority (Baseline excluded, mean p ≥ 0.6) and the physics rule name the same fault. The first such window is ADVISORY.
- The alarm stays latched until acknowledged, even if the fault clears first. **ACK only marks it acknowledged.**
- **Reset to NORMAL** on the first window evaluation where the alarm is acknowledged and the last 2 consecutive windows were healthy. An ACK after the fault has already cleared resets on the next window, never inside the ACK itself.
- A new episode gets a new `alarm_id` (`a_0001`, `a_0002`, …), never reused while the server runs, even across scenario switches. One alarm is active at a time.
- While latched, `decision` reports the evidence that raised it (`witnesses: [physics, classifier]`, `consecutive_windows` ≥ 2, live while the fault still matches).
- NE 107 asset health: misfire/knock alarm and UNCLASSIFIED → OFF_SPEC, injector clog alarm → MAINTENANCE_REQUIRED, everything else NORMAL. Sensor health comes from the watchdog.

## Files

| File | Purpose |
|---|---|
| `main.py` | FastAPI app and the three WebSocket endpoints |
| `runtime.py` | The three rate loops, fan-out to clients, ACK handling |
| `rule_layer.py` | Deterministic rule layer and NE 107 mapping |
| `messages.py` | Contract 1.3 message builders; validation against `contract/schemas` |
| `signal_frame.py` | Binary frame encode/decode |
| `scenarios.py` | L0 fake scenarios (input stage only) |
| `sources/` | Source registry (`make_source`) and the live simulator source (L1) |
| `watchdog.py` | Backend sensor watchdog (3 s) |
| `probe.py` | Command-line WebSocket probe |
| `tests/` | Rule layer, messages and frames, scenarios on a fake clock, end-to-end sockets |
