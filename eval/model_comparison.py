"""Stage 2 column A: model comparison, leakage gap and open-set tests on the sim v2.1 dataset.

All models train on TRAIN (+ the healthy-only train runs) and are scored on TEST engines (grouped by
engine, never by window); the held-out 3000-3500 rpm runs are reported separately. Thresholds and
operating points come from VAL only. 3 seeds per learned model, mean +- sd.

  Baseline  the physics rules as a classifier: the first rule that fires names the class (and cylinder)
  SVM       RBF SVM, sigmoid-calibrated (the fast_v1 recipe, C = 1), cylinder by logistic regression
  ANN       MLP (64, 32) + softmax, early stopping; cylinder head = a second MLP on fault windows
  CNN       2-channel (mic, accel) order-normalised log spectrogram, 4 x 720 deg frames x 64 order bands
            (models/make_spectra.py), small conv net with a class head and a cylinder head (CPU)

Leakage gap: the SVM on a random-by-window split of the same windows vs the grouped-by-engine split.
Open set: unknown = valve_tick windows (never trained) vs known = test windows; scores = healthy-model
Mahalanobis (fast_v1), 1 - max p (SVM), -max logit (ANN, CNN). AUROC and pAUROC (FPR <= 0.1, McClish).
Second test: retrain without knock, unknown = test knock windows.

Writes eval/results/model_comparison.json.   Usage:  python -m eval.model_comparison
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from dsp.rules import all_rules, load_all
from models.classifier import load as load_fast
from models.features import FEATURE_NAMES
from models.make_dataset import CLASSES, MANIFEST, OUT_DIR
from models.make_spectra import OPEN_DIR, SPEC_DIR, openset_plan

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "model_comparison.json"
SEEDS = (0, 1, 2)
MAX_PER_CLASS, MAX_HEALTHY = 3000, 9000
F = {n: i for i, n in enumerate(FEATURE_NAMES)}


# ---------------------------------------------------------------- data

def load(split, man, spectra=True):
    Xs, Ss, ys, cs, ms, gs = [], [], [], [], [], []
    for job in (j for j in man["runs"] if j["split"] == split):
        d = np.load(OUT_DIR / f"{job['run_id']}.npz")
        Xs.append(d["X"]); ys.append(d["y"]); cs.append(d["cyl"]); ms.append(d["meta"])
        gs.append(np.full(len(d["y"]), job["engine"] * 100 + CLASSES.index(job["fault"])))
        if spectra:
            S = np.load(SPEC_DIR / f"{job['run_id']}.npz")["S"]
            assert len(S) == len(d["y"]), job["run_id"]
            Ss.append(S)
    cat = np.concatenate
    return {"X": cat(Xs).astype(float), "S": cat(Ss).astype(np.float32) if spectra else None, "y": cat(ys).astype(int),
            "cyl": cat(cs).astype(int), "meta": cat(ms), "group": cat(gs)}


def merge(a, b):
    return {k: (np.concatenate([a[k], b[k]]) if a[k] is not None else None) for k in a}


def subsample(d, seed, exclude=()):
    rng = np.random.default_rng(seed)
    idx = []
    for c in range(len(CLASSES)):
        if CLASSES[c] in exclude:
            continue
        ii = rng.permutation(np.flatnonzero(d["y"] == c))
        idx.append(ii[:MAX_HEALTHY if c == 0 else MAX_PER_CLASS])
    return np.sort(np.concatenate(idx))


def metrics(y, pred, cyl_true, cyl_pred, classes=CLASSES):
    labels = list(range(len(classes)))
    named = (y > 0) & (pred == y)
    return {"n": int(len(y)), "macro_f1": float(f1_score(y, pred, labels=labels, average="macro", zero_division=0)),
            "recall": {c: float((pred[y == i] == i).mean()) if (y == i).any() else None for i, c in enumerate(classes)},
            "localisation_accuracy": float((cyl_pred[named] == cyl_true[named]).mean()) if named.any() else None,
            "localisation_n": int(named.sum())}


def pauc(y_unknown, score):
    return float(roc_auc_score(y_unknown, score, max_fpr=0.1))


# ---------------------------------------------------------------- models

def baseline_predict(d):
    th = load_all()
    pred, cyl = np.zeros(len(d["y"]), int), np.zeros(len(d["y"]), int)
    X = d["X"]
    for i in range(len(X)):
        f = {"half_order_energy": X[i, F["half_order_energy"]] / 100.0,
             "roughness": X[i, [F[f"roughness_c{k}"] for k in range(1, 5)]] / 100.0,
             "knock_db": X[i, [F[f"knock_db_c{k}"] for k in range(1, 5)]],
             "rpm_mean": X[i, F["rpm_mean"]] * 1000.0, "map_kpa": X[i, F["map_kpa"]] * 10.0,
             "crank_valid": bool(d["meta"][i, 2]), "accel_valid": bool(d["meta"][i, 3])}
        obd = {"n": 30, "trim_mean_pct": X[i, F["trim_mean_pct"]], "spark_retard_deg": X[i, F["spark_retard_deg"]]}
        for r in all_rules(f, obd, th):
            if r["fired"]:
                pred[i] = CLASSES.index(r["rule"])
                cyl[i] = r["location"] or 0
                break
    return pred, cyl


class SvmModel:
    def __init__(self, seed, C=1.0):
        self.seed, self.C = seed, C

    def fit(self, d, idx):
        self.sc = StandardScaler().fit(d["X"][idx])
        Z = self.sc.transform(d["X"][idx])
        folds = list(GroupKFold(3).split(Z, d["y"][idx], d["group"][idx]))
        self.m = CalibratedClassifierCV(SVC(kernel="rbf", C=self.C, gamma="scale", class_weight="balanced",
                                            random_state=self.seed), method="sigmoid", cv=folds).fit(Z, d["y"][idx])
        self.cyl = {}
        for c in np.unique(d["y"][idx]):
            if c == 0:
                continue
            s = idx[(d["y"][idx] == c) & (d["cyl"][idx] > 0)]
            self.cyl[c] = LogisticRegression(max_iter=2000).fit(self.sc.transform(d["X"][s]), d["cyl"][s])
        return self

    def predict(self, d):
        Z = self.sc.transform(d["X"])
        P = self.m.predict_proba(Z)
        pred = self.m.classes_[P.argmax(1)]
        cyl = np.zeros(len(pred), int)
        for c, m in self.cyl.items():
            s = pred == c
            if s.any():
                cyl[s] = m.predict(Z[s])
        return pred, cyl, 1.0 - P.max(1)


class AnnModel:
    def __init__(self, seed):
        self.seed = seed

    def fit(self, d, idx):
        self.sc = StandardScaler().fit(d["X"][idx])
        Z = self.sc.transform(d["X"][idx])
        y = d["y"][idx]
        # balance classes by repetition (MLPClassifier has no class weights)
        rng = np.random.default_rng(self.seed)
        counts = np.bincount(y)
        rep = np.concatenate([rng.choice(np.flatnonzero(y == c), counts.max(), replace=True) for c in np.unique(y)])
        self.m = MLPClassifier((64, 32), early_stopping=True, max_iter=200, random_state=self.seed).fit(Z[rep], y[rep])
        f = (y > 0) & (d["cyl"][idx] > 0)
        Zc = np.hstack([Z[f], np.eye(len(CLASSES))[y[f]]])
        self.mc = MLPClassifier((64,), early_stopping=True, max_iter=200, random_state=self.seed).fit(Zc, d["cyl"][idx][f])
        return self

    def logits(self, Z):
        # pre-softmax output of the MLP (forward pass by hand)
        a = Z
        for i, (W, b) in enumerate(zip(self.m.coefs_, self.m.intercepts_)):
            a = a @ W + b
            if i < len(self.m.coefs_) - 1:
                a = np.maximum(a, 0)
        return a

    def predict(self, d):
        Z = self.sc.transform(d["X"])
        L = self.logits(Z)
        pred = self.m.classes_[L.argmax(1)]
        cyl = self.mc.predict(np.hstack([Z, np.eye(len(CLASSES))[pred]]))
        cyl[pred == 0] = 0
        return pred, cyl, -L.max(1)


class _Net(torch.nn.Module):
    def __init__(self, n_cls):
        super().__init__()
        self.f = torch.nn.Sequential(
            torch.nn.Conv2d(2, 16, (2, 5), padding=(0, 2)), torch.nn.BatchNorm2d(16), torch.nn.ReLU(),
            torch.nn.Conv2d(16, 32, (2, 5), padding=(0, 2)), torch.nn.BatchNorm2d(32), torch.nn.ReLU(),
            torch.nn.AdaptiveAvgPool2d((1, 16)), torch.nn.Flatten(), torch.nn.Linear(32 * 16, 64), torch.nn.ReLU(),
            torch.nn.Dropout(0.2))
        self.cls = torch.nn.Linear(64, n_cls)
        self.cyl = torch.nn.Linear(64, 5)

    def forward(self, x):
        h = self.f(x)
        return self.cls(h), self.cyl(h)


class CnnModel:
    def __init__(self, seed, epochs=40):          # chosen on VAL (6 / 20 / 40: 0.499 / 0.556 / 0.602)
        self.seed, self.epochs = seed, epochs

    def fit(self, d, idx):
        torch.manual_seed(self.seed)
        S = d["S"][idx]
        self.mu, self.sd = S.mean((0, 2), keepdims=True), S.std((0, 2), keepdims=True) + 1e-6
        X = torch.tensor((S - self.mu) / self.sd)
        self.classes = np.unique(d["y"][idx])
        remap = {c: i for i, c in enumerate(self.classes)}
        y = torch.tensor([remap[c] for c in d["y"][idx]])
        cy = torch.tensor(d["cyl"][idx])
        w = torch.tensor(len(y) / (len(self.classes) * np.bincount(y.numpy())), dtype=torch.float32)
        self.net = _Net(len(self.classes))
        opt = torch.optim.Adam(self.net.parameters(), 1e-3)
        g = torch.Generator().manual_seed(self.seed)
        self.net.train()
        for _ in range(self.epochs):
            for b in torch.randperm(len(y), generator=g).split(256):
                lc, ll = self.net(X[b])
                loss = torch.nn.functional.cross_entropy(lc, y[b], weight=w)
                m = cy[b] > 0
                if m.any():
                    loss = loss + torch.nn.functional.cross_entropy(ll[m], cy[b][m])
                opt.zero_grad(); loss.backward(); opt.step()
        return self

    @torch.no_grad()
    def predict(self, d):
        self.net.eval()
        X = torch.tensor((d["S"] - self.mu) / self.sd)
        L, C = [], []
        for b in X.split(4096):
            lc, ll = self.net(b)
            L.append(lc); C.append(ll)
        L, C = torch.cat(L).numpy(), torch.cat(C).numpy()
        pred = self.classes[L.argmax(1)]
        cyl = C[:, 1:].argmax(1) + 1
        cyl[pred == 0] = 0
        return pred, cyl, -L.max(1)


# ---------------------------------------------------------------- main

def summarise(runs):
    keys = ("macro_f1", "localisation_accuracy")
    out = {k: {"mean": float(np.mean([r[k] for r in runs])), "sd": float(np.std([r[k] for r in runs], ddof=1)) if len(runs) > 1 else 0.0}
           for k in keys}
    out["recall"] = {c: {"mean": float(np.mean([r["recall"][c] for r in runs])),
                         "sd": float(np.std([r["recall"][c] for r in runs], ddof=1)) if len(runs) > 1 else 0.0}
                     for c in runs[0]["recall"] if runs[0]["recall"][c] is not None}
    out["n"] = runs[0]["n"]
    out["per_seed"] = runs
    return out


def main():
    t0 = time.perf_counter()
    man = json.loads(MANIFEST.read_text())
    train = merge(load("train", man), load("train_healthy", man))
    test, test_hr = load("test", man), load("test_heldout_rpm", man)
    val = merge(load("val", man), load("val_healthy", man))
    ops = []
    for j in openset_plan():
        d = np.load(OPEN_DIR / f"{j['run_id']}.npz")
        ops.append({"X": d["X"].astype(float), "S": d["S"].astype(np.float32), "tick": d["meta"][:, 4] > 0,
                    "meta": d["meta"]})
    unk = {"X": np.concatenate([o["X"][o["tick"]] for o in ops]), "S": np.concatenate([o["S"][o["tick"]] for o in ops]),
           "meta": np.concatenate([o["meta"][o["tick"]] for o in ops])}
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "column": "A",
           "split": "grouped by engine (train 700-759, test 775-789)", "sim_version": man["sim_version"],
           "test_windows": int(len(test["y"])), "heldout_rpm_windows": int(len(test_hr["y"])),
           "unknown_valve_tick_windows": int(len(unk["X"])), "models": {}, "heldout_rpm": {}}

    # baseline (deterministic)
    for name, d, key in (("test", test, "models"), ("heldout_rpm", test_hr, "heldout_rpm")):
        p, c = baseline_predict(d)
        res[key]["Baseline"] = summarise([metrics(d["y"], p, d["cyl"], c)])
    print(f"baseline done ({time.perf_counter() - t0:.0f} s)", flush=True)

    scores = {}
    for name, cls in (("SVM", SvmModel), ("ANN", AnnModel), ("CNN", CnnModel)):
        runs, runs_hr = [], []
        for seed in SEEDS:
            idx = subsample(train, seed)
            m = cls(seed).fit(train, idx)
            p, c, s = m.predict(test)
            runs.append(metrics(test["y"], p, test["cyl"], c))
            p2, c2, _ = m.predict(test_hr)
            runs_hr.append(metrics(test_hr["y"], p2, test_hr["cyl"], c2))
            _, _, su = m.predict(unk)
            yu = np.r_[np.zeros(len(s)), np.ones(len(su))]
            sc = np.r_[s, su]
            scores.setdefault(name, []).append({"auroc": float(roc_auc_score(yu, sc)), "pauc_fpr_0.1": pauc(yu, sc)})
            print(f"  {name} seed {seed}: macro-F1 {runs[-1]['macro_f1']:.3f}  held-out rpm {runs_hr[-1]['macro_f1']:.3f} "
                  f"({time.perf_counter() - t0:.0f} s)", flush=True)
        res["models"][name] = summarise(runs)
        res["heldout_rpm"][name] = summarise(runs_hr)

    # open set 1: valve tick vs known test windows
    fast = load_fast()
    def maha(d):
        sc, thr = fast.anomaly(d["X"], d["meta"][:, 1])
        return sc / thr
    yu = np.r_[np.zeros(len(test["y"])), np.ones(len(unk["X"]))]
    sm = np.r_[maha(test), maha(unk)]
    res["open_set_valve_tick"] = {
        "unknown": "valve_tick windows (tick present), 15 test engines, never trained", "known": "all test windows",
        "Mahalanobis": {"auroc": float(roc_auc_score(yu, sm)), "pauc_fpr_0.1": pauc(yu, sm),
                        "tpr_at_val_threshold": float((maha(unk) > 1.0).mean()),
                        "fpr_known_at_val_threshold": float((maha(test) > 1.0).mean())},
        **{f"{k} (max-logit)" if k != "SVM" else "SVM (1 - max p)": {
            "auroc": float(np.mean([s["auroc"] for s in v])), "auroc_sd": float(np.std([s["auroc"] for s in v], ddof=1)),
            "pauc_fpr_0.1": float(np.mean([s["pauc_fpr_0.1"] for s in v]))} for k, v in scores.items()},
    }

    # open set 2: knock held out of training
    knock = CLASSES.index("knock")
    known_test = {k: (v[test["y"] != knock] if v is not None else None) for k, v in test.items()}
    unk_k = {k: (v[test["y"] == knock] if v is not None else None) for k, v in test.items()}
    ko = {}
    for name, cls in (("SVM", SvmModel), ("ANN", AnnModel), ("CNN", CnnModel)):
        r = []
        for seed in SEEDS:
            m = cls(seed).fit(train, subsample(train, seed, exclude=("knock",)))
            _, _, s = m.predict(known_test)
            _, _, su = m.predict(unk_k)
            yk = np.r_[np.zeros(len(s)), np.ones(len(su))]
            r.append({"auroc": float(roc_auc_score(yk, np.r_[s, su])), "pauc_fpr_0.1": pauc(yk, np.r_[s, su])})
        ko[name] = {"auroc": float(np.mean([x["auroc"] for x in r])), "auroc_sd": float(np.std([x["auroc"] for x in r], ddof=1)),
                    "pauc_fpr_0.1": float(np.mean([x["pauc_fpr_0.1"] for x in r]))}
    # Mahalanobis needs no retraining for this (fit on healthy only): knock vs other test windows
    yk = np.r_[np.zeros(len(known_test["y"])), np.ones(len(unk_k["y"]))]
    smk = np.r_[maha(known_test), maha(unk_k)]
    ko["Mahalanobis"] = {"auroc": float(roc_auc_score(yk, smk)), "pauc_fpr_0.1": pauc(yk, smk)}
    res["open_set_knock_held_out"] = {"unknown": "test knock windows; classifiers retrained without knock",
                                      "known": "test windows of the other classes", **ko}
    print(f"open set done ({time.perf_counter() - t0:.0f} s)", flush=True)

    # leakage gap: the SVM, random-by-window vs grouped-by-engine, same windows (train + test pooled)
    pool = merge(train, test)
    is_test_engine = np.r_[np.zeros(len(train["y"]), bool), np.ones(len(test["y"]), bool)]
    gap = {"grouped": [], "random": []}
    for seed in SEEDS:
        rng = np.random.default_rng(seed)
        rand_test = rng.random(len(pool["y"])) < is_test_engine.mean()
        for kind, te in (("grouped", is_test_engine), ("random", rand_test)):
            tr_idx = np.flatnonzero(~te)
            sub = {k: (v[tr_idx] if v is not None else None) for k, v in pool.items()}
            idx = subsample(sub, seed)
            m = SvmModel(seed).fit(sub, idx)
            ted = {k: (v[te] if v is not None else None) for k, v in pool.items()}
            p, c, _ = m.predict(ted)
            gap[kind].append(metrics(ted["y"], p, ted["cyl"], c)["macro_f1"])
    res["split_gap_svm_macro_f1"] = {k: {"mean": float(np.mean(v)), "sd": float(np.std(v, ddof=1)), "per_seed": v,
                                         "n": int(len(test["y"]))} for k, v in gap.items()}
    res["wall_s"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: {m: round(v["macro_f1"]["mean"], 3) for m, v in res[k].items()} for k in ("models", "heldout_rpm")}))
    print(json.dumps({k: res[k] for k in ("open_set_valve_tick", "open_set_knock_held_out", "split_gap_svm_macro_f1")}, indent=0)[:2500])


if __name__ == "__main__":
    main()
