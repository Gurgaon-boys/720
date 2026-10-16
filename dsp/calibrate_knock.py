"""Calibrate the crank-angle knock rule on HEALTHY simulator runs only (sim v2, regimes on).

Same engines, seeds, conditions and guards as dsp/calibrate_misfire.py (calibration engines 0-11,
validation 50-53, the held-out rpm band, "test" region and valve_tick refused).

Feature: knock_db per cylinder (dsp.features.accel_features): ringing energy of the 5-15 kHz
accelerometer band in TDC+0..40 deg against the sensor's own quiet stretch. Per rpm band and per
cylinder, the healthy baseline is the median; the threshold is baseline + the (1 - TARGET_FPR)
quantile of (knock_db - baseline) pooled over cylinders, plus MARGIN_DB.

Writes models/thresholds/knock_band_energy.json.
Usage (from the repo root):  python -m dsp.calibrate_knock
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from multiprocessing import Pool

import numpy as np

from dsp.calibrate_misfire import (BANDS, CAL_ENGINES, CAL_RUN_OFFSET, LOADS_NM, N_CYCLES, OVERLAP, RECORD_S,
                                   REGIMES, TARGET_FPR, VAL_ENGINES, VAL_RUN_OFFSET, WARMUP_S, in_band, jobs, upper95)
from dsp.features import KNOCK_BAND_HZ, KNOCK_GATE_DEG, KNOCK_REF_DEG, RING_MIN_S, window_features
from dsp.rules import KNOCK_BAND_ENERGY
from models.features import StreamFeatures
from sim import SIM_VERSION, EngineParams, EngineSim
from sim.guard import check_chunk, check_condition

MARGIN_DB = 1.0            # ~1.25 x in energy, as the misfire rule's margin


def healthy_rows(job) -> list[list[float]]:
    """[rpm_mean, knock_db 1..4] for every window of one healthy run."""
    engine_seed, run_seed, rpm, load = job
    check_condition(rpm, "train")
    sim = EngineSim(EngineParams.randomised(engine_seed, "train"), seed=run_seed, regimes=REGIMES,
                    purpose="calibration")
    sim.set_operating_point(rpm_target=rpm, load_nm=load)
    sf = StreamFeatures()
    out = []
    for _ in range(int((WARMUP_S + RECORD_S) / 0.5)):
        chunk = sim.step(0.5)
        check_chunk(chunk)
        for w in sf.push(chunk):
            if w.win.t_start < WARMUP_S:
                continue
            f = w.features
            if f["accel_valid"]:
                out.append([f["rpm_mean"], *f["knock_db"]])
    return out


def collect(engines, offset, pool):
    return np.array([r for part in pool.map(healthy_rows, jobs(engines, offset)) for r in part])


def main():
    t0 = time.perf_counter()
    with Pool(int(__import__('os').environ.get('S720_WORKERS', 10))) as pool:
        cal = collect(CAL_ENGINES, CAL_RUN_OFFSET, pool)
        val = collect(VAL_ENGINES, VAL_RUN_OFFSET, pool)
    bands = []
    for name, lo, hi, rpms in BANDS:
        c, v = in_band(cal, lo, hi), in_band(val, lo, hi)
        base = np.median(c[:, 1:], axis=0)
        excess = (c[:, 1:] - base).ravel()
        thr_excess = float(np.quantile(excess, 1 - TARGET_FPR) + MARGIN_DB)
        fired_val = ((v[:, 1:] - base) > thr_excess).any(axis=1)
        fp = int(fired_val.sum())
        bands.append({
            "name": name, "rpm_lo": lo, "rpm_hi": hi, "rpm_targets": list(rpms),
            "knock_db": {"baseline_per_cylinder": [round(float(x), 3) for x in base],
                         "threshold_excess_db": round(thr_excess, 3),
                         "healthy_max_excess_calibration": round(float(excess.max()), 3)},
            "calibration_windows": int(len(c)),
            "validation": {"windows": int(len(v)), "false_positives": fp,
                           "fpr": fp / len(v) if len(v) else None, "fpr_upper_95": upper95(fp, len(v))},
        })
        print(f"  {name:5s} baseline {np.round(base, 2)} dB, threshold +{thr_excess:.2f} dB "
              f"(healthy max +{excess.max():.2f}); validation {fp}/{len(v)} windows fired")
    result = {
        "rule": "knock_band_energy", "version": 1, "feature": "knock_db", "rule_basis": "crank_angle",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sim_version": SIM_VERSION,
        "source": "simulated (healthy runs only)", "window": {"n_cycles": N_CYCLES, "overlap": OVERLAP},
        "target_fpr_per_window": TARGET_FPR, "margin_db": MARGIN_DB, "regimes": asdict(REGIMES),
        "feature_definition": (f"per cylinder, worst firing in the window: 10 log10 of the {KNOCK_BAND_HZ} Hz "
                               f"accelerometer ringing energy (0.1 ms mean, {RING_MIN_S * 1e3:.1f} ms moving minimum) "
                               f"in TDC+{KNOCK_GATE_DEG} deg over the window median in TDC+{KNOCK_REF_DEG} deg"),
        "bands": bands,
        "calibration": {"engine_seeds": CAL_ENGINES, "run_seed_offset": CAL_RUN_OFFSET, "param_region": "train",
                        "loads_nm": list(LOADS_NM), "n_windows": int(len(cal))},
        "validation": {"engine_seeds": VAL_ENGINES, "run_seed_offset": VAL_RUN_OFFSET, "n_windows": int(len(val))},
        "held_out_never_used": {"faults": ["valve_tick"], "rpm": [3000, 3500], "param_region": "test"},
    }
    KNOCK_BAND_ENERGY.write_text(json.dumps(result, indent=1))
    print(f"calibration windows {len(cal)}, validation {len(val)} ({time.perf_counter() - t0:.0f} s) -> {KNOCK_BAND_ENERGY}")


if __name__ == "__main__":
    main()
