# `eval.json` shape (PROPOSED, not agreed with Diljit)

**Status: proposal from the frontend.** This is the shape the dashboard's evidence panel reads. It becomes the contract only once Diljit agrees; until then field names may change. The current stand-in is `src/data/mockEval.js`, which has this shape with every value `null` and `"placeholder": true`.

Where it goes: `frontend/public/eval.json` (copied into `dist/`, read once at load; no network needed on the demo laptop). If the file is missing, the panel shows the placeholder structure and says so.

## Rules the panel enforces

1. **Every result carries `column` and `split`.** `column` is `A` (simulated), `B` (injected: knock and valve tick only) or `C` (fully real). Tables are never mixed. A result without `column` or `split` is shown as *incomplete*, not as a number.
2. **Every per-hour rate carries its event count and exposure.** A `rate` needs `events` (integer count) **and** `exposure_hours`. If either is `null` or missing, the panel shows *incomplete* instead of the rate **and** its upper bound.
3. **Every other number carries its `n`.** A `proportion` also needs `successes`; a `metric` needs `n`.
4. **`null` means not measured.** It is shown as *incomplete*, never as 0.
5. **Column B holds only `knock` and `valve_tick`.** Any other key there is shown as an error.
6. **The operator line is a projection.** It is shown only when both `value` and `assumptions` are supplied, and is labelled as a projection, not an observed fleet result.

## Upper-bound method for rates

For `events = k` observed over `exposure_hours = T`:

- point rate = `k / T` per hour
- **one-sided 95% upper bound = `chi2.ppf(0.95, 2·(k + 1)) / (2·T)` per hour** (exact Poisson / Garwood bound). For `k = 0` this is `2.996 / T` (the "rule of three").

In Python: `scipy.stats.chi2.ppf(0.95, 2 * (k + 1)) / (2 * T)`. Use `bound_method: "poisson_exact_one_sided_95"` so the method travels with the number. The panel displays the supplied values; it does not recompute them.

Detection rates (proportions) carry **Wilson 95% intervals** (`ci_95: [lo, hi]`, `ci_method: "wilson_95"`), as `eval/README.md` requires.

## Result kinds

The numbers below are **made-up format examples, not results**. They are only internally consistent (the bound and interval follow from the counts).

```jsonc
// per-hour rate
{ "kind": "rate", "events": 3, "exposure_hours": 41.5,
  "per_hour": 0.0723, "upper_95_per_hour": 0.1868, "bound_method": "poisson_exact_one_sided_95",
  "n_files": 12, "column": "C", "split": "held-out files" }

// proportion (e.g. detection rate)
{ "kind": "proportion", "successes": 87, "n": 100, "value": 0.87,
  "ci_95": [0.790, 0.922], "ci_method": "wilson_95", "column": "A", "split": "grouped by run" }

// any other metric (AUROC, pAUC, latency, smallest detectable clog …)
{ "kind": "metric", "value": 0.91, "unit": null, "n": 400,
  "ci_95": null, "column": "A", "split": "held-out class", "note": null }
```

## File

```jsonc
{
  "eval_version": "proposed-0.1",
  "placeholder": false,                  // true only for the frontend's stand-in
  "generated_at": "2026-10-16T12:00:00Z",
  "source_commit": "abc1234",            // the repo commit that produced it (every number traces to code)

  "false_positives": {
    "headline":  { "dataset": "carOBD held-out files",
                   "anomaly_flags": <rate>, "advisories": <rate>,
                   "classifier_false_faults": <rate>, "alarms": <rate> },
    "secondary": { "dataset": "AudioSet P3 (vetted car idle)",
                   "anomaly_flags": <rate>, "advisories": <rate>,
                   "classifier_false_faults": <rate>,
                   "alarms": <rate + "structurally_limited": true> }
  },

  // optional; shown only with both value and assumptions
  "operator_projection": {
    "engines": 50, "value": <number>, "unit": "alarms per hour",
    "basis": "upper_95_per_hour of false_positives.headline.alarms × engines",
    "assumptions": "engines independent; same false-alarm rate as the held-out carOBD files",
    "column": "C", "split": "held-out files"
  },

  "column_a": {
    "split_gap":        { "random": <metric>, "grouped": <metric> },
    "model_comparison": [ { "model": "Baseline", "result": <metric>, "sd_across_seeds": null }, … ],
    "held_out_class":   { "valve_tick": { "auroc": <metric>, "pauc": <metric> },
                          "knock":      { "auroc": <metric>, "pauc": <metric> } },
    "injector_detectability": { "smallest_clog_at_fixed_far": <metric> },
    "latency_ms": <metric>
  },
  "column_b": { "knock": <proportion>, "valve_tick": <proportion> },
  "column_c": { "obd_branch": <metric> }
}
```

Open questions for Diljit: which split names to use; whether `n_files` is enough context for carOBD (or per-file hours too); what `sensor_ablation` and `score_vs_severity` should look like (left out until there is data to chart).
