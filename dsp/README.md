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

```bash
python -m dsp.calibrate_misfire     # ~2.5 min
python -m pytest dsp/tests
```

