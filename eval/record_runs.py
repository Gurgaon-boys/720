"""Record full-chain runs for L3 (eval/chain.py), in parallel, resumable (one npz per run).

Job sets:
  val_thr   160 healthy runs (3 min) on 80 extra VALIDATION engines: the healthy-model threshold (models/calibrate_healthy.py)
  fresh2    the 40 healthy soak_fresh2 runs again (same operating points as eval/soak_l2.py), now recorded
  fresh3    40 healthy runs on the THIRD untouched range (engines 1100-1139)
  faults    fault runs on robustness engines (unseen): misfire p 0.5, knock 0.5, injector_clog s 0.5, valve_tick
            0.3 / 0.6 / 1.0; 20 runs each, 30 s healthy then 90 s of fault

Usage:  python -m eval.record_runs val_thr fresh2 fresh3 faults      (S720_WORKERS caps the pool)
"""

from __future__ import annotations

import os
import sys
import time
from multiprocessing import Pool

from eval.chain import record

WORKERS = int(os.environ.get("S720_WORKERS", 10))
FAULTS = (("misfire", 0.5), ("knock", 0.5), ("injector_clog", 0.5), ("valve_tick", 0.3), ("valve_tick", 0.6),
          ("valve_tick", 1.0))
FAULT_RUNS, FAULT_S = 20, 90.0


def jobs(name: str) -> list[tuple]:
    if name == "val_thr":
        return [("val_thr", i, None, 0.0, 180.0) for i in range(160)]
    if name == "fresh2":
        return [("soak_fresh2", i, None, 0.0, 180.0) for i in range(40)]
    if name == "fresh3":
        return [("soak_fresh3", i, None, 0.0, 180.0) for i in range(40)]
    if name == "faults":
        return [("robustness", k, f, s, FAULT_S) for f, s in FAULTS for k in range(FAULT_RUNS)]
    raise ValueError(name)


def _one(job):
    rng, i, fault, sev, run_s = job
    return str(record(rng, i, fault=fault, severity=sev, run_s=run_s, tag="faults" if fault else "soak"))


def main():
    todo = [j for n in sys.argv[1:] for j in jobs(n)]
    t0 = time.perf_counter()
    with Pool(WORKERS) as pool:
        for k, _ in enumerate(pool.imap_unordered(_one, todo), 1):
            if k % 20 == 0 or k == len(todo):
                print(f"  {k}/{len(todo)} runs ({time.perf_counter() - t0:.0f} s)", flush=True)


if __name__ == "__main__":
    main()
