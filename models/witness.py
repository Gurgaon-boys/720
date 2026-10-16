"""Adapter: the fast classifier and healthy model as the rule layer's CLASSIFIER and anomaly witnesses.

The rule layer is not forked: this turns one window's feature vector into the contract's `models`
entries, the classifier's cylinder (Evidence.classifier_location) and the anomaly score. The score
is the Mahalanobis distance scaled so the band's threshold sits at ANOMALY_LIMIT (0.5, the same
scale as the per-cylinder bars), so `anomaly.threshold` keeps one meaning on the dashboard.
Open set: when a window lies outside the training data of the fault the SVM names (class-conditional
Mahalanobis), the classifier abstains (p capped at OPEN_SET_P < P_MIN), so an unknown fault with a high
anomaly score becomes UNCLASSIFIED instead of a confident wrong name.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from models.classifier import CHECKPOINT, load

ANOMALY_LIMIT = 0.5
MIN_OBD_ROWS = 20     # enable condition: the classifier only saw windows with a full OBD history (30 s warm-up),
                      # so before the OBD window holds 20 rows (the trim rule's own minimum) it abstains
OPEN_SET_P = 0.5      # below the rule layer's P_MIN (0.6): an abstaining classifier is no witness
WITNESS_P = 0.8       # a named fault below this calibrated p abstains. Chosen on VAL: false faults on healthy
                      # val runs 1.9 % -> 0.01 % of windows; per-window recall misfire 0.92, knock 0.92, clog 0.70


@dataclass
class WitnessOut:
    models: list            # contract models[] entries
    location: int | None    # classifier's cylinder for a named fault
    score: float | None     # None when no model is loaded (schema 1.5)
    cpu_ms: float


class ClassifierWitness:
    def __init__(self, path: Path | None = CHECKPOINT):
        self.model = load(path) if path is not None and path.exists() else None

    @property
    def available(self) -> bool:
        return self.model is not None

    def __call__(self, vec: np.ndarray, rpm: float, valid: bool = True, obd_rows: int | None = None) -> WitnessOut:
        if self.model is None:
            return WitnessOut([], None, None, 0.0)
        t0 = time.perf_counter()
        labels, p, cyl, _ = self.model.predict(vec[None, :])
        d, thr = self.model.anomaly(vec[None, :], np.array([rpm]))
        ms = (time.perf_counter() - t0) * 1e3
        label = labels[0]
        pv = float(p[0])
        short = obd_rows is not None and obd_rows < MIN_OBD_ROWS
        if label != "healthy" and (short or pv < WITNESS_P or not self.model.in_class(vec[None, :], [label])[0]):
            pv = min(pv, OPEN_SET_P)      # not confident, or outside that fault's training data: abstain
        loc = int(cyl[0]) if label != "healthy" and cyl[0] > 0 and pv > OPEN_SET_P else None
        # a window with a crank or accelerometer dropout gets no anomaly score (sensor health covers it)
        score = round(float(ANOMALY_LIMIT * d[0] / thr[0]), 4) if valid else None
        return WitnessOut([{"model": "SVM", "label": label, "p": round(pv, 3)}], loc, score, ms)
