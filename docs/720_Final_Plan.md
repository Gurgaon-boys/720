# 720: Final Plan

**HackHive 2026 · Honeywell PS2: Operation with multivariable data — automobile engine data: signature extraction and pattern recognition**

*Final version 1.4 · Fri 16 Oct 2026, 00:10. Supersedes the earlier recap and project plan. Merges our design sessions with Diljit's research brief, Nishant's critique, and four final external reviews. **Locked.***

**Team:** Dipen (simulator, signal processing, models) · Diljit (data and ML evaluation) · Nishant (backend, decision layer, OPC UA, pitch) · Livana (frontend)

---

## 0. The idea in one paragraph

> **720 is a condition monitor for combustion engines that names the fault, says when it doesn't know, and leaves the alarm decision to a deterministic rule layer and a human.** It folds every sensor onto the engine's 720° four-stroke cycle, the way production engine controllers analyse misfire and knock. It is trained and stress-tested on a physics-informed engine simulator, and judged on real recordings with honest splits, false alarms per hour, and a properly tested "unknown" output.

**Positioning for Honeywell:** the car engine is our test bed for **reciprocating combustion assets: gas-engine compressors, standby and prime-power gensets, engine fleets.** Honeywell's rotating-equipment monitoring focuses on imbalance, misalignment, bearings and cavitation. Combustion faults (misfire, knock, valve-train) are a different class of signature. *Don't claim any Honeywell product does or lacks this; we have no evidence either way.*

**Our three-part thesis:** **real recordings are the evidence · the simulator is the test bench · the deterministic layer decides.**

---

## 1. Standouts

Most PS2 teams will ship the same thing: spectrograms, a CNN/SVM/ANN table showing 95–99% from a random window split, and a confidence bar. To an industrial-automation engineer, near-identical high numbers signal **data leakage**, not skill. Our edge is evidence discipline, physics, and a deterministic decision layer.

| # | Standout | What judges see | Why it lands with Honeywell IA engineers |
|---|---|---|---|
| ★1 | **Deterministic decision layer with human ACK** | ML only *recommends*. An alarm is raised only when a physics rule **and** the classifier agree for 2 consecutive windows. It latches until an operator acknowledges it. A watchdog shows "diagnostics offline" if data goes stale | Matches Honeywell's "deterministic AI, people retain oversight" direction. AI runs beside the control logic, not inside it |
| ★2 | **"I don't know", tested properly** | A fault class held out of training (valve tick) appears. ML says **"abnormal, unclassified: inspect"**, and the physics rule adds "impact at valve-closing angle". We report how well "unknown" works (AUROC) | Most teams claiming unknown-fault detection set a softmax threshold and never test it |
| ★3 | **The leakage gap, shown openly** | The same model scored under a random window split and under an honest split by recording, side by side. The gap is our rebuttal to every 99% slide in the room | Directly addresses the main reason industrial judges distrust ML results |
| ★4 | **False positives per hour on real healthy data, at every level** | Anomaly flags, advisories, classifier false-fault rate and alarms per hour, each with a 95% upper bound. **carOBD is the headline real bound**; the vetted-audio bound is secondary (small n). Scaled honestly: "an operator watching 50 engines at our rate would see N alarms per hour, against the guideline of about 12 per operator" | "Would operators trust it?" is the first question in industrial monitoring |
| ★5 | **720° crank-angle analysis: our name and hero visual** | Order-tracked, cycle-averaged signals. A misfire collapses one lobe of the 720° fingerprint; half-order energy spikes; knock shows as a burst just after firing | Mirrors how production engine controllers detect misfire and knock. Audio-only teams can't do this |
| ★6 | **Multivariable fusion proven per fault, not assumed** | An injector clog barely changes the audio, but the fuel trims drift a few percent and crank speed shows one weak cylinder. A per-fault ablation shows which sensors catch which fault | An honest fusion story. Published multi-vehicle work finds the benefit of fusion is fault-dependent |
| ★7 | **Early detection, measured honestly** | A slowly worsening injector clog (simulated, labelled so): **"detectable from X% clog at our fixed false-alarm rate"**, an anomaly-score-vs-severity curve, and measured window-to-alarm latency (Y ms). **No comparison against any ECU code.** One clog moves bank trim by only ~x/4, so the modelled lean code would set only when the cylinder is nearly dead, and any such race would be rigged | Honeywell sells its own AI on early detection. We report ours with no referee we built |
| ★8 | **Speaks the plant's language** | Health published over **OPC UA** using the standard NE 107 states (NORMAL, FAILURE, OFF_SPEC…), viewed in a standard OPC UA client. Pipeline mapped to **ISO 13374** | "This drops into Experion." Machine health is kept separate from sensor health |
| ★9 | **Realistic engine simulator, validated against real data** | Simulator spectra overlaid on real idle recordings; simulator OBD ranges overlaid on the calibration files of carOBD (duration to be confirmed) | We call it what it is, a physics-informed simulator, and show where it matches reality |

**Rule:** anything that doesn't strengthen one of these nine waits until all nine work.

---

## 2. What we cut, and why

| Cut | Reason |
|---|---|
| "Earlier than the ECU" race | We'd build both referee and contestant. OBD-II's 200/1,000-revolution windows are reporting windows, not detection latency (200 revolutions is 15 s at 800 rpm). Replaced by ★7 |
| ELM327 emulator fed by the simulator | Simulated data through an emulated protocol into our own code proves nothing about diagnosis. OPC UA speaks Honeywell; ELM327 reads as consumer automotive |
| Ignition-coil current stream | Can't be measured on a real car in a demo; adds bandwidth, not diagnosis. Replaced by MAP |
| Separate knock sensor | A knock sensor *is* a block-mounted accelerometer. Merged into one 48 kHz block accelerometer |
| Twin inversion for severity | Deferred: only if Dipen is ahead at hour 14 |
| Grad-CAM as the explanation | Unreliable on spectra (it can highlight irrelevant regions). We explain with physics evidence instead |
| 3D engine on the main screen | Reads as decoration to IA judges under ISA-101 display norms. Moved to an optional "explain" view, built last |

---

## 3. Requirements coverage

