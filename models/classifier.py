"""Fast classifier + healthy model on the per-window hand features (plan sections 9 and 10).

Classifier: RBF SVM (class-weighted) with sigmoid-calibrated probabilities. The calibration folds
are grouped by run, so no run is both fitted and calibrated on. Output: class, p, and for a named
fault the cylinder, from a per-fault multinomial logistic regression on the same features.

Healthy model: Mahalanobis distance per rpm band (idle / mid / high, as the physics thresholds) with
Ledoit-Wolf shrinkage covariance, fitted on windows of HEALTHY TRAIN runs; threshold = the
(1 - ANOMALY_FPR) quantile of the distance on VAL healthy runs (fixed false-positive rate). Only windows
with crank and accelerometer present are used and scored: a sensor dropout is sensor health, not an anomaly.

Trained on train; checked on val; test is only read by the evaluation (stage 2).
Writes models/checkpoints/fast_v1.pkl (git-ignored) and models/fast_v1.json (metadata, committed).
Usage:  python -m models.classifier
"""

from __future__ import annotations

import json
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.covariance import LedoitWolf
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from models.features import FEATURE_NAMES
from models.make_dataset import CLASSES, MANIFEST, load_split

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = ROOT / "models" / "checkpoints" / "fast_v1.pkl"
META = ROOT / "models" / "fast_v1.json"
MAX_PER_CLASS = 3000          # SVM training windows per fault class (random, by window, inside train only)
MAX_HEALTHY = 9000            # healthy covers every operating point, so it gets more
SVM_C, SVM_GAMMA = float(__import__("os").environ.get("S720_SVM_C", 1.0)), "scale"
ANOMALY_FPR = 1e-3
BANDS = (("idle", 0.0, 1100.0), ("mid", 1100.0, 2000.0), ("high", 2000.0, 1e9))
SEED = 720


def band_of(rpm: float) -> str:
    return next(n for n, lo, hi in BANDS if lo <= rpm < hi)


class FastModel:
    """Everything the live witness needs, picklable."""

    def __init__(self, scaler, svm, cyl_models, maha, feature_names, class_maha=None):
        self.scaler, self.svm, self.cyl_models, self.maha = scaler, svm, cyl_models, maha
        self.feature_names = list(feature_names)
        self.class_maha = class_maha or {}

    def in_class(self, X: np.ndarray, labels) -> np.ndarray:
        """Open-set check: is each window within its predicted fault class's training distribution
        (class-conditional Mahalanobis below the class's 99.9 % val quantile)? Healthy is always True."""
        Z = self.scaler.transform(np.atleast_2d(X))
        out = np.ones(len(Z), dtype=bool)
        for i, lab in enumerate(labels):
            m = self.class_maha.get(lab)
            if m is not None:
                dz = Z[i] - m["mean"]
                out[i] = float(np.sqrt(dz @ m["prec"] @ dz)) <= m["threshold"]
        return out

    def predict(self, X: np.ndarray):
        """(labels, p_max, cylinders) for windows X (n x features)."""
        Z = self.scaler.transform(np.atleast_2d(X))
        P = self.svm.predict_proba(Z)
        k = P.argmax(axis=1)
        cyl = np.zeros(len(k), dtype=int)
        for i, ci in enumerate(k):
            m = self.cyl_models.get(CLASSES[ci])
            if m is not None:
                cyl[i] = int(m.predict(Z[i:i + 1])[0])
        return [CLASSES[i] for i in k], P.max(axis=1), cyl, P

    def anomaly(self, X: np.ndarray, rpm: np.ndarray):
        """(Mahalanobis distance, band threshold) per window."""
        Z = self.scaler.transform(np.atleast_2d(X))
        d, thr = np.zeros(len(Z)), np.zeros(len(Z))
        for i, (z, r) in enumerate(zip(Z, np.atleast_1d(rpm))):
            m = self.maha[band_of(float(r))]
            dz = z - m["mean"]
            d[i] = float(np.sqrt(dz @ m["prec"] @ dz))
            thr[i] = m["threshold"]
        return d, thr


