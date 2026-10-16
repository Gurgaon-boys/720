"""OBD branch on real carOBD: calibrate on calibration files, report false positives on held-out files.

    python -m models.obd.run_carobd

Protocol (plan sections 4, 10.1, 13; CONVENTIONS "Splits" and "Evidence"):
  * data/splits/carobd_split.json fixes calibration vs held-out BY FILE (committed at H0).
  * The calibration files are split again BY FILE (stratified by type, seed) into fit / validation.
    The MAP model and the healthy model are fitted on fit files only; every threshold is set on
    validation files only. Held-out files are evaluated once, with nothing refitted.
  * Each window goes through the real rule layer (backend/rule_layer.py) with the OBD rules as the
    physics witness and the Mahalanobis distance as the anomaly score. There is no OBD classifier
    yet (it needs the simulator's OBD channels), so a two-witness ALARM is structurally impossible
    here; the honest numbers are the anomaly-flag, UNCLASSIFIED and ADVISORY rates.
  * carOBD is all healthy, so every flag, advisory and alarm is a false positive.

Writes models/thresholds/obd_branch.json (model + thresholds) and eval/obd_carobd.json (results).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "data"))
import carobd  # noqa: E402
import carobd_split  # noqa: E402
from _common import git_commit, utc_now_iso, write_json  # noqa: E402

from backend.rule_layer import Evidence, RuleLayer  # noqa: E402
from eval.stats import episodes, proportion_summary, rate_summary  # noqa: E402
from models.obd import rules  # noqa: E402
from models.obd.features import HOP_S, REGIMES, WINDOW_S, MapModel, windows  # noqa: E402
from models.obd.healthy import HealthyModel  # noqa: E402

SPLIT = REPO / "data" / "splits" / "carobd_split.json"
THRESHOLDS = REPO / "models" / "thresholds" / "obd_branch.json"
RESULTS = REPO / "eval" / "obd_carobd.json"

ANOMALY_QUANTILE = 0.995  # per-regime threshold on validation distances
RULE_QUANTILE = 0.999  # rule thresholds: healthy validation quantile ...
RULE_MARGIN = 1.25  # ... times this margin (same recipe as dsp/calibrate_misfire)
TRANSIENT_RPM_RANGE = 600.0  # a window whose 5-95% RPM spread exceeds this is a transient


def calibrate_rules(val: list[dict]) -> dict:
    warm = np.array([w["total_trim"] for w in val if w["regime"] != "warmup"])
    ev = np.array([w["retard_events"] for w in val])
    n_min = next(n for n in range(2, 100) if (ev >= n).mean() <= 1 - RULE_QUANTILE)
    return {
        "total_trim_max": round(float(RULE_MARGIN * np.quantile(warm, RULE_QUANTILE)), 3),
        "total_trim_val_q999": round(float(np.quantile(warm, RULE_QUANTILE)), 3),
        "retard_events_min": int(n_min),
        "retard_events_val_frac_at_min": round(float((ev >= n_min).mean()), 5),
    }


def evaluate(files: dict[str, list[dict]], hours: dict[str, float], types: dict[str, str],
             model: HealthyModel, th: dict) -> dict:
    tot = Counter()
    ep = Counter()
    by_regime: dict[str, Counter] = {r: Counter() for r in REGIMES}
    by_type: dict[str, Counter] = {}
    trans = {"transient": Counter(), "steady": Counter()}
    per_file = []
    for f, ws in files.items():
        rl = RuleLayer()
        flags, adv, uncl, trim, ret, alarm_ids = [], [], [], [], [], set()
        for w in ws:
            d, thr = model.distance(w), model.threshold(w)
            pr = rules.evaluate(w, th)
            dec = rl.step(Evidence(models=[], physics_rules=pr, anomaly_score=d, anomaly_threshold=thr))
            flag = d > thr
            flags.append(flag)
            trim.append(pr[0]["fired"])
            ret.append(pr[1]["fired"])
            adv.append(dec["state"] == "ADVISORY")
            uncl.append(dec["state"] == "UNCLASSIFIED")
            if dec["state"] == "ALARM":
                alarm_ids.add(dec["alarm_id"])
            by_regime[w["regime"]].update(windows=1, flags=flag)
            by_type.setdefault(types[f], Counter()).update(windows=1, flags=flag)
            if types[f] != "idle":
                trans["transient" if w["rpm_range"] > TRANSIENT_RPM_RANGE else "steady"].update(windows=1, flags=flag)
        c = {"windows": len(ws), "flags": sum(flags), "trim": sum(trim), "retard": sum(ret),
             "advisory": sum(adv), "unclassified": sum(uncl)}
        e = {"flags": episodes(flags), "trim": episodes(trim), "retard": episodes(ret),
             "advisory": episodes(adv), "unclassified": episodes(uncl), "alarms": len(alarm_ids)}
        tot.update(c)
        ep.update(e)
        per_file.append({"file": f, "type": types[f], "hours": round(hours[f], 3), **c,
                         **{f"{k}_episodes": v for k, v in e.items()}})
    H = sum(hours[f] for f in files)
    n = tot["windows"]
    return {
        "files": len(files),
        "hours": round(H, 3),
        "windows": n,
        "levels": {
            "1_anomaly_flags": {"windows": proportion_summary(tot["flags"], n),
                                "episodes": rate_summary(ep["flags"], H)},
            "2_unclassified": {"windows": proportion_summary(tot["unclassified"], n),
                               "episodes": rate_summary(ep["unclassified"], H)},
            "3_advisories": {"windows": proportion_summary(tot["advisory"], n),
                             "episodes": rate_summary(ep["advisory"], H),
                             "trim_drift_rule_episodes": rate_summary(ep["trim"], H),
                             "timing_retard_rule_episodes": rate_summary(ep["retard"], H)},
            "4_classifier_false_faults": "not available: the OBD classifier is trained on simulator OBD "
                                         "channels, which sim v1 does not produce yet",
            "5_alarms": {**rate_summary(ep["alarms"], H),
                         "note": "structurally limited: ALARM needs a classifier witness, and there is none yet"},
        },
        "anomaly_flags_by_regime": {r: proportion_summary(c["flags"], c["windows"]) for r, c in by_regime.items()
                                    if c["windows"]},
        "anomaly_flags_by_type": {t: proportion_summary(c["flags"], c["windows"]) for t, c in sorted(by_type.items())},
        "rpm_transients_non_idle_files": {k: proportion_summary(c["flags"], c["windows"]) for k, c in trans.items()},
        "per_file": per_file,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=720)
    args = ap.parse_args()

    split = json.loads(SPLIT.read_text())
    names = split["calibration"] + split["heldout"]
    frames = {f: carobd.load(carobd.CAROBD_DIR / f) for f in names}
    types = {f: carobd.file_type(f) for f in names}
    hours = {f: carobd.duration_s(frames[f]) / 3600 for f in names}

    cal = split["calibration"]
    val_set = carobd_split.split({f: len(frames[f]) for f in cal}, {f: types[f] for f in cal}, args.seed, 0.30)
    fit_files = [f for f in cal if f not in val_set]
    val_files = [f for f in cal if f in val_set]

    mm = MapModel().fit([frames[f] for f in fit_files])
    W = {f: windows(frames[f], mm) for f in names}
    fit_w = [w for f in fit_files for w in W[f]]
    val_w = [w for f in val_files for w in W[f]]

    model = HealthyModel().fit(fit_w)
    model.set_thresholds(val_w, ANOMALY_QUANTILE)
    th = calibrate_rules(val_w)

    meta = {"created_at": utc_now_iso(), "git_commit": git_commit(), "seed": args.seed,
            "produced_by": "python -m models.obd.run_carobd"}
    write_json(THRESHOLDS, {
        **meta,
        "rule_basis": rules.RULE_BASIS,
        "source": "real carOBD calibration files only (data/splits/carobd_split.json), healthy",
        "window": {"seconds": WINDOW_S, "hop_seconds": HOP_S},
        "fit_files": fit_files, "val_files": val_files,
        "anomaly": {"method": f"Mahalanobis per regime, Ledoit-Wolf covariance, threshold = validation "
                              f"q{ANOMALY_QUANTILE}", **model.to_json()},
        "map_model": {"terms": list(MapModel.TERMS), "coef": mm.coef.round(5).tolist()},
        "rules": {"recipe": f"{RULE_MARGIN} x healthy validation q{RULE_QUANTILE} (trim); smallest event count "
                            f"with validation rate <= {1 - RULE_QUANTILE:.3f} (retard)", **th},
    })

    val_res = evaluate({f: W[f] for f in val_files}, hours, types, model, th)
    held_res = evaluate({f: W[f] for f in split["heldout"]}, hours, types, model, th)
    write_json(RESULTS, {
        **meta,
        "what": "OBD branch false positives on real healthy carOBD (plan 10.1, 13). Every flag is a false positive.",
        "data": "carOBD (2014 Toyota Etios), split by file: data/splits/carobd_split.json",
        "thresholds": "models/thresholds/obd_branch.json (fit + validation files only)",
        "stats": "rates: exact Poisson one-sided 95% upper bound (k = 0 -> ~3/T); window proportions: Wilson 95%",
        "episode_definition": "a run of consecutive windows (30 s, 15 s hop) in the same state within one file",
        "heldout": held_res,
        "validation": val_res,
    })

    print(f"fit {len(fit_files)} files / {len(fit_w)} windows, validation {len(val_files)} / {len(val_w)}, "
          f"held-out {held_res['files']} / {held_res['windows']} ({held_res['hours']:.2f} h)")
    print("rule thresholds:", th)
    print("anomaly thresholds:", {r: round(p["threshold"], 2) for r, p in model.params.items()})
    for name, res in (("validation", val_res), ("HELD-OUT", held_res)):
        L = res["levels"]
        print(f"\n{name}: {res['hours']:.2f} h, {res['windows']} windows")
        for k, lab in (("1_anomaly_flags", "anomaly flags"), ("2_unclassified", "UNCLASSIFIED"),
                       ("3_advisories", "ADVISORY")):
            e, wdw = L[k]["episodes"], L[k]["windows"]
            print(f"  {lab:14} {e['count']:4d} episodes = {e['per_hour']:.2f}/h (95% <= {e['upper95_per_hour']:.2f}/h);"
                  f" {100 * wdw['rate']:.2f}% of windows")
        print(f"  {'ALARM':14} {L['5_alarms']['count']:4d} (structurally limited, no classifier)")
        print("  transients vs steady (flag rate):",
              {k: v["rate"] for k, v in res["rpm_transients_non_idle_files"].items()})
    print(f"\nWrote {THRESHOLDS.relative_to(REPO)} and {RESULTS.relative_to(REPO)}")


if __name__ == "__main__":
    main()