| Brief requirement | How 720 meets it |
|---|---|
| Multiple sensor streams, ≥ 10 variables | 12 quantities + event streams (section 5). **carOBD alone gives 10+ real variables from a real ECU**, so the rule is met with real data, not only simulated |
| Acoustic: knock, misfire, valve-train wear | 48 kHz microphone; all three modelled; valve train caught by the physics rule (section 9) |
| Vibration across the combustion cycle | 48 kHz block accelerometer, resampled to crank angle |
| Telemetry via ECU / OBD-II / CAN | Simulator ECU model producing standard OBD-II quantities at realistic rates; real OBD from carOBD |
| FFT and STFT signatures | FFT, STFT, log-mel, order spectrum, 720° maps (section 8) |
| Normal baseline | Healthy-only models per RPM band (section 10) |
| Predict component wear | Severity trends, simulated and labelled: injector clog from the regressor/anomaly score; **valve clearance from the physics rule's impact amplitude** (valve tick is held out of ML training, so no regressor exists for it) |
| Real-time faults | Streaming pipeline with measured latency |
| CNN / SVM / ANN + softmax | All three on identical grouped splits, plus a trivial threshold baseline (section 9) |
| Dashboard | ISA-101-style React dashboard (section 12) |
| Simulink / synthetic / public data | Python physics-informed simulator + public real datasets (section 4) |

**Ask the organisers on day one:**
1. How do they count the "10 variables" (distinct quantities, or accelerometer axes separately)?
2. What are the judging criteria?
3. When are the checkpoints?

---

## 4. Data plan

**Constraints:** no real car, no new recordings, no IEEE DataPort access (unless a friend gets it).

| Source | What it is | Role | Limits |
|---|---|---|---|
| **Physics-informed simulator** | Every stream, any fault/cylinder/severity, true crank angle | Training data, stress tests (noise, RPM, held-out class), live demo | Simulated. Results always in their own column |
| **AudioSet "Idling" / "Engine"** | Real engine audio from YouTube (10 s clips) | **(a)** background texture (P1) and base audio for additive injection (P2); **(b)** held-out clips (P3) give a **secondary** false-positive bound at every level (section 4) | Unknown engines, no crank reference, variable quality |
| **AudioSet "Engine knocking"** (~718 clips, no quality assessment from Google) | Real knock-labelled audio | **Weak out-of-domain check only, never training.** On YouTube, "knocking" often means rod knock or lifter tick rather than detonation | Two people tag clips independently; we report only on the subset they agree on |
| **carOBD** | Healthy OBD-II logs, 2014 Toyota Etios, 27 parameters at ~1 Hz, split into idle / drive / campus / long-trip files. Includes RPM, throttle, load, coolant, short- and long-term fuel trim, manifold pressure, intake air temperature, timing advance, commanded equivalence ratio, speed, catalyst temperatures, module voltage | **10+ real variables from a real ECU**; calibrates the simulator's OBD ranges and dynamics; **false-alarm bound for the OBD branch** | No fault labels; **no O2 voltage** (commanded equivalence ratio only, so O2 ripple stays simulator-only); can't train fault classes. **Total hours unverified:** Diljit sums the rows before any duration goes on a slide |
| IEEE DataPort Fiesta set *(only if access appears; $40, or free with an IEEE Society membership)* | 2,148 real 1 s clips, 12 classes incl. cylinder 1/4 misfire | The only possible **real misfire test**. Split by contiguous time blocks to avoid leakage | Behind a paywall |

### AudioSet rules (fixed at hour 0)

**1. Split by YouTube video ID into three disjoint pools.** Clips from one video never cross pools. This prevents the leakage we criticise in other teams.

| Pool | Used for |
|---|---|
| **P1, simulator base** | **Background texture only** for the simulator's microphone: high-passed residual, ambient sound, microphone character. **Never the engine's firing**, which the simulator synthesises itself |
| **P2, injected training** | Real base clips with **additive** simulated faults only (knock bursts, valve impacts), for training and validation |
| **P3, held-out real test** | False alarms per hour, the knock check, and the demo replay. **Never touched by training or the simulator** |

The pool assignment goes into Diljit's **timestamped held-out list** at hour 0.

**2. Vetting before use (decided).** AudioSet "Idling" mixes cars, motorbikes, trucks, generators, diesels and V8s. Our pipeline assumes a 4-cylinder, 4-stroke petrol engine, so mismatched clips would measure the mismatch, not our system.
- Download broadly. **Keep only clips two people independently agree are passenger cars, plausibly 4-cylinder, with no speech or music.** The surviving count is our n, reported per pool.
- **Even vetted clips have an unknown cylinder count, stroke and RPM.** So on real AudioSet clips:
    - **No 720° fingerprint and no cylinder localisation.** These stay on simulator data. The "firing slot" concept is retired: real clips get no localisation at all.
    - False alarms are computed only from **features that don't depend on cylinder count or RPM**: band energies, spectral shape, kurtosis, MFCC statistics, and anomaly distance on those.

**3. Download losses.** AudioSet is distributed as YouTube IDs, and many videos are gone. Diljit's hours 1–6 budget for this, and we report the count that survives per pool.

### Three result columns, never mixed

- **A, Simulated:** trained and tested on the simulator, with held-out parameter regions. Measures only the simulated world.
- **B, Injected onto real audio: knock and valve tick only.** These faults are *additive* (a burst, an impact), so they can be added onto real AudioSet clips cleanly, **split by source clip**. **Misfire and injector clog are not in column B.** A misfire is a *missing* firing pulse, and adding a signal can't remove a pulse in a real recording whose timing we don't know. Those two stay in column A.
- **C, Fully real:**
    - false positives at every level on held-out carOBD files (headline) and vetted P3 audio (secondary)
    - a weak knock check on agreed AudioSet clips
    - a real misfire test only if IEEE access arrives

### carOBD split (fixed at hour 0)

carOBD both calibrates the simulator and tests false alarms, so it's **split by file** into:
- **calibration files** for Dipen
- **held-out files** for the false-positive test

Hold out at least one file of each type (idle, drive, campus, long trip). The split goes into the same timestamped list as the AudioSet pools.