def _healthy_runs_mask(split, run_idx, man):
    runs = [j for j in man["runs"] if j["split"] == split]
    healthy_run = np.array([runs[i]["fault"] == "healthy" for i in run_idx])
    return healthy_run


def train(manifest: dict | None = None) -> dict:
    t0 = time.perf_counter()
    man = manifest or json.loads(MANIFEST.read_text())
    X, y, cyl, meta, run = load_split("train", man)
    Xv, yv, cylv, metav, runv = load_split("val", man)
    # the SVM also sees the healthy-only train runs: more operating points per engine (light load at
    # high rpm was thin, and healthy engines there were called injector_clog on val)
    Xe, ye, _, _, re_ = load_split("train_healthy", man)
    Xs, ys, rs = np.vstack([X, Xe]), np.concatenate([y, ye]), np.concatenate([run, re_ + run.max() + 1])
    rng = np.random.default_rng(SEED)
    cap = {0: MAX_HEALTHY}
    idx = np.concatenate([rng.permutation(np.flatnonzero(ys == c))[:cap.get(c, MAX_PER_CLASS)] for c in range(len(CLASSES))])
    scaler = StandardScaler().fit(X)
    Z, Zv = scaler.transform(X), scaler.transform(Xv)
    Zs = scaler.transform(Xs)
    folds = list(GroupKFold(3).split(Zs[idx], ys[idx], rs[idx]))
    svm = CalibratedClassifierCV(SVC(kernel="rbf", C=SVM_C, gamma=SVM_GAMMA, class_weight="balanced"),
                                 method="sigmoid", cv=folds).fit(Zs[idx], ys[idx])
    cyl_models = {}
    for ci, c in enumerate(CLASSES):
        if c == "healthy":
            continue
        sel = (y == ci) & (cyl > 0)
        cyl_models[c] = LogisticRegression(max_iter=2000, C=1.0).fit(Z[sel], cyl[sel])

    maha = {}
    # healthy model data: healthy runs of train (+ the extra healthy-only runs), threshold on val's
    Xh, yh, _, mh, rh = load_split("train_healthy", man)
    Xhv, yhv, _, mhv, rhv = load_split("val_healthy", man)
    valid = lambda m: (m[:, 2] > 0) & (m[:, 3] > 0)        # crank and accelerometer both present (no dropout)
    h_tr = np.concatenate([_healthy_runs_mask("train", run, man) & (y == 0) & valid(meta), valid(mh)])
    h_va = np.concatenate([_healthy_runs_mask("val", runv, man) & (yv == 0) & valid(metav), valid(mhv)])
    Z = np.vstack([Z, scaler.transform(Xh)])
    meta = np.vstack([meta, mh])
    Zv_all = np.vstack([Zv, scaler.transform(Xhv)])
    metav_all = np.vstack([metav, mhv])
    for name, lo, hi in BANDS:
        s = h_tr & (meta[:, 1] >= lo) & (meta[:, 1] < hi)
        lw = LedoitWolf().fit(Z[s])
        prec = np.linalg.pinv(lw.covariance_)
        sv = h_va & (metav_all[:, 1] >= lo) & (metav_all[:, 1] < hi)
        dz = Zv_all[sv] - lw.location_
        dv = np.sqrt(np.einsum("ij,jk,ik->i", dz, prec, dz))
        maha[name] = {"mean": lw.location_, "prec": prec, "threshold": float(np.quantile(dv, 1 - ANOMALY_FPR)),
                      "shrinkage": float(lw.shrinkage_), "n_train": int(s.sum()), "n_val": int(sv.sum())}

    # open-set rejection per fault class: Ledoit-Wolf on that class's train windows, threshold on val's
    class_maha = {}
    Ztr, Zva = scaler.transform(X), scaler.transform(Xv)
    for ci, c in enumerate(CLASSES):
        if c == "healthy":
            continue
        lw = LedoitWolf().fit(Ztr[y == ci])
        prec = np.linalg.pinv(lw.covariance_)
        dz = Zva[yv == ci] - lw.location_
        dv = np.sqrt(np.einsum("ij,jk,ik->i", dz, prec, dz))
        class_maha[c] = {"mean": lw.location_, "prec": prec, "threshold": float(np.quantile(dv, 1 - ANOMALY_FPR)),
                         "n_train": int((y == ci).sum()), "n_val": int((yv == ci).sum())}
    model = FastModel(scaler, svm, cyl_models, maha, FEATURE_NAMES, class_maha)
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT, "wb") as f:
        pickle.dump(model, f)

    # validation summary (val only; test is for stage 2)
    t1 = time.perf_counter()
    lab, p, cy, _ = model.predict(Xv)
    ms_per_window = (time.perf_counter() - t1) * 1e3 / len(Xv)
    pred = np.array([CLASSES.index(l) for l in lab])
    recall = {c: float((pred[yv == i] == i).mean()) for i, c in enumerate(CLASSES)}
    f1 = []
    for i in range(len(CLASSES)):
        tp = ((pred == i) & (yv == i)).sum()
        prec_i = tp / max((pred == i).sum(), 1)
        rec_i = tp / max((yv == i).sum(), 1)
        f1.append(0.0 if tp == 0 else 2 * prec_i * rec_i / (prec_i + rec_i))
    named = (yv > 0) & (pred == yv)
    loc = float((cy[named] == cylv[named]).mean()) if named.any() else float("nan")
    labh, ph, _, _ = model.predict(Xhv)
    val_healthy_ff = float(np.mean((np.array(labh) != "healthy") & (ph >= 0.6)))
    d, thr = model.anomaly(Xv, metav[:, 1])
    ok = (metav[:, 2] > 0) & (metav[:, 3] > 0)
    d, thr, yv_a = d[ok], thr[ok], yv[ok]
    doc = {
        "model": "fast_v1", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": str(MANIFEST.relative_to(ROOT)), "sim_version": man["sim_version"], "classes": list(CLASSES),
        "features": list(FEATURE_NAMES), "svm": {"kernel": "rbf", "C": SVM_C, "gamma": SVM_GAMMA,
                                                 "class_weight": "balanced", "calibration": "sigmoid, 3 folds grouped by run",
                                                 "train_windows": int(len(idx)), "max_per_class": MAX_PER_CLASS},
        "cylinder": "multinomial logistic regression per fault on the same standardised features",
        "open_set": {"method": "class-conditional Mahalanobis (Ledoit-Wolf) per fault class; the classifier witness "
                               "abstains (p capped below P_MIN) when a window is outside its predicted class",
                     "threshold": f"(1 - {ANOMALY_FPR}) quantile of val windows of that class",
                     "classes": {k: {kk: v[kk] for kk in ("threshold", "n_train", "n_val")} for k, v in class_maha.items()}},
        "healthy_model": {"method": "Mahalanobis per rpm band, Ledoit-Wolf shrinkage, healthy TRAIN runs",
                          "threshold": f"(1 - {ANOMALY_FPR}) quantile on VAL healthy runs",
                          "bands": {k: {kk: v[kk] for kk in ("threshold", "shrinkage", "n_train", "n_val")}
                                    for k, v in maha.items()}},
        "val": {"windows": int(len(yv)), "per_class_windows": {c: int((yv == i).sum()) for i, c in enumerate(CLASSES)},
                "recall": recall, "macro_f1": float(np.mean(f1)), "localisation_accuracy": loc,
                "anomaly_rate_on_faulty_windows": float((d[yv_a > 0] > thr[yv_a > 0]).mean()),
                "anomaly_rate_on_healthy_windows": float((d[yv_a == 0] > thr[yv_a == 0]).mean()),
                "false_fault_rate_val_healthy_runs": val_healthy_ff,
                "anomaly_note": "valid windows only (no crank or accelerometer dropout); a dropout window has no score"},
        "cpu_ms_per_window": round(ms_per_window, 3),
        "fit_s": round(t1 - t0, 1),
    }
    META.write_text(json.dumps(doc, indent=1))
    return doc


def load(path: Path = CHECKPOINT) -> FastModel:
    with open(path, "rb") as f:
        return pickle.load(f)


if __name__ == "__main__":
    from models import classifier as _module      # pickle FastModel as models.classifier.FastModel, not __main__
    print(json.dumps(_module.train(), indent=1))
