"""OBD-only classifier trained on the SIMULATOR's OBD channels, run on REAL held-out carOBD (plan 10.1).

    python -m models.obd.sim_classifier        # ~10-20 min: simulates, trains, validates, reads carOBD once

The plan's honest sim-to-real test: classes healthy / injector_clog / knock (retard pattern), trained
only on sim 2.1 OBD rows, then run on the 39 held-out carOBD files, where every engine is healthy, so
every fault the classifier names is a false fault.

* Same feature code for sim and real: sim OBD rows are renamed to carOBD columns and go through
  models/obd/features.py (30 s windows, 15 s hop, fresh-value logic), with the MAP model and the healthy
  model of the pre-registered OBD branch v1 (models/thresholds/obd_branch.json), fitted on carOBD
  calibration files only. Vehicle speed is not simulated (set to 0; not a classifier input).
* Sim data: own engine ranges (eval/common.py SEEDS obd_clf_train / obd_clf_val), hard regimes on,
  purpose "training" (the simulator refuses valve_tick). Each run: 60 s healthy warm-up, then 300 s with the
  fault (clog s 0.1-0.6 or knock p 0.3-1.0 on one random cylinder, or none); the operating point
  changes every 45 s (rpm 700-2950, outside the held-out 3000-3500 band; load 3-45 N m) so transients
  are in the training data. Windows that end before the fault are healthy; windows starting >= 15 s
  after it carry the fault label; windows in between are dropped.
* Model: RBF SVM, class-weighted, Platt probabilities, on the 14 OBD features (healthy-model features
  + total trim). Validation engines only are used to report sim accuracy; nothing is tuned on carOBD.
* carOBD: every held-out window goes through backend/rule_layer.py with all three witnesses: this
  classifier (label + p; the rule layer's own P_MIN applies), the OBD physics rules, and the v1 healthy
  model. Reported at every level, now including ALARM, which is no longer structurally impossible.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, f1_score, recall_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "data"))
import carobd  # noqa: E402
from _common import git_commit, utc_now_iso, write_json  # noqa: E402

from backend.rule_layer import P_MIN, Evidence, RuleLayer  # noqa: E402
from eval.common import SEEDS  # noqa: E402
from eval.stats import episodes, proportion_summary, rate_summary  # noqa: E402
from models.obd import features as F  # noqa: E402
from models.obd import rules  # noqa: E402
from models.obd.healthy import FEATURES as HM_FEATURES  # noqa: E402
from models.obd.healthy import HealthyModel  # noqa: E402
from sim import SIM_VERSION, EngineParams, EngineSim  # noqa: E402
from sim.params import Regimes  # noqa: E402

V1 = REPO / "models" / "thresholds" / "obd_branch.json"
SPLIT = REPO / "data" / "splits" / "carobd_split.json"
OUT = REPO / "eval" / "obd_classifier_carobd.json"

CLASSES = ("healthy", "injector_clog", "knock")
CLF_FEATURES = tuple(HM_FEATURES) + ("total_trim",)
WARMUP_S, FAULT_S, SEGMENT_S, SETTLE_S = 60.0, 300.0, 45.0, 15.0
RPM_RANGE, LOAD_RANGE = (700.0, 2950.0), (3.0, 45.0)
SEVERITY = {"injector_clog": (0.1, 0.6), "knock": (0.3, 1.0)}
SIM_TO_CAROBD = {"rpm": F.RPM, "load_pct": F.LOAD, "map_kpa": F.MAP, "stft_pct": F.STFT, "ltft_pct": F.LTFT,
                 "spark_adv_deg": F.TIMING, "coolant_c": F.COOLANT}


def jobs() -> list[dict]:
    out = []
    for split in ("train", "val"):
        engines, off = SEEDS[f"obd_clf_{split}"]
        for e in engines:
            rng = np.random.default_rng([e, 0x0BD])
            for k, cls in enumerate(CLASSES):
                lo, hi = SEVERITY.get(cls, (0.0, 0.0))
                out.append({"split": split, "engine": e, "run_seed": off + 10 * e + k, "fault": cls,
                            "cylinder": int(rng.integers(1, 5)), "severity": round(float(rng.uniform(lo, hi)), 3),
                            "schedule": [(round(float(rng.uniform(*RPM_RANGE)), 1), round(float(rng.uniform(*LOAD_RANGE)), 2))
                                         for _ in range(int((WARMUP_S + FAULT_S) // SEGMENT_S) + 1)]})
    return out


def sim_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).rename(columns=SIM_TO_CAROBD)
    df["t_s"] = df["t"] - df["t"].iloc[0]
    df[F.SPEED] = 0.0
    return df.dropna(subset=[F.RPM])  # sensor dropouts (regimes) leave gaps


def run_one(job: dict) -> list[dict]:
    sim = EngineSim(EngineParams.randomised(job["engine"], "train"), seed=job["run_seed"],
                    regimes=Regimes.eval(), purpose="training")
    rows, seg = [], -1
    for k in range(int((WARMUP_S + FAULT_S) / 0.5)):
        t = k * 0.5
        if int(t // SEGMENT_S) != seg:
            seg = int(t // SEGMENT_S)
            rpm, load = job["schedule"][seg]
            sim.set_operating_point(rpm_target=rpm, load_nm=load)
        if job["fault"] != "healthy" and t == WARMUP_S:
            sim.inject(job["fault"], job["cylinder"], job["severity"])
        rows += sim.step(0.5)["obd"]
    mm = F.MapModel(json.loads(V1.read_text())["map_model"]["coef"])
    out = []
    for w in F.windows(sim_frame(rows), mm):
        if w["t0"] < 30.0:  # start-up transient of the run
            continue
        if job["fault"] == "healthy" or w["t0"] + F.WINDOW_S <= WARMUP_S:
            label = "healthy"
        elif w["t0"] >= WARMUP_S + SETTLE_S:
            label = job["fault"]
        else:
            continue
        out.append({**w, "label": label, "engine": job["engine"], "split": job["split"], "run_fault": job["fault"]})
    return out


def xmat(ws: list[dict]) -> np.ndarray:
    return np.array([[w[f] for f in CLF_FEATURES] for w in ws], float)


def classifier_witness(clf, X: np.ndarray) -> list[dict]:
    proba = clf.predict_proba(X)
    labels = clf.classes_
    return [{"model": "OBD-SVM", "label": str(labels[i]), "p": round(float(p[i]), 4)}
            for p in proba for i in [int(np.argmax(p))]]


def carobd_eval(clf, model: HealthyModel, th: dict, mm: F.MapModel) -> dict:
    split = json.loads(SPLIT.read_text())
    tot, ep = Counter(), Counter()
    per_file, faults_named = [], Counter()
    hours = 0.0
    for f in split["heldout"]:
        df = carobd.load(carobd.CAROBD_DIR / f)
        hours += carobd.duration_s(df) / 3600
        ws = F.windows(df, mm)
        wit = classifier_witness(clf, xmat(ws)) if ws else []
        rl = RuleLayer()
        series = {k: [] for k in ("flag", "clf_fault", "advisory", "unclassified")}
        alarms = set()
        for w, m in zip(ws, wit):
            d, thr = model.distance(w), model.threshold(w)
            dec = rl.step(Evidence(models=[m], physics_rules=rules.evaluate(w, th), anomaly_score=d, anomaly_threshold=thr))
            clf_fault = m["label"] != "healthy" and m["p"] >= P_MIN
            if clf_fault:
                faults_named[m["label"]] += 1
            series["flag"].append(d > thr)
            series["clf_fault"].append(clf_fault)
            series["advisory"].append(dec["state"] == "ADVISORY")
            series["unclassified"].append(dec["state"] == "UNCLASSIFIED")
            if dec["state"] == "ALARM":
                alarms.add(dec["alarm_id"])
        tot.update({k: sum(v) for k, v in series.items()} | {"windows": len(ws)})
        ep.update({k: episodes(v) for k, v in series.items()} | {"alarms": len(alarms)})
        per_file.append({"file": f, "windows": len(ws), "clf_fault_windows": sum(series["clf_fault"]),
                         "alarms": len(alarms)})
    n = tot["windows"]
    lv = lambda k: {"windows": proportion_summary(tot[k], n), "episodes": rate_summary(ep[k], hours)}
    return {"files": len(split["heldout"]), "hours": round(hours, 3), "windows": n,
            "levels": {"1_anomaly_flags": lv("flag"), "2_unclassified": lv("unclassified"),
                       "3_advisories": lv("advisory"), "4_classifier_false_faults": {**lv("clf_fault"),
                       "by_fault": dict(faults_named)},
                       "5_alarms": rate_summary(ep["alarms"], hours)},
            "per_file": per_file}


def main() -> None:
    t0 = time.time()
    js = jobs()
    with ProcessPoolExecutor() as ex:
        W = [w for ws in ex.map(run_one, js, chunksize=2) for w in ws]
    tr = [w for w in W if w["split"] == "train"]
    va = [w for w in W if w["split"] == "val"]
    print(f"simulated {len(js)} runs -> {len(tr)} train / {len(va)} val windows in {time.time() - t0:.0f} s; "
          f"train labels {dict(Counter(w['label'] for w in tr))}")

    clf = make_pipeline(StandardScaler(), SVC(C=1.0, gamma="scale", class_weight="balanced",
                                              probability=True, random_state=0))
    clf.fit(xmat(tr), [w["label"] for w in tr])
    y_va = [w["label"] for w in va]
    p_va = clf.predict(xmat(va))
    wit_va = classifier_witness(clf, xmat(va))
    called = [m["label"] if m["label"] != "healthy" and m["p"] >= P_MIN else "healthy" for m in wit_va]
    healthy_va = [i for i, y in enumerate(y_va) if y == "healthy"]
    sim_val = {
        "engines": SEEDS["obd_clf_val"][0][0:1] + SEEDS["obd_clf_val"][0][-1:], "windows": len(va),
        "macro_f1": round(float(f1_score(y_va, p_va, average="macro")), 4),
        "recall": {c: round(float(r), 4) for c, r in zip(CLASSES, recall_score(y_va, p_va, labels=list(CLASSES), average=None))},
        "confusion_rows_true_cols_pred": {"labels": list(CLASSES),
                                          "matrix": confusion_matrix(y_va, p_va, labels=list(CLASSES)).tolist()},
        "false_fault_on_healthy_windows_at_p_min": proportion_summary(
            sum(called[i] != "healthy" for i in healthy_va), len(healthy_va)),
    }

    v1 = json.loads(V1.read_text())
    model = HealthyModel.from_json(v1["anomaly"])
    mm = F.MapModel(v1["map_model"]["coef"])
    real = carobd_eval(clf, model, v1["rules"], mm)

    write_json(OUT, {
        "created_at": utc_now_iso(), "git_commit": git_commit(), "produced_by": "python -m models.obd.sim_classifier",
        "what": "OBD-only classifier trained on SIMULATED OBD (sim 2.1), false faults on REAL held-out carOBD (plan 10.1)",
        "columns": {"sim_validation": "A (simulated)", "carobd_heldout": "C (real; every fault named is false)"},
        "sim_version": SIM_VERSION, "features": list(CLF_FEATURES), "p_min": P_MIN,
        "sim_data": {"runs": len(js), "train_windows": len(tr), "val_windows": len(va),
                     "train_engines": [SEEDS["obd_clf_train"][0][0], SEEDS["obd_clf_train"][0][-1]],
                     "val_engines": [SEEDS["obd_clf_val"][0][0], SEEDS["obd_clf_val"][0][-1]],
                     "warmup_s": WARMUP_S, "fault_s": FAULT_S, "operating_point_every_s": SEGMENT_S},
        "healthy_model_and_rules": "pre-registered OBD branch v1 (models/thresholds/obd_branch.json)",
        "sim_validation": sim_val,
        "carobd_heldout": real,
    })
    L = real["levels"]
    print(f"sim validation: macro-F1 {sim_val['macro_f1']}, recall {sim_val['recall']}, false fault on healthy "
          f"{sim_val['false_fault_on_healthy_windows_at_p_min']['rate']}")
    print(f"carOBD held-out {real['hours']:.2f} h, {real['windows']} windows:")
    for k in ("1_anomaly_flags", "2_unclassified", "3_advisories", "4_classifier_false_faults"):
        e = L[k]["episodes"]
        print(f"  {k:28} {e['count']:4d} episodes = {e['per_hour']:.2f}/h (95% <= {e['upper95_per_hour']:.2f}/h); "
              f"{100 * L[k]['windows']['rate']:.2f}% of windows")
    print(f"  classifier false faults by fault: {L['4_classifier_false_faults']['by_fault']}")
    a = L["5_alarms"]
    print(f"  {'5_alarms':28} {a['count']:4d} = {a['per_hour']:.2f}/h (95% <= {a['upper95_per_hour']:.2f}/h)")
    print(f"Wrote {OUT.relative_to(REPO)} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
