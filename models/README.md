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

`thresholds/`: calibrated rule thresholds with their calibration metadata (committed; JSON, small). `misfire_half_order.json` (v2: `half_order_energy`, per rpm band, sim v2 with regimes) comes from `python -m dsp.calibrate_misfire`. `misfire_half_order_v1.json` is the L1 sim-v1 file (half-order ratio), kept for comparison.

## L2: fast classifier, healthy model, two-witness ALARM (`feature/dipen-classifier`)

| File | What |
|---|---|
| `features.py` | `StreamFeatures`: sim chunks → 4-cycle windows → 36 hand features (`FEATURE_NAMES`); used by training **and** the live source |
| `make_dataset.py` | sim 2.1 dataset, split by engine (train 700–759, val 760–774, test 775–789), hard regimes on, valve_tick never generated; resumable npz in `data/generated/sim_v2/` (git-ignored); manifest `data/splits/sim_v2_dataset.json` (committed) |
| `classifier.py` | RBF SVM (C 1, sigmoid-calibrated on folds grouped by run) + cylinder (logistic regression per fault) + healthy model (Mahalanobis per rpm band, Ledoit-Wolf) + open-set check per class → `checkpoints/fast_v1.pkl` (git-ignored), `fast_v1.json` (committed) |
| `witness.py` | adapter into the rule layer: `models[]` entry, `Evidence.classifier_location`, `anomaly.score` (limit at 0.5); abstains below p 0.8 or outside the predicted class |

Dataset windows: train 29,707 healthy / 14,722 misfire / 2,718 knock / 15,264 clog (240 runs, 60 engines), plus 120 healthy-only runs; val 60 runs + 60 healthy-only; test 60; held-out 3000–3500 rpm 60 (test engines).
Val: macro-F1 0.942, localisation 0.950. 2 h soak: ADVISORY 17.0/h, ALARM 0. Details and caveats: DECISIONS D76–D87.

```bash
python -m models.make_dataset     # ~20 min (10 workers; S720_WORKERS)
python -m models.classifier       # ~1 min
python -m eval.soak_l2            # 2 h healthy soak, ~4 min
```

## Stage 2 evidence (D88–D94)

| Script | Output | What |
|---|---|---|
| `eval/column_c.py` | `eval/results/column_c_detail.json` | OBD branch on carOBD held-out files (read once): false positives per hour at every level |
| `eval/soak_l2.py --range soak_fresh2` | `eval/results/soak_l2_fresh2.json` | 2 h healthy soak on engines never used anywhere |
| `eval/robustness_v2.py` | `eval/results/robustness_v2.json/png` | misfire detection, localisation, latency on sim 2.1 |
| `models/make_spectra.py` | `data/generated/sim_v2_spec/`, `sim_v2_openset/` | CNN inputs (crank-angle spectra) and valve-tick open-set runs |
| `eval/model_comparison.py` | `eval/results/model_comparison.json` | Baseline / SVM / ANN / CNN on test, leakage gap, open set |
| `eval/shortcut_case.py` | `eval/results/shortcut_case.json` | the "short OBD history" shortcut test (not reproduced, D90) |
| `eval/clog_sweep.py` | `eval/results/clog_sweep.json` | clog detectability at the committed operating point |
| `eval/misfire_counter.py` | `eval/results/misfire_counter.json` | OBD-II-style misfire counter (stage 3) |
| `eval/build_eval_json.py` | `eval/results/column_a.json`, `column_c.json`, `eval.json` → `frontend/public/eval.json` | the evidence panel's file |

`thresholds/`: calibrated rule thresholds with their calibration metadata (committed; JSON, small). `misfire_half_order.json` comes from `python -m dsp.calibrate_misfire`.

## OBD branch v1 (`models/obd/`, Diljit)

`python -m models.obd.run_carobd` (~25 s) calibrates on carOBD calibration files (fit / validation by file) and evaluates once on the 39 held-out files → `models/thresholds/obd_branch.json`, `eval/obd_carobd.json`. Every result goes through the real `backend/rule_layer.py`. No OBD classifier yet (needs simulator OBD channels), so ALARM is structurally impossible on carOBD; report anomaly flags, UNCLASSIFIED and ADVISORY rates. See DECISIONS D55–D58.