### Statistics we quote

- **Zero events in T hours bounds the rate at about 3/T per hour** at 95% confidence (the "rule of three").
    - **Set expectations on audio:** after vetting, P3 may hold only dozens of clips. 50 clips is ~8 minutes, which bounds the rate only at ~22 per hour. **The audio bound is secondary.**
    - **carOBD's held-out files are the headline real bound** (duration confirmed once Diljit sums the rows).
- **Report false positives at every level, not just alarms.** On audio-only data most physics rules can't run, so the two-witness layer **structurally limits alarms**, and "zero false alarms" would be guaranteed by design. So we report:
    1. anomaly flags per hour
    2. advisories per hour
    3. classifier false-fault rate
    4. alarms per hour, with the note that alarms are structurally limited on audio-only data

  **The honest numbers are the anomaly and advisory rates.**
- **Scaling to the operator guideline:** the ~12 alarms per hour guideline applies to one operator across *all* the assets they watch. We phrase it as "an operator watching 50 engines at our rate would see N per hour".
- **Detection rates carry Wilson 95% intervals**, never a bare "100%". For example, 10/10 detections only supports 72–100%.
- Every number on a slide states **n, data source and split**.

### Audio sample rate: 48 kHz

- Knock energy sits at roughly 5–15 kHz, so a 16 kHz rate would miss most of it.
- The simulator generates at 48 kHz.
- Real clips are used at their native rate. If a mirror only provides 16 kHz, we state that the knock check on those clips is band-limited.

---

## 5. Variables: 12 quantities + event streams

| # | Variable | Rate | Fault evidence |
|---|---|---|---|
| 1 | Microphone (engine bay) | 48 kHz | Misfire, knock, valve tick |
| 2 | Block accelerometer (also the knock sensor) | 48 kHz | Knock, valve tick, misfire |
| 3 | Crank timing / speed (60-2 tooth wheel) | Per tooth | Misfire, weak cylinder |
| 4 | RPM | ~1 Hz real OBD; faster inside the simulator, labelled | Operating condition |
| 5 | Engine load | ~1 Hz | Operating condition |
| 6 | Throttle position | ~1 Hz | Transients |
| 7 | Manifold pressure (MAP) | ~1 Hz | Load, air-path faults; calibrated by carOBD |
| 8 | Coolant temperature | ~1 Hz | Thermal context |
| 9 | Short-term fuel trim | ~1 Hz | Injector clog (small, about ÷4 of the clog) |
| 10 | Long-term fuel trim | ~1 Hz | Slow drift |
| 11 | O2 / lambda | Fast inside the simulator; real carOBD has commanded equivalence ratio only | Lean cylinder: ripple at firing frequency (**simulator-only**) |
| 12 | Spark advance | ~1 Hz | Knock: ECU pulls timing, then recovers |
| + | Misfire counters, fault codes | Events | Context and ground truth inside the simulator |

**Honesty rule:** fast values that are internal to the simulator are badged as such. Real OBD is shown at its true rate of about 1 Hz.

### Which sensor sees which fault

● strong · ○ secondary

| Fault | Mic | Accel | Crank | Trims | O2 | Spark adv. |
|---|---|---|---|---|---|---|
| Misfire | ● | ● | ● | ○ | ● | |
| Knock | ● | ● | | | | ● |
| Valve tick | ● | ● | ○ | | | |
| Injector clog | ○ | ○ | ● | ○ | ● | |

---

## 6. The engine simulator (Dipen)

**Wording for slides:** "physics-informed engine simulator", used as a healthy reference and test bench. Avoid "digital twin" unless qualified. In industrial automation that term usually means a model kept in sync with one specific asset.

**Jobs:** generate labelled data, run stress tests, produce the healthy reference at a given RPM and load, and drive the live demo.

### 6.1 Core model

- 4 cylinders, 4-stroke, firing order **1-3-4-2**, one firing every 180°.
- **Wiebe combustion** per cylinder: x_b(θ) = 1 − exp(−a·((θ−θ0)/Δθ)^(m+1)), a ≈ 5, m ≈ 2.
- **Crank dynamics:** gas torque minus load and friction, through crank inertia, gives instantaneous speed, sampled per tooth on a 60-2 wheel.
- **Steppable in ~0.5 s chunks with live commands** (fault, cylinder, severity, sensor on/off).

### 6.2 Realism (what makes the results mean something)

Without these, misfire detection is trivially easy and the results are worthless.

- **Cycle-to-cycle combustion variation:** real engines never fire identically.
- **Fixed per-tooth spacing errors** on the crank wheel.
- **Idle-speed controller**, so a continuous misfire doesn't stall the model.
- **Randomised engine parameters, with one parameter region held out for testing.**
- Noise at set signal-to-noise ratios, RPM jitter, occasional sensor dropouts.

### 6.3 Structure, sound and ECU loops

- **Block accelerometer:** combustion and valve impacts excite a few damped structural modes. Knock adds bursts at the chamber resonance, about 5–15 kHz. Draper's formula puts the first mode near 6 kHz for typical bores; check the source before quoting it.
- **Microphone:** **the engine's firing pulses are synthesised by the simulator itself**, so a simulated misfire really is a missing pulse in the audio, and labels never contradict the signal.
    - Real audio from pool P1 supplies **background texture only**: high-passed residual, ambient sound, microphone character.
    - The real engine's own firing never appears under a simulated label.
    - Pool P3 is never used here.
- **ECU loops:**
    - **Fuel trim** corrects O2 error. A clog of x in one of four cylinders shifts the bank trim by about **x/4**.
    - **Knock control** retards spark within a few cycles, then re-advances in small steps.
    - **Misfire counters** run in the background.
    - Output ranges are calibrated against **carOBD calibration files only** (held-out files are reserved for testing).

### 6.4 Faults (severity 0 to 1)

