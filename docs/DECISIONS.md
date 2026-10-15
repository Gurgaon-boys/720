# Decisions log

Newest entries go at the bottom. Each entry has a date, the decision, the reason and its source. Never rewrite a past entry; add a new one that supersedes it.

## 2026-10-15 · Locked in plan v1.4 (`720_Final_Plan.md`)

| # | Decision | Why | Plan |
|---|---|---|---|
| D1 | **Audio at 48 kHz** for the simulator and every pipeline (real clips stay at their native rate) | Knock energy sits at ~5–15 kHz, so 16 kHz would miss most of it | §4 |
| D2 | **Cut:** the "earlier than the ECU" race | We would build both the referee and the contestant. Replaced by measured early detection (★7) | §2 |
| D3 | **Cut:** the ELM327 emulator | Simulated data through an emulated protocol proves nothing. OPC UA speaks Honeywell | §2 |
| D4 | **Cut:** the ignition-coil current stream | Not measurable on a real car in a demo. Replaced by MAP | §2 |
| D5 | **Cut:** a separate knock sensor | A knock sensor *is* a block accelerometer, so it is merged into one 48 kHz block accelerometer | §2 |
| D6 | **Deferred:** twin inversion for severity | Only if Dipen is ahead at hour 14 | §2 |
| D7 | **Cut:** Grad-CAM as the explanation | Unreliable on spectra. We explain with physics evidence instead | §2 |
| D8 | **Cut:** 3D engine on the main screen | Reads as decoration under ISA-101. It becomes an optional "explain" view | §2 |
| D9 | **Additive-only injection onto real audio:** column B is knock and valve tick only | A misfire is a *missing* pulse and can't be added. Misfire and injector clog stay simulator-only | §4 |
| D10 | **carOBD is the headline real false-alarm bound**, split by file (calibration vs held-out, ≥1 held-out file of each type). Vetted P3 audio is secondary | P3 may hold only dozens of clips (~22/h bound at 50 clips) | §4 |
| D11 | **AudioSet split by video ID into P1 / P2 / P3**, timestamped at H0. P1 is background texture only, and P3 is never touched by training or the simulator | Prevents the leakage we criticise | §4 |
| D12 | **No 720° fingerprint and no cylinder localisation on real clips.** False alarms there use only features that don't depend on cylinder count or RPM | Real clips have an unknown cylinder count, stroke and RPM, and no cam/TDC reference | §4, §7 |
| D13 | **Every physics rule has a `crank_angle` and a `firing_event` version.** If no rule can run, the system can only raise an advisory | Real audio has no crank angle | §8 |
| D14 | **NE 107 states reused for asset health; sensor health kept separate.** Misfire/knock alarm → OFF_SPEC · injector clog → MAINTENANCE_REQUIRED · unclassified → OFF_SPEC · advisory → NORMAL + event · stale stream → FAILURE (sensor node) · injection console active → CHECK_FUNCTION | NE 107 is a field-device standard, used here as an explicit analogy | §11.2 |
| D15 | **Two-layer watchdog:** the backend marks a stream stalled for 3 s as sensor FAILURE, and the UI shows "diagnostics offline" after 3 s without messages | A crashed backend can't report its own crash | §11.1 |
| D16 | **The rule layer is the single source of truth for alarm and ACK state** | The dashboard and OPC UA can never disagree | §11.1 |
| D17 | **3D engine: protected slot at hours 14–18** only if the 2D dashboard and WebSocket client are done by hour 14. **Hard cut-off at hour 20 → 2D fallback, no debate** | First thing cut if anything slips | §12 |
| D18 | **Three WebSocket channels by rate** (`signal` binary, `state` ~10 Hz, `obd` ~1 Hz) plus `ack` | One big message per update would choke the WebSocket | §12 |
| D19 | **`valve_tick` held out of ML training.** Open-set AUROC is measured on the ML side without the physics rule | Tests "unknown" honestly | §10 |

## 2026-10-15 · Step 1 repo setup (contract v1.3)

