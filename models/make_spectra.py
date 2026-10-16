"""CNN inputs and open-set runs for stage 2 (plan section 10).

1. Crank-angle spectra for every dataset run: the same runs as models/make_dataset.py, re-simulated
   (deterministic, same seeds, so window k here is window k in the feature npz). Per window, mic and
   accelerometer are low-passed below the angle grid's Nyquist, resampled to 0.25 deg of crank angle,
   cut into the window's 4 cycles (720 deg per frame), and each frame's Hann-windowed spectrum is pooled
   into N_BANDS log-spaced order bands: an order-normalised log spectrogram, 2 x 4 x N_BANDS per window.
2. Open-set runs (valve_tick, never in training): one per TEST engine, purpose="eval", features AND
   spectra, for the unknown-fault test only.

Resumable: data/generated/sim_v2_spec/<run_id>.npz and data/generated/sim_v2_openset/<run_id>.npz.
Usage:  python -m models.make_spectra
"""

from __future__ import annotations

import os
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy import signal as sps

from eval.common import SEEDS
from models.features import StreamFeatures
from models.make_dataset import FAULT_S, WARMUP_S, plan
from sim import FS, EngineParams, EngineSim
from sim.guard import check_chunk, check_condition
from sim.params import Regimes

ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "data" / "generated" / "sim_v2_spec"
OPEN_DIR = ROOT / "data" / "generated" / "sim_v2_openset"
WORKERS = int(os.environ.get("S720_WORKERS", 10))
STEP_DEG = 0.25
N_BANDS = 64
# order bands: 16 half-order bands (0.25-8.25: the 0.5 order of a misfire gets its own), then 48 log-spaced to 720
_EDGES = np.concatenate([np.arange(16) * 0.5 + 0.25, np.geomspace(8.25, 720.0, N_BANDS - 15)])


def crank_spectrum(win) -> np.ndarray:
    """(2, n_cycles, N_BANDS) log10 band power of mic and accel per 720 deg frame."""
    rpm = float(np.mean(win.rpm))
    fs_angle = rpm / 60.0 * 360.0 / STEP_DEG                       # angle-grid samples per second
    grid = 720.0 * win.first_cycle + np.arange(int(win.n_cycles * 720 / STEP_DEG)) * STEP_DEG
    n = int(720 / STEP_DEG)
    orders = np.fft.rfftfreq(n, d=STEP_DEG / 360.0)
    band = np.digitize(orders, _EDGES) - 1
    hann = np.hanning(n)
    out = np.zeros((2, win.n_cycles, N_BANDS), dtype=np.float32)
    for ch, x in enumerate((win.mic, win.accel)):
        x = np.nan_to_num(np.asarray(x, dtype=float))
        if fs_angle / 2 < 0.45 * FS:                               # anti-alias below the grid's Nyquist
            sos = sps.butter(6, 0.45 * fs_angle, fs=FS, output="sos")
            x = sps.sosfiltfilt(sos, x)
        y = np.interp(grid, win.theta, x)
        for c in range(win.n_cycles):
            seg = y[c * n:(c + 1) * n]
            p = np.abs(np.fft.rfft((seg - seg.mean()) * hann)) ** 2
            ok = (band >= 0) & (band < N_BANDS)
            s = np.bincount(band[ok], weights=p[ok], minlength=N_BANDS) / np.maximum(np.bincount(band[ok], minlength=N_BANDS), 1)
            out[ch, c] = np.log10(s + 1e-12)
    return out


def _simulate(job, purpose, guard):
    sim = EngineSim(EngineParams.randomised(job["engine"], "train"), seed=job["run_seed"], regimes=Regimes.eval(),
                    purpose=purpose)
    sim.set_operating_point(rpm_target=job["rpm"], load_nm=job["load_nm"])
    sf = StreamFeatures()
    injected = job["fault"] == "healthy"
    for k in range(int((WARMUP_S + FAULT_S) / 0.5)):
        if not injected and k * 0.5 >= WARMUP_S:
            sim.inject(job["fault"], job["cylinder"], job["severity"])
            injected = True
        chunk = sim.step(0.5)
        if guard:
            check_chunk(chunk)
        for w in sf.push(chunk):
            if w.win.t_start >= WARMUP_S:
                yield w


def spec_one(job) -> str:
    path = SPEC_DIR / f"{job['run_id']}.npz"
    if path.exists():
        return "skip"
    heldout = job["split"] == "test_heldout_rpm"
    if not heldout:
        check_condition(job["rpm"], "train")
    S = [crank_spectrum(w.win) for w in _simulate(job, "eval" if heldout else "training", not heldout)]
    np.savez_compressed(path, S=np.array(S, dtype=np.float16))
    return "ok"


def openset_plan() -> list[dict]:
    engines, off = SEEDS["dataset_test"]
    jobs = []
    for e in engines:
        rng = np.random.default_rng([e, 0x0FE7])
        job = {"split": "openset_valve_tick", "engine": e, "run_seed": off + 4000 + 10 * e,
               "rpm": round(float(rng.uniform(700, 2950)), 1), "load_nm": round(float(rng.uniform(3, 45)), 2),
               "fault": "valve_tick", "cylinder": int(rng.integers(1, 5)), "severity": round(float(rng.uniform(0.3, 1.0)), 3)}
        job["run_id"] = f"sim_{job['run_seed']:05d}_valve_tick_c{job['cylinder']}_s{job['severity']:.2f}"
        jobs.append(job)
    return jobs


def openset_one(job) -> str:
    path = OPEN_DIR / f"{job['run_id']}.npz"
    if path.exists():
        return "skip"
    X, S, meta = [], [], []
    for w in _simulate(job, "eval", guard=False):
        tick = any(lab.get("valve_lash") is not None and max(lab["valve_lash"]) > 0 for lab in w.win.labels)
        X.append(w.vector)
        S.append(crank_spectrum(w.win))
        meta.append([w.win.t_end, w.features["rpm_mean"], float(w.features["crank_valid"]), float(w.features["accel_valid"]),
                     float(tick)])
    np.savez_compressed(path, X=np.array(X, dtype=np.float32), S=np.array(S, dtype=np.float16),
                        meta=np.array(meta, dtype=np.float32))
    return "ok"


def main():
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    OPEN_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [(spec_one, j) for j in plan()] + [(openset_one, j) for j in openset_plan()]
    t0 = time.perf_counter()
    with Pool(WORKERS) as pool:
        for i, _ in enumerate(pool.imap_unordered(_call, jobs), 1):
            if i % 50 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} ({time.perf_counter() - t0:.0f} s)", flush=True)


def _call(fj):
    f, j = fj
    return f(j)


if __name__ == "__main__":
    main()
