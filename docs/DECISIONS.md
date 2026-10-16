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

*Renumbered on 2026-10-16 from D24–D32 to D37–D45: the simulator sections below were written in parallel and use D24–D32 for different decisions (and `dsp/` cites their D24). Content unchanged.*

| # | Decision | Why | Plan |
|---|---|---|---|
| D37 | **OPC UA server at `opc.tcp://127.0.0.1:4840/720/`, security None, anonymous**, started with the backend (`S720_OPCUA=0` turns it off, `S720_OPCUA_ENDPOINT` moves it). If it can't start, the backend logs it and the dashboard keeps running | Local demo laptop with UaExpert. Production would use Basic256Sha256 Sign&Encrypt and user authentication | §11.2 |
| D38 | **The bridge only mirrors the rule layer's broadcast** (the same `state`/`obd` dicts the WebSocket clients get). The `Acknowledge` method goes through the same `Runtime.ack()` as a dashboard ACK, with the same rejection reasons | Keeps the rule layer the single source of truth, so OPC UA and the dashboard can never disagree (D16) | §11.1, §11.2 |
| D39 | **NE 107 is a custom enum `NE107DeviceHealth` with the DI `DeviceHealthEnumeration` values**: NORMAL 0, FAILURE 1, CHECK_FUNCTION 2, OFF_SPEC 3, MAINTENANCE_REQUIRED 4 (with `EnumValues`). The DI nodeset itself isn't loaded | Same values a DI-aware client expects, without importing the DI nodeset into asyncua | §11.2, D14 |
| D40 | **Address space under `Objects/EngineAsset`**: identity, `OperatingPoint`, `Obd`, `Diagnostics`, `Decision`, `AssetHealth`, and a separate `SensorHealth` object with one NE 107 node per sensor. Measured values are AnalogItems with EURange and EngineeringUnits (UNECE codes). Contract nulls map to Cylinder 0, NaN for Rpm/LoadPct/Severity, empty strings, and the OPC UA null DateTime (1601-01-01) | AnalogItems with ranges and units are what the plan asks for and what plant clients display properly. Sensor health stays separate from asset health (D14) | §11.2 |
| D41 | **Faults are `EngineFaultEventType` events fired on transitions only**, with severity 800 misfire/knock alarm, 700 unclassified, 600 injector clog alarm, 500 sensor FAILURE or diagnostics offline, 300 advisory, 200 acknowledged, 100 cleared / back to normal / recovered / run changed. Each event is emitted from `EngineAsset` and from the `Server` object | asyncua has no server-side Alarms & Conditions, so the latch stays in the rule layer and OPC UA carries events. UaExpert's Event View subscribes to the Server object | §11.2 |
| D42 | **`Inject(Fault, Cylinder, Severity)` exists but returns `(false, "not supported until L1")`.** CHECK_FUNCTION isn't published until the injection console exists | Interactive injection waits for Dipen's simulator (L1); the method signature is fixed now so UaExpert demos don't change later | §11.2, §12 |
| D43 | **OPC UA `Diagnostics/DiagnosticsOnline` goes false after 3 s with no `state`**, with one severity-500 event, and back to true (severity 100) when state resumes | Mirrors the UI watchdog (D15), so an OPC UA client is never shown stale "healthy" either | §11.1 |
| D44 | **CHECK_FUNCTION only in an explicit maintenance/test mode** (`POST /maintenance/on`, `/maintenance/off`), and only while nothing is wrong: a latched alarm or an unclassified anomaly keeps OFF_SPEC / MAINTENANCE_REQUIRED. The mapping is computed once (`rule_layer.asset_ne107`) for the dashboard and OPC UA alike, and the mode never touches the rule layer. Supersedes the "injection console active → CHECK_FUNCTION" part of D14 and the last sentence of D42 | An active simulation console must not hide a real fault's health state; only a deliberate maintenance/test mode should show CHECK_FUNCTION | §11.2 |
| D45 | **OPC UA freshness and provenance:** a sensor the current recording doesn't have reads `Bad_NotConnected` instead of NORMAL; `SensorHealth/DiagnosticStream` (NE 107 NORMAL / FAILURE after 3 s with no state) and `LastStateAgeS` give stream freshness; `EngineAsset/DiagnosticTimestamp` is the latest decision's `sent_at`; events carry `DataSource` (REAL / SIMULATED / INJECTED) and `Time` = the decision's `sent_at` | An absent accelerometer on a real clip must not look healthy, and a plant client needs to see how fresh and where from each decision is | §11.1, §11.2 |
## 2026-10-16 · Simulator v1 (`sim_version` 1.0.0)