| Fault | Injection | Status |
|---|---|---|
| Misfire | Cylinder k loses (or partly loses) combustion with probability p per cycle | Trained class |
| Knock | Damped burst at chamber modes after peak pressure in cylinder k; ECU retards spark | Trained class |
| Injector clog | Fuel to cylinder k scaled by (1 − s); trims shift ~s/4; weak cylinder in crank speed; O2 ripple | Trained class; also the slow-ramp early-warning demo |
| **Valve tick** (excess clearance) | Louder, earlier closing impact at the valve-closing angle (once per 720°) | **Held out of ML training** to test "unknown". Caught by the physics rule. Framed as a mechanical-lash engine (bikes, diesels), since many petrol engines use hydraulic adjusters with zero lash |

### 6.5 Simulator validation slide

- An **illustrative** overlay only: simulator order peaks next to a **P1** AudioSet clip at an *estimated* firing frequency. Not used for any metric.
- OBD histograms overlaid on carOBD calibration files.
- Knock band taken from Draper's formula for the assumed bore.
- Misfire behaviour consistent with published production thresholds.
- Stated plainly: **uncalibrated for any specific vehicle.**

---

## 7. Fault outputs

| Output | How it's decided |
|---|---|
| Healthy | Anomaly low, classifier healthy, physics rules quiet |
| Misfire / Knock / Injector clog | Classifier **and** the matching physics rule agree for 2 windows → alarm (latched) |
| **Abnormal, unclassified: inspect** | Anomaly high, no confident classifier label. The physics-rule hint is shown if one fires (e.g. valve-closing impact) |
| Watch (advisory) | One witness only, or a single window. Shown in yellow, **not an alarm** |
| Diagnostics offline | Watchdog: stale or missing input |

**Terminology follows ISA-18.2:** "alarm" only means prompt operator action is needed; everything else is an alert or advisory.

**Localisation:**
- Simulator data reports cylinder numbers.
- Injected data (knock and valve events placed by the simulator) also carries the simulator's cylinder.
- **Real AudioSet clips report no localisation.** Naming a cylinder needs a cam or TDC reference, which every real engine has but our clips don't.

---

## 8. Signal processing (Dipen)

1. **Windowing:** 4 complete engine cycles per window, 50% overlap.
2. **RPM:** from crank timing in the simulator (and on injected data, which inherits simulator timing). **Real AudioSet clips skip order tracking** and use only features that don't depend on cylinder count or RPM.
3. **Order tracking:** resample to crank angle so signatures stay put when RPM changes. This also helps models generalise across speeds.
4. **Time-synchronous average per cylinder** over the 720° cycle, giving the **720° fingerprint**.
5. **Physics indicators (also the rule-layer witnesses). Every rule has two versions from the start (decided):**

| Rule | Crank-angle version (simulator / injected) | Firing-event version (real audio) |
|---|---|---|
| Misfire | Half-order energy ratio + per-event crank roughness | Missing or weak pulse in the sequence of detected firing pulses |
| Knock | Knock-band energy in a crank-angle window just after TDC vs noise reference | Knock-band burst within a short window **after each detected firing pulse** vs noise reference |
| Valve tick | Envelope peak at cam order, gated to the valve-closing angle | Repeating impact at a stable offset from detected firing events |
| Injector clog | Weak-cylinder crank residual + O2 ripple + small trim shift | Not available (needs crank/O2); simulator only |

- The contract records **`rule_basis: crank_angle | firing_event`** for every rule that fires.
- **If no rule can run on a clip, the rule layer can only raise an advisory (one witness), never an alarm.** That's the two-witness design working as intended.
6. **Spectra and inputs:** FFT, STFT, log-mel, order spectrum.
7. **Features for SVM/ANN:**
    - RMS, kurtosis, crest factor
    - half-order ratio
    - knock-band ratio
    - cam-order envelope peak
    - MFCCs
    - per-cylinder energies (simulator and injected data only)
    - trim/O2/spark-advance statistics
    - simulator residuals
    - RPM and load as context

---

## 9. Models (Dipen designs; Diljit runs the experiments)

| Model | Input | Purpose |
|---|---|---|
| **Trivial baseline** | RMS threshold + half-order ratio threshold | If ML can't beat this, we need to know first |
| **SVM** (RBF, class-weighted) | Engineered feature vector | Brief requirement; strong on small data |
| **ANN + softmax** (2–3 dense layers) | Same vector | Brief requirement |
| **CNN** | **Two-channel order-normalised log spectrogram (mic + accelerometer)** | Brief requirement |
| *Stretch:* fusion CNN with sensor dropout | Per-sensor branches; random sensors blanked in training | Diagnoses with missing sensors; built only if the core is done |

**All models use identical grouped splits:** by simulation run, by source clip for injected and real audio, and across multiple seeds.

**Trained classes:** healthy, misfire, knock, injector clog. Valve tick is held out (section 10).

---

## 10. Normal baseline and "unknown" (open-set evaluation, DCASE-style)

- **Healthy model:** fitted on **healthy data only**. Mahalanobis distance on healthy feature vectors, per RPM band (normal at idle ≠ normal at 3,000 rpm).
- **Classifier-side open-set score:** the classifier's maximum logit, a competitive open-set baseline.
- **Thresholds fixed on validation data, never on test.**
- **Held-out class:** valve tick never appears in training.
- **Valve tick has its own physics rule, and that's fine.** The rule only gives a *mechanism hint* on the dashboard. The open-set AUROC is measured on the ML side **without the rule**, so "unknown" is tested honestly.
- **Line to use in the demo and Q&A:** *"The learned model is blind to it and says unclassified. The rule is a separate physics hint, not the classifier."*
- **Second held-out test (cheap stretch, recommended):** a separate run with **knock** held out, so "unknown" is demonstrated on two different classes.
- **Metrics:**
    - AUROC and partial AUROC (false-positive rate ≤ 0.1) on each held-out class
    - the share of held-out samples sent to "unclassified" versus mislabelled
    - false positives at every level on real healthy data (section 4)
- **Further stretch:** leave-one-fault-out for every fault class.

---

### 10.1 The OBD branch: what runs on carOBD

carOBD is our **headline real evidence**, but it has no audio and no crank signal, and it updates at 1 Hz. The cycle-based windows, order tracking and mic+accelerometer CNN don't apply. The OBD branch is its own small detector:

