# dsp/

**Owner:** Dipen

Signal processing (plan section 8):

- Windowing: 4 complete engine cycles, 50% overlap
- Order tracking (resample to crank angle) on simulator and injected data only. Real AudioSet clips skip it.
- Time-synchronous average per cylinder over 720°, which gives the 720° fingerprint
- FFT, STFT, log-mel and order spectrum
- Feature vectors for SVM/ANN

**Physics rules (the rule-layer witnesses). Every rule has two versions:**

| Rule | `crank_angle` (simulator / injected) | `firing_event` (real audio) |
|---|---|---|
| Misfire | Half-order energy ratio + per-event crank roughness | Missing or weak pulse in the sequence of detected firing pulses |
| Knock | Knock-band energy just after TDC vs a noise reference | Knock-band burst after each detected firing pulse |
| Valve tick | Cam-order envelope peak gated to the valve-closing angle | Repeating impact at a stable offset from firing events |
| Injector clog | Weak-cylinder crank residual + O2 ripple + trim shift | Not available (simulator only) |

Every rule that fires reports `rule_basis` (see `contract/`).

## Built so far (L1)

| File | What |
|---|---|
| `windows.py` | `CycleWindower`: 4 whole cycles, 50% overlap, cut on crank angle (cycle boundary = cyl 1 firing TDC), incremental over simulator chunks |
| `features.py` | Tooth-time crank speed at nominal tooth angles; **half-order ratio** (0.5 / 2nd-order amplitude of crank speed); **per-cylinder roughness** (speed drop from segment [TDC−60°, TDC+120°) to the next, minus its neighbours' mean, worst firing in the window); **720° fingerprint** (mic RMS per 2° bin); rpm and regime |
| `rules.py` | `misfire_half_order` (crank-angle version, `rule_basis: crank_angle`, location = largest roughness); `misfire_firing_event` stub for real audio |
| `calibrate_misfire.py` | Threshold calibration on **healthy** simulator runs only → `models/thresholds/misfire_half_order.json` |

**Calibration (sim v1, healthy only):** 10 engines × 3 speeds (800/1500/2500 rpm) × 40 s = 7,942 windows; threshold = 1.25 × the 99.9th percentile (half-order ratio 0.395, roughness 0.0082). **Held out:** 4 other engines, 3,177 windows, **0 false positives** (95% upper bound 9.4×10⁻⁴ per window, target 10⁻³). By construction about 0.1% of the calibration windows sit above the threshold (worst 0.51). Steady speed only; no transients yet.

**Detection (sim, idle, misfire cyl 3 p = 0.5):** fires on cyl 3 in the 2nd window that holds a misfire, 1.24 s after injection (a fault starts at the next unplanned cycle, and a 4-cycle window lasts 0.6 s at idle). A single misfire in a 4-cycle window often stays under the half-order threshold while roughness already flags it: a candidate second firing condition.

## Sim v2 (D66–D75)

The L1 numbers above are sim v1 and kept for the record (`models/thresholds/misfire_half_order_v1.json`). On sim v2 (reciprocating inertia, regimes on), the misfire rule changed:

- **Feature:** `half_order_energy` = 0.5-order amplitude of crank speed × mean rpm / 10⁶, replacing the half-order *ratio*. Once inertia torque is modelled, the 2nd order cancels near 2,000 rpm and the ratio blows up on healthy engines: its healthy maximum is 0.56 at idle, 38 at mid speed and 108 at high speed. `half_order_ratio` is still computed and shown as `features_summary.half_order_ratio_v1`.
- **Per-rpm-band thresholds** for idle (<1,100), mid (1,100–2,000) and high (2,000–3,000 rpm): 1.25 × the healthy 99.9th percentile, on calibration engines 0–11 at 5/25/45 N·m with regimes on; validated on engines 50–53.

| Band | half_order_energy | roughness | Validation FP |
|---|---|---|---|
| idle | 0.00898 | 0.0235 | 0 / 3,614 |
| mid | 0.01029 | 0.0094 | 0 / 6,514 |
| high | 0.01131 | 0.0113 | 1 / 10,788 |

- The rule does not fire when a crank dropout leaves too few teeth (`crank_valid`).
- `window_features` also returns `map_kpa`; `CycleWindow` carries `accel`.

**Evidence (`eval/`, evaluation engines only, regimes on):**

- `healthy_soak.py`: 10.0 h on unseen engines, 269,761 windows. Fired windows 3.9/h, ADVISORY 3.6/h (Wilson 2.6–5.0), ALARM 0 (structural: no classifier yet).
- `robustness_v2.py`: detection, localisation, SNR and crank-noise sweeps, and latency. Results are in `eval/results/robustness_v2.json` and `docs/figures/robustness_v2.png`.

The SNR sweep is flat by construction, because the rule reads the crank, not the mic.

```bash
python -m dsp.calibrate_misfire     # v2: ~6 min, 12 engines x 9 conditions, Pool
python -m eval.healthy_soak         # ~30 min
python -m eval.robustness_v2        # ~15 min
python -m pytest dsp/tests
```


## L2 physics rules (D77–D79)

| Rule | Basis | Feature | Threshold (calibration) |
|---|---|---|---|
| `misfire_half_order` | crank angle | half-order energy, **quadratic crank-speed trend removed first** | per rpm band 0.00553 / 0.00564 / 0.00739, healthy sim calibration engines, val 0 FP |
| `knock_band_energy` | crank angle | 5–15 kHz accel *ringing* energy in TDC + retard + 0…30° vs the sensor's quiet TDC+90…150°, minus the cylinder's healthy baseline | +5.59 / +5.62 / +6.12 dB per band, healthy sim calibration engines |
| `trim_drift` | **obd** | 30 s mean of (1+STFT)(1+LTFT) − 1; not while the ECU pulls spark | 9.71 % from 90 carOBD calibration files (6,094 windows); sim healthy check 0/360 |

`obd_window.py` builds the 30 s OBD windows (simulator rows or carOBD rows, same code). `rules.all_rules()` returns the three witnesses for one window.

```bash
python -m dsp.calibrate_misfire && python -m dsp.calibrate_knock && python -m dsp.calibrate_trim
```
