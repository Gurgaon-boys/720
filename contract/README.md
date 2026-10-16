# contract/

**Owner:** Nishant · **schema_version: `1.5`** · Source: plan section 12 (1.4 = 1.3 + `state.injection`, D46; 1.5 = 1.4 + nullable `anomaly.score` and `rule_basis: "obd"`, D55, proposed by Dipen)

This contract is the only interface between the simulator/DSP/models, the backend and the dashboard. **It is locked.** Any change bumps `schema_version` in every schema and example, gets a line in `docs/DECISIONS.md`, and must pass `python contract/validate.py`.

## Channels

| Channel | Direction | Format | Rate | Content |
|---|---|---|---|---|
| `signal` | server → UI | binary | fast, server-downsampled | Waveform frames and spectrogram columns. See [`signal_frame.md`](signal_frame.md) |
| `state` | server → UI | JSON | ~10 Hz | Decision, features, anomaly, models, physics rules, health, 720° fingerprint |
| `obd` | server → UI | JSON | ~1 Hz | OBD channels at their true rate, each with its source |
| `ack` | UI → rule layer | JSON | on click | Operator acknowledgement of a latched alarm |

We send one channel per rate because a single big message per update would choke the WebSocket.

## Rules

1. **Every message carries `schema_version` and `sent_at`** (unix seconds, float).
2. **Units:** every numeric field carries its unit, either in the `units` map or in the field name (`load_pct`, `latency_ms`, `sample_rate_hz`).
3. **The rule layer is the single source of truth for alarm and ACK state.** The UI sends `ack`. The rule layer clears the latch and broadcasts a new `state`, and both the dashboard and OPC UA update only from that broadcast, so they can never disagree. The UI never clears an alarm locally.
4. **Diagnostics offline:** the UI shows "diagnostics offline" itself after **3 s** with no message, checked against `sent_at`. This is measurement/sensor health, **not** engine health, so it is **not** a `decision.state`. The backend separately reports a stalled stream as `FAILURE` under `health.sensors`.
5. **`localisation`** is `"cylinder"` or `"none"`. There is no "slot". Real clips are always `"none"`.
6. **`signature`** (the 720° fingerprint) is **empty on real AudioSet clips**. It is filled only for simulated and injected data.
7. **`source`** is `simulated`, `injected` or `real`. The dashboard badges every view with it.
8. **`rule_basis`** is `crank_angle` (simulator/injected) or `firing_event` (real audio), recorded for every physics rule that fires.
9. **`decision.state`** is `ALARM`, `ADVISORY`, `UNCLASSIFIED` or `NORMAL`. ALARM needs both witnesses (physics + classifier) for 2 consecutive windows.
10. **Health** uses the NAMUR NE 107 states `NORMAL`, `FAILURE`, `CHECK_FUNCTION`, `OFF_SPEC` and `MAINTENANCE_REQUIRED`. `health.asset_ne107` is asset (engine) health. `health.sensors` is sensor health, kept separate.
11. **`injection`** (1.4, required) echoes the accepted `POST /inject` body while a simulator fault is injected: `{active: true, fault, cylinder, severity, started_at}`, and is `null` otherwise (after `POST /clear_faults`, on a scenario switch, and on every non-simulator source). `severity` is the **requested** value, not a measurement; the rule layer's estimate stays in `decision.severity`. An injected fault goes through the normal rule layer, and clearing it doesn't clear a latched alarm. CHECK_FUNCTION is not tied to injection (D44: explicit maintenance mode only).
12. **`anomaly.score`** (1.5) is `null` when no healthy (anomaly) model stands behind it, e.g. the live simulator before the healthy model exists. Never send a placeholder number. A null score is not an anomaly witness. `per_location` and `threshold` stay numbers.
13. **`rule_basis: "obd"`** (1.5) marks a physics rule computed from OBD values (the injector-clog trim-drift rule: 30 s of STFT + LTFT). Trims are bank-wide, so it reports `location: null`.

The schemas enforce rules 5 and 6 for `source: "real"`: `localisation` must be `"none"` and the `signature` arrays must be empty.

## Files

- `examples/`: example messages. The schema is picked by filename prefix (`state*.json` → `state.schema.json`).
  - `state.json`, `obd.json`, `ack.json`: the examples from plan section 12 (numbers are placeholders, not results)
  - `state_real_p3.json`: an extra example of a real P3 replay (no localisation, empty signature, `firing_event` basis)
  - `state_sim_injection.json`: the live simulator with a misfire injected on cylinder 3 (ADVISORY, physics witness only, `injection` filled)
  - `signal_header.json`: an example header of a binary `signal` frame
- `schemas/`: JSON Schema (draft 2020-12), one per message type
- `signal_frame.md`: the binary frame layout
- `validate.py`: validates every example against its schema and exits non-zero on failure

```bash
python contract/validate.py
```