- **Windows:** fixed **30 s time windows** with 50% overlap, not engine cycles.
- **Features:**
    - short- and long-term fuel trim mean and slope
    - timing-advance retard events
    - MAP-versus-load consistency
    - coolant behaviour
    - RPM and load as context
- **Healthy model:** Mahalanobis distance on these features, **per RPM band**, fitted on carOBD calibration files and simulator healthy OBD.
- **OBD physics rules** (the advisory-level witness):
    - **trim-drift rule:** sustained short-term trim shift beyond a calibrated band
    - **timing-retard rule:** repeated spark pull-back under load
- **Classifier:** an **OBD-only classifier trained on the simulator's OBD channels** (healthy vs injector clog vs knock-retard pattern). On carOBD, where every file is healthy, its false-fault rate is reported directly.
- **Why it's honest:** a model trained on the simulator and run on real carOBD is a genuine sim-to-real test. Any domain shift shows up as false positives, and we report them.
- **RPM transitions:** carOBD's drive files contain real RPM and load changes, so **"RPM changes aren't mistaken for faults" is shown on carOBD**, not on 10-second audio idles.

## 11. Decision layer and integration (Nishant)

### 11.1 Deterministic rule layer

- **Two witnesses:** a physics-rule detector and the classifier must agree on the same fault.
- **Persistence:** agreement for **2 consecutive windows** before an alarm.
- **Hysteresis**, so alarms don't flicker on and off.
- **Latch + operator ACK:** an alarm stays until acknowledged. **No automatic action;** a human decides. **The rule layer is the single source of truth for alarm and ACK state.** The dashboard and OPC UA only reflect it, so they can never disagree.
- **Watchdog, two layers:**
    - Backend: a stalled input stream for 3 s → sensor health = FAILURE.
    - **UI (decided):** no message received for 3 s (checked against `sent_at`) → the UI shows "diagnostics offline" itself, because a crashed backend can't report its own crash.
    - **"Diagnostics offline" is measurement/sensor health, kept separate from engine health.**
- **Single code path:** live and replay share everything after the input stage.

### 11.2 OPC UA (primary integration)

- `asyncua` server with:
    - an **EngineAsset** object
    - analog items with ranges and units
    - **DeviceHealth using NAMUR NE 107 states** (NORMAL, FAILURE, CHECK_FUNCTION, OFF_SPEC, MAINTENANCE_REQUIRED)
- **NE 107 is a field-device diagnostic standard** (for sensors and transmitters). We **reuse its states for asset health** as an explicit analogy and keep sensor health separate:

| 720 condition | Published state | Layer |
|---|---|---|
| Healthy | NORMAL | Asset health |
| Latched **misfire** or **knock** alarm (acute) | OFF_SPEC | Asset health |
| Latched **injector clog** alarm (slow-developing) | MAINTENANCE_REQUIRED | Asset health |
| Unclassified anomaly | OFF_SPEC, root cause "unclassified" | Asset health |
| Advisory / watch | NORMAL + advisory event | Asset health |
| Dropped or stale sensor stream | FAILURE | Sensor health (separate node) |
| Maintenance / test mode (injection console active) | CHECK_FUNCTION | Asset health |

Line to have ready: *"We reuse NE 107 states for asset health and keep sensor health separate."*
- Faults are published as **events with severity**, plus inject and acknowledge methods. asyncua doesn't implement server-side Alarms & Conditions, so we model it this way and say so.
- Demo: **UaExpert** (a standard OPC UA client) subscribed to the engine object.
- A physical stack light is an optional stretch, only if the hardware exists and OPC UA already works end to end. Otherwise latch and ACK run in software on the dashboard.

### 11.3 ISO 13374 mapping (one slide)

| Block | 720 module |
|---|---|
| Data acquisition | Simulator stream; real clip and carOBD replay |
| Data manipulation | Order tracking, 720° averaging, FFT/STFT, features |
| State detection | Healthy model per RPM band, anomaly score |
| Health assessment | Physics rules + SVM/ANN/CNN; cylinder localisation where a crank reference exists |
| Prognostic assessment | Severity trend for the slow injector-clog ramp |
| Advisory generation | Rule layer, latched alarms + ACK, OPC UA NE 107 health, dashboard |

---

## 12. Dashboard (Livana)

**Design language: ISA-101.** Grey by default; colour only where attention is needed. That's a deliberate signal to IA judges.

**Panels:**
1. **Header:** 720 · asset · health state (NE 107) · data-source badge (**REAL / SIMULATED / INJECTED**).
2. **720° fingerprint:** the hero visual. Live vs healthy baseline, one lobe per cylinder. **Simulator data only;** hidden on real clips.
3. **Signals:** mic and accelerometer waveforms + STFT; OBD panel at true rates with real/sim badges.
4. **Per-cylinder bars:** weak-cylinder residuals.
5. **Alarm banner:** latched alarm, two-witness evidence, **ACK button**; advisory and offline states.
6. **Evidence panel:** rendered from Diljit's results file, with real and simulated in separate columns.
7. **Injection console:** clearly marked **SIMULATED**.

**Changes to Livana's existing build:**
- Rename to **720**.
- Animation speed driven by RPM, never a fixed loop.
- Restyle to ISA-101 grey.
- Add the states above.
- Credit the intro video's generator, or restore its watermark, and keep the intro to about 10 s.

**3D engine (decided):**
- Optional "explain view", fully data-driven from the contract.
- **Protected slot at hours 14–18, only if the 2D dashboard and WebSocket client are done by hour 14.**
- **Hard cut-off at hour 20: fall back to the 2D cylinder bank, no debate.** It's the first thing cut if anything slips.
- If it ships, it gets a 10-second beat in the misfire demo moment.

### Data contract (agree in hour 0–1; Nishant owns)

Every numeric field carries its unit in the field name or in a `units` map, and `schema_version` is bumped on any change, so the frontend and backend can't drift apart.

**Three channels by rate (decided).** One big message per update would choke the WebSocket.

| Channel | Rate | Content |
|---|---|---|
| `signal` (binary) | Fast, server-downsampled | Waveform frames, spectrogram columns |
| `state` (JSON) | ~10 Hz | Decision, features, anomaly, models, rules, health, fingerprint |
| `obd` (JSON) | ~1 Hz | OBD channels |
| `ack` (JSON, UI → server) | On click | Operator acknowledgement to the rule layer |

