"""Table A, first slice: misfire vs healthy on sim v1 -> eval/table_a_misfire.json

    python -m models.table_a.misfire            # ~2-4 min on 12 cores; features cached in data/raw/sim_cache/

Plan sections 9 and 13: trivial baseline vs SVM vs ANN on IDENTICAL splits across seeds, and the
random-vs-grouped split gap (our rebuttal to inflated results). Column A only: simulated data,
never mixed with real results. The CNN and the knock / injector-clog classes wait for sim v2.

Data: randomised engines from the "train" parameter region, each at 800 / 1500 / 2500 rpm, one
healthy run and misfire runs at p = 0.2 / 0.5 / 1.0 on one cylinder; plus engines from the held-out
"test" region (structural-mode frequency scale) for a held-out-region test. Windows: 4 cycles,
50% overlap (dsp.windows). A window is labelled misfire if any of its cycles actually misfired
(sim labels), so low-severity runs also contribute healthy windows.

Features (v0, provisional until Dipen's engineered vector exists): half-order ratio, per-cylinder
crank roughness (+ max), share of mic energy in each cylinder's 180 deg firing slot (from the 720 deg
TSA), log mic RMS, mic kurtosis, mean rpm.

Splits:
  grouped  folds by ENGINE: every run (rpm, fault, severity) of a simulated engine in one fold
  random   folds by window (leaks run and engine identity between train and test)
Metric: balanced accuracy (and AUROC), 5 folds x 3 seeds; thresholds/fits only ever on training folds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy.stats import kurtosis
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.model_selection import KFold
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from dsp.features import fingerprint, window_features
from dsp.windows import CycleWindower
from eval.stats import proportion_summary
from sim import SIM_VERSION, EngineParams, EngineSim

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "data" / "raw" / "sim_cache"
OUT = REPO / "eval" / "table_a_misfire.json"

TRAIN_ENGINES = list(range(0, 12))
TEST_REGION_ENGINES = list(range(200, 204))
RPMS = (800, 1500, 2500)
SEVERITIES = (0.2, 0.5, 1.0)
WARMUP_S, RECORD_S = 3.0, 12.0
SEEDS = (0, 1, 2)
N_FOLDS = 5
TDC_DEG = {1: 0.0, 3: 180.0, 4: 360.0, 2: 540.0}  # DECISIONS D24
FEATURES = ["half_order_ratio", "rough_max", "rough_1", "rough_2", "rough_3", "rough_4",
            "slot_1", "slot_2", "slot_3", "slot_4", "mic_rms_log", "mic_kurtosis", "rpm_mean"]


def runs() -> list[dict]:
    out = []
    for region, engines in (("train", TRAIN_ENGINES), ("test", TEST_REGION_ENGINES)):
        for e in engines:
            for rpm in RPMS:
                out.append({"region": region, "engine": e, "rpm": rpm, "cylinder": 0, "severity": 0.0})
                for i, p in enumerate(SEVERITIES):
                    out.append({"region": region, "engine": e, "rpm": rpm, "cylinder": (e + i) % 4 + 1, "severity": p})
    for i, r in enumerate(out):
        r["run_id"] = i
        r["run_seed"] = int(hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()[:8], 16)
    return out


def window_vector(win) -> list[float]:
    f = window_features(win)
    centres, e = fingerprint(win)
    energy = e ** 2
    total = energy.sum() or 1.0
    slots = [energy[(centres >= TDC_DEG[c]) & (centres < TDC_DEG[c] + 180)].sum() / total for c in (1, 2, 3, 4)]
    mic = win.mic.astype(float)
    r = f["roughness"]
    return [f["half_order_ratio"], float(r.max()), *map(float, r), *slots,
            float(np.log10(np.sqrt(np.mean(mic ** 2)) + 1e-12)), float(kurtosis(mic)), f["rpm_mean"]]


def simulate(run: dict) -> tuple[list, list, list]:
    sim = EngineSim(EngineParams.randomised(run["engine"], run["region"]), seed=run["run_seed"])
    sim.set_operating_point(rpm_target=run["rpm"])
    if run["severity"] > 0:
        sim.inject("misfire", cylinder=run["cylinder"], severity=run["severity"])
    w = CycleWindower(4, 0.5)
    X, y, loc = [], [], []
    for _ in range(int((WARMUP_S + RECORD_S) / 0.5)):
        for win in w.push(sim.step(0.5)):
            if win.t_start < WARMUP_S:
                continue
            mis = np.array([l["misfired"] for l in win.labels], bool)  # cycles x 4 cylinders
            X.append(window_vector(win))
            y.append(int(mis.any()))
            loc.append(int(np.argmax(mis.sum(axis=0)) + 1) if mis.any() else 0)
    return X, y, loc


def dataset(workers: int) -> dict:
    rs = runs()
    key = hashlib.sha256(json.dumps([SIM_VERSION, rs, FEATURES, WARMUP_S, RECORD_S]).encode()).hexdigest()[:12]
    path = CACHE / f"table_a_misfire_{key}.npz"
    if path.exists():
        d = dict(np.load(path))
        return {**d, "runs": rs}
    t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(simulate, rs))
    cols = {"X": [], "y": [], "loc": [], "run": [], "engine": [], "region": [], "severity": [], "rpm": []}
    for r, (X, y, loc) in zip(rs, res):
        cols["X"] += X
        cols["y"] += y
        cols["loc"] += loc
        for k in ("run", "engine", "severity", "rpm"):
            cols[k] += [r["run_id" if k == "run" else k]] * len(y)
        cols["region"] += [r["region"] == "test"] * len(y)
    d = {k: np.asarray(v, float if k in ("X", "severity") else int) for k, v in cols.items()}
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(path, **d)
    print(f"simulated {len(rs)} runs -> {len(d['y'])} windows in {time.time() - t0:.0f} s ({path.name})")
    return {**d, "runs": rs}


# ------------------------------------------------------------------ models
class Baseline:
    """Plan section 9 trivial baseline: half-order-ratio threshold OR low-mic-RMS threshold,
    both chosen on the training fold to maximise balanced accuracy."""

    def __init__(self, seed: int = 0):
        self.i_hor, self.i_rms = FEATURES.index("half_order_ratio"), FEATURES.index("mic_rms_log")

    def fit(self, X, y):
        qs = np.linspace(0.02, 0.98, 25)
        best = (-1.0, None)
        for t1 in np.quantile(X[:, self.i_hor], qs):
            for t2 in [-np.inf, *np.quantile(X[:, self.i_rms], qs[:12])]:
                s = balanced_accuracy_score(y, self._rule(X, t1, t2))
                if s > best[0]:
                    best = (s, (t1, t2))
        self.t1, self.t2 = best[1]
        return self

    def _rule(self, X, t1, t2):
        return ((X[:, self.i_hor] > t1) | (X[:, self.i_rms] < t2)).astype(int)

    def predict(self, X):
        return self._rule(X, self.t1, self.t2)

    def score(self, X):
        return X[:, self.i_hor] / self.t1


def make_model(name: str, seed: int):
    if name == "Baseline":
        return Baseline(seed)
    if name == "SVM":
        return make_pipeline(StandardScaler(), SVC(C=1.0, gamma="scale", class_weight="balanced"))
    if name == "ANN":
        return make_pipeline(StandardScaler(), MLPClassifier((32, 16), alpha=1e-3, early_stopping=True,
                                                             max_iter=500, random_state=seed))
    raise ValueError(name)


def scores(model, X) -> np.ndarray:
    if hasattr(model, "decision_function"):
        return model.decision_function(X)
    if hasattr(model, "predict_proba"):
        return model.predict_proba(X)[:, 1]
    return model.score(X)


def folds(kind: str, n: int, groups: np.ndarray, seed: int):
    if kind == "random":
        yield from KFold(N_FOLDS, shuffle=True, random_state=seed).split(np.zeros(n))
        return
    ug = np.unique(groups)
    rng = np.random.default_rng(seed)
    rng.shuffle(ug)
    for k in range(N_FOLDS):
        test_g = ug[k::N_FOLDS]
        te = np.isin(groups, test_g)
        yield np.nonzero(~te)[0], np.nonzero(te)[0]


def cv(name: str, X, y, groups, kind: str) -> dict:
    accs, aucs, oof = [], [], None
    for seed in SEEDS:
        pred = np.zeros(len(y), int)
        sc = np.zeros(len(y))
        for tr, te in folds(kind, len(y), groups, seed):
            m = make_model(name, seed).fit(X[tr], y[tr])
            pred[te], sc[te] = m.predict(X[te]), scores(m, X[te])
        accs.append(balanced_accuracy_score(y, pred))
        aucs.append(roc_auc_score(y, sc))
        if seed == SEEDS[0]:
            oof = pred
    return {"mean": round(float(np.mean(accs)), 4), "sd": round(float(np.std(accs, ddof=1)), 4),
            "per_seed": [round(float(a), 4) for a in accs], "auroc_mean": round(float(np.mean(aucs)), 4),
            "n_test_windows": int(len(y)), "_oof": oof}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    d = dataset(args.workers)
    tr = d["region"] == 0
    X, y, eng, sev = d["X"][tr], d["y"][tr], d["engine"][tr], d["severity"][tr]
    Xt, yt = d["X"][~tr], d["y"][~tr]
    print(f"train region: {len(y)} windows ({y.mean():.0%} misfire) from {len(np.unique(eng))} engines; "
          f"held-out region: {len(yt)} windows from {len(np.unique(d['engine'][~tr]))} engines")

    models = {}
    for name in ("Baseline", "SVM", "ANN"):
        t0 = time.time()
        g = cv(name, X, y, eng, "grouped")
        r = cv(name, X, y, eng, "random")
        held = []
        for seed in SEEDS:
            m = make_model(name, seed).fit(X, y)
            held.append(balanced_accuracy_score(yt, m.predict(Xt)))
        oof = g.pop("_oof")
        r.pop("_oof")
        # detection rate per severity and false-positive rate on healthy windows (grouped, seed 0)
        by_sev = {}
        for p in (0.0, *SEVERITIES):
            sel = sev == p
            if p == 0.0:
                by_sev["healthy_runs_false_positive"] = proportion_summary(int(oof[sel].sum()), int(sel.sum()))
            else:
                m_sel = sel & (y == 1)
                by_sev[f"p{p}_detected"] = proportion_summary(int(oof[m_sel].sum()), int(m_sel.sum()))
        models[name] = {
            "grouped": g, "random": r,
            "heldout_region": {"mean": round(float(np.mean(held)), 4), "sd": round(float(np.std(held, ddof=1)), 4),
                               "n_test_windows": int(len(yt))},
            "grouped_by_severity_seed0": by_sev,
        }
        print(f"{name:8} grouped {g['mean']:.3f}±{g['sd']:.3f}  random {r['mean']:.3f}±{r['sd']:.3f}  "
              f"held-out region {np.mean(held):.3f}  (AUROC grouped {g['auroc_mean']:.3f})  {time.time() - t0:.0f} s")

    best = max(("SVM", "ANN"), key=lambda m: models[m]["grouped"]["mean"])
    out = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "produced_by": "python -m models.table_a.misfire",
        "column": "A (simulated only)",
        "sim_version": SIM_VERSION,
        "task": "misfire vs healthy, per 4-cycle window",
        "metric": "balanced accuracy",
        "features": FEATURES,
        "features_status": "v0 provisional (Diljit), until Dipen's engineered vector exists",
        "data": {"train_region_engines": TRAIN_ENGINES, "test_region_engines": TEST_REGION_ENGINES, "rpm": list(RPMS),
                 "severities": list(SEVERITIES), "record_s_per_run": RECORD_S, "runs": len(d["runs"]),
                 "train_region_windows": int(len(y)), "train_region_misfire_frac": round(float(y.mean()), 4),
                 "heldout_region_windows": int(len(yt))},
        "splits": {"grouped": f"{N_FOLDS} folds by engine (all runs of an engine in one fold)",
                   "random": f"{N_FOLDS} folds by window", "seeds": list(SEEDS),
                   "heldout_region": "train on all train-region engines, test on test-region engines"},
        "models": models,
        "split_gap": {"model": best, "random": models[best]["random"], "grouped": models[best]["grouped"],
                      "gap": round(models[best]["random"]["mean"] - models[best]["grouped"]["mean"], 4)},
        "not_yet": ["CNN (needs order-normalised spectrogram pipeline)", "knock / injector_clog classes (sim v2)",
                    "valve_tick open-set test (sim v2)"],
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"split gap ({best}): random {out['split_gap']['random']['mean']:.3f} vs grouped "
          f"{out['split_gap']['grouped']['mean']:.3f} -> {out['split_gap']['gap']:+.3f}")
    print(f"Wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
