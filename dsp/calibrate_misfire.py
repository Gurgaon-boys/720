"""Calibrate the crank-angle misfire rule on HEALTHY simulator runs only.

Calibration engines and held-out engines are disjoint (different engine_seeds and run seeds),
both drawn from the "train" parameter region, at three steady speeds. The threshold is the
(1 - target FPR) quantile of the healthy per-window half-order ratio, times a safety margin;
the same is done for the per-cylinder roughness that sets location and the dashboard bars.
The held-out healthy engines then measure the false-positive rate actually achieved.

Writes models/thresholds/misfire_half_order.json.

Usage (from the repo root):  python -m dsp.calibrate_misfire
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone

import numpy as np

from dsp.features import window_features
from dsp.rules import MISFIRE_HALF_ORDER
from dsp.windows import CycleWindower
from sim import SIM_VERSION, EngineParams, EngineSim

TARGET_FPR = 1e-3          # per window
MARGIN = 1.25              # threshold = margin x healthy quantile
CAL_ENGINES = list(range(0, 10))
HELD_OUT_ENGINES = [100, 101, 102, 103]
RPM_TARGETS = (800, 1500, 2500)
WARMUP_S, RECORD_S = 3.0, 40.0
N_CYCLES, OVERLAP = 4, 0.5


def healthy_features(engine_seed: int, run_seed: int, rpm: float) -> list[dict]:
    sim = EngineSim(EngineParams.randomised(engine_seed, "train"), seed=run_seed)
    sim.set_operating_point(rpm_target=rpm)
    w = CycleWindower(N_CYCLES, OVERLAP)
    out = []
    for _ in range(int((WARMUP_S + RECORD_S) / 0.5)):
        for win in w.push(sim.step(0.5)):
            if win.t_start >= WARMUP_S:
                out.append(window_features(win))
    return out


def collect(engines, run_offset):
    feats = []
    for e in engines:
        for rpm in RPM_TARGETS:
            feats += healthy_features(e, run_offset + e, rpm)
    hor = np.array([f["half_order_ratio"] for f in feats])
    rough = np.array([f["roughness"].max() for f in feats])
    return hor, rough


def rule_of_three(n_fp: int, n: int) -> float:
    """95% upper bound on a rate: rule of three when there are no events, else Wilson."""
    if n_fp == 0:
        return 3.0 / n
    z = 1.96
    p = n_fp / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre + half) / (1 + z * z / n)


def main():
    t0 = time.perf_counter()
    hor_cal, rough_cal = collect(CAL_ENGINES, 1000)
    hor_thr = float(MARGIN * np.quantile(hor_cal, 1 - TARGET_FPR))
    rough_thr = float(MARGIN * np.quantile(rough_cal, 1 - TARGET_FPR))

    hor_ho, rough_ho = collect(HELD_OUT_ENGINES, 5000)
    fp = int((hor_ho > hor_thr).sum())
    n = int(hor_ho.size)

    result = {
        "rule": "misfire_half_order",
        "rule_basis": "crank_angle",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sim_version": SIM_VERSION,
        "source": "simulated (healthy runs only)",
        "window": {"n_cycles": N_CYCLES, "overlap": OVERLAP},
        "target_fpr_per_window": TARGET_FPR,
        "margin": MARGIN,
        "half_order_ratio": {
            "definition": "half-order / 2nd-order amplitude of tooth-time crank speed, per window",
            "threshold": round(hor_thr, 5),
            "healthy_quantile": round(hor_thr / MARGIN, 5),
            "healthy_max_calibration": round(float(hor_cal.max()), 5),
        },
        "roughness": {
            "definition": "max over cylinders of the relative speed drop after TDC minus its neighbours' mean, per window",
            "threshold": round(rough_thr, 6),
            "healthy_quantile": round(rough_thr / MARGIN, 6),
            "healthy_max_calibration": round(float(rough_cal.max()), 6),
        },
        "calibration": {
            "engine_seeds": CAL_ENGINES, "run_seeds": [1000 + e for e in CAL_ENGINES], "param_region": "train",
            "rpm_targets": list(RPM_TARGETS), "warmup_s": WARMUP_S, "record_s_per_run": RECORD_S,
            "n_windows": int(hor_cal.size),
        },
        "held_out": {
            "engine_seeds": HELD_OUT_ENGINES, "run_seeds": [5000 + e for e in HELD_OUT_ENGINES], "param_region": "train",
            "rpm_targets": list(RPM_TARGETS), "n_windows": n, "false_positives": fp,
            "fpr": fp / n, "fpr_upper_95": rule_of_three(fp, n),
            "healthy_max": round(float(hor_ho.max()), 5),
        },
        "limits": "Steady speed only (idle PI controller at fixed targets); no transients, knock or other faults in "
                  "the healthy set; one simulator. A sim-to-real check needs real crank data.",
    }
    MISFIRE_HALF_ORDER.parent.mkdir(parents=True, exist_ok=True)
    with open(MISFIRE_HALF_ORDER, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    print(f"calibration windows {hor_cal.size}, held-out windows {n}  ({time.perf_counter() - t0:.0f} s)")
    print(f"half-order ratio: healthy q{1 - TARGET_FPR:.3f} = {hor_thr / MARGIN:.4f}, threshold {hor_thr:.4f}")
    print(f"roughness:        healthy q{1 - TARGET_FPR:.3f} = {rough_thr / MARGIN:.5f}, threshold {rough_thr:.5f}")
    print(f"held-out healthy: {fp}/{n} false positives, FPR {fp / n:.2e}, 95% upper {rule_of_three(fp, n):.2e} "
          f"(target {TARGET_FPR:.0e})")
    print(f"wrote {MISFIRE_HALF_ORDER}")


if __name__ == "__main__":
    main()
