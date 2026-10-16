# models/

**Owner:** Dipen (design) / Diljit (runs experiments)

Plan sections 9, 10 and 10.1:

- **Trivial baseline:** RMS threshold + half-order ratio threshold
- **SVM** (RBF, class-weighted) and **ANN + softmax** on the engineered feature vector
- **CNN** on a two-channel order-normalised log spectrogram (mic + accelerometer)
- **Healthy model:** Mahalanobis distance per RPM band; maximum logit as the open-set score
- **OBD branch:** 30 s windows, Mahalanobis per RPM band, an OBD-only classifier trained on simulator OBD
- *Stretch:* fusion CNN with sensor dropout

Trained classes: `healthy`, `misfire`, `knock`, `injector_clog`. **`valve_tick` is held out.**
All models use identical grouped splits (by run, by source clip) across several seeds. Thresholds are fixed on validation data, never on test data.
Checkpoints (`*.pt`, `*.pkl`, `*.onnx`) are not committed.

`thresholds/`: calibrated rule thresholds with their calibration metadata (committed; JSON, small). `misfire_half_order.json` comes from `python -m dsp.calibrate_misfire`.

## OBD branch v1 (`models/obd/`, Diljit)

`python -m models.obd.run_carobd` (~25 s) calibrates on carOBD calibration files (fit / validation by file) and evaluates once on the 39 held-out files → `models/thresholds/obd_branch.json`, `eval/obd_carobd.json`. Every result goes through the real `backend/rule_layer.py`. No OBD classifier yet (needs simulator OBD channels), so ALARM is structurally impossible on carOBD; report anomaly flags, UNCLASSIFIED and ADVISORY rates. See DECISIONS D55–D58.
