"""OBD classifier v2 (POST-HOC, D128): fixes aimed at the causes found in D126 -> eval/obd_classifier_v2.json

    python -m models.obd.sim_classifier_v2

v1 (D125/D126) called 73% of real healthy carOBD windows a fault because it keyed on operating point
and ECU behaviour sim 2.1 doesn't reproduce (load, spark-advance level and spread, rpm), while the
fault signal itself (total trim) matched. v2 changes exactly two things:
  1. features: only fault-relevant, operating-point-free ones: total trim, STFT mean, LTFT mean,
     spark-retard events (no load, rpm, spark level, MAP residual, coolant);
  2. operating points: rpm drawn from the carOBD CALIBRATION windows' rpm distribution (clipped to
     700-2950, outside the held-out 3000-3500 band), load 3-25 N m instead of 3-45.
Everything else is v1's code (runs, labels, SVM, P_MIN, rule layer, v1 healthy model and OBD rules).

It is designed AFTER v1's held-out result, so the held-out files are NOT read here: v2 is evaluated
on simulated validation engines and on the 90 carOBD CALIBRATION files (29.1 h, all healthy; never
used to train this classifier), reported as development evidence. v1 on the same calibration files
(the D126 diagnosis: 61% of windows called a fault) is the comparison.
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402
from concurrent.futures import ProcessPoolExecutor, as_completed  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
from sklearn.metrics import f1_score, recall_score  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from sklearn.svm import SVC  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "data"))
import carobd  # noqa: E402
from _common import git_commit, utc_now_iso, write_json  # noqa: E402

from backend.rule_layer import P_MIN, Evidence, RuleLayer  # noqa: E402
from eval.common import SEEDS  # noqa: E402
from eval.stats import episodes, proportion_summary, rate_summary  # noqa: E402
from models.obd import features as F  # noqa: E402
from models.obd import rules  # noqa: E402
from models.obd import sim_classifier as V1  # noqa: E402
from models.obd.healthy import HealthyModel  # noqa: E402

OUT = REPO / "eval" / "obd_classifier_v2.json"
FEATURES_V2 = ("total_trim", "stft_mean", "ltft_mean", "retard_events")
LOAD_RANGE_V2 = (3.0, 25.0)


def calibration_windows() -> dict[str, list[dict]]:
    v1 = json.loads(V1.V1.read_text())
    mm = F.MapModel(v1["map_model"]["coef"])
    cal = json.loads(V1.SPLIT.read_text())["calibration"]
    return {f: F.windows(carobd.load(carobd.CAROBD_DIR / f), mm) for f in cal}


def jobs_v2(rpm_pool: np.ndarray) -> list[dict]:
    out = V1.jobs()
    for j in out:
        rng = np.random.default_rng([j["engine"], 0x0BD2, V1.CLASSES.index(j["fault"])])
        j["schedule"] = [(round(float(np.clip(rng.choice(rpm_pool), 700, 2950)), 1),
                          round(float(rng.uniform(*LOAD_RANGE_V2)), 2)) for _ in j["schedule"]]
    return out


def xmat(ws: list[dict]) -> np.ndarray:
    return np.array([[w[f] for f in FEATURES_V2] for w in ws], float)


def main() -> None:
    t0 = time.time()
    cal = calibration_windows()
    rpm_pool = np.array([w["rpm_mean"] for ws in cal.values() for w in ws if w["regime"] != "warmup"])
    js = jobs_v2(rpm_pool)
    W = []
    with ProcessPoolExecutor() as ex:
        futs = [ex.submit(V1.run_one, j) for j in js]
        for i, fut in enumerate(as_completed(futs), 1):
            W += fut.result()
            if i % 25 == 0 or i == len(js):
                print(f"  {i}/{len(js)} runs simulated ({time.time() - t0:.0f} s)", flush=True)
    W.sort(key=lambda w: (w["split"], w["engine"], w["run_fault"], w["t0"]))
    tr = [w for w in W if w["split"] == "train"]
    va = [w for w in W if w["split"] == "val"]

    clf = make_pipeline(StandardScaler(), SVC(C=1.0, gamma="scale", class_weight="balanced",
                                              probability=True, random_state=0))
    clf.fit(xmat(tr), [w["label"] for w in tr])
    y_va, p_va = [w["label"] for w in va], clf.predict(xmat(va))

    def witness(ws):
        proba = clf.predict_proba(xmat(ws)) if ws else np.zeros((0, len(clf.classes_)))
        return [{"model": "OBD-SVM-v2", "label": str(clf.classes_[int(np.argmax(p))]), "p": float(np.max(p))}
                for p in proba]

    called_va = [m["label"] if m["label"] != "healthy" and m["p"] >= P_MIN else "healthy" for m in witness(va)]
    hv = [i for i, y in enumerate(y_va) if y == "healthy"]

    v1 = json.loads(V1.V1.read_text())
    model = HealthyModel.from_json(v1["anomaly"])
    th = v1["rules"]
    tot, ep, named = Counter(), Counter(), Counter()
    hours = 0.0
    for f, ws in cal.items():
        hours += carobd.duration_s(carobd.load(carobd.CAROBD_DIR / f)) / 3600
        rl, s, alarms = RuleLayer(), {k: [] for k in ("clf_fault", "advisory", "unclassified")}, set()
        for w, m in zip(ws, witness(ws)):
            dec = rl.step(Evidence(models=[m], physics_rules=rules.evaluate(w, th),
                                   anomaly_score=model.distance(w), anomaly_threshold=model.threshold(w)))
            cf = m["label"] != "healthy" and m["p"] >= P_MIN
            named[m["label"]] += cf
            s["clf_fault"].append(cf)
            s["advisory"].append(dec["state"] == "ADVISORY")
            s["unclassified"].append(dec["state"] == "UNCLASSIFIED")
            if dec["state"] == "ALARM":
                alarms.add(dec["alarm_id"])
        tot.update({k: sum(v) for k, v in s.items()} | {"windows": len(ws)})
        ep.update({k: episodes(v) for k, v in s.items()} | {"alarms": len(alarms)})
    n = tot["windows"]
    lv = lambda k: {"windows": proportion_summary(tot[k], n), "episodes": rate_summary(ep[k], hours)}
    cal_res = {"files": len(cal), "hours": round(hours, 3), "windows": n,
               "classifier_false_faults": {**lv("clf_fault"), "by_fault": {k: v for k, v in named.items() if v}},
               "advisories": lv("advisory"), "unclassified": lv("unclassified"),
               "alarms": rate_summary(ep["alarms"], hours)}

    shift = json.loads((REPO / "eval" / "obd_sim_real_shift.json").read_text())
    v1_cal = shift["classifier_calls_on_carobd_calibration"]
    write_json(OUT, {
        "created_at": utc_now_iso(), "git_commit": git_commit(), "produced_by": "python -m models.obd.sim_classifier_v2",
        "status": "POST-HOC (D128): designed after v1's held-out result; held-out files NOT read",
        "features": list(FEATURES_V2), "load_range_nm": list(LOAD_RANGE_V2),
        "rpm_source": f"carOBD calibration windows ({len(rpm_pool)}), clipped to 700-2950",
        "sim_validation": {"windows": len(va), "macro_f1": round(float(f1_score(y_va, p_va, average="macro")), 4),
                           "recall": {c: round(float(r), 4) for c, r in
                                      zip(V1.CLASSES, recall_score(y_va, p_va, labels=list(V1.CLASSES), average=None))},
                           "false_fault_on_healthy_at_p_min": proportion_summary(
                               sum(called_va[i] != "healthy" for i in hv), len(hv))},
        "carobd_calibration": cal_res,
        "v1_on_calibration_for_comparison": {"calls": v1_cal, "fault_share": round(
            1 - v1_cal.get("healthy", 0) / sum(v1_cal.values()), 4), "source": "eval/obd_sim_real_shift.json (D126)"},
    })
    c = cal_res["classifier_false_faults"]
    print(f"sim validation: macro-F1 {json.loads(OUT.read_text())['sim_validation']['macro_f1']}, recall "
          f"{json.loads(OUT.read_text())['sim_validation']['recall']}")
    print(f"carOBD calibration ({cal_res['hours']:.1f} h, {n} windows): classifier false faults "
          f"{100 * c['windows']['rate']:.1f}% of windows, {c['episodes']['per_hour']:.2f} episodes/h "
          f"(<= {c['episodes']['upper95_per_hour']:.2f}); by fault {c['by_fault']}; ADVISORY "
          f"{cal_res['advisories']['episodes']['per_hour']:.2f}/h; ALARM {cal_res['alarms']['count']} "
          f"({cal_res['alarms']['per_hour']:.2f}/h)")
    print(f"v1 on the same calibration windows: {100 * json.loads(OUT.read_text())['v1_on_calibration_for_comparison']['fault_share']:.1f}% called a fault")
    print(f"Wrote {OUT.relative_to(REPO)} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
