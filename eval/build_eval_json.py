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
        "classifier_false_faults": classifier_false_faults(split),
        "alarms": rate_from({k: L["5_alarms"][k] for k in ("count", "hours", "per_hour", "upper95_per_hour")},
                            h["files"], split, src, structurally_limited=True, note=L["5_alarms"]["note"]),
    }


def classifier_false_faults(split: str) -> dict:
    c = load("obd_classifier_carobd.json")
    if c is None:
        return null_rate("C", split, note="not available: no OBD classifier yet")
    r = c["carobd_heldout"]
    return rate_from(r["levels"]["4_classifier_false_faults"]["episodes"], r["files"], split,
                     "eval/obd_classifier_carobd.json", unit="episodes (runs of consecutive 30 s windows)",
                     note="OBD-only classifier trained on SIMULATED OBD (D105, D106), run on real held-out carOBD: "
                          "a sim-to-real test, NOT part of the deployed OBD branch, whose other levels are shown here. "
                          f"With it in the chain: ADVISORY {r['levels']['3_advisories']['episodes']['per_hour']}/h, "
                          f"ALARM {r['levels']['5_alarms']['per_hour']}/h.")


def secondary_fp() -> dict:
    a = load("audio_p3.json")
    split = "held-out pool P3 (by video id)"
    if a is None:
        return {"dataset": "AudioSet P3 (vetted car idle; pending)",
                "anomaly_flags": null_rate("C", split), "advisories": null_rate("C", split),
                "classifier_false_faults": null_rate("C", split),
                "alarms": null_rate("C", split, structurally_limited=True)}
    p, L, src = a["P3"], a["P3"]["levels"], "eval/audio_p3.json"
    ep = L["1_anomaly_flags"]["episodes"]
    unit = "episodes (runs of consecutive 1 s windows)"
    as_rate = lambda r, **kw: rate_from({k: r[k] for k in ("count", "hours", "per_hour", "upper95_per_hour")},
                                        p["clips"], split, src, unit=unit, **kw)
    return {
        "dataset": f"AudioSet P3, single-rater vetted ({p['clips']} clips, {p['hours'] * 60:.1f} min)",
        "anomaly_flags": as_rate(ep),
        "advisories": as_rate(L["3_advisories"], structurally_limited=True, note=L["3_advisories"]["note"]),
        "classifier_false_faults": null_rate("C", split, note=L["4_classifier_false_faults"]),
        "alarms": as_rate(L["5_alarms"], structurally_limited=True, note=L["5_alarms"]["note"]),
    }


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


def column_b() -> dict:
    t = load("table_b.json")
    split = "5 folds by source clip (vetted P2, >= 8 kHz)"
    if t is None:
        return {"knock": null_proportion("B", split), "valve_tick": null_proportion("B", split)}
    h, src = t["headline"], "eval/table_b.json"
    prop = lambda d, **kw: {"kind": "proportion", "successes": d["k"], "n": d["n"], "value": d["rate"],
                            "ci_95": d["wilson95"], "ci_method": "wilson_95", "column": "B", "split": split,
                            "source": src, **kw}
    cond = f"injected at {h['level_db']:+.0f} dB re clip 5-15 kHz RMS, severities pooled, windows"
    return {"knock": prop(h["knock_detection"], measure="detection", condition=cond,
                          false_positive_on_healthy=h["knock_false_positive_on_healthy"]["rate"]),
            "valve_tick": prop(h["valve_tick_unknown"], measure="sent to unknown (held-out class)", condition=cond,
                               mislabelled_knock=h["valve_tick_mislabelled_knock"]["rate"])}


def column_c() -> dict:
    obd = load("obd_carobd.json")
    if obd is None:
        return {"obd_branch": null_metric("C", "held-out carOBD files")}
    w = obd["heldout"]["levels"]["1_anomaly_flags"]["windows"]
    out = {}
    a = load("audio_p3.json")
    if a is not None:
        d = a["knock_check"]["detection"]
        out["knock_check"] = {"kind": "proportion", "successes": d["k"], "n": d["n"], "value": d["rate"],
                              "ci_95": d["wilson95"], "ci_method": "wilson_95", "column": "C",
                              "split": "knock_check pool (single-rater agreed detonation, >= 15 kHz)",
                              "detector": a["knock_check"]["detector"], "source": "eval/audio_p3.json"}
    return {**out, "obd_branch": {"kind": "metric", "value": w["rate"], "unit": "fraction of 30 s windows flagged (healthy data)",
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
        # alarms are structurally zero without a classifier witness, so a fleet projection would mislead
        "operator_projection": {"engines": 50, "value": None, "unit": "alarms per hour", "basis": None,
                                "assumptions": None, "column": "C", "split": "held-out files"},
        "column_a": column_a(),
        "column_b": column_b(),
        "column_c": column_c(),
        "notes": {"column_a": "sim 2.1 four-class results (eval/results/column_a.json, Dipen, DECISIONS D90-D94); the sim v1 "
                              "misfire-only slice (eval/table_a_misfire.json, D63) is an earlier result",
                  "column_c": "headline = the pre-registered OBD branch v1 (eval/obd_carobd.json, D55-D58). A second, "
                              "later read of the same held-out files with a different OBD model (eval/results/column_c.json) "
                              "is a secondary analysis and not on the panel"},
    }
    OUT.write_text(json.dumps(d, indent=2) + "\n")
    FRONTEND_COPY.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OUT, FRONTEND_COPY)
    print(f"Wrote {OUT.relative_to(REPO)} and {FRONTEND_COPY.relative_to(REPO)} (commit {commit})")


if __name__ == "__main__":
    main()
