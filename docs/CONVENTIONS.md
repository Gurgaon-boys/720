# 720 conventions

These come from `720_Final_Plan.md` (v1.4), which wins wherever this file and the plan disagree. To change a convention, add an entry to `DECISIONS.md` first.

## Signals

- **Audio:** 48 kHz, mono, float32 everywhere, for both the simulator and real clips. Real clips stay at their native rate. If a mirror only offers 16 kHz, say so: the knock check on those clips is band-limited.
- **Windows:**
  - Simulator and injected data: **4 complete engine cycles, 50% overlap**
  - OBD branch: **30 s, 50% overlap**
- **Engine:** 4-cylinder, 4-stroke, firing order **1-3-4-2**, one firing every 180°. Cylinders are numbered 1–4.
- Real OBD is shown at its true rate (~1 Hz). Fast values internal to the simulator are badged as simulated.

## Classes and results

- **Trained classes:** `healthy`, `misfire`, `knock`, `injector_clog`
- **`valve_tick` is held out of ML training.** It tests "unknown", and only the physics rule names it.
- **Result columns are never mixed:**
  - **A** simulated
  - **B** injected onto real audio, **knock and valve tick only** (additive faults)
  - **C** fully real
- Misfire and injector clog are never injected onto real audio.
- No 720° fingerprint and no cylinder localisation on real clips.

## Splits

- Always split **by simulation run**, **by AudioSet video ID** or **by carOBD file**. **Never by window.**
- AudioSet pools P1 / P2 / P3 and the carOBD calibration / held-out split are written to `data/splits/` with a timestamp at H0, before any training.
- Thresholds are fixed on validation data, never on test data.

## File naming

| Kind | Pattern | Example |
|---|---|---|
| Simulator run | `sim_<runid>_<fault>_c<cyl>_s<severity>.npz` | `sim_0412_misfire_c3_s0.12.npz` |
| Real audio | `real_<videoid>.wav` | `real_dQw4w9WgXcQ.wav` |
| carOBD | `carobd_<file>.csv` | `carobd_idle01.csv` |

`<fault>` is one of `healthy`, `misfire`, `knock`, `injector_clog` or `valve_tick`. Healthy runs use `c0_s0`.

Data and checkpoints are git-ignored. Only scripts and split lists are committed.

## Where things live

- **Rule layer:** `backend/rule_layer.py` (the only one; there is no `backend/app/`). Physics rules: `dsp/rules.py`. Thresholds: `models/thresholds/*.json`.
- **Seeds:** calibration, validation and evaluation engine-seed ranges are in `eval/common.py` (`SEEDS`), disjoint and asserted. Never tune on evaluation seeds.
- **Held out:** the `valve_tick` fault, 3000–3500 rpm and the `"test"` parameter region. `sim/guard.py` refuses them in calibration and training.

## Git

- Branches: `feature/<owner>-<thing>` (e.g. `feature/dipen-crank-model`)
- PRs go to `main`. **Merge only code that runs**, so `main` is always demoable.
- Any change to `contract/` bumps `schema_version` and must pass `python contract/validate.py`.

## Evidence

- **Every number on a slide must trace to a file in `eval/` produced by code in this repo.**
- Every number states its n, data source and split. Detection rates carry Wilson 95% intervals. Zero-event rates use the rule of three (about 3/T per hour).
- False positives are reported at every level: anomaly flags, advisories, classifier false faults and alarms.
- Open the cited source before any figure from the plan goes on a slide.
