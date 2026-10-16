"""OBD windows and features.

Input is a DataFrame with carOBD column names (data/carobd.py:load) and `t_s` in seconds.
carOBD writes ~2 rows/s but each PID refreshes at only ~0.55 Hz (DECISIONS D47), so a 30 s
window holds ~15 fresh values per channel; rows that repeat the previous value carry no new
information and are dropped before slopes and events are computed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOW_S = 30.0
HOP_S = 15.0  # 50% overlap
MIN_FRESH = 8  # windows with fewer fresh RPM values are skipped (logger gaps)

RPM, LOAD, MAP = "ENGINE_RPM", "ENGINE_LOAD", "INTAKE_MANIFOLD_PRESSURE"
STFT, LTFT = "SHORT_TERM_FUEL_TRIM_BANK_1", "LONG_TERM_FUEL_TRIM_BANK_1"
TIMING, COOLANT, SPEED = "TIMING_ADVANCE", "COOLANT_TEMPERATURE", "VEHICLE_SPEED"

# Retard event: timing falls by >= RETARD_DEG between consecutive fresh values while the engine is
# under load and RPM is steady, i.e. not a lift-off, gear change or deceleration fuel cut.
RETARD_DEG = 5.0
RETARD_MIN_LOAD = 40.0
RETARD_MAX_DRPM = 150.0

WARM_C = 75.0  # below this coolant temperature the window is in the warm-up regime
REGIMES = ("warmup", "idle", "mid", "high")


def regime(rpm_mean: float, coolant_mean: float) -> str:
    if coolant_mean < WARM_C:
        return "warmup"
    if rpm_mean < 1000:
        return "idle"
    return "mid" if rpm_mean < 1800 else "high"


def fresh(t: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Keep the first row of every run of repeated values."""
    keep = np.r_[True, np.diff(v) != 0]
    return t[keep], v[keep]


def slope_per_min(t: np.ndarray, v: np.ndarray) -> float:
    if len(t) < 3 or np.ptp(t) == 0:
        return 0.0
    return float(np.polyfit(t, v, 1)[0] * 60.0)


def retard_events(win: pd.DataFrame) -> int:
    """Timing pull-backs under steady load (counted on fresh timing values)."""
    t, adv = fresh(win["t_s"].to_numpy(float), win[TIMING].to_numpy(float))
    if len(t) < 2:
        return 0
    idx = np.searchsorted(win["t_s"].to_numpy(float), t)
    load = win[LOAD].to_numpy(float)[idx]
    rpm = win[RPM].to_numpy(float)[idx]
    drop = -np.diff(adv)
    ok = (drop >= RETARD_DEG) & (load[1:] >= RETARD_MIN_LOAD) & (np.abs(np.diff(rpm)) <= RETARD_MAX_DRPM)
    return int(ok.sum())


def window_features(win: pd.DataFrame, map_model: "MapModel | None" = None) -> dict | None:
    """Feature dict for one window, or None if it has too few fresh values."""
    t = win["t_s"].to_numpy(float)
    t_rpm, rpm = fresh(t, win[RPM].to_numpy(float))
    if len(rpm) < MIN_FRESH or np.median(rpm) < 300:  # engine off / cranking
        return None
    t_st, st = fresh(t, win[STFT].to_numpy(float))
    t_lt, lt = fresh(t, win[LTFT].to_numpy(float))
    t_co, co = fresh(t, win[COOLANT].to_numpy(float))
    stft = win[STFT].to_numpy(float)
    ltft = win[LTFT].to_numpy(float)
    f = {
        "t0": float(t[0]),
        "rpm_mean": float(rpm.mean()),
        "rpm_range": float(np.percentile(rpm, 95) - np.percentile(rpm, 5)),
        "load_mean": float(win[LOAD].mean()),
        "speed_mean": float(win[SPEED].mean()),
        "coolant_mean": float(win[COOLANT].mean()),
        "coolant_slope": slope_per_min(t_co, co) if len(co) > 1 else 0.0,
        "stft_mean": float(stft.mean()),
        "stft_std": float(stft.std()),
        "stft_slope": slope_per_min(t_st, st) if len(st) > 1 else 0.0,
        "ltft_mean": float(ltft.mean()),
        "ltft_slope": slope_per_min(t_lt, lt) if len(lt) > 1 else 0.0,
        "total_trim": float(stft.mean() + ltft.mean()),
        "timing_mean": float(win[TIMING].mean()),
        "timing_std": float(win[TIMING].std()),
        "retard_events": retard_events(win),
    }
    if map_model is not None:
        r = map_model.residual(win)
        f["map_resid_mean"] = float(r.mean())
        f["map_resid_std"] = float(r.std())
    f["regime"] = regime(f["rpm_mean"], f["coolant_mean"])
    return f


def windows(df: pd.DataFrame, map_model: "MapModel | None" = None,
            window_s: float = WINDOW_S, hop_s: float = HOP_S) -> list[dict]:
    """All 30 s / 50%-overlap windows of one file, in time order."""
    t = df["t_s"].to_numpy(float)
    out = []
    t0 = 0.0
    while t0 + window_s <= t[-1] + 1e-9:
        lo, hi = np.searchsorted(t, t0), np.searchsorted(t, t0 + window_s)
        if hi - lo > 1:
            f = window_features(df.iloc[lo:hi], map_model)
            if f is not None:
                out.append(f)
        t0 += hop_s
    return out


class MapModel:
    """Expected manifold pressure from load and RPM (least squares on healthy warm rows).

    On a healthy engine MAP is nearly a function of calculated load and RPM; an air-path
    or sensor fault shows as a residual."""

    TERMS = ("1", "load", "rpm_k", "load*rpm_k")

    def __init__(self, coef: list[float] | None = None):
        self.coef = None if coef is None else np.asarray(coef, float)

    @staticmethod
    def _design(df: pd.DataFrame) -> np.ndarray:
        load = df[LOAD].to_numpy(float)
        rpm_k = df[RPM].to_numpy(float) / 1000.0
        return np.column_stack([np.ones_like(load), load, rpm_k, load * rpm_k])

    def fit(self, frames: list[pd.DataFrame]) -> "MapModel":
        rows = pd.concat([d[(d[COOLANT] >= WARM_C) & (d[RPM] > 300)] for d in frames])
        self.coef, *_ = np.linalg.lstsq(self._design(rows), rows[MAP].to_numpy(float), rcond=None)
        return self

    def residual(self, df: pd.DataFrame) -> np.ndarray:
        return df[MAP].to_numpy(float) - self._design(df) @ self.coef
