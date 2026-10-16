"""Measure the three slow OBD sensors added in L3 on carOBD CALIBRATION files only (never "heldout").

  intake_air_temp_c  INTAKE_AIR_TEMP               (degC)
  battery_v          CONTROL_MODULE_VOLTAGE        (V; module supply = battery / alternator voltage)
  baro_kpa           ABSOLUTE_BAROMETRIC_PRESSURE  (kPa)

Per file, rows with the engine running (rpm > 400): the file median (the level of that car on that day),
the slow wander (sd of 30 s window means around the file median) and the fast noise (median sd inside
30 s windows), how often the value changes (a lower bound on the PID refresh: a slow value often repeats), and the reporting resolution (smallest step between distinct values).
Across files: mean and sd of the levels, so the simulator draws one level per engine.

Loads carOBD only through data/carobd.py:load(). Writes sim/sensor_calibration.json.
Usage:  python -m sim.calibrate_sensors
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from data.carobd import CAROBD_DIR, load
from sim.calibrate_ecu import calibration_files

OUT = Path(__file__).resolve().parent / "sensor_calibration.json"
PIDS = {"intake_air_temp_c": "INTAKE_AIR_TEMP", "battery_v": "CONTROL_MODULE_VOLTAGE",
        "baro_kpa": "ABSOLUTE_BAROMETRIC_PRESSURE"}


def file_stats(name: str) -> dict | None:
    df = load(CAROBD_DIR / name)
    run = df[df["ENGINE_RPM"] > 400]
    if len(run) < 120:
        return None
    out = {}
    for k, c in PIDS.items():
        ok = np.isfinite(run[c].to_numpy(float))
        if ok.sum() < 120:
            continue
        t, v = run["t_s"].to_numpy(float)[ok], run[c].to_numpy(float)[ok]
        med = float(np.median(v))
        means, sds = [], []
        for a in np.arange(t[0], t[-1] - 30, 30):
            s = (t >= a) & (t < a + 30)
            if s.sum() >= 10:
                means.append(v[s].mean())
                sds.append(v[s].std())
        u = np.unique(v)
        out[k] = {"median": med, "wander_sd": float(np.std(np.array(means) - med)) if means else None,
                  "noise_sd": float(np.median(sds)) if sds else None,
                  "change_hz": float(np.count_nonzero(np.diff(v) != 0) / (t[-1] - t[0])),
                  "step": float(np.min(np.diff(u))) if len(u) > 1 else None}
    return out


def main():
    files = calibration_files()
    per = {f: s for f in files if (s := file_stats(f)) is not None}
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "source": "carOBD calibration files only (data/splits/carobd_split.json 'calibration'), engine running",
           "files": len(per), "channels": {}}
    for k, c in PIDS.items():
        have = [s[k] for s in per.values() if k in s]
        lv = np.array([s["median"] for s in have])
        get = lambda key: np.array([s[key] for s in have if s[key] is not None])
        steps = get("step")
        res["channels"][k] = {
            "carobd_column": c, "level_mean": float(lv.mean()), "level_sd_across_files": float(lv.std(ddof=1)),
            "level_range": [float(lv.min()), float(lv.max())],
            "wander_sd": float(np.median(get("wander_sd"))), "noise_sd": float(np.median(get("noise_sd"))),
            "files": len(have), "change_hz": float(np.median(get("change_hz"))),
            "step": float(np.median(steps)) if len(steps) else None}
    OUT.write_text(json.dumps(res, indent=1))
    for k, v in res["channels"].items():
        print(f"  {k:18s} level {v['level_mean']:.3f} (sd across files {v['level_sd_across_files']:.3f}, "
              f"range {v['level_range'][0]:.2f}-{v['level_range'][1]:.2f}), wander {v['wander_sd']:.3f}, "
              f"noise {v['noise_sd']:.3f}, value changes {v['change_hz']:.2f} Hz, step {v['step']}, files {v['files']}")
    print(f"{res['files']} calibration files -> {OUT}")


if __name__ == "__main__":
    main()
