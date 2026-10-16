"""Column C (fully real): the OBD branch on carOBD, a healthy real ECU (plan sections 4, 10.1).

OBD branch = the trim-drift physics rule (dsp.rules.trim_drift, threshold from carOBD calibration files)
+ an OBD healthy model: Mahalanobis per rpm band (Ledoit-Wolf) on 30 s OBD-window features, fitted on
2/3 of the CALIBRATION files (by file) with its threshold at the 99.9 % quantile of the other 1/3
(validation). Both feed the real rule layer as witnesses; there is no classifier on carOBD, so ALARM is
structurally impossible (it needs two witnesses) and is reported as such.

The HELD-OUT carOBD files are read ONCE, here, after everything above is fixed. Exposure hours come
from ENGINE_RUN_TIME (data.carobd.duration_s). Every carOBD file is loaded through data.carobd.load().

Reports, overall and per file type: windows, anomaly flags, physics (trim) windows, ADVISORY and
UNCLASSIFIED, each as windows and episodes (contiguous runs of windows), ALARM (structural 0), each per hour with the exact Poisson one-sided
95 % bound (docs EVAL_JSON); plus an rpm-transition check on the held-out drive files.

Writes eval/results/column_c_detail.json (eval/build_eval_json.py turns it into the panel's column_c.json).   Usage:  python -m eval.column_c
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import chi2
from sklearn.covariance import LedoitWolf

from backend.rule_layer import Evidence, RuleLayer
from data.carobd import CAROBD_DIR, duration_s, file_type, load
from dsp.obd_window import WINDOW_S, obd_window
from dsp.rules import PER_LOCATION_THRESHOLD, TRIM_DRIFT, load_thresholds, trim_drift
from sim.calibrate_ecu import SPLIT

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "column_c_detail.json"   # the panel-shaped column_c.json: eval/build_eval_json.py
FEATURES = ("trim_mean_pct", "trim_sd_pct", "ltft_pct", "rpm", "load_pct", "map_kpa", "throttle_pct", "coolant_c",
            "spark_adv_deg")
COLS = {"rpm": "ENGINE_RPM", "load_pct": "ENGINE_LOAD", "map_kpa": "INTAKE_MANIFOLD_PRESSURE", "throttle_pct": "THROTTLE",
        "coolant_c": "COOLANT_TEMPERATURE", "spark_adv_deg": "TIMING_ADVANCE"}
BANDS = (("idle", 0.0, 1100.0), ("mid", 1100.0, 2000.0), ("high", 2000.0, 1e9))
ANOMALY_FPR = 1e-3
TRANSITION_RPM = 600.0          # rpm range inside a 30 s window that counts as a transition


def windows(name: str) -> list[dict]:
    """30 s windows (50 % overlap) over the running, warm part of one file."""
    df = load(CAROBD_DIR / name)
    d = df[(df["ENGINE_RPM"] > 400) & (df["COOLANT_TEMPERATURE"] > 70)]
    if len(d) < 20:
        return []
    t = d["t_s"].to_numpy(float)
    out = []
    for t_end in np.arange(t[0] + WINDOW_S, t[-1] + 1e-9, WINDOW_S / 2):
        w = obd_window(t, d["SHORT_TERM_FUEL_TRIM_BANK_1"], d["LONG_TERM_FUEL_TRIM_BANK_1"], d["TIMING_ADVANCE"], t_end)
        if w["n"] < 20:
            continue
        sel = (t > t_end - WINDOW_S) & (t <= t_end)
        for k, c in COLS.items():
            w[k] = float(d[c].to_numpy(float)[sel].mean())
        rp = d["ENGINE_RPM"].to_numpy(float)[sel]
        w["rpm_range"] = float(rp.max() - rp.min())
        w["t_end"] = float(t_end)
        out.append(w)
    return out


def band_of(rpm):
    return next(n for n, lo, hi in BANDS if lo <= rpm < hi)


def vec(w):
    return np.array([w[k] for k in FEATURES], dtype=float)


def fit_obd_model(fit_files, val_files) -> dict:
    fw = [w for f in fit_files for w in windows(f)]
    vw = [w for f in val_files for w in windows(f)]
    X = np.array([vec(w) for w in fw])
    mu, sd = X.mean(0), X.std(0) + 1e-9
    model = {"mu": mu, "sd": sd, "bands": {}}
    for name, lo, hi in BANDS:
        Z = (np.array([vec(w) for w in fw if lo <= w["rpm"] < hi]) - mu) / sd
        lw = LedoitWolf().fit(Z)
        prec = np.linalg.pinv(lw.covariance_)
        Zv = (np.array([vec(w) for w in vw if lo <= w["rpm"] < hi]) - mu) / sd
        dz = Zv - lw.location_
        dv = np.sqrt(np.einsum("ij,jk,ik->i", dz, prec, dz))
        model["bands"][name] = {"mean": lw.location_, "prec": prec, "threshold": float(np.quantile(dv, 1 - ANOMALY_FPR)),
                                "n_fit": int(len(Z)), "n_val": int(len(Zv))}
    return model


def score(model, w) -> float:
    b = model["bands"][band_of(w["rpm"])]
    dz = (vec(w) - model["mu"]) / model["sd"] - b["mean"]
    return float(PER_LOCATION_THRESHOLD * np.sqrt(dz @ b["prec"] @ dz) / b["threshold"])


def rate(k: int, hours: float, **extra) -> dict:
    return {"kind": "rate", "events": int(k), "exposure_hours": round(hours, 4), "per_hour": k / hours if hours else None,
            "upper_95_per_hour": float(chi2.ppf(0.95, 2 * (k + 1)) / (2 * hours)) if hours else None,
            "bound_method": "poisson_exact_one_sided_95", "column": "C", "split": "carOBD held-out files", **extra}


def run_file(name, model, trim_th, unclassified_windows: int = 2) -> dict:
    """Windows and episodes (contiguous runs of windows) per level for one file."""
    from eval.chain import episodes
    ws = windows(name)
    layer = RuleLayer(unclassified_windows=unclassified_windows)
    m = {k: [] for k in ("anomaly_flags", "physics", "advisory", "unclassified", "alarm")}
    c = {"windows": 0, "transition_windows": 0, "transition_flags": 0, "steady_flags": 0}
    prev = "NORMAL"
    c["alarms_raised"] = 0
    for w in ws:
        s = score(model, w)
        rule = trim_drift(w, trim_th)
        d = layer.step(Evidence(models=[], physics_rules=[rule], anomaly_score=s, anomaly_threshold=PER_LOCATION_THRESHOLD))
        st = d["state"]
        flag = s > PER_LOCATION_THRESHOLD
        c["windows"] += 1
        m["anomaly_flags"].append(flag)
        m["physics"].append(rule["fired"])
        m["advisory"].append(st == "ADVISORY")
        m["unclassified"].append(st == "UNCLASSIFIED")
        m["alarm"].append(st == "ALARM")
        c["alarms_raised"] += st == "ALARM" and prev != "ALARM"
        trans = w["rpm_range"] > TRANSITION_RPM
        c["transition_windows"] += trans
        c["transition_flags"] += trans and (flag or rule["fired"])
        c["steady_flags"] += (not trans) and (flag or rule["fired"])
        prev = st
    for k, v in m.items():
        c[f"{k}_windows"] = int(sum(v))
        c[f"{k}_episodes"] = episodes(np.array(v, dtype=bool)) if v else 0
    c["hours"] = duration_s(load(CAROBD_DIR / name)) / 3600
    return c


def main():
    split = json.loads(SPLIT.read_text())
    calib, heldout = sorted(split["calibration"]), sorted(split["heldout"])
    assert not set(calib) & set(heldout)
    # fit / validation split of the CALIBRATION files, by file, stratified by type
    fit, val = [], []
    for ty in sorted({file_type(f) for f in calib}):
        fs = [f for f in calib if file_type(f) == ty]
        rng = np.random.default_rng(720)
        fs = list(rng.permutation(fs))
        k = max(1, len(fs) // 3)
        val += fs[:k]
        fit += fs[k:]
    model = fit_obd_model(fit, val)
    trim_th = load_thresholds(TRIM_DRIFT)

    # ---- the held-out files, read once
    # (L3: also replayed with the pre-L3 one-window UNCLASSIFIED, for the before/after; nothing is refitted)
    per_file = {f: run_file(f, model, trim_th) for f in heldout}
    per_file_before = {f: run_file(f, model, trim_th, unclassified_windows=1) for f in heldout}

    def summarise(files, pf=None):
        pf = pf or per_file
        tot = {k: sum(pf[f][k] for f in files) for k in pf[files[0]]}
        h = tot["hours"]
        return {
            "files": len(files), "hours": round(h, 3), "windows": tot["windows"],
            "anomaly_flags": rate(tot["anomaly_flags_episodes"], h, unit="episodes", windows=tot["anomaly_flags_windows"]),
            "physics_trim_windows": rate(tot["physics_episodes"], h, unit="episodes", windows=tot["physics_windows"]),
            "advisories": rate(tot["advisory_episodes"], h, unit="episodes", windows=tot["advisory_windows"]),
            "unclassified": rate(tot["unclassified_episodes"], h, unit="episodes", windows=tot["unclassified_windows"]),
            "classifier_false_faults": None,
            "alarms": rate(tot["alarms_raised"], h, unit="episodes", structurally_limited=True,
                           note="no classifier on carOBD: ALARM needs two witnesses, so it cannot occur"),
            "transition_check": {"transition_windows": tot["transition_windows"], "transition_flagged": tot["transition_flags"],
                                 "steady_windows": tot["windows"] - tot["transition_windows"], "steady_flagged": tot["steady_flags"],
                                 "transition_rule": f"rpm range > {TRANSITION_RPM:.0f} in the 30 s window"},
        }

    types = sorted({file_type(f) for f in heldout})
    res = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "column": "C",
        "source": "carOBD (eron93br/carOBD), healthy real ECU; held-out files, read once",
        "obd_branch": {"physics": "trim_drift (threshold %.2f %% from calibration files)" % trim_th["threshold_pct"],
                       "healthy_model": "Mahalanobis per rpm band, Ledoit-Wolf, features " + ", ".join(FEATURES),
                       "fit_files": len(fit), "threshold_files": len(val),
                       "thresholds": {k: round(v["threshold"], 3) for k, v in model["bands"].items()},
                       "n_fit": {k: v["n_fit"] for k, v in model["bands"].items()},
                       "n_threshold": {k: v["n_val"] for k, v in model["bands"].items()}},
        "unclassified_windows_required": 2,
        "overall": summarise(heldout),
        "overall_before_l3": {**summarise(heldout, per_file_before), "note": "one-window UNCLASSIFIED (before L3)"},
        "by_type": {ty: summarise([f for f in heldout if file_type(f) == ty]) for ty in types},
        "per_file": {f: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items()} for f, c in per_file.items()},
    }
    OUT.write_text(json.dumps(res, indent=1))
    o = res["overall"]
    print(f"held-out: {o['files']} files, {o['hours']:.2f} h, {o['windows']} windows")
    for lab, o in (("after (2-window UNCLASSIFIED)", res["overall"]), ("before (1-window)", res["overall_before_l3"])):
        print(lab)
        for k in ("anomaly_flags", "physics_trim_windows", "advisories", "unclassified", "alarms"):
            r = o[k]
            print(f"  {k:22s} episodes {r['events']:5d} = {r['per_hour']:.2f}/h (95% upper {r['upper_95_per_hour']:.2f}/h)"
                  f"  windows {r.get('windows', r['events'])}")
    for ty, s in res["by_type"].items():
        print(f"  {ty:6s} {s['hours']:.2f} h  advisories {s['advisories']['events']}  unclassified {s['unclassified']['events']}  flags {s['anomaly_flags']['events']}")
    print("  transitions:", o["transition_check"])


if __name__ == "__main__":
    main()