**`state` message:**

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

- `signature` is empty on real AudioSet clips (no fingerprint there).
- "Diagnostics offline" is **not** a decision state. The UI derives it from `sent_at` silence (3 s), and the backend reports sensor failures under `health.sensors`.

**`obd` message:**

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

**`ack` message (UI → rule layer):**

```json
{ "schema_version": "1.3", "type": "ack", "alarm_id": "a_0007", "operator": "judge", "sent_at": 1760551240.1 }
```

The rule layer clears the latch and broadcasts the new state. The dashboard and OPC UA both update only from that broadcast.

*All numbers are placeholders, not results.*

---

## 13. Validation (Diljit leads)

| Result | What it shows |
|---|---|
| **Tables A / B / C** (simulated / injected / real), never mixed | Where each claim comes from |
| **Random vs grouped split gap** | Our rebuttal to inflated results |
| SVM vs ANN vs CNN vs trivial baseline, identical splits, variance across seeds | A fair model comparison |
| **Per-fault sensor ablation** (audio / vibration / OBD / fused) | Which sensors matter for which fault (★6) |
| **Held-out-class AUROC + partial AUROC**, % sent to "unclassified" | "Unknown" actually works (★2) |
| **False positives per hour at every level** (anomaly flags, advisories, classifier false faults, alarms) + 95% upper bounds, on **held-out carOBD files (headline)** and vetted P3 audio (secondary), including RPM transitions | Operator trust (★4) |
| Injected results (column B) for **knock and valve tick only** | Sim-to-real for additive faults |
| Accuracy vs noise level (simulator sweep, plus **public real ambient noise** mixed at fixed levels: **ESC-50** environmental sounds and/or AudioSet background classes, split by source clip and named in the results) | Robustness |
| Anomaly score stays under threshold through **real RPM/load transitions in carOBD drive files** (plus the simulator RPM sweep) | RPM changes aren't mistaken for faults |
| **OBD branch on held-out carOBD** (section 10.1): false positives at every level for a simulator-trained OBD model | Honest sim-to-real test on real ECU data |
| **Shortcut probe:** confirm models don't key on the source clip (group by clip; test on unseen clips) | The model learned faults, not recordings |
| Weak knock check on two-person-agreed AudioSet clips | Out-of-domain sanity check |
| Injector-clog ramp: smallest clog detectable at the fixed false-alarm rate + anomaly-score-vs-severity curve (**simulated**; no ECU comparison) | Early detection (★7) |
| Window-to-alarm latency (ms), laptop CPU, on-premises | Real-time feasibility |

**Honesty statement (report and slide):**
> Training faults are simulated by a physics-informed engine simulator. Real-data results use public engine recordings (AudioSet) and public OBD logs (carOBD), reported in a separate column. We had no access to a physically faulty engine; that is the next step. Every number shown was produced by code in our repository.

**Citations:** several figures in this plan come from teammates' research and haven't been rechecked. **Open the source before any number goes on a slide.**

---

## 14. Build plan (~30 build hours; last 6 frozen)

**Hours here are counted from H0, the moment the team starts building, not from the event kickoff.** Once organisers announce checkpoint and judging times, Nishant maps H0–H30 to the real clock and posts it, so everyone reads the gates the same way.

**Hour 0–1, everyone:**
- Agree the data contract (section 12), including `schema_version` and units.
- 48 kHz audio rate and the channel list.
- Diljit writes the **AudioSet pool split (by video ID), the carOBD file split (calibration vs held-out, at least one of each file type held out) and the held-out test list** into the repo **with a timestamp**, before any training.

**Load rebalanced (decided):** Nishant focuses on the backend, rule layer and OPC UA. Livana takes the UI side of the contract, the slide visuals and the backup recording. To keep Livana from overloading, **Nishant writes the deck's narrative text and Diljit produces draft charts directly from his results**, which Livana restyles.

| Hours | Dipen (simulator, DSP) | Diljit (data, evaluation) | Nishant (backend, rules, OPC UA, deck text) | Livana (frontend, UI contract, deck visuals) |
|---|---|---|---|---|
| 0–1 | Contract; channel list | Contract; AudioSet pool split + carOBD file split + held-out list | Owns the contract doc (3 channels + ack); WebSocket stub with fake data | ISA-101 grey layout against the stub |
| 1–6 | Crank + combustion: 1-3-4-2, Wiebe, cycle variation, tooth errors, idle controller, misfire | Download AudioSet broadly (budget for dead links); two-person vetting (passenger car, plausibly 4-cylinder, no speech/music); pools by video ID; knock tagging; sum carOBD hours; ESC-50 | FastAPI replay mode; windowing; 3-channel WebSocket with server-side downsampling | WebSocket client; UI watchdog on `sent_at`; waveforms, STFT, OBD panel with source badges; **deck skeleton (visual template)** |
| 6–12 | Accelerometer with knock bursts + retard loop; valve events; fuel loop; injector clog; mic = synthesised firing + P1 background texture | Feature pipeline; grouped splits; trivial baseline; SVM, ANN, CNN; **OBD branch (30 s windows, Mahalanobis, simulator-trained OBD classifier)** | Rule layer: two witnesses, 2-window persistence, hysteresis, latch, ACK handling, backend watchdog, NE 107 mapping, `rule_basis` | Alarm banner + ACK sending; per-location bars; healthy vs observed overlay; 720° fingerprint (simulator/injected only) |
| 12–18 | Order tracking + averaging (simulator/injected); **physics rules in both crank-angle and firing-event versions**; simulator vs real overlays | Tables A/B/C; random vs grouped gap; ablation; held-out-class AUROC (valve + knock); false alarms per hour; **draft charts** | OPC UA server (EngineAsset, asset + sensor health, events, inject/ACK methods), tested in UaExpert; **deck narrative text** | Evidence panel from the results file; injection console marked SIMULATED; **3D slot 14–18 only if the 2D dashboard and WebSocket client are done by 14** |
| 18–24 | Noise and RPM sweeps; injector-clog detectability curve; optional severity fit | Ambient-noise mixing at fixed levels; shortcut probe; final figures with n and intervals | Live mode end to end; measured latency | **3D hard cut-off at hour 20 → 2D fallback**; restyle Diljit's charts; fill deck visuals; empty, error and offline states |
| 24–30 | **Freeze.** Rehearse the technical explanation | **Freeze.** Q&A numbers sheet | **Freeze.** Demo checklist; Q&A sheet | **Freeze.** Backup demo recording + click-through script; rehearse on the demo laptop |

