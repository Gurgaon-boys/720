"""Shared evaluation plumbing: seed ranges, one simulated run through the full chain
(sim -> 4-cycle windows -> features -> misfire rule -> the real rule layer), and statistics.

Seed ranges are disjoint by construction and asserted below; nothing here tunes anything.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from backend.rule_layer import Evidence, RuleLayer
from dsp.calibrate_misfire import CAL_ENGINES, VAL_ENGINES
from dsp.features import TDC_DEG, window_features
from dsp.rules import band_for, load_thresholds, misfire_half_order
from dsp.windows import CycleWindower
from sim import EngineParams, EngineSim
from sim.params import Regimes

# engine-seed ranges (run seed = engine seed + offset)
SEEDS = {
    "calibration": (CAL_ENGINES, 1000),
    "validation": (VAL_ENGINES, 2000),
    "soak": (list(range(200, 240)), 3000),
    "robustness": (list(range(400, 440)), 5000),
    "held_out_region": (list(range(600, 640)), 7000),
    # classifier dataset (models/make_dataset.py), split by engine
    "dataset_train": (list(range(700, 760)), 10000),
    "dataset_val": (list(range(760, 775)), 20000),
    "dataset_test": (list(range(775, 790)), 30000),
    # fresh soak engines (L2 stage 2): never used for calibration, training, tuning or any earlier evaluation
    "soak_fresh": (list(range(900, 940)), 40000),
    "soak_fresh2": (list(range(940, 980)), 41000),   # after the start-up enable condition (D89)
    # L3: a THIRD untouched soak range, read only after the healthy-model threshold was fixed on validation
    "soak_fresh3": (list(range(1100, 1140)), 42000),
    # L3: extra VALIDATION engines for the healthy-model threshold (engine-to-engine spread); tuning allowed here
    "val_thr": (list(range(1200, 1280)), 50000),
}
_used = [e for k, (es, _) in SEEDS.items() for e in es]
assert len(_used) == len(set(_used)), "seed ranges overlap"
EVAL_RANGES = ("soak", "robustness", "held_out_region", "soak_fresh", "soak_fresh2", "soak_fresh3")
VALIDATION_RANGES = ("val_thr",)       # simulated like evaluation runs, but used for tuning (never reported as evidence)


def eval_engine(kind: str, i: int) -> tuple[EngineParams, int]:
    """(params, run seed) for the i-th engine of an evaluation range; held_out_region uses the 'test' region."""
    if kind not in EVAL_RANGES + VALIDATION_RANGES:
        raise ValueError(f"{kind!r} is not an evaluation range")
    engines, off = SEEDS[kind]
    e = engines[i % len(engines)]
    region = "test" if kind == "held_out_region" else "train"
    return EngineParams.randomised(e, region), off + e + 1000 * (i // len(engines))


@dataclass
class WindowResult:
    t_end: float
    rpm: float
    valid: bool
    fired: bool
    location: int | None
    true_misfire: list = field(default_factory=list)   # cylinders that misfired in this window
    first_misfire_t: float | None = None                # time of the earliest misfired combustion in it
    decision: str = "NORMAL"
    compute_ms: float = 0.0
    value: float = 0.0                                  # the rule's feature (half-order energy)
    threshold: float = 0.0                              # the band threshold it was compared with


def run(params: EngineParams, seed: int, rpm: float, load_nm: float, seconds: float, *,
        regimes: Regimes | None = None, fault: tuple | None = None, inject_at: float = 0.0,
        warmup_s: float = 4.0, thresholds: dict | None = None, mic_snr_db: float | None = None,
        accel_snr_db: float | None = None) -> list[WindowResult]:
    """Simulate one run and return every window after warm-up, with the rule-layer decision.

    fault = (name, cylinder, severity), injected at `inject_at` seconds after warm-up.
    """
    from dataclasses import replace
    if mic_snr_db is not None or accel_snr_db is not None:
        params = replace(params, **({"snr_db": mic_snr_db} if mic_snr_db is not None else {}),
                         **({"accel_snr_db": accel_snr_db} if accel_snr_db is not None else {}))
    th = thresholds or load_thresholds()
    sim = EngineSim(params, seed=seed, regimes=regimes if regimes is not None else Regimes.eval(), purpose="eval")
    sim.set_operating_point(rpm_target=rpm, load_nm=load_nm)
    w = CycleWindower()
    rules = RuleLayer()
    out = []
    injected = fault is None
    n_chunks = int(round((warmup_s + seconds) / 0.5))
    for k in range(n_chunks):
        if not injected and k * 0.5 >= warmup_s + inject_at:
            sim.inject(*fault)
            injected = True
        chunk = sim.step(0.5)
        for win in w.push(chunk):
            t0 = time.perf_counter()
            f = window_features(win)
            r = misfire_half_order(f, th)
            decision = rules.step(Evidence(models=[], physics_rules=[r], anomaly_score=None, anomaly_threshold=0.5))
            ms = (time.perf_counter() - t0) * 1e3
            if win.t_start < warmup_s:
                continue
            mis = sorted({c + 1 for lab in win.labels for c in range(4) if lab["misfired"][c]})
            first = _first_misfire_time(win)
            feat = th.get("feature", "half_order_ratio")
            thr = band_for(th, f["rpm_mean"], f.get("map_kpa"))[feat]["threshold"]
            out.append(WindowResult(win.t_end, f["rpm_mean"], f["crank_valid"], r["fired"], r["location"],
                                    mis, first, decision["state"], ms, float(f[feat]), float(thr)))
    return out


def _first_misfire_time(win) -> float | None:
    times = []
    for lab in win.labels:
        for c in range(4):
            if lab["misfired"][c]:
                ang = 720.0 * lab["cycle"] + TDC_DEG[c + 1]
                if win.theta[0] <= ang <= win.theta[-1]:
                    times.append(float(np.interp(ang, win.theta, win.t)))
    return min(times) if times else None


# ---------------------------------------------------------------- statistics

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def rate_per_hour(k: int, n_windows: int, hours: float) -> dict:
    """Events per hour with a 95% bound: 3/T (rule of three) for zero events, else a Wilson interval
    on the per-window proportion scaled by windows per hour."""
    out = {"events": k, "hours": round(hours, 3), "per_hour": k / hours if hours else float("nan")}
    if k == 0:
        out.update(upper_95=3.0 / hours, method="rule of three (3/T)")
    else:
        lo, hi = wilson(k, n_windows)
        out.update(lower_95=lo * n_windows / hours, upper_95=hi * n_windows / hours, method="Wilson on per-window proportion")
    return out


def rpm_band(rpm: float) -> str:
    return "idle" if rpm < 1100 else "mid" if rpm < 2000 else "high" if rpm < 3000 else "held-out 3000-3500"
