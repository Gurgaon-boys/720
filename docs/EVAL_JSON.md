# eval.json: evidence panel results (v0.1)

**Producer:** Diljit, `python -m eval.build_eval_json` → `eval/eval.json` and a copy at `frontend/public/eval.json`.
**Consumer:** Livana, `frontend/src/api/evalSource.js` → `EvidencePanel.jsx`, checked by `frontend/src/lib/evidence.js:checkResult`.

The shape is exactly `frontend/src/data/mockEval.js` (Livana's proposal), now filled from results files. Rebuild after any new result.

## Rules

- Every number is copied from a results file in `eval/` made by code in this repo. The builder computes nothing beyond unit conversion.
- A result that doesn't exist yet stays `null`, and the panel shows it as "incomplete". No placeholder numbers.
- Every result carries `column` (`A` simulated · `B` injected · `C` real), `split`, its n (`n`, `n_files` or `exposure_hours`) and `source` (the results file).
- `placeholder: false` and `source_commit` say which commit produced the file.

## Result kinds

| kind | required fields (panel shows nothing until all are present) | extra fields we add |
|---|---|---|
| `rate` | `events`, `exposure_hours`, `per_hour`, `upper_95_per_hour` (exact one-sided 95% Poisson; 0 events → ~3/T) | `n_files`, `unit`, `structurally_limited`, `note`, `source` |
| `proportion` | `successes`, `n`, `value`, `ci_95` (Wilson 95%) | `source` |
| `metric` | `value`, `n` | `unit`, `ci_95`, `task`, `model`, `source` |

## Where each section comes from

| Section | Source file | Status |
|---|---|---|
| `false_positives.headline` | `eval/obd_carobd.json` (OBD branch v1, pre-registered; held-out carOBD files) | anomaly flags, advisories, alarms filled; classifier false faults null (no OBD classifier yet) |
| `false_positives.secondary` | AudioSet P3 audio pipeline (to do) | null until vetting + audio pipeline |
| `operator_projection` | — | null: alarms are structurally 0 without a classifier witness, so a fleet projection would mislead |
| `column_a` | `eval/table_a_misfire.json` (simulator misfire vs healthy) | filled when that file exists |
| `column_b` | injected knock / valve tick (to do; needs P2 + injection generator) | null |
| `column_c.obd_branch` | `eval/obd_carobd.json`: share of held-out 30 s windows flagged, Wilson 95% | filled |

Events in `rate` results are **episodes**: runs of consecutive 30 s windows (15 s hop) in the same state, so overlapping windows that see one event count once.
