"""Build eval.json for the dashboard's evidence panel from the committed results files.

    python -m eval.build_eval_json            # writes eval/eval.json and frontend/public/eval.json

Shape: docs/EVAL_JSON.md (= frontend/src/data/mockEval.js). Rules:
  * every number is copied from a results file in eval/ produced by code in this repo; nothing is
    computed here except unit conversions;
  * a result we don't have yet stays null, so the panel shows it as "incomplete";
  * every result carries its column (A simulated / B injected / C real), split, n and source file.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVAL = REPO / "eval"
OUT = EVAL / "eval.json"
FRONTEND_COPY = REPO / "frontend" / "public" / "eval.json"
EVAL_VERSION = "0.1"
BOUND = "poisson_exact_one_sided_95"


def load(name: str) -> dict | None:
    p = EVAL / name
    return json.loads(p.read_text()) if p.exists() else None


def null_rate(column: str, split: str, **extra) -> dict:
    return {"kind": "rate", "events": None, "exposure_hours": None, "per_hour": None, "upper_95_per_hour": None,
            "bound_method": BOUND, "n_files": None, "column": column, "split": split, **extra}


def null_metric(column: str, split: str, **extra) -> dict:
    return {"kind": "metric", "value": None, "unit": None, "n": None, "ci_95": None, "column": column,
            "split": split, **extra}


def null_proportion(column: str, split: str) -> dict:
    return {"kind": "proportion", "successes": None, "n": None, "value": None, "ci_95": None,
            "ci_method": "wilson_95", "column": column, "split": split}


def rate_from(summary: dict, n_files: int, split: str, source: str, **extra) -> dict:
    return {"kind": "rate", "events": summary["count"], "exposure_hours": summary["hours"],
            "per_hour": summary["per_hour"], "upper_95_per_hour": summary["upper95_per_hour"],
            "bound_method": BOUND, "n_files": n_files, "column": "C", "split": split, "source": source, **extra}


def headline_fp() -> dict:
    obd = load("obd_carobd.json")
    split = "held-out carOBD files (by file)"
    if obd is None:
        return {"dataset": "carOBD held-out files", **{k: null_rate("C", split) for k in
                ("anomaly_flags", "advisories", "classifier_false_faults", "alarms")}}
    h, L, src = obd["heldout"], obd["heldout"]["levels"], "eval/obd_carobd.json"
    unit = "episodes (runs of consecutive 30 s windows)"
    return {
        "dataset": f"carOBD held-out files ({h['files']} files, {h['hours']:.2f} h), OBD branch {obd.get('version', 'v1')}",
        "anomaly_flags": rate_from(L["1_anomaly_flags"]["episodes"], h["files"], split, src, unit=unit),
        "advisories": rate_from(L["3_advisories"]["episodes"], h["files"], split, src, unit=unit),
        "classifier_false_faults": null_rate("C", split, note=L["4_classifier_false_faults"]),
        "alarms": rate_from({k: L["5_alarms"][k] for k in ("count", "hours", "per_hour", "upper95_per_hour")},
                            h["files"], split, src, structurally_limited=True, note=L["5_alarms"]["note"]),
    }


def secondary_fp() -> dict:
    # AudioSet P3 results don't exist until vetting is finished and the audio pipeline runs on P3
    split = "held-out pool P3 (by video id)"
    return {"dataset": "AudioSet P3 (single-rater vetted car idle; pending)",
            "anomaly_flags": null_rate("C", split), "advisories": null_rate("C", split),
            "classifier_false_faults": null_rate("C", split),
            "alarms": null_rate("C", split, structurally_limited=True)}


def column_a() -> dict:
    # sim 2.1, four classes, test split grouped by engine (Dipen, eval/build_eval_l2.py); the earlier
    # sim v1 misfire-only Table A slice stays in eval/table_a_misfire.json and is named in the note below
    l2 = load("results/column_a.json")
    if l2 is not None:
        return l2["column_a"]
    t = load("table_a_misfire.json")
    gsplit = "grouped by run (identical across models)"
    models = ["Baseline", "SVM", "ANN", "CNN"]
    out = {
        "split_gap": {"random": null_metric("A", "random"), "grouped": null_metric("A", "grouped by run")},
        "model_comparison": [{"model": m, "result": null_metric("A", gsplit), "sd_across_seeds": None} for m in models],
        "held_out_class": {c: {"auroc": null_metric("A", "held-out class"), "pauc": null_metric("A", "held-out class")}
                           for c in ("valve_tick", "knock")},
        "injector_detectability": {"smallest_clog_at_fixed_far": null_metric("A", "grouped by run")},
        "latency_ms": null_metric("A", "live pipeline", unit="ms"),
    }
    if t is None:
        return out
    src, metric = "eval/table_a_misfire.json", t["metric"]
    task = f"{t['task']} ({metric}), sim {t['sim_version']}"
    for m in out["model_comparison"]:
        r = t["models"].get(m["model"])
        if r:
            m["result"] = {"kind": "metric", "value": r["grouped"]["mean"], "unit": metric, "n": r["grouped"]["n_test_windows"],
                           "ci_95": None, "column": "A", "split": gsplit, "task": task, "source": src}
            m["sd_across_seeds"] = r["grouped"]["sd"]
    gap = t.get("split_gap")
    if gap:
        for k, label in (("random", "random (by window)"), ("grouped", "grouped by run")):
            out["split_gap"][k] = {"kind": "metric", "value": gap[k]["mean"], "unit": metric, "n": gap[k]["n_test_windows"],
                                   "ci_95": None, "column": "A", "split": label, "model": gap["model"],
                                   "task": task, "source": src}
    return out


def operator_projection() -> dict:
    # carOBD alarms are structurally zero (no classifier on OBD), so the projection uses the simulated
    # third soak, where both witnesses run (Dipen, eval/alarm_budget.py, DECISIONS D100)
    ab = load("results/alarm_budget.json")
    if ab is None:
        return {"engines": 50, "value": None, "unit": "alarms per hour", "basis": None,
                "assumptions": None, "column": "C", "split": "held-out files"}
    p, sim = ab["operator_projection"], ab["healthy"]["sim"]
    return {"engines": p["engines"], "value": p["fleet_per_hour"], "upper_95": p["fleet_upper_95_per_hour"],
            "unit": "operator notifications per hour (ALARM + UNCLASSIFIED episodes)",
            "basis": f"{p['per_engine_operator_notifications']['events']} episodes in {sim['hours']:.2f} engine-h "
                     f"x {p['engines']} engines ({sim['label']}, {sim['split']}); exact Poisson 95 % upper "
                     f"{p['fleet_upper_95_per_hour']:.1f} per hour, above the ~12/h manageable guidance: too little "
                     f"exposure yet to bound a {p['engines']}-engine fleet",
            "guidance_per_operator_hour": p["guidance_per_operator_hour"], "assumptions": p["assumptions"],
            "column": "A", "split": sim["split"], "source": "eval/results/alarm_budget.json"}


def column_c() -> dict:
    obd = load("obd_carobd.json")
    if obd is None:
        return {"obd_branch": null_metric("C", "held-out carOBD files")}
    w = obd["heldout"]["levels"]["1_anomaly_flags"]["windows"]
    return {"obd_branch": {"kind": "metric", "value": w["rate"], "unit": "fraction of 30 s windows flagged (healthy data)",
                           "n": w["n"], "ci_95": w["wilson95"], "column": "C", "split": "held-out carOBD files (by file)",
                           "source": "eval/obd_carobd.json"}}


def main() -> None:
    commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True).strip()
    d = {
        "eval_version": EVAL_VERSION,
        "placeholder": False,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_commit": commit,
        "produced_by": "python -m eval.build_eval_json",
        "false_positives": {"headline": headline_fp(), "secondary": secondary_fp()},
        "operator_projection": operator_projection(),
        "column_a": column_a(),
        "column_b": {"knock": null_proportion("B", "grouped by source clip"),
                     "valve_tick": null_proportion("B", "grouped by source clip")},
        "column_c": column_c(),
        "notes": {"column_a": "sim 2.1 four-class results (eval/results/column_a.json, Dipen, DECISIONS D90-D94); the sim v1 "
                              "misfire-only slice (eval/table_a_misfire.json, D63) is an earlier result",
                  "column_c": "headline = the pre-registered OBD branch v1 (eval/obd_carobd.json, D55-D58). A second, "
                              "later read of the same held-out files with a different OBD model (eval/results/column_c.json) "
                              "is a secondary analysis and not on the panel",
                  "l3": "L3 (Dipen, D95-D101): UNCLASSIFIED needs 2 windows and the healthy-model threshold comes from "
                        "engine-to-engine spread on validation engines; the third untouched soak and the alarm budget are "
                        "in eval/results/soak_l3_fresh3.json and alarm_budget.json. The pre-registered carOBD headline "
                        "(OBD branch v1) is unchanged"},
    }
    OUT.write_text(json.dumps(d, indent=2) + "\n")
    FRONTEND_COPY.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OUT, FRONTEND_COPY)
    print(f"Wrote {OUT.relative_to(REPO)} and {FRONTEND_COPY.relative_to(REPO)} (commit {commit})")


if __name__ == "__main__":
    main()
