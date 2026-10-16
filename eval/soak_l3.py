"""L3 soak: before / after for every level, windows AND episodes, on identical recorded runs.

The healthy runs of a soak range are recorded once through the full live chain (eval/chain.py, same
engines and operating points as eval/soak_l2.py) and the real rule layer is replayed under:
  before    healthy-model thresholds from pooled val windows (models/fast_v1.json), UNCLASSIFIED after 1 window
  persist2  same thresholds, UNCLASSIFIED after 2 consecutive windows (the L3 rule-layer change alone)
  after     thresholds from engine-to-engine spread on validation engines (models/thresholds/healthy_model.json),
            UNCLASSIFIED after 2 windows: the committed configuration
Windows are 4-cycle windows (50 % overlap); an episode is a contiguous run of windows in that state.
Episode rates carry the exact one-sided 95 % Poisson bound (docs/EVAL_JSON.md); window rates keep the
Wilson-on-windows bound used by soak_l2 for comparability.

Writes eval/results/soak_l3_<fresh2|fresh3>.json.   Usage:  python -m eval.soak_l3 --range soak_fresh3
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.stats import chi2

from eval.chain import CACHE, LEVELS, levels, load_rec, record, replay, total
from eval.common import SEEDS, rate_per_hour
from models.witness import HEALTHY_THRESHOLDS
from sim import SIM_VERSION

ROOT = Path(__file__).resolve().parents[1]
WORKERS = int(os.environ.get("S720_WORKERS", 10))
RUNS, RUN_S = 40, 180.0
ALL_LEVELS = LEVELS + ("alarms_raised", "alarms_raised_startup")


def poisson(k: int, hours: float) -> dict:
    return {"events": int(k), "exposure_hours": round(hours, 4), "per_hour": k / hours,
            "upper_95_per_hour": float(chi2.ppf(0.95, 2 * (k + 1)) / (2 * hours)), "bound_method": "poisson_exact_one_sided_95"}


def configs() -> dict:
    after = json.loads(HEALTHY_THRESHOLDS.read_text())["bands"]
    return {"before": (None, 1), "persist2": (None, 2), "after": (after, 2)}


def summarise(recs: list[dict], th, uw) -> dict:
    per_run = [levels(replay(r, th, uw)) for r in recs]
    t = total(per_run)
    hours = sum(r["meta"]["run_s"] for r in recs) / 3600
    out = {"hours": hours, "windows": t["counted_windows"], "levels": {}}
    for lv in ALL_LEVELS:
        out["levels"][lv] = {"windows": rate_per_hour(t[lv]["windows"], t["counted_windows"], hours),
                             "episodes": poisson(t[lv]["episodes"], hours)}
    out["unclassified_episodes_per_run"] = [r["unclassified"]["episodes"] for r in per_run]
    return out


def _rec(job):
    return str(record(*job))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", default="soak_fresh3")
    args = ap.parse_args()
    paths = [CACHE / "soak" / args.range / f"run_{i:03d}.npz" for i in range(RUNS)]
    todo = [(args.range, i) for i, p in enumerate(paths) if not p.exists()]
    if todo:
        with Pool(WORKERS) as pool:
            pool.map(_rec, todo)
    recs = [load_rec(p) for p in paths]
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sim_version": SIM_VERSION,
           "label": "SIM 2.1", "column": "A", "split": f"{args.range} engines {SEEDS[args.range][0][0]}-{SEEDS[args.range][0][-1]} (unseen)",
           "runs": RUNS, "run_s_counted": RUN_S, "window": "4-cycle windows, 50 % overlap",
           "configs": {}, "runs_detail": [r["meta"] for r in recs]}
    for name, (th, uw) in configs().items():
        res["configs"][name] = {"healthy_thresholds": th or "checkpoint (pooled val windows)",
                                "unclassified_windows_required": uw, **summarise(recs, th, uw)}
    old = ROOT / "eval" / "results" / f"soak_l2_{args.range.replace('soak_', '')}.json"
    if old.exists():               # the recording must reproduce the earlier soak exactly
        o = json.loads(old.read_text())
        b = res["configs"]["before"]["levels"]
        same = {k: o["per_hour"][k2]["events"] == b[k]["windows"]["events"] for k, k2 in
                (("anomaly_flags", "anomaly_flags"), ("classifier_false_faults", "classifier_false_faults"),
                 ("physics_windows", "physics_windows"), ("advisory", "advisory"), ("unclassified", "unclassified"),
                 ("alarm", "alarm_windows"), ("advisory_persist2", "advisory_persist2"))}
        res["reproduces"] = {"file": old.name, "windows_equal": same}
        print("reproduces", old.name, same)
    out = ROOT / "eval" / "results" / f"soak_l3_{args.range.replace('soak_', '')}.json"
    out.write_text(json.dumps(res, indent=1))
    print(f"{args.range}: {RUNS} runs, {res['configs']['after']['hours']:.2f} h, {res['configs']['after']['windows']} windows")
    print(f"  {'level':24s} " + "  ".join(f"{c:>26s}" for c in res["configs"]))
    for lv in ALL_LEVELS:
        cells = []
        for c in res["configs"].values():
            w, e = c["levels"][lv]["windows"], c["levels"][lv]["episodes"]
            cells.append(f"{w['events']:5d} w / {e['events']:4d} ep ({e['per_hour']:5.2f}/h)")
        print(f"  {lv:24s} " + "  ".join(f"{x:>26s}" for x in cells))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
