"""Healthy soak: false positives per HOUR at window, ADVISORY and ALARM level (plan section 13, star 4).

Simulated healthy engines only (sim v2, Regimes.eval() on), on evaluation seeds disjoint from
calibration. Main soak: train-region engines at 800-2800 rpm x 3 loads, >= 10 h. Held-out region:
test-region engines (heavier flywheel, shifted structural modes) at 3100/3400 rpm, reported apart.

Levels:
  window     windows where the misfire rule fired
  advisory   rule-layer transitions into ADVISORY (one event per episode, not per window)
  alarm      rule-layer transitions into ALARM. Structurally zero today: there is no classifier,
             so the second witness never exists. Reported, but it says nothing about the rule.

Writes eval/results/soak_v2.json.  Usage (repo root):  python -m eval.healthy_soak [--minutes 5]
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from dsp.rules import MISFIRE_HALF_ORDER, load_thresholds
from eval.common import SEEDS, eval_engine, rate_per_hour, rpm_band, run
from sim import SIM_VERSION
from sim.params import Regimes

OUT = Path(__file__).resolve().parent / "results" / "soak_v2.json"
MAIN_RPM = (800.0, 1300.0, 1800.0, 2300.0, 2800.0)
HELD_RPM = (3100.0, 3400.0)
LOADS = (5.0, 25.0, 45.0)


def job(args):
    kind, i, rpm, load, seconds = args
    params, seed = eval_engine(kind, i)
    wins = run(params, seed, rpm, load, seconds)
    states = [w.decision for w in wins]
    trans = lambda s: sum(1 for a, b in zip(["NORMAL"] + states, states) if b == s and a != s)  # noqa: E731
    return {"kind": kind, "engine": i, "rpm": rpm, "load": load, "seconds": seconds,
            "windows": len(wins), "valid": sum(w.valid for w in wins),
            "window_fp": sum(w.fired for w in wins), "advisory": trans("ADVISORY"),
            "alarm": trans("ALARM"), "unclassified": trans("UNCLASSIFIED"),
            "rpm_mean": float(np.mean([w.rpm for w in wins])) if wins else rpm,
            "compute_ms_median": float(np.median([w.compute_ms for w in wins])) if wins else None}


def summarise(rows):
    hours = sum(r["seconds"] for r in rows) / 3600
    n = sum(r["windows"] for r in rows)
    return {
        "hours": round(hours, 3), "windows": n, "valid_windows": sum(r["valid"] for r in rows),
        "window": rate_per_hour(sum(r["window_fp"] for r in rows), n, hours),
        "advisory": rate_per_hour(sum(r["advisory"] for r in rows), n, hours),
        "alarm": {**rate_per_hour(sum(r["alarm"] for r in rows), n, hours),
                  "note": "structurally zero: no classifier yet, so ALARM's second witness never exists"},
        "unclassified": rate_per_hour(sum(r["unclassified"] for r in rows), n, hours),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=5.0, help="simulated minutes per run")
    ap.add_argument("--engines", type=int, default=8, help="engines per main condition")
    ap.add_argument("--held-engines", type=int, default=4)
    a = ap.parse_args()
    secs = a.minutes * 60
    jobs = [("soak", i, rpm, load, secs) for rpm in MAIN_RPM for load in LOADS for i in range(a.engines)]
    jobs += [("held_out_region", i, rpm, load, secs) for rpm in HELD_RPM for load in LOADS for i in range(a.held_engines)]
    jobs.sort(key=lambda j: -j[2])                                  # slow (high rpm) runs first
    t0 = time.perf_counter()
    rows = []
    with Pool() as pool:
        for k, r in enumerate(pool.imap_unordered(job, jobs), 1):
            rows.append(r)
            if k % 20 == 0 or k == len(jobs):
                print(f"  {k}/{len(jobs)} runs  ({time.perf_counter() - t0:.0f} s)", flush=True)

    main_rows = [r for r in rows if r["kind"] == "soak"]
    held_rows = [r for r in rows if r["kind"] == "held_out_region"]
    by_band = {}
    for r in main_rows:
        by_band.setdefault(rpm_band(r["rpm"]), []).append(r)
    th = load_thresholds()
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sim_version": SIM_VERSION, "source": "simulated (healthy only)",
        "thresholds": {"file": MISFIRE_HALF_ORDER.name, "created_at": th["created_at"], "feature": th.get("feature")},
        "regimes": Regimes.eval().__dict__,
        "seeds": {k: {"engines": [SEEDS[k][0][0], SEEDS[k][0][-1]], "run_seed_offset": SEEDS[k][1]}
                  for k in ("soak", "held_out_region")},
        "grid": {"main_rpm": MAIN_RPM, "held_out_rpm": HELD_RPM, "loads_nm": LOADS, "minutes_per_run": a.minutes,
                 "engines_per_condition": a.engines, "held_out_engines_per_condition": a.held_engines},
        "main": summarise(main_rows),
        "main_by_rpm_band": {b: summarise(rs) for b, rs in sorted(by_band.items())},
        "held_out_region": summarise(held_rows),
        "compute_ms_per_window_median": float(np.median([r["compute_ms_median"] for r in rows if r["compute_ms_median"]])),
        "runs": sorted(rows, key=lambda r: (r["kind"], r["rpm"], r["load"], r["engine"])),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")

    for name, s in (("MAIN (train region, unseen engines)", result["main"]),
                    *((f"  band {b}", s) for b, s in result["main_by_rpm_band"].items()),
                    ("HELD-OUT REGION (3100-3400 rpm, test engines)", result["held_out_region"])):
        print(f"{name}: {s['hours']:.2f} h, {s['windows']} windows")
        for lvl in ("window", "advisory", "alarm"):
            x = s[lvl]
            rng = f"95% upper {x['upper_95']:.3f}/h" if x["events"] == 0 else f"95% CI {x['lower_95']:.3f}-{x['upper_95']:.3f}/h"
            print(f"    {lvl:8s} {x['events']:5d} events = {x['per_hour']:.3f}/h  ({rng})")
    print(f"wrote {OUT}  ({time.perf_counter() - t0:.0f} s)")


if __name__ == "__main__":
    main()
