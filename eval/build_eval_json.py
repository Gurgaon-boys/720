"""Assemble the evidence panel's eval.json (frontend/docs/EVAL_JSON.md shape, PROPOSED) from result files
produced by code in this repo. Nothing here computes a result; it only copies numbers with their n,
column and split.

  eval/results/column_a.json   {"column_a": ...}      from model_comparison.json, clog_sweep.json, l2_demo.json
  eval/results/column_c.json   {"false_positives": {"headline"}, "column_c": ...}   from column_c_detail.json
  eval/results/eval.json       the whole file (column B and the audio false positives: null, not measured)
  frontend/public/eval.json    a copy, so the dashboard's evidence panel loads it

Usage:  python -m eval.build_eval_json
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "eval" / "results"


def metric(value, n, split, unit=None, note=None, ci=None, column="A"):
    return {"kind": "metric", "value": value, "unit": unit, "n": n, "ci_95": ci, "column": column, "split": split, "note": note}


def column_c_part() -> tuple[dict, dict]:
    """(false_positives.headline, column_c) from eval/column_c.py's result; writes eval/results/column_c.json."""
    cc = json.loads((RES / "column_c_detail.json").read_text())
    o = cc["overall"]
    nf = o["files"]

    def with_files(r):
        return None if r is None else {**r, "n_files": nf}
    headline = {"dataset": "carOBD held-out files", "anomaly_flags": with_files(o["anomaly_flags"]),
                "advisories": with_files(o["advisories"]), "classifier_false_faults": None,
                "alarms": with_files(o["alarms"])}
    column_c = {"obd_branch": with_files(o["unclassified"]),
                "by_file_type": {ty: {k: with_files(s[k]) for k in ("anomaly_flags", "advisories", "unclassified", "alarms")}
                                 for ty, s in cc["by_type"].items()},
                "rpm_transition_check": o["transition_check"]}
    (RES / "column_c.json").write_text(json.dumps({"false_positives": {"headline": headline}, "column_c": column_c,
                                                   "notes": {"classifier_false_faults": "no classifier on carOBD: not measured",
                                                             "obd_branch": "UNCLASSIFIED episodes from the OBD healthy model (no fault named)"}},
                                                  indent=1))
    return headline, column_c


def main():
    mc = json.loads((RES / "model_comparison.json").read_text())
    cs = json.loads((RES / "clog_sweep.json").read_text())
    demo = json.loads((ROOT / "docs" / "figures" / "l2_demo.json").read_text())
    n_test = mc["test_windows"]
    grouped = "TEST split: engines 775-789, grouped by engine"

    gap = mc["split_gap_svm_macro_f1"]
    split_gap = {k: metric(round(gap[k]["mean"], 4), gap[k]["n"], f"{k} split" + (" by window" if k == "random" else " by engine"),
                           unit="macro-F1 (SVM)", note=f"mean of 3 seeds, sd {gap[k]['sd']:.4f}") for k in ("random", "grouped")}
    comp = [{"model": m, "result": metric(round(v["macro_f1"]["mean"], 4), v["n"], grouped, unit="macro-F1",
                                          note=f"localisation {v['localisation_accuracy']['mean']:.3f}; "
                                               + ("deterministic" if m == "Baseline" else "3 seeds")),
             "sd_across_seeds": None if m == "Baseline" else round(v["macro_f1"]["sd"], 4)}
            for m, v in mc["models"].items()]
    hm = mc["open_set_healthy_model_vs_healthy"]

    def oc(key, split):
        r = hm[key]
        n = r["n_unknown"] + r["n_known"]
        note = (f"healthy-model Mahalanobis; unknown n={r['n_unknown']} vs healthy n={r['n_known']}; at the VAL threshold "
                f"TPR {r['tpr_at_val_threshold']:.3f}, FPR {r['fpr_at_val_threshold']:.3f}")
        return {"auroc": metric(round(r["auroc"], 4), n, split, unit="AUROC", note=note),
                "pauc": metric(round(r["pauc_fpr_0.1"], 4), n, split, unit="pAUROC, FPR <= 0.1 (McClish)", note=note)}
    held = {"valve_tick": oc("valve_tick", "held-out class: valve_tick (15 test engines) vs healthy test windows"),
            "knock": oc("knock", "knock (never seen by the healthy model) vs healthy test windows")}
    s50, s90 = cs["smallest_clog_alarm_rate_ge_0.5"], cs["smallest_clog_alarm_rate_ge_0.9"]
    k = f"{s50:.2f}" if s50 is not None else None
    clog = metric(s50, cs["per_severity"][k]["n_runs"] if k else None, cs["split"], unit="clog severity s (fuel lost)",
                  note=f"smallest s with a two-witness injector_clog ALARM in >= 50 % of runs "
                       f"({cs['per_severity'][k]['alarm']['successes']}/{cs['per_severity'][k]['n_runs']} at s = {s50}); "
                       f"no s reached 90 % (s = {s90}). At s = 0.6 lean misfire takes over (misfire rule, UNCLASSIFIED)" if k else None)
    rb = json.loads((RES / "robustness_v2.json").read_text())
    lat = rb["latency"]
    li = lat["p1.0_idle"]
    mis = next(f for f in demo["faults"] if f["fault"] == "misfire")
    latency = metric(round(li["median_ms"]), li["n"], f"robustness engines (unseen), sim {rb['sim_version']}", unit="ms",
                     note=(f"first true misfire -> ADVISORY window end, p 1.0, idle (the slowest band); median, p95 "
                           f"{li['p95_ms']:.0f} ms. mid {lat['p1.0_mid']['median_ms']:.0f}, high {lat['p1.0_high']['median_ms']:.0f}, "
                           f"held-out rpm {lat['p1.0_held-out']['median_ms']:.0f} ms. Live take: POST /inject -> ALARM "
                           f"{1000 * mis['time_to_state_s']:.0f} ms (n = 1)"))
    column_a = {"split_gap": split_gap, "model_comparison": comp, "held_out_class": held,
                "injector_detectability": {"smallest_clog_at_fixed_far": clog}, "latency_ms": latency}

    headline, column_c = column_c_part()

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    full = {"eval_version": "proposed-0.1", "placeholder": False,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source_commit": commit,
            "false_positives": {"headline": headline, "secondary": None},
            "column_a": column_a, "column_b": None, "column_c": column_c,
            "notes": {"classifier_false_faults": "no classifier runs on carOBD (OBD branch = trim rule + OBD healthy model): not measured",
                      "secondary": "AudioSet P3 audio branch not built yet: not measured",
                      "column_b": "injected-on-real-audio results not produced yet",
                      "obd_branch": "column C row = UNCLASSIFIED episodes from the OBD healthy model (no fault named)"}}
    (RES / "column_a.json").write_text(json.dumps({"column_a": column_a}, indent=1))
    (RES / "eval.json").write_text(json.dumps(full, indent=1))
    shutil.copy(RES / "eval.json", ROOT / "frontend" / "public" / "eval.json")
    print("wrote column_a.json, column_c.json, eval.json and frontend/public/eval.json")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--column-c"]:
        column_c_part()
        print("wrote eval/results/column_c.json")
    else:
        main()
