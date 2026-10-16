"""Stage 3: the OBD-II-style misfire counter (dsp/misfire_counter.py) over 200 and 1000 revolutions.

Healthy runs on soak engines and misfire p = 0.1 runs on robustness engines (both never trained or tuned
on), regimes on, 30 s warm-up, then non-overlapping blocks of 200 / 1000 revolutions. A block is flagged
when one cylinder's count reaches c. c is the smallest count whose healthy block-flag rate per hour is
at or below the full-chain soak's physics-witness rate (eval/results/soak_l2.json), i.e. the same
false-positive budget. Reported: per-cylinder counts, c, and the p = 0.1 detection rate (the right
cylinder reaches c) with Wilson intervals.

Writes eval/results/misfire_counter.json.   Usage:  python -m eval.misfire_counter
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from dsp.features import window_features
from dsp.misfire_counter import WINDOWS_REVS, MisfireCounter
from dsp.rules import load_thresholds
from dsp.windows import CycleWindower
from eval.common import eval_engine, wilson
from sim import SIM_VERSION, EngineSim
from sim.params import Regimes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "misfire_counter.json"
WORKERS = int(os.environ.get("S720_WORKERS", 10))
RUNS, RUN_S, WARMUP_S = 20, 240.0, 30.0


def one(job):
    kind, i = job
    params, seed = eval_engine("soak" if kind == "healthy" else "robustness", 20 + i)
    rng = np.random.default_rng([seed, 0x3C0])
    rpm, load, cyl = float(rng.uniform(700, 2950)), float(rng.uniform(3, 45)), int(rng.integers(1, 5))
    sim = EngineSim(params, seed=seed, regimes=Regimes.eval(), purpose="eval")
    sim.set_operating_point(rpm_target=rpm, load_nm=load)
    th = load_thresholds()
    counters = {r: MisfireCounter(th) for r in WINDOWS_REVS}
    start = {r: None for r in WINDOWS_REVS}
    blocks = {r: [] for r in WINDOWS_REVS}
    w = CycleWindower()
    for n in range(int((WARMUP_S + RUN_S) / 0.5)):
        if kind == "misfire" and n * 0.5 == WARMUP_S:
            sim.inject("misfire", cyl, 0.1)
        for win in w.push(sim.step(0.5)):
            if win.t_start < WARMUP_S + 1.0:
                continue
            f = window_features(win)
            for r, ctr in counters.items():
                ctr.push(win, f["rpm_mean"], f["map_kpa"])
                if ctr.last_index is None:
                    continue
                if start[r] is None:
                    start[r] = ctr.last_index
                if ctr.last_index - start[r] >= 2 * r:          # a full block of r revolutions
                    blocks[r].append(ctr.counts(r))
                    start[r] = ctr.last_index
    hours = RUN_S / 3600
    return {"kind": kind, "i": i, "rpm": round(rpm, 1), "load_nm": round(load, 2), "cylinder": cyl if kind == "misfire" else 0,
            "hours": hours, "blocks": {str(r): b for r, b in blocks.items()}}


def main():
    t0 = time.perf_counter()
    jobs = [("healthy", i) for i in range(RUNS)] + [("misfire", i) for i in range(RUNS)]
    with Pool(WORKERS) as pool:
        runs = pool.map(one, jobs)
    soak = json.loads((ROOT / "eval" / "results" / "soak_l2.json").read_text())
    budget = soak["per_hour"]["physics_windows"]["per_hour"]
    hours = sum(r["hours"] for r in runs if r["kind"] == "healthy")
    res = {}
    for r in map(str, WINDOWS_REVS):
        hb = [max(b) for x in runs if x["kind"] == "healthy" for b in x["blocks"][r]]
        mb = [(b[x["cylinder"] - 1], int(np.argmax(b)) + 1 == x["cylinder"]) for x in runs if x["kind"] == "misfire"
              for b in x["blocks"][r]]
        c = next(c for c in range(1, 10_000) if sum(h >= c for h in hb) / hours <= budget)
        k = sum(cnt >= c and right for cnt, right in mb)
        lo, hi = wilson(k, len(mb))
        res[r] = {"healthy_blocks": len(hb), "healthy_max_count": int(max(hb)) if hb else None,
                  "healthy_count_p99": float(np.quantile(hb, 0.99)) if hb else None,
                  "count_threshold": int(c), "healthy_flag_rate_per_hour": sum(h >= c for h in hb) / hours,
                  "p0.1_blocks": len(mb), "p0.1_median_count_on_cylinder": float(np.median([m[0] for m in mb])) if mb else None,
                  "p0.1_expected_count": 0.1 * int(r) / 2,
                  "p0.1_detection": {"kind": "proportion", "successes": k, "n": len(mb), "value": k / len(mb) if mb else None,
                                     "ci_95": [lo, hi], "ci_method": "wilson_95", "column": "A",
                                     "split": "robustness engines (unseen)"}}
    doc = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sim_version": SIM_VERSION,
           "false_positive_budget_per_hour": budget, "budget_source": "soak_l2.json physics_windows per hour",
           "healthy_hours": hours, "per_revs": res, "runs": runs, "wall_s": round(time.perf_counter() - t0, 1)}
    OUT.write_text(json.dumps(doc, indent=1))
    for r, v in res.items():
        d = v["p0.1_detection"]
        print(f"  {r:>4} revs: healthy max {v['healthy_max_count']} (p99 {v['healthy_count_p99']:.1f}), count >= {v['count_threshold']}; "
              f"p=0.1 median {v['p0.1_median_count_on_cylinder']} (expected {v['p0.1_expected_count']:.0f}); "
              f"detected {d['successes']}/{d['n']} ({d['value']:.2f}, CI {d['ci_95'][0]:.2f}-{d['ci_95'][1]:.2f})")


if __name__ == "__main__":
    main()
