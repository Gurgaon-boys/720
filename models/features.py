"""One feature pipeline for training and live use: simulator chunks -> 4-cycle windows -> the per-window
hand-feature vector of the fast classifier and the healthy model (plan section 9).

StreamFeatures is what both models/make_dataset.py and backend/sources/sim_source.py run, so the
classifier sees exactly the same numbers in training and in the live demo.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dsp.features import N_SECTORS, window_features
from dsp.obd_window import ObdHistory
from dsp.windows import CycleWindower

FEATURE_NAMES = (
    ["half_order_energy"]
    + [f"roughness_c{i}" for i in range(1, 5)]
    + [f"weak_cyl_c{i}" for i in range(1, 5)]
    + [f"knock_db_c{i}" for i in range(1, 5)]
    + [f"accel_sector_{s:02d}" for s in range(N_SECTORS)]
    + ["lambda_sd", "trim_mean_pct", "trim_sd_pct", "ltft_pct", "spark_retard_deg", "rpm_mean", "map_kpa"]
)
LAMBDA_KEEP_S = 2.0


@dataclass
class WindowOut:
    win: object                # dsp.windows.CycleWindow
    features: dict             # dsp.features.window_features
    obd: dict                  # dsp.obd_window.obd_window over the last 30 s
    vector: np.ndarray         # FEATURE_NAMES order, float64


def vector(f: dict, obd: dict, lambda_sd: float) -> np.ndarray:
    """The classifier / healthy-model input for one window. Per-cylinder values are in cylinder order."""
    return np.concatenate([
        [f["half_order_energy"] * 100.0],
        np.asarray(f["roughness"]) * 100.0,
        np.asarray(f["weak_cyl"]) * 1000.0,
        np.asarray(f["knock_db"]),
        np.asarray(f["accel_sectors"]),
        [lambda_sd * 100.0, obd["trim_mean_pct"], obd["trim_sd_pct"], obd["ltft_pct"], obd["spark_retard_deg"],
         f["rpm_mean"] / 1000.0, (f["map_kpa"] or 0.0) / 10.0],
    ]).astype(float)


class StreamFeatures:
    """Push simulator chunks; get a WindowOut for every finished 4-cycle window (50% overlap)."""

    def __init__(self):
        self.windower = CycleWindower(4, 0.5)
        self.obd = ObdHistory()
        self._lam_t = np.zeros(0)
        self._lam = np.zeros(0)
        self._ret_t = np.zeros(0)
        self._ret = np.zeros((0, 4))

    def push(self, chunk: dict) -> list[WindowOut]:
        self.obd.push(chunk["obd"])
        fast = chunk.get("ecu_fast")
        if fast is not None and len(fast["t"]):
            self._lam_t = np.concatenate([self._lam_t, fast["t"]])
            self._lam = np.concatenate([self._lam, fast["lambda"]])
            keep = self._lam_t > self._lam_t[-1] - LAMBDA_KEEP_S
            self._lam_t, self._lam = self._lam_t[keep], self._lam[keep]
            if "retard_deg" in fast:
                self._ret_t = np.concatenate([self._ret_t, fast["t"]])[-400:]
                self._ret = np.concatenate([self._ret, fast["retard_deg"]])[-400:]
        out = []
        for win in self.windower.push(chunk):
            f = window_features(win, retard_deg=self.retard(win.t_start, win.t_end))
            o = self.obd.window(win.t_end)
            sel = (self._lam_t >= win.t_start) & (self._lam_t <= win.t_end)
            lam_sd = float(np.std(self._lam[sel])) if sel.sum() >= 4 else 0.0
            out.append(WindowOut(win, f, o, vector(f, o, lam_sd)))
        return out

    def retard(self, t0: float, t1: float):
        """Per-cylinder ECU knock retard over the window (max), or None before any ECU record."""
        sel = (self._ret_t >= t0) & (self._ret_t <= t1)
        if sel.any():
            return self._ret[sel].max(axis=0)
        return self._ret[-1] if len(self._ret) else None