### Gates

- **Hour 6:** real evidence = false positives on held-out carOBD files (headline) + vetted AudioSet audio (secondary) + the weak knock check. If IEEE access appears, add the Fiesta misfire test, split by contiguous time blocks.
- **Hour 12:** if the crank model isn't producing stable misfire and weak-cylinder residuals, **freeze simulator scope at misfire, knock and injector clog**. Valve tick stays only as the held-out unknown test, if it works.
- **Hour 18:** OPC UA must work end to end. If it doesn't, the latch and ACK show in software on the dashboard only.
- **Always demoable:** merge only what runs. Checkpoint times are unknown.

---

## 15. Demo script (3 minutes; replay mode by default, same code path as live)

| Time | What judges see | What we say |
|---|---|---|
| 0:00–0:20 | One slide: gas-engine compressor, genset, engine fleet | "Reciprocating engines run compressors and standby power. When one cylinder starts failing, nobody hears it until it trips. 720 names the fault, admits when it doesn't know, and leaves the decision to a deterministic layer and a human." |
| 0:20–1:10 | Real healthy replay of a **vetted passenger-car idle clip from pool P3**: grey dashboard, spectral view, health NORMAL (badged REAL). Then the simulator (badged SIMULATED): symmetric 720° fingerprint; a misfire is injected; half-order spike, one lobe collapses, physics rule and classifier agree for 2 windows, alarm latches, **a judge presses ACK**. *(If the 3D view shipped: 10 s cut to cylinder 3 going dark on its firing stroke.)* | "Two independent witnesses had to agree before this became an alarm, and a human acknowledges it. Please press ACK." |
| 1:10–1:40 | Valve tick (held out of training): "abnormal, unclassified: inspect", health OFF_SPEC; the physics hint names a valve-closing impact | "The learned model is blind to this fault and says unclassified. The rule is a separate physics hint, not the classifier. A model forced to pick a label would guess; ours sends the technician to inspect, not to the wrong part." |
| 1:40–2:10 | Injector clog: audio barely changes, trims drift a few percent, crank residual shows one weak cylinder; the detectability curve shows the smallest clog caught at our false-alarm rate | "This is why multiple variables matter. The microphone can't see this fault; fuel trims and crank speed can. We catch it from X% clog." |
| 2:10–2:45 | Evidence panel: false positives at every level on real healthy data, **carOBD as headline** (with bounds), random vs grouped gap, models on identical splits, held-out-class AUROC | "Our honest split cost us N points. Here's the gap. Any 99% you see today without it is measuring the recording, not the engine." |
| 2:45–3:00 | UaExpert showing the engine object, fault event and NE 107 health | "Python diagnoses, a deterministic layer decides, and the result is on OPC UA where a plant system can read it." |

Recorded backup video on the desktop.

---

## 16. Q&A preparation (Nishant owns; Diljit fills in numbers)

**Format for every answer:** a number, its sample size and split, the limitation in one sentence, and what we did about it.

| Question | Answer direction |
|---|---|
| "You trained and tested on your own simulator." | Simulated results are in their own column and measure only the simulated world. Real evidence: false positives at every level on held-out carOBD files (headline bound) and vetted real audio (secondary), plus a weak knock check on agreed real clips. carOBD was split by file, so calibration and testing never share data. The simulator is our test bench. |
| "Zero false alarms on real audio is guaranteed by your design." | Yes, alarms need two witnesses and most rules can't run on audio alone. That's why we report anomaly flags, advisories and classifier false faults per hour too. Those are the honest numbers. |
| "How do you inject a misfire into a real recording?" | We don't. A misfire is a missing pulse, which can't be added. Column B (injected onto real audio) covers only additive faults, knock and valve tick. Misfire and injector clog are simulator-only, and the simulator synthesises its own firing pulses. |
| "How did you split the data?" | By simulation run and by source clip, never by window. Here's the same model under a random split, and the gap. |
| "False-alarm rate? What about RPM changes?" | False positives at every level per hour, with 95% upper bounds. The headline is held-out carOBD files. Their **drive files contain real RPM and load transitions**, and with per-RPM-band baselines the anomaly score stays under threshold through them. Real audio clips are 10-second idles, so we don't claim RPM robustness from them; the simulator's RPM sweep is the stress test there. |
| "The ECU already detects misfire. What do you add?" | We don't claim to beat the ECU on misfire. We add knock and valve-train coverage, a retrofit path for gensets and compressors without accessible ECUs, an independent second witness, and severity trends. |
| "Prove the extra variables mattered." | Per-fault ablation on the same split. Fusion helps for some faults and not others; published multi-vehicle work finds the same. |
| "How does 'unknown' work and how was it tested?" | Healthy-only Mahalanobis + maximum logit, thresholds fixed on validation. Valve tick held out of training; we report AUROC, partial AUROC and the unclassified share. |
| "CNN vs SVM?" | Identical grouped splits, variance across seeds, plus a trivial baseline. No settled ranking in the literature; here's what our noise sweep showed. |
| "Is this really a digital twin?" | It's a physics-informed simulator used as a healthy reference. Validated by overlays against real audio and carOBD; uncalibrated to any specific vehicle, and we say so. |
| "Your knock data is from YouTube." | Yes. AudioSet labels are unassessed and "knocking" on YouTube often isn't detonation. Two of us tagged clips independently; we report only on the agreed subset, as a weak check, never as training data. |
| "Which cylinder, on a real engine?" | Only with a phase reference. The simulator knows crank angle, so it names the cylinder. On real audio we don't localise at all. A cam/TDC input names the cylinder, and every real engine has one. |
| "What if the Python crashes?" | In our prototype the rule layer runs in the same backend, so it would go down too. The **UI's own watchdog** catches 3 s of silence and shows "diagnostics offline", so the operator is never shown stale "healthy". In production the rule layer would sit on a PLC or DCS, separate from the ML. No automatic actions either way; a human approves. |
| "Latency? Where does it run?" | Measured window-to-alarm latency of X ms on a laptop CPU, fully on-premises, no cloud. |
| "Did the simulator see your test clips?" | No. AudioSet was split by YouTube video ID into three disjoint pools before any training: simulator base, injected training, held-out test. The split is timestamped in the repo. |
| "NE 107 is for field devices, not machines." | Correct. We reuse its states for asset health as an explicit analogy and keep sensor health separate: a dropped stream is FAILURE on the sensor node; an engine fault is OFF_SPEC or MAINTENANCE_REQUIRED on the asset. |
| "Valve tick has a physics rule, so it isn't unknown." | The rule only gives a mechanism hint. The open-set AUROC is measured on the ML side without it, and we repeated the test with knock held out. |
| "Your fingerprint assumes 4 cylinders. AudioSet has bikes and trucks." | We vetted real clips down to passenger cars, but even then the cylinder count is a guess. So we don't show the fingerprint on real clips at all: real false alarms use only features that don't depend on cylinder count or RPM. The fingerprint is shown where we know the engine, i.e. the simulator. |
| "Your physics rules need crank angle. Real audio doesn't have it." | Each rule has a second version relative to detected firing events, and the output records which basis fired. If no rule can run, the system can only raise an advisory, never an alarm. |
| "Isn't your early-warning number rigged?" | We deliberately don't race any ECU code. With one clog in four cylinders, bank trim moves only ~x/4, so a lean code would be a meaningless reference. We report the smallest detectable clog at a fixed false-alarm rate, plus latency. |
| "Is this ISO 20816 compliant?" | No, and it isn't meant to be: no part covers passenger-car engines. We borrow the zone-style vocabulary against each engine's own baseline. |