| # | Decision | Why |
|---|---|---|
| D24 | Crank angle 0° = cylinder 1 firing TDC; firing TDCs at 0 / 180 / 360 / 540° for cylinders 1 / 3 / 4 / 2 | One angle reference for sim, DSP, contract `signature.crank_deg` and the dashboard |
| D25 | Faults are decided per engine cycle; an `inject` applies from the next cycle whose first compression hasn't begun. Random draws are made whether or not a fault is active | Clean labels per cycle; misfire p = 0 is bit-identical to healthy |
| D26 | Fixed per-cylinder heat-release imbalance (1.5%) added on top of cycle-to-cycle variation (3%) | Without it the healthy half-order is unrealistically near zero and misfire detection is too easy (plan §6.2) |
| D27 | Held-out parameter region for randomised engines = structural-mode frequency scale (train 0.90–1.05, test 1.05–1.15) | Tests that classifiers generalise to an engine that sounds different |
| D28 | Idle defaults tuned to MAP ≈ 35 kPa, peak cylinder pressure ≈ 10 bar at 800 rpm; continuous single-cylinder misfire raises MAP to ≈ 45 kPa and holds 800 rpm | Plausible idle operating point; the controller keeps a misfiring engine from stalling |
| D29 | `sim/analysis.py` holds minimal order-spectrum / 720° TSA / tooth-speed helpers for the demo and tests only | Production versions belong in `dsp/` |

## 2026-10-16 · L1 integration (sim drives backend and dashboard)

| # | Decision | Why |
|---|---|---|
| D30 | Misfire physics rule (crank-angle) fires on **half-order ratio** = 0.5-order / 2nd-order amplitude of tooth-time crank speed per 4-cycle window; threshold 1.25 × healthy 99.9th percentile, calibrated on healthy sim runs only, held-out engines checked | Dimensionless, so it doesn't scale with load; whole-cycle windows put both orders on FFT bins |
| D31 | **Location** = cylinder with the largest roughness: speed drop from segment [TDC−60°, TDC+120°) to the next, minus its neighbours' mean | TDC-aligned segments blamed the next cylinder in firing order; plain drops lit the previous cylinder too. This located the right cylinder in 88–94% of windows holding a p = 0.5 misfire |
| D32 | L1 sends **no classifier output** (`models: []`) and a placeholder `anomaly.score = 0.0`; `per_location` = roughness scaled to 0–1 with its limit at 0.5 | No second witness is faked, so the rule layer gives ADVISORY, never ALARM. **Open:** with a real healthy model, misfire without a classifier label becomes UNCLASSIFIED under the rule layer's precedence; agree before the healthy model lands |
| D33 | The rule layer steps **once per finished window**; state ticks between windows resend the last decision | Otherwise "2 consecutive windows" would count the same window twice at 10 Hz |
| D34 | Sources share one interface (`backend/sources`); the simulator is selected as source `sim` and takes faults via `POST /inject` / `POST /clear_faults` | Same code path after the input stage for fake scenarios, simulator and (later) replay |
| D35 | The fingerprint is mic **RMS** per 2° bin (square root of the mean energy), normalised to the healthy reference's peak | Same ordering as energy, but readable on the polar plot |
| D36 | Demo injections use p = 1.0 | At p = 0.5 the controller raises MAP and half the cycles still fire, so the average lobe barely shrinks; the rule and bars still work at p = 0.5 |

## 2026-10-16 · Contract 1.4 (Nishant, approved)

| # | Decision | Why |
|---|---|---|
| D46 | **Schema 1.4 = 1.3 + `state.injection`** (required): `null`, or `{active: true, fault, cylinder, severity, started_at}` echoing the accepted `POST /inject` body. `null` after `POST /clear_faults`, on any source switch, and on every non-simulator source; a rejected inject is never echoed. `severity` is the requested value, not a measurement. The field is `cylinder`, as in the `/inject` body and Livana's draft. Injection does **not** set CHECK_FUNCTION (D44). Every message's `schema_version` is `"1.4"` | The dashboard confirms an injection from the broadcast, the way it confirms an ACK, instead of trusting its own request; the backend stays the single source of truth |

## 2026-10-16 · Simulator v2 (`sim_version` 2.0.0) and contract 1.5 (proposed)

