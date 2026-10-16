"""Calibrate the crank-angle misfire rule on HEALTHY simulator runs only (sim v2, regimes on).

Per rpm band (idle / mid / high), the threshold is MARGIN x the (1 - TARGET_FPR) quantile of the
healthy per-window half-order energy (FEATURE); the same for the per-cylinder roughness that sets
location and the dashboard bars. The v1 half-order ratio's healthy statistics are recorded beside
it for comparison (DECISIONS.md D58). Engines come from the "train" parameter region; the held-out band
(3000-3500 rpm), the "test" region and valve_tick are refused by sim.guard. Calibration, validation
and evaluation seeds are disjoint ranges (see SEEDS in eval/common.py).

Writes models/thresholds/misfire_half_order.json (v1's is kept as misfire_half_order_v1.json).
Usage (from the repo root):  python -m dsp.calibrate_misfire
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict
from datetime import datetime, timezone
from multiprocessing import Pool

import numpy as np

from dsp.features import window_features
from dsp.rules import MISFIRE_HALF_ORDER
from dsp.windows import CycleWindower
from sim import SIM_VERSION, EngineParams, EngineSim
from sim.guard import check_chunk, check_condition
from sim.params import Regimes

FEATURE = "half_order_energy"
TARGET_FPR = 1e-3          # per window, per band
MARGIN = 1.25              # threshold = margin x healthy quantile
BANDS = (                  # (name, rpm_lo, rpm_hi, rpm targets run in this band)
    ("idle", 0.0, 1100.0, (700.0, 850.0, 1000.0)),
    ("mid", 1100.0, 2000.0, (1200.0, 1500.0, 1850.0)),
    ("high", 2000.0, 3000.0, (2100.0, 2500.0, 2900.0)),
)
LOADS_NM = (5.0, 25.0, 45.0)
CAL_ENGINES = list(range(0, 12))
VAL_ENGINES = list(range(50, 54))
CAL_RUN_OFFSET, VAL_RUN_OFFSET = 1000, 2000
WARMUP_S, RECORD_S = 3.0, 30.0
N_CYCLES, OVERLAP = 4, 0.5
REGIMES = Regimes.eval()


def healthy_windows(job) -> list[tuple[float, float, float, float]]:
    """(rpm_mean, half_order_energy, max roughness, v1 half_order_ratio) for every valid window of one healthy run."""
    engine_seed, run_seed, rpm, load = job
    check_condition(rpm, "train")
    sim = EngineSim(EngineParams.randomised(engine_seed, "train"), seed=run_seed, regimes=REGIMES,
                    purpose="calibration")
    sim.set_operating_point(rpm_target=rpm, load_nm=load)
    w = CycleWindower(N_CYCLES, OVERLAP)
    out = []
    for _ in range(int((WARMUP_S + RECORD_S) / 0.5)):
        chunk = sim.step(0.5)
        check_chunk(chunk)
        for win in w.push(chunk):
            if win.t_start < WARMUP_S:
                continue
            f = window_features(win)
            if f["crank_valid"]:
                out.append((f["rpm_mean"], f[FEATURE], float(f["roughness"].max()), f["half_order_ratio"]))
    return out


def jobs(engines, run_offset):
    return [(e, run_offset + e, rpm, load) for e in engines for _, _, _, rpms in BANDS for rpm in rpms for load in LOADS_NM]


def collect(engines, run_offset, pool):
    rows = [r for part in pool.map(healthy_windows, jobs(engines, run_offset)) for r in part]
    return np.array(rows)


def upper95(k: int, n: int) -> float:
    """95% upper bound on a proportion: rule of three for zero events, else Wilson."""
    if n == 0:
        return float("nan")
    if k == 0:
        return 3.0 / n
    z, p = 1.96, k / n
    return (p + z * z / (2 * n) + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def in_band(rows, lo, hi):
    return rows[(rows[:, 0] >= lo) & (rows[:, 0] < hi)]


def main():
    t0 = time.perf_counter()
    with Pool(int(__import__('os').environ.get('S720_WORKERS', 10))) as pool:
        cal = collect(CAL_ENGINES, CAL_RUN_OFFSET, pool)
        val = collect(VAL_ENGINES, VAL_RUN_OFFSET, pool)

    bands = []
    for name, lo, hi, rpms in BANDS:
        c, v = in_band(cal, lo, hi), in_band(val, lo, hi)
        hor_thr = float(MARGIN * np.quantile(c[:, 1], 1 - TARGET_FPR))
        rough_thr = float(MARGIN * np.quantile(c[:, 2], 1 - TARGET_FPR))
        fp = int((v[:, 1] > hor_thr).sum())
        bands.append({
            "name": name, "rpm_lo": lo, "rpm_hi": hi, "rpm_targets": list(rpms),
            FEATURE: {"threshold": round(hor_thr, 6), "healthy_quantile": round(hor_thr / MARGIN, 6),
                      "healthy_max_calibration": round(float(c[:, 1].max()), 6)},
            "v1_half_order_ratio_for_comparison": {"healthy_quantile": round(float(np.quantile(c[:, 3], 1 - TARGET_FPR)), 4),
                                                   "healthy_max": round(float(c[:, 3].max()), 4)},
            "roughness": {"threshold": round(rough_thr, 6), "healthy_quantile": round(rough_thr / MARGIN, 6),
                          "healthy_max_calibration": round(float(c[:, 2].max()), 6)},
            "calibration_windows": int(len(c)),
            "validation": {"windows": int(len(v)), "false_positives": fp,
                           "fpr": fp / len(v) if len(v) else None, "fpr_upper_95": upper95(fp, len(v))},
        })

    result = {
        "rule": "misfire_half_order",
        "version": 2,
        "feature": FEATURE,
        "rule_basis": "crank_angle",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sim_version": SIM_VERSION,
        "source": "simulated (healthy runs only)",
        "window": {"n_cycles": N_CYCLES, "overlap": OVERLAP},
        "target_fpr_per_window": TARGET_FPR,
        "margin": MARGIN,
        "regimes": asdict(REGIMES),
        "feature_definition": "half-order amplitude of tooth-time crank speed (rpm) x mean rpm / 1e6, per window",
        "roughness_definition": "max over cylinders of the relative speed drop after TDC minus its neighbours' mean",
        "bands": bands,
        "calibration": {"engine_seeds": CAL_ENGINES, "run_seed_offset": CAL_RUN_OFFSET, "param_region": "train",
                        "loads_nm": list(LOADS_NM), "warmup_s": WARMUP_S, "record_s_per_run": RECORD_S,
                        "n_windows": int(len(cal))},
        "validation": {"engine_seeds": VAL_ENGINES, "run_seed_offset": VAL_RUN_OFFSET, "param_region": "train",
                       "n_windows": int(len(val))},
        "held_out_never_used": {"faults": ["valve_tick"], "rpm": [3000, 3500], "param_region": "test"},
        "limits": "Simulator only. Steady speed per run (no transients). Above 3000 rpm the high band is "
                  "extrapolated. Evaluation lives in eval/ (healthy_soak.py, robustness_v2.py).",
    }
    MISFIRE_HALF_ORDER.parent.mkdir(parents=True, exist_ok=True)
    with open(MISFIRE_HALF_ORDER, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    print(f"calibration windows {len(cal)}, validation windows {len(val)}  ({time.perf_counter() - t0:.0f} s)")
    for b in bands:
        v = b["validation"]
        print(f"  {b['name']:5s} {b['rpm_lo']:5.0f}-{b['rpm_hi']:5.0f} rpm: {FEATURE} thr {b[FEATURE]['threshold']:.5f} "
              f"(healthy q {b[FEATURE]['healthy_quantile']:.5f}, max {b[FEATURE]['healthy_max_calibration']:.5f}; "
              f"v1 ratio q {b['v1_half_order_ratio_for_comparison']['healthy_quantile']:.3f} max {b['v1_half_order_ratio_for_comparison']['healthy_max']:.2f}), "
              f"roughness thr {b['roughness']['threshold']:.5f}; n cal {b['calibration_windows']}; "
              f"validation {v['false_positives']}/{v['windows']} FP, upper95 {v['fpr_upper_95']:.1e}")
    print(f"wrote {MISFIRE_HALF_ORDER}")


if __name__ == "__main__":
    main()