---

## 17. Risks

| Risk | Mitigation |
|---|---|
| Judges discount simulated results | A/B/C columns, real false-alarm bounds, simulator validation slide, honesty statement |
| Leakage inflates numbers | Grouped splits; AudioSet pools split by video ID; **carOBD split by file**; the random vs grouped gap shown openly; held-out list timestamped at hour 0 |
| Labels contradict audio | Simulator synthesises its own firing pulses; P1 gives background texture only; misfire never injected onto real audio |
| Wrong cylinder assumption on real clips | Vetting to passenger cars; **no fingerprint and no localisation on real clips**; only features that don't depend on cylinder count or RPM |
| Few usable AudioSet clips | Audit early; report whatever count survives; IEEE set if access appears |
| Simulator too idealised | Cycle variation, tooth errors, idle controller, randomised parameters, held-out region |
| Scope creep | Section 2 cuts are final; gates at hours 6/12/18 |
| Integration fails late | Contract at hour 0–1; single code path; merge only what runs |
| Live demo breaks | Replay by default; recorded backup |
| One person becomes a bottleneck | Contract-first; Diljit evaluates independently; Nishant and Livana build against the stub |

---

## 18. Still open

- [ ] Organiser answers: "10 variables" counting, judging criteria, checkpoint times
- [ ] IEEE DataPort access via a friend (adds the only real misfire test)
- [ ] Map H0–H30 to the real clock once checkpoint times are known (Nishant)
- [ ] carOBD total duration (Diljit sums rows)
- [ ] Surviving AudioSet clip counts per pool and per engine tag
- [ ] Final numbers for every placeholder (X, Y, N), filled only from our own runs

---

## 19. Key sources

*Verify each before quoting on a slide.*

- OBD-II misfire monitor windows (200 / 1,000 revolutions): [FenderBender](https://www.fenderbender.com/running-a-shop/operations/article/33022601/ford-mode-06-misfire-tests), [CARB 13 CCR 1968.2](https://ww2.arb.ca.gov/sites/default/files/barcu/regact/obdii06/19682clean.pdf)
- Data leakage in fault-diagnosis studies: [arXiv 2509.22267](https://arxiv.org/html/2509.22267v3)
- Audio vs vibration per fault on 232 vehicles: [arXiv 2403.11037](https://arxiv.org/pdf/2403.11037)
- Open-set evaluation protocol: [DCASE 2023 Task 2](https://dcase.community/challenge2023/task-first-shot-unsupervised-anomalous-sound-detection-for-machine-condition-monitoring); maximum-logit baseline: [Vaze et al.](https://arxiv.org/abs/2110.06207)
- Alarm-management numbers: [Chemical Engineering](https://www.chemengonline.com/alarm-management-numbers/)
- NE 107 states in OPC UA DI: [OPC Foundation](https://reference.opcfoundation.org/DI/v104/docs/4.5.4); asyncua: [opcua-asyncio](https://github.com/FreeOpcUa/opcua-asyncio)
- Grad-CAM limits on vibration spectra: [Mey and Neufeld 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9736871)
- Honeywell Experion Cognition: [Honeywell](https://www.honeywell.com/us/en/news/press-releases/2026/06/honeywell-introduces-experion-cognition-to-deliver-autonomous-control-room-operations-for-borouge-international); Sundyne acquisition: [Honeywell](https://www.honeywell.com/us/en/news/press-releases/2025/06/honeywell-completes-acquisition-of-sundyne-to-expand-process-industry-capabilities)
- Data: [AudioSet engine knocking](https://research.google.com/audioset/balanced_train/engine_knocking.html), [carOBD](https://github.com/eron93br/carOBD), [IEEE DataPort Fiesta set](https://ieee-dataport.org/documents/acoustic-fault-dataset-internal-combustion-engine-under-controlled-conditions-ignition), [ESC-50 environmental sounds](https://github.com/karolpiczak/ESC-50)
- ISO 13374 blocks: [ISO 13374-2 preview](https://webstore.ansi.org/preview-pages/ISO/preview_ISO+13374-2-2007.pdf)
