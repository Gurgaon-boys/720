"""Calibrate the trim-drift (injector clog, OBD) rule on carOBD CALIBRATION files only.

carOBD is a healthy real ECU, so it sets what "normal" total fuel trim looks like: 30 s windows
with 50% overlap over every calibration file, engine running (rpm > 400) and warm (coolant > 70 degC,
closed-loop fuelling). Threshold = MARGIN x the (1 - TARGET_FPR) quantile of the window mean of
(1 + STFT)(1 + LTFT) - 1. The held-out carOBD files are never opened here (tested); they are read once,
for the column C false-positive rate.

The result is then checked against healthy SIMULATOR runs (calibration engines, OBD at 0.55 Hz):
how often the sim's own healthy windows would fire.

Writes models/thresholds/trim_drift.json.   Usage:  python -m dsp.calibrate_trim
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
import os
from multiprocessing import Pool

import numpy as np

from data.carobd import CAROBD_DIR, load
from dsp.calibrate_misfire import CAL_ENGINES, CAL_RUN_OFFSET, REGIMES, upper95
from dsp.obd_window import WINDOW_S, obd_window
from dsp.rules import TRIM_DRIFT
from sim import SIM_VERSION, EngineParams, EngineSim
from sim.calibrate_ecu import calibration_files
from sim.guard import check_chunk, check_condition

WORKERS = int(os.environ.get("S720_WORKERS", min(10, os.cpu_count() or 1)))   # ~0.5 GB each
TARGET_FPR = 1e-3
MARGIN = 1.25
MIN_SAMPLES = 20             # rows in a 30 s window (carOBD ~60, simulator 30)
RETARD_EXCLUDE_DEG = 1.0
SIM_CHECK_S = 120.0


def carobd_windows(name: str) -> list[float]:
    df = load(CAROBD_DIR / name)
    ok = (df["ENGINE_RPM"] > 400) & (df["COOLANT_TEMPERATURE"] > 70)
    d = df[ok]
    t = d["t_s"].to_numpy(float)
    out = []
    if len(d) < MIN_SAMPLES:
        return out
    for t_end in np.arange(t[0] + WINDOW_S, t[-1] + 1e-9, WINDOW_S / 2):
        w = obd_window(t, d["SHORT_TERM_FUEL_TRIM_BANK_1"], d["LONG_TERM_FUEL_TRIM_BANK_1"],
                       d["TIMING_ADVANCE"], t_end)
        if w["n"] >= MIN_SAMPLES:
            out.append(w["trim_mean_pct"])
    return out


def sim_windows(job) -> list[float]:
    engine, rpm, load_nm = job
    check_condition(rpm, "train")
    sim = EngineSim(EngineParams.randomised(engine, "train"), seed=CAL_RUN_OFFSET + engine, regimes=REGIMES,
                    purpose="calibration")
    sim.set_operating_point(rpm_target=rpm, load_nm=load_nm)
    rows = []
    for _ in range(int(SIM_CHECK_S / 0.5)):
        c = sim.step(0.5)
        check_chunk(c)
        rows += c["obd"]
    g = lambda k: np.array([r[k] for r in rows], dtype=float)
    t = g("t")
    return [obd_window(t, g("stft_pct"), g("ltft_pct"), g("spark_adv_deg"), te)["trim_mean_pct"]
            for te in np.arange(WINDOW_S + 15.0, t[-1] + 1e-9, WINDOW_S / 2)]


def main():
    files = calibration_files()
    real = np.array([x for f in files for x in carobd_windows(f)])
    q = float(np.quantile(real, 1 - TARGET_FPR))
    thr = MARGIN * q
    with Pool(WORKERS) as pool:
        sim = np.array([x for part in pool.map(sim_windows, [(e, r, l) for e in CAL_ENGINES
                                                             for r in (800.0, 1500.0, 2500.0) for l in (5.0, 25.0)])
                        for x in part])
    fp_sim = int((sim > thr).sum())
    doc = {
        "rule": "trim_drift", "version": 1, "rule_basis": "obd",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "carOBD calibration files (healthy real ECU); checked on healthy simulator runs",
        "feature_definition": "30 s window mean of (1 + STFT)(1 + LTFT) - 1, %; fires above threshold (lean correction)",
        "window_s": WINDOW_S, "overlap": 0.5, "min_samples": MIN_SAMPLES, "retard_exclude_deg": RETARD_EXCLUDE_DEG,
        "target_fpr_per_window": TARGET_FPR, "margin": MARGIN,
        "threshold_pct": round(thr, 3),
        "carobd": {"files": len(files), "windows": int(len(real)), "quantile_pct": round(q, 3),
                   "p50_pct": round(float(np.median(real)), 3), "p01_pct": round(float(np.quantile(real, 0.01)), 3),
                   "p99_pct": round(float(np.quantile(real, 0.99)), 3), "max_pct": round(float(real.max()), 3)},
        "sim_check": {"sim_version": SIM_VERSION, "engine_seeds": CAL_ENGINES, "windows": int(len(sim)),
                      "false_positives": fp_sim, "fpr_upper_95": upper95(fp_sim, len(sim)),
                      "p50_pct": round(float(np.median(sim)), 3), "p99_pct": round(float(np.quantile(sim, 0.99)), 3),
                      "max_pct": round(float(sim.max()), 3)},
        "clog_expected_trim_pct": {f"s={s}": round(100 * s / (4 - s), 2) for s in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)},
    }
    TRIM_DRIFT.write_text(json.dumps(doc, indent=1))
    print(json.dumps({k: doc[k] for k in ("threshold_pct", "carobd", "sim_check", "clog_expected_trim_pct")}, indent=1))


if __name__ == "__main__":
    main()
