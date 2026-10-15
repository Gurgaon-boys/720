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
