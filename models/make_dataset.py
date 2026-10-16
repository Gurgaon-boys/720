"""Generate the sim v2 classifier dataset: classes healthy, misfire, knock, injector_clog (plan section 9).

- valve_tick is NEVER generated here (purpose="training": the simulator refuses it; sim.guard checks
  every chunk). It stays the held-out "unknown" class for the open-set test.
- Split BY ENGINE (and so by run), never by window: train / val / test engine ranges are disjoint from
  each other and from calibration and evaluation seeds (eval/common.py SEEDS, asserted).
- Hard regimes ON (Regimes.eval()). rpm 700-2950 in train/val/test; the held-out 3000-3500 rpm band is
  generated only for "test_heldout_rpm", on test engines, and reported separately.
- Severities: misfire p 0.2-1.0, knock 0.3-1.0, clog s 0.1-0.6. Fault injected after a 30 s warm-up;
  windows from 30 s on are kept (full OBD history: without it, knock, which the ECU suppresses within
  seconds, correlated with a short OBD history and the SVM learned that instead), each labelled from what happened IN it:
    misfire  a misfire occurred in the window      knock  a knock event occurred in the window
    injector_clog  clog active (from 2 s after injection)   otherwise healthy
  `run_fault` keeps the injected fault so windows of a faulty run that look healthy can be told apart.
- Resumable: one npz per run in data/generated/sim_v2/ (git-ignored); finished runs are skipped.

Writes data/splits/sim_v2_dataset.json (the manifest, committed).
Usage:  python -m models.make_dataset [--quick]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
import os
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from eval.common import SEEDS
from models.features import FEATURE_NAMES, StreamFeatures
from sim import SIM_VERSION, EngineParams, EngineSim
from sim.guard import check_chunk, check_condition
from sim.params import Regimes

WORKERS = int(os.environ.get("S720_WORKERS", min(10, os.cpu_count() or 1)))   # ~0.5 GB each
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "generated" / "sim_v2"
MANIFEST = ROOT / "data" / "splits" / "sim_v2_dataset.json"
CLASSES = ("healthy", "misfire", "knock", "injector_clog")
SEVERITY = {"misfire": (0.2, 1.0), "knock": (0.3, 1.0), "injector_clog": (0.1, 0.6)}
WARMUP_S, FAULT_S = 30.0, 36.0   # 30 s warm-up: every kept window has a full 30 s of OBD history, as live
RUNS_PER_ENGINE = 4           # one per class
EXTRA_HEALTHY = {"train": 2, "val": 4}   # healthy-only runs per engine for the healthy model


def plan(quick: bool = False) -> list[dict]:
    jobs = []
    for split in ("train", "val", "test", "test_heldout_rpm"):
        engines, off = SEEDS["dataset_" + ("test" if split == "test_heldout_rpm" else split)]
        if quick:
            engines = engines[:2]
        for e in engines:
            rng = np.random.default_rng([e, 0xDA7A, split == "test_heldout_rpm"])
            for k, cls in enumerate(CLASSES):
                rpm = rng.uniform(3000, 3450) if split == "test_heldout_rpm" else rng.uniform(700, 2950)
                job = {"split": split, "engine": e, "run_seed": off + 10 * e + k + (5 if split == "test_heldout_rpm" else 0),
                       "rpm": round(float(rpm), 1), "load_nm": round(float(rng.uniform(3, 45)), 2), "fault": cls,
                       "cylinder": int(rng.integers(1, 5)),
                       "severity": round(float(rng.uniform(*SEVERITY[cls])), 3) if cls in SEVERITY else 0.0}
                job["run_id"] = f"sim_{job['run_seed']:05d}_{cls}_c{job['cylinder'] if cls != 'healthy' else 0}_s{job['severity']:.2f}"
                jobs.append(job)
    # extra healthy-only runs for the healthy (Mahalanobis) model: more operating points per engine
    for split, base, n in (("train_healthy", "train", EXTRA_HEALTHY["train"]), ("val_healthy", "val", EXTRA_HEALTHY["val"])):
        engines, off = SEEDS["dataset_" + base]
        if quick:
            engines = engines[:2]
        for e in engines:
            rng = np.random.default_rng([e, 0x4EA1])
            for k in range(n):
                job = {"split": split, "engine": e, "run_seed": off + 5000 + 10 * e + k, "rpm": round(float(rng.uniform(700, 2950)), 1),
                       "load_nm": round(float(rng.uniform(3, 45)), 2), "fault": "healthy", "cylinder": 0, "severity": 0.0}
                job["run_id"] = f"sim_{job['run_seed']:05d}_healthy_c0_s0.00"
                jobs.append(job)
    return jobs


def run_one(job: dict) -> dict:
    path = OUT_DIR / f"{job['run_id']}.npz"
    if path.exists():
        return {"run_id": job["run_id"], "skipped": True}
    t0 = time.perf_counter()
    heldout = job["split"] == "test_heldout_rpm"
    if not heldout:
        check_condition(job["rpm"], "train")
    sim = EngineSim(EngineParams.randomised(job["engine"], "train"), seed=job["run_seed"], regimes=Regimes.eval(),
                    purpose="eval" if heldout else "training")
    sim.set_operating_point(rpm_target=job["rpm"], load_nm=job["load_nm"])
    sf = StreamFeatures()
    X, y, cyl, meta = [], [], [], []
    injected = job["fault"] == "healthy"
    for k in range(int((WARMUP_S + FAULT_S) / 0.5)):
        if not injected and k * 0.5 >= WARMUP_S:
            sim.inject(job["fault"], job["cylinder"], job["severity"])
            injected = True
        chunk = sim.step(0.5)
        if not heldout:
            check_chunk(chunk)
        for w in sf.push(chunk):
            if w.win.t_start < WARMUP_S:
                continue
            labs = w.win.labels
            mis = [c + 1 for lab in labs for c in range(4) if lab["misfired"][c]]
            kn = [c + 1 for lab in labs for c in range(4) if lab["knocked"][c]]
            clog = any(max(lab["clog"]) > 0 for lab in labs) and w.win.t_start >= WARMUP_S + 2.0
            if mis:
                lab, c = "misfire", max(set(mis), key=mis.count)
            elif kn:
                lab, c = "knock", max(set(kn), key=kn.count)
            elif clog:
                lab, c = "injector_clog", job["cylinder"]
            else:
                lab, c = "healthy", 0
            X.append(w.vector)
            y.append(CLASSES.index(lab))
            cyl.append(c)
            meta.append([w.win.t_end, w.features["rpm_mean"], float(w.features["crank_valid"]), float(w.features["accel_valid"])])
    np.savez_compressed(path, X=np.array(X, dtype=np.float32), y=np.array(y, dtype=np.int8),
                        cyl=np.array(cyl, dtype=np.int8), meta=np.array(meta, dtype=np.float32))
    return {"run_id": job["run_id"], "windows": len(y), "s": round(time.perf_counter() - t0, 1)}


def load_split(split: str, manifest: dict | None = None):
    """(X, y, cyl, meta, run_index) for one split, from the manifest's run list."""
    man = manifest or json.loads(MANIFEST.read_text())
    Xs, ys, cs, ms, rs = [], [], [], [], []
    for i, job in enumerate(j for j in man["runs"] if j["split"] == split):
        d = np.load(OUT_DIR / f"{job['run_id']}.npz")
        Xs.append(d["X"]); ys.append(d["y"]); cs.append(d["cyl"]); ms.append(d["meta"])
        rs.append(np.full(len(d["y"]), i))
    return (np.concatenate(Xs).astype(float), np.concatenate(ys), np.concatenate(cs), np.concatenate(ms),
            np.concatenate(rs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jobs = plan(args.quick)
    t0 = time.perf_counter()
    done = 0
    with Pool(WORKERS) as pool:
        for r in pool.imap_unordered(run_one, jobs):
            done += 1
            if done % 25 == 0 or done == len(jobs):
                print(f"  {done}/{len(jobs)} runs ({time.perf_counter() - t0:.0f} s)", flush=True)
    counts = {}
    for j in jobs:
        d = np.load(OUT_DIR / f"{j['run_id']}.npz")
        j["windows"] = int(len(d["y"]))
        j["window_labels"] = {c: int((d["y"] == i).sum()) for i, c in enumerate(CLASSES)}
        s = counts.setdefault(j["split"], {"runs": 0, "engines": set(), **{c: 0 for c in CLASSES}})
        s["runs"] += 1
        s["engines"].add(j["engine"])
        for c in CLASSES:
            s[c] += j["window_labels"][c]
    for s in counts.values():
        s["engines"] = len(s["engines"])
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    MANIFEST.write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_commit": commit,
        "produced_by": "models/make_dataset.py", "sim_version": SIM_VERSION, "regimes": "Regimes.eval()",
        "split_by": "engine seed (each run belongs to one engine; no engine in two splits)",
        "classes": list(CLASSES), "held_out_never_generated": ["valve_tick"],
        "feature_names": list(FEATURE_NAMES), "severity_ranges": SEVERITY,
        "warmup_s": WARMUP_S, "fault_s": FAULT_S, "data_dir": "data/generated/sim_v2 (git-ignored)",
        "counts": counts, "runs": jobs}, indent=1))
    print(json.dumps(counts, indent=1))
    print(f"{len(jobs)} runs in {time.perf_counter() - t0:.0f} s -> {MANIFEST}")


if __name__ == "__main__":
    main()