| # | Decision | Why |
|---|---|---|
| D20 | The `signal` binary frame is a `uint32` header length + a UTF-8 JSON header + a float32 LE payload. The header adds `kind` (waveform/spectrogram), `t0`, and, for spectrograms only, `n_bins`, `n_frames` and `f_max_hz` | Plan §12 doesn't specify a layout. JSON headers are easy to read in both JS and Python, and `t0` aligns frames with `state.t` |
| D21 | The example messages use one concrete value where the plan lists alternatives (e.g. `"source": "simulated"`). The schemas enforce the full enums | An example has to be a valid message |
| D22 | The schemas are strict (`additionalProperties: false`). For `source: "real"` they enforce `localisation: "none"` and an empty `signature`. For `ALARM` they enforce 2 witnesses and ≥2 consecutive windows | Catches frontend/backend drift and contract violations at validation time |
| D23 | `operating_point.rpm` and `load_pct` may be `null` | RPM is unknown on real AudioSet clips, which skip order tracking |

## 2026-10-16 · OPC UA bridge (Nishant, approved)

| # | Decision | Why | Plan |
|---|---|---|---|
| D24 | **OPC UA server at `opc.tcp://127.0.0.1:4840/720/`, security None, anonymous**, started with the backend (`S720_OPCUA=0` turns it off, `S720_OPCUA_ENDPOINT` moves it). If it can't start, the backend logs it and the dashboard keeps running | Local demo laptop with UaExpert. Production would use Basic256Sha256 Sign&Encrypt and user authentication | §11.2 |
| D25 | **The bridge only mirrors the rule layer's broadcast** (the same `state`/`obd` dicts the WebSocket clients get). The `Acknowledge` method goes through the same `Runtime.ack()` as a dashboard ACK, with the same rejection reasons | Keeps the rule layer the single source of truth, so OPC UA and the dashboard can never disagree (D16) | §11.1, §11.2 |
| D26 | **NE 107 is a custom enum `NE107DeviceHealth` with the DI `DeviceHealthEnumeration` values**: NORMAL 0, FAILURE 1, CHECK_FUNCTION 2, OFF_SPEC 3, MAINTENANCE_REQUIRED 4 (with `EnumValues`). The DI nodeset itself isn't loaded | Same values a DI-aware client expects, without importing the DI nodeset into asyncua | §11.2, D14 |
| D27 | **Address space under `Objects/EngineAsset`**: identity, `OperatingPoint`, `Obd`, `Diagnostics`, `Decision`, `AssetHealth`, and a separate `SensorHealth` object with one NE 107 node per sensor. Measured values are AnalogItems with EURange and EngineeringUnits (UNECE codes). Contract nulls map to Cylinder 0, NaN for Rpm/LoadPct/Severity, empty strings, and the OPC UA null DateTime (1601-01-01) | AnalogItems with ranges and units are what the plan asks for and what plant clients display properly. Sensor health stays separate from asset health (D14) | §11.2 |
| D28 | **Faults are `EngineFaultEventType` events fired on transitions only**, with severity 800 misfire/knock alarm, 700 unclassified, 600 injector clog alarm, 500 sensor FAILURE or diagnostics offline, 300 advisory, 200 acknowledged, 100 cleared / back to normal / recovered / run changed. Each event is emitted from `EngineAsset` and from the `Server` object | asyncua has no server-side Alarms & Conditions, so the latch stays in the rule layer and OPC UA carries events. UaExpert's Event View subscribes to the Server object | §11.2 |
| D29 | **`Inject(Fault, Cylinder, Severity)` exists but returns `(false, "not supported until L1")`.** CHECK_FUNCTION isn't published until the injection console exists | Interactive injection waits for Dipen's simulator (L1); the method signature is fixed now so UaExpert demos don't change later | §11.2, §12 |
| D30 | **OPC UA `Diagnostics/DiagnosticsOnline` goes false after 3 s with no `state`**, with one severity-500 event, and back to true (severity 100) when state resumes | Mirrors the UI watchdog (D15), so an OPC UA client is never shown stale "healthy" either | §11.1 |
