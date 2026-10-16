"""Documented leakage case: the "short OBD history" shortcut (DECISIONS: dataset warm-up).

With a 4 s warm-up, knock windows (the ECU's retard suppresses knock within seconds of injection) almost
all had less than 30 s of OBD history, while healthy windows mostly did not; the trim / spark-retard
features computed over a partial OBD window then told the SVM "early in the run", a cue that does not
exist live. Reproduced here on the same 30 train engines (700-729), all four classes, generated twice:
4 s warm-up (before) and 30 s warm-up (after, the committed dataset recipe). Same SVM recipe for both.

  cue        share of each class's windows with < 30 s of OBD history
  effect     both SVMs scored on the TEST split (full OBD history, as live): macro-F1, knock recall,
             and on held-out valve_tick windows: share called "knock" at p >= 0.8 (the witness cut), separately
             for SHORT-history windows (valve tick injected at 4 s on the 15 test engines) and full-history ones

Resumable npz in data/generated/shortcut_{4s,30s}/. Writes eval/results/shortcut_case.json.
Usage:  python -m eval.shortcut_case
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

import models.make_dataset as md
from eval.model_comparison import SvmModel, load, metrics, subsample
from models.make_spectra import OPEN_DIR, openset_plan

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "shortcut_case.json"
WORKERS = int(os.environ.get("S720_WORKERS", 10))
ENGINES = range(700, 730)
VARIANTS = {"4s": 4.0, "30s": 30.0}


def _job(args):
    variant, job = args
    md.WARMUP_S = VARIANTS[variant]
    md.OUT_DIR = ROOT / "data" / "generated" / f"shortcut_{variant}"
    md.OUT_DIR.mkdir(parents=True, exist_ok=True)
    return md.run_one(job)


def _load(variant, jobs):
    d = ROOT / "data" / "generated" / f"shortcut_{variant}"
    X, y, cyl, meta, grp = [], [], [], [], []
    for j in jobs:
        z = np.load(d / f"{j['run_id']}.npz")
        X.append(z["X"]); y.append(z["y"]); cyl.append(z["cyl"]); meta.append(z["meta"])
        grp.append(np.full(len(z["y"]), j["engine"] * 100 + md.CLASSES.index(j["fault"])))
    c = np.concatenate
    return {"X": c(X).astype(float), "S": None, "y": c(y).astype(int), "cyl": c(cyl).astype(int), "meta": c(meta), "group": c(grp)}


def _valve_short(job):
    """A valve_tick run injected at 4 s (purpose eval), its windows from 4 s on, with t_end."""
    import models.make_spectra as ms
    md.WARMUP_S = ms.WARMUP_S = 4.0        # make_spectra imported the constant at import time
    path = ROOT / "data" / "generated" / "shortcut_valve4s" / f"{job['run_id']}.npz"
    if not path.exists():
        X, t = [], []
        for w in ms._simulate(job, "eval", guard=False):
            if any(max(lab["valve_lash"]) > 0 for lab in w.win.labels):
                X.append(w.vector); t.append(w.win.t_end)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, X=np.array(X, dtype=np.float32), t=np.array(t, dtype=np.float32))
    return str(path)


def main():
    jobs = [j for j in md.plan() if j["split"] == "train" and j["engine"] in ENGINES]
    with Pool(WORKERS) as pool:
        pool.map(_job, [(v, j) for v in VARIANTS for j in jobs])
        vpaths = pool.map(_valve_short, openset_plan())
    vs = [np.load(p) for p in vpaths]
    vs_X = np.concatenate([z["X"] for z in vs]).astype(float)
    vs_short = np.concatenate([z["t"] for z in vs]) < 30.0
    man = json.loads(md.MANIFEST.read_text())
    test = load("test", man, spectra=False)
    vt = np.concatenate([np.load(OPEN_DIR / f"{j['run_id']}.npz")["X"] for j in openset_plan()]).astype(float)
    vt_tick = np.concatenate([np.load(OPEN_DIR / f"{j['run_id']}.npz")["meta"][:, 4] > 0 for j in openset_plan()])
    vt = {"X": vt[vt_tick]}
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "engines": [min(ENGINES), max(ENGINES)],
           "valve_tick_4s_windows": {"short_history": int(vs_short.sum()), "full_history": int((~vs_short).sum())},
           "runs_per_variant": len(jobs), "test_windows": int(len(test["y"])), "valve_tick_windows": int(len(vt["X"])),
           "variants": {}}
    for v, C in ((v, C) for v in VARIANTS for C in (1.0, 10.0)):
        d = _load(v, jobs)
        # OBD history at a window = time since the run started, capped at 30 s (t_end is meta[:, 0])
        short = d["meta"][:, 0] < 30.0
        cue = {c: float(short[d["y"] == i].mean()) for i, c in enumerate(md.CLASSES)}
        runs = []
        for seed in (0, 1, 2):
            m = SvmModel(seed, C=C).fit(d, subsample(d, seed))
            p, cy, _ = m.predict(test)
            met = metrics(test["y"], p, test["cyl"], cy)
            P = m.m.predict_proba(m.sc.transform(vt["X"]))
            knock_col = list(m.m.classes_).index(md.CLASSES.index("knock"))
            vt_knock = float(((P.argmax(1) == knock_col) & (P.max(1) >= 0.8)).mean())
            Ps = m.m.predict_proba(m.sc.transform(vs_X))
            ks = (Ps.argmax(1) == knock_col) & (Ps.max(1) >= 0.8)
            runs.append({"macro_f1": met["macro_f1"], "knock_recall": met["recall"]["knock"], "valve_tick_called_knock": vt_knock,
                         "valve_tick_called_knock_short_history": float(ks[vs_short].mean()),
                         "valve_tick_called_knock_full_history_4s_runs": float(ks[~vs_short].mean())})
        v_key = f"{v}_C{C:g}"
        res["variants"][v_key] = {"warmup_s": VARIANTS[v], "svm_C": C, "windows": int(len(d["y"])),
                              "share_short_obd_history_by_class": cue,
                              **{k: {"mean": float(np.mean([r[k] for r in runs])), "sd": float(np.std([r[k] for r in runs], ddof=1))}
                                 for k in runs[0]}, "per_seed": runs}
        print(v_key, json.dumps({k: res["variants"][v_key][k] for k in ("share_short_obd_history_by_class", "macro_f1", "knock_recall",
                                                              "valve_tick_called_knock", "valve_tick_called_knock_short_history",
                                                              "valve_tick_called_knock_full_history_4s_runs")}))
    OUT.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