| # | Decision | Why |
|---|---|---|
| D47 | **Proposed for Nishant and Livana: schema 1.5 = 1.4 + nullable `anomaly.score`** (`number \| null`). `null` means "no healthy model", and the rule layer treats it as no anomaly witness. The simulator now sends `null` instead of D32's placeholder `0.0`; OPC UA maps `null` to NaN; the dashboard shows "no healthy model yet". The frontend accepts 1.3–1.5. Every message's `schema_version` is `"1.5"` | A fake 0.0 reads as "measured and perfectly healthy"; null can't be mistaken for a measurement. Resolves the anomaly part of D32 |
| D48 | **Sim v2 channels:** block accelerometer (the knock sensor: chamber modes per Draper, first mode about 6 kHz, energy 5–15 kHz) at 48 kHz; ECU/OBD layer sampled at 1 Hz (rpm, load, throttle, MAP, coolant, STFT, LTFT, spark advance, lambda). Per-firing ECU values are `sim_internal`. Lambda is simulator only (carOBD has no O2). ECU constants are defaults marked `# TODO carOBD-calib`, because the carOBD calibration split doesn't exist yet | Covers the plan's 12 variables; the TODO marks every value that must come from carOBD calibration files, never from held-out ones |
| D49 | **Reciprocating inertia torque** from exact slider-crank kinematics (0.45 kg per cylinder) is added to the gas torque | Without it, crank-speed orders are unrealistically clean at speed; with it the 2nd order cancels near 2,000 rpm, as on real engines |
| D50 | **The misfire feature changes from half-order *ratio* (D30) to `half_order_energy`** = 0.5-order crank-speed amplitude × mean rpm / 10⁶, with **per-rpm-band thresholds** (idle <1,100 / mid / high 2,000–3,000 rpm), each 1.25 × the healthy 99.9th percentile. On v2 the ratio's healthy maximum is 0.56 / 38 / 108 per band (the D30 threshold was 0.395). New thresholds: 0.00898 / 0.01029 / 0.01131; validation FP 0 / 0 / 1 out of 3,614 / 6,514 / 10,788 windows. **This revises the L1 "half-order ratio" fact:** the rule is still half-order plus neighbour-relative roughness, but the half-order term is now an energy. The v1 file is kept | The ratio divides by a 2nd order that inertia cancels, so it is ill-conditioned exactly where the engine spends its mid-range |
| D51 | **Fault models:** knock = Bernoulli per cycle (p = severity), with a burst at the chamber modes and ECU retard of 3° per detected knock (max 12°), recovering at 0.5°/s. Injector clog = cylinder fuel × (1 − s); the ECU's STFT integrates `1 − 1/λ` from the exhaust lambda (80 ms transport, 20 ms mixing) and LTFT learns from STFT, so the steady bank trim is s/(4 − s) ≈ s/4 (unit-tested). Valve tick = an earlier, sharper seat impact on the accelerometer, once per 720° | Each fault leaves the evidence its rule needs (knock band and retard, trim and O2 ripple, cam-gated impact) |
| D52 | **Held out, enforced in code:** the `valve_tick` fault, 3,000–3,500 rpm, and the `"test"` parameter region. `EngineSim(purpose="calibration"\|"training")` refuses to inject valve_tick (`HeldOutFaultError`); `sim/guard.py` refuses held-out labels, chunks and conditions; calibration calls the guard. Labels and meta carry `held_out` | A convention that only lives in a README gets broken by accident |
| D53 | **Harder regimes are flags, OFF by default and ON for calibration and eval** (`Regimes.eval()`): tooth jitter 2 µs; torsional flex 0.02° × (rpm/1000)^1.5 (AR(2) at 280 Hz); load-torque noise 1 N·m (AR(1), τ 0.15 s); sensor dropouts 0.3/min, 0.5–4 s, on mic/accel/crank/obd. Sizes were picked on calibration seeds only. Each feature has its own RNG stream, so regimes off (and severity 0) stay bit-identical to v1 healthy | Keeps the v1 API and tests intact while making the evidence honest |
| D54 | **Seeds are split and asserted disjoint** (`eval/common.py`): calibration engines 0–11, validation 50–53, soak 200–239, robustness 400–439, held-out region 600–639, each with its own run-seed offset. Background noise for the SNR sweep is coloured (pink/brown) noise, because AudioSet/ESC-50 aren't downloaded; the source is logged in the result JSON | Never tune on evaluation seeds; the noise source is stated, not implied |
| D55 | **SimSource v2:** sensors mic/accel/obd/crank. Mic and accel are shown at 4 kHz (so knock above 2 kHz isn't visible on the dashboard: display only). OBD sends all 9 channels at 1 Hz. A dropout stops that channel's output, so the watchdog marks it FAILURE after 3 s; crank, which has no stream, is fed on state ticks (`polled_sensors`). `/inject` accepts all four faults with the simulator's validation (422 otherwise), and the runtime echoes it (D46). `per_location` uses the per-band roughness threshold. **No new rules:** knock, clog and valve tick reach the dashboard as OBD and signals only, until their rules exist | One injection console for every fault; sensor health is real, not cosmetic |
| D56 | **Evidence numbers (sim v2, column A, regimes on, evaluation engines only):** soak 10.0 h, 269,761 windows; ADVISORY 3.6/h (Wilson 95% 2.6–5.0/h), ALARM 0 (structural); held-out region 2.0 h, ADVISORY 3/h. Robustness: detection at p = 1.0 is 0.99 in every trained band and 0.91–0.97 held out; median latency to ADVISORY at p = 1.0 is 588 / 289 / 162 ms (idle / mid / high). Separation (median misfire value / threshold) ranges 0.41–3.3×, never above 4× everywhere, so no further hardening. **Weak spot:** localisation in the held-out region (different flywheel) is 0.39–0.66 | Numbers trace to `eval/results/soak_v2.json` and `robustness_v2.json`; the weak spot is reported, not tuned away (that would need eval seeds) |
