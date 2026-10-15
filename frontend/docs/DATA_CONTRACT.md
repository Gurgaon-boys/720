# Data contract (schema 1.4, from 720 Final Plan v1.4, section 12)

**Owner: Nishant.** This file mirrors the locked plan. If the backend changes a field, bump `schema_version`, update this file, the mock data and the components together.

Every numeric field carries its unit in the field name or in the `units` map.

## Channels (WebSocket)

| Channel | Direction | Rate | Content |
|---|---|---|---|
| `signal` (binary) | server → UI | fast, server-downsampled | waveform frames (mic, accelerometer), spectrogram columns |
| `state` (JSON) | server → UI | ~10 Hz | decision, features, anomaly, models, rules, health, fingerprint |
| `obd` (JSON) | server → UI | ~1 Hz | OBD channels |
| `ack` (JSON) | UI → server | on click | operator acknowledgement to the rule layer |

The binary `signal` frame layout is not yet specified in the plan. **Agree it with Nishant at hour 0** (suggested: small JSON header + Float32Array body, one frame per channel).

## `state` message

```json
{
  "schema_version": "1.3",
  "sent_at": 1760551234.512,
  "units": { "t": "s", "sent_at": "unix s", "rpm": "rev/min", "load_pct": "%", "latency_ms": "ms", "severity": "0-1", "crank_deg": "deg" },
  "run_id": "sim_0412",
  "source": "simulated | injected | real",
  "audioset_pool": "P1 | P2 | P3 | null",
  "engine_tag": "vetted_car | null",
  "mode": "live | replay",
  "t": 12.5,
  "operating_point": { "rpm": 2400, "load_pct": 38, "regime": "cruise" },
  "localisation": "cylinder | none",
  "features_summary": { "half_order_ratio": 3.1, "knock_band_ratio": 1.0, "cam_env_peak": 0.2 },
  "anomaly": { "score": 0.82, "threshold": 0.60, "per_location": [0.12, 0.10, 0.91, 0.15] },
  "models": [
    { "model": "Baseline", "label": "misfire", "p": 0.70 },
    { "model": "SVM", "label": "misfire", "p": 0.86 },
    { "model": "ANN", "label": "misfire", "p": 0.88 },
    { "model": "CNN", "label": "misfire", "p": 0.91 }
  ],
  "physics_rules": [ { "rule": "misfire", "fired": true, "location": 3, "rule_basis": "crank_angle | firing_event" } ],
  "decision": {
    "state": "ALARM | ADVISORY | UNCLASSIFIED | NORMAL",
    "alarm_id": "a_0007",
    "fault": "misfire", "location": 3, "severity": 0.12,
    "witnesses": ["physics", "classifier"], "consecutive_windows": 2,
    "latched": true, "acked": false, "acked_by": null, "acked_at": null
  },
  "health": { "asset_ne107": "OFF_SPEC", "sensors": { "mic": "NORMAL", "accel": "NORMAL", "obd": "NORMAL" } },
  "latency_ms": 41,
  "signature": { "crank_deg": [], "amplitude": [], "baseline_amplitude": [] }
}
```

Notes:
- `signature` is **empty on real AudioSet clips**, and `localisation` is `none` there. The firing-slot concept is retired: real clips get no localisation at all.
- "Diagnostics offline" is **not** a decision state. The UI derives it from `sent_at` silence (3 s); the backend reports sensor failures under `health.sensors`.
- Fault labels: `healthy | misfire | knock | injector | valve` (valve is held out of ML training and appears via UNCLASSIFIED + physics hint).
- `health.asset_ne107` mapping: healthy → NORMAL; latched misfire or knock → OFF_SPEC; latched injector clog → MAINTENANCE_REQUIRED; unclassified → OFF_SPEC; advisory → NORMAL + advisory event; injection console active → CHECK_FUNCTION. Sensor failures → FAILURE on `health.sensors.*` only.

## `obd` message

```json
{
  "schema_version": "1.3",
  "sent_at": 1760551234.9,
  "channels": {
    "rpm":      { "t": [12.0, 13.0], "v": [2398, 2405], "unit": "rev/min", "source": "real_carobd | simulator" },
    "stft_pct": { "t": [12.0, 13.0], "v": [1.6, 4.2],   "unit": "%",       "source": "simulator" }
  }
}
```

## `ack` message (UI → rule layer)

```json
{ "schema_version": "1.3", "type": "ack", "alarm_id": "a_0007", "operator": "judge", "sent_at": 1760551240.1 }
```

The rule layer clears the latch and broadcasts the new `state`. **The dashboard never clears an alarm locally**; it updates only from that broadcast (mock mode simulates the round trip).

## Schema 1.4: injection console (agreed, DECISIONS.md D46; `contract/` is authoritative)

1.4 = 1.3 plus one `state` field. Every message's `schema_version` becomes `"1.4"`; nothing else changes. The agreed schema is `contract/schemas/state.schema.json` (same `injection` definition as the draft `docs/state-1.4.proposed.schema.json`). The dashboard accepts both 1.3 and 1.4; the mock speaks 1.4.

The console uses the backend's existing simulator routes (L1, `backend/README.md`), unchanged:

- `POST /inject` body `{ "fault": "...", "cylinder": 1-4, "severity": 0-1 }` → 200; 409 when the source has no simulator; 501 for a fault not in the simulator yet; 422 for bad values. The `detail` is shown in the console.
- `POST /clear_faults` → 200.

`state.injection` is the one addition (required in 1.4, `null` when nothing is being injected). It echoes the accepted request, so the dashboard can confirm it from the broadcast like an ACK:

```json
"injection": { "active": true, "fault": "misfire", "cylinder": 3, "severity": 1.0, "started_at": 1760551250.0 }
```

- `fault`: `misfire | knock | injector_clog | valve_tick`; `cylinder`: 1–4; `severity`: the **requested** severity (0–1) as sent to the simulator, not a measurement. The rule layer's own estimate stays in `decision.severity`.
- Only on `mode: "live"` with `source: "simulated"`. Injection alone does not set CHECK_FUNCTION: that is only for the backend's explicit maintenance/test mode, and never over a fault (D44, superseding that part of D14). The injected fault goes through the normal rule layer; no shortcut to ALARM. Clearing does not clear a latched alarm: the usual ACK + healthy windows apply.
- One injection at a time is enough for the console; if the simulator holds several, send the latest.

The UI never assumes a request worked: it shows an injection only once `state.injection` says so. A live backend that sends no `injection` field (schema 1.3) gets a disabled console with "backend injection unavailable".

## Evidence file (`eval.json`, from Diljit)

Loaded once, not streamed. Shape to be agreed with Diljit. Must carry, for every number: value, n, data source (A simulated / B injected / C real), split type, and interval or bound where applicable. Expected sections: **false positives per hour at every level** (anomaly flags, advisories, classifier false faults, alarms — alarms noted as structurally limited on audio-only data), each with a 95% upper bound, **carOBD held-out files as the headline** and vetted P3 audio as secondary; the operator-scaling line ("an operator watching 50 engines at our rate would see N per hour"); random-vs-grouped split gap; model comparison (Baseline/SVM/ANN/CNN, identical grouped splits, variance across seeds), per-fault sensor ablation, held-out-class AUROC + pAUC (valve, knock), injector-clog detectability (smallest clog at the fixed false-alarm rate + score-vs-severity curve, simulated), column B results for knock and valve tick only, OBD-branch results on held-out carOBD, latency.

*All numbers above are placeholders, not results.*
