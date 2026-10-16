"""Measure the sim's ECU/OBD constants on carOBD CALIBRATION files only (never "heldout").

Writes sim/ecu_calibration.json; EcuParams defaults are set from it (see sim/params.py).
Loads carOBD only through data/carobd.py:load() (trailing-comma column shift).

Measured, per file type and pooled, from rows with the engine running (rpm > 400):
  - PID refresh rate (fresh values per second of ENGINE_RUN_TIME), per channel
  - idle (rpm < 1000, speed 0, coolant > 75 degC): MAP, throttle, calculated load, timing advance, rpm
  - STFT: sd within 30 s windows (noise) and per-file mean; LTFT: per-file mean, sd across files,
    and drift (change per hour within a file)
  - coolant: warm level (median when > 75 degC) and warm-up rate (degC/min while < 70 degC)
  - timing advance range (5th..95th percentile) overall and at idle

Usage (repo root):  python -m sim.calibrate_ecu
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from data.carobd import CAROBD_DIR, duration_s, file_type, load

ROOT = Path(__file__).resolve().parents[1]
SPLIT = ROOT / "data" / "splits" / "carobd_split.json"
OUT = Path(__file__).resolve().parent / "ecu_calibration.json"
PIDS = {"rpm": "ENGINE_RPM", "map_kpa": "INTAKE_MANIFOLD_PRESSURE", "throttle_pct": "THROTTLE",
        "load_pct": "ENGINE_LOAD", "coolant_c": "COOLANT_TEMPERATURE", "stft_pct": "SHORT_TERM_FUEL_TRIM_BANK_1",
        "ltft_pct": "LONG_TERM_FUEL_TRIM_BANK_1", "spark_adv_deg": "TIMING_ADVANCE"}


def calibration_files() -> list[str]:
    """The calibration list. Calibration code never opens anything else (tested)."""
    return list(json.loads(SPLIT.read_text())["calibration"])


def _refresh_hz(x: np.ndarray, t: np.ndarray) -> float:
    changes = np.count_nonzero(np.diff(x) != 0)
    span = t[-1] - t[0]
    return float(changes / span) if span > 0 else float("nan")


def file_stats(name: str) -> dict:
    df = load(CAROBD_DIR / name)
    run = df[df["ENGINE_RPM"] > 400]
    t = run["t_s"].to_numpy(float)
    out = {"file": name, "type": file_type(name), "hours": duration_s(df) / 3600, "running_rows": len(run)}
    if len(run) < 60:
        return out
    out["refresh_hz"] = {k: _refresh_hz(run[c].to_numpy(float), t) for k, c in PIDS.items()}
    idle = run[(run["ENGINE_RPM"] < 1000) & (run["VEHICLE_SPEED"] == 0) & (run["COOLANT_TEMPERATURE"] > 75)]
    if len(idle) > 30:
        out["idle"] = {k: float(np.median(idle[c])) for k, c in PIDS.items()}
        out["idle_rows"] = len(idle)
        it, ist = idle["t_s"].to_numpy(float), idle[PIDS["stft_pct"]].to_numpy(float)
        sds = [float(np.std(ist[(it >= a) & (it < a + 30)])) for a in np.arange(it[0], it[-1] - 30, 30)
               if ((it >= a) & (it < a + 30)).sum() > 40]
        out["idle_stft_sd_30s"] = float(np.median(sds)) if sds else float("nan")
        steps = np.abs(np.diff(ist))
        out["stft_step_pct"] = float(np.min(steps[steps > 0])) if np.any(steps > 0) else float("nan")
    stft, ltft = run[PIDS["stft_pct"]].to_numpy(float), run[PIDS["ltft_pct"]].to_numpy(float)
    win = []
    for t0 in np.arange(t[0], t[-1] - 30, 30):
        sel = (t >= t0) & (t < t0 + 30)
        if sel.sum() > 20:
            win.append(float(np.std(stft[sel])))
    out["stft_sd_30s_median"] = float(np.median(win)) if win else float("nan")
    out["stft_mean"], out["ltft_mean"] = float(np.mean(stft)), float(np.mean(ltft))
    hrs = (t[-1] - t[0]) / 3600
    out["ltft_drift_pct_per_h"] = float((ltft[-30:].mean() - ltft[:30].mean()) / hrs) if hrs > 0.1 else float("nan")
    cool = run[PIDS["coolant_c"]].to_numpy(float)
    out["coolant_warm_median"] = float(np.median(cool[cool > 75])) if np.any(cool > 75) else float("nan")
    cold = cool < 70
    if cold.sum() > 120 and cool[0] < 60:
        tt = t[cold]
        out["warmup_c_per_min"] = float(np.polyfit(tt / 60, cool[cold], 1)[0])
        if np.any(cool > 75):
            # first-order: slope = (warm - T) / tau at the cold segment's mean temperature
            out["coolant_tau_s"] = float(60 * (np.median(cool[cool > 75]) - cool[cold].mean()) / out["warmup_c_per_min"])
    adv = run[PIDS["spark_adv_deg"]].to_numpy(float)
    out["adv_p05"], out["adv_p95"] = float(np.percentile(adv, 5)), float(np.percentile(adv, 95))
    return out


def _med(vals):
    v = [x for x in vals if x is not None and np.isfinite(x)]
    return float(np.median(v)) if v else None


def main():
    files = calibration_files()
    stats = [file_stats(f) for f in files]
    ok = [s for s in stats if "refresh_hz" in s]
    idle = [s["idle"] for s in ok if "idle" in s]
    pooled = {
        "refresh_hz": {k: _med([s["refresh_hz"][k] for s in ok]) for k in PIDS},
        "idle": {k: _med([i[k] for i in idle]) for k in PIDS},
        "idle_files": len(idle),
        "stft_sd_30s": _med([s["stft_sd_30s_median"] for s in ok]),
        "stft_mean": _med([s["stft_mean"] for s in ok]),
        "ltft_mean": _med([s["ltft_mean"] for s in ok]),
        "ltft_sd_across_files": float(np.std([s["ltft_mean"] for s in ok])),
        "ltft_drift_pct_per_h_abs": _med([abs(s.get("ltft_drift_pct_per_h", np.nan)) for s in ok]),
        "coolant_warm": _med([s["coolant_warm_median"] for s in ok]),
        "warmup_c_per_min": _med([s.get("warmup_c_per_min") for s in ok]),
        "warmup_files": sum("warmup_c_per_min" in s for s in ok),
        "idle_stft_sd_30s": _med([s.get("idle_stft_sd_30s") for s in ok]),
        "stft_step_pct": _med([s.get("stft_step_pct") for s in ok]),
        "coolant_tau_s": _med([s.get("coolant_tau_s") for s in ok]),
        "adv_p05": _med([s["adv_p05"] for s in ok]),
        "adv_p95": _med([s["adv_p95"] for s in ok]),
    }
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                cwd=ROOT).stdout.strip()
    except OSError:
        commit = None
    doc = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_commit": commit,
           "split": "carobd_split.json: calibration only", "n_files": len(files), "n_used": len(ok),
           "hours": round(sum(s["hours"] for s in stats), 3), "pooled": pooled, "per_file": stats}
    OUT.write_text(json.dumps(doc, indent=1))
    print(json.dumps(pooled, indent=1))
    print(f"{len(ok)}/{len(files)} calibration files, {doc['hours']:.2f} h -> {OUT}")


if __name__ == "__main__":
    main()
