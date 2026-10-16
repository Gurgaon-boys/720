"""Healthy model for the OBD branch: Mahalanobis distance per regime (plan section 10.1).

"Normal at idle is not normal at 3,000 rpm", so each regime (warm-up, idle, mid, high RPM) gets
its own mean and Ledoit-Wolf shrunk covariance, fitted on healthy windows only. The anomaly
threshold per regime is a quantile of distances on separate healthy VALIDATION windows.
"""
from __future__ import annotations

import numpy as np
from sklearn.covariance import LedoitWolf

FEATURES = ("stft_mean", "stft_std", "stft_slope", "ltft_mean", "ltft_slope", "timing_mean", "timing_std",
            "map_resid_mean", "map_resid_std", "coolant_mean", "rpm_mean", "load_mean", "retard_events")


class HealthyModel:
    def __init__(self, features=FEATURES):
        self.features = tuple(features)
        self.params: dict[str, dict] = {}

    def _x(self, windows: list[dict]) -> np.ndarray:
        return np.array([[w[f] for f in self.features] for w in windows], float)

    def fit(self, windows: list[dict]) -> "HealthyModel":
        for reg in sorted({w["regime"] for w in windows}):
            x = self._x([w for w in windows if w["regime"] == reg])
            scale = x.std(axis=0)
            scale[scale == 0] = 1.0
            lw = LedoitWolf().fit((x - x.mean(axis=0)) / scale)
            self.params[reg] = {"n_fit": len(x), "mean": x.mean(axis=0), "scale": scale,
                                "precision": lw.precision_, "shrinkage": float(lw.shrinkage_)}
        return self

    def distance(self, w: dict) -> float:
        p = self.params[w["regime"]]
        z = (np.array([w[f] for f in self.features], float) - p["mean"]) / p["scale"]
        return float(np.sqrt(z @ p["precision"] @ z))

    def set_thresholds(self, val_windows: list[dict], quantile: float) -> None:
        for reg, p in self.params.items():
            d = [self.distance(w) for w in val_windows if w["regime"] == reg]
            if not d:
                raise ValueError(f"no validation windows in regime {reg}")
            p["threshold"] = float(np.quantile(d, quantile))
            p["n_val"] = len(d)

    def threshold(self, w: dict) -> float:
        return self.params[w["regime"]]["threshold"]

    def to_json(self) -> dict:
        return {"features": list(self.features),
                "regimes": {r: {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in p.items()}
                            for r, p in self.params.items()}}

    @classmethod
    def from_json(cls, d: dict) -> "HealthyModel":
        m = cls(d["features"])
        m.params = {r: {k: (np.asarray(v) if isinstance(v, list) else v) for k, v in p.items()}
                    for r, p in d["regimes"].items()}
        return m
