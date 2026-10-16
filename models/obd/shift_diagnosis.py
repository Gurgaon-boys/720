"""Why does the sim-trained OBD classifier call real healthy carOBD a fault? -> eval/obd_sim_real_shift.json

    python -m models.obd.shift_diagnosis

Compares the 14 classifier features on HEALTHY simulator windows (the classifier's own training runs)
with warm windows of the carOBD CALIBRATION files (never the held-out files), feature by feature
(standardised mean difference), and reports how often the classifier names a fault on those
calibration windows. Diagnosis only: nothing here is fitted or tuned.
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import json  # noqa: E402
import sys  # noqa: E402
from collections import Counter  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "data"))
import carobd  # noqa: E402
from _common import git_commit, utc_now_iso, write_json  # noqa: E402

from models.obd import features as F  # noqa: E402
from models.obd import sim_classifier as M  # noqa: E402

OUT = REPO / "eval" / "obd_sim_real_shift.json"


def main() -> None:
    js = [j for j in M.jobs() if j["split"] == "train"]
    with ProcessPoolExecutor() as ex:
        W = [w for ws in ex.map(M.run_one, js, chunksize=2) for w in ws]
    sim_h = [w for w in W if w["label"] == "healthy"]
    v1 = json.loads(M.V1.read_text())
    mm = F.MapModel(v1["map_model"]["coef"])
    cal = json.loads(M.SPLIT.read_text())["calibration"]
    real = [w for f in cal for w in F.windows(carobd.load(carobd.CAROBD_DIR / f), mm) if w["regime"] != "warmup"]

    rows = []
    for f in M.CLF_FEATURES:
        a, b = np.array([w[f] for w in sim_h]), np.array([w[f] for w in real])
        sd = float(np.sqrt((a.var() + b.var()) / 2)) or 1.0
        rows.append({"feature": f, "shift_sd": round(abs(a.mean() - b.mean()) / sd, 3),
                     "sim_mean": round(float(a.mean()), 3), "sim_sd": round(float(a.std()), 3),
                     "carobd_mean": round(float(b.mean()), 3), "carobd_sd": round(float(b.std()), 3)})
    rows.sort(key=lambda r: -r["shift_sd"])

    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import SVC
    tr = [w for w in W if w["split"] == "train"]
    clf = make_pipeline(StandardScaler(), SVC(C=1.0, gamma="scale", class_weight="balanced", probability=True, random_state=0))
    clf.fit(M.xmat(tr), [w["label"] for w in tr])
    wit = M.classifier_witness(clf, M.xmat(real))
    called = Counter(m["label"] if m["label"] != "healthy" and m["p"] >= M.P_MIN else "healthy" for m in wit)
    rpm_sim, rpm_real = np.array([w["rpm_mean"] for w in sim_h]), np.array([w["rpm_mean"] for w in real])

    write_json(OUT, {
        "created_at": utc_now_iso(), "git_commit": git_commit(), "produced_by": "python -m models.obd.shift_diagnosis",
        "what": "sim-vs-real feature shift for the OBD classifier (diagnosis; carOBD CALIBRATION files only)",
        "sim_healthy_windows": len(sim_h), "carobd_calibration_windows": len(real),
        "classifier_calls_on_carobd_calibration": dict(called),
        "features_by_shift": rows,
        "operating_point": {"sim_rpm_p5_p50_p95": [round(float(v)) for v in np.percentile(rpm_sim, [5, 50, 95])],
                            "carobd_rpm_p5_p50_p95": [round(float(v)) for v in np.percentile(rpm_real, [5, 50, 95])]},
    })
    print(f"sim healthy {len(sim_h)} windows vs carOBD calibration {len(real)} windows; "
          f"classifier on calibration: {dict(called)}")
    for r in rows:
        print(f"  {r['feature']:16} {r['shift_sd']:5.2f} sd | sim {r['sim_mean']:8.2f} ({r['sim_sd']:.2f}) | "
              f"carOBD {r['carobd_mean']:8.2f} ({r['carobd_sd']:.2f})")
    print(f"Wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
