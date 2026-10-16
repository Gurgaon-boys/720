"""Plug-in point for witness 2 of the OBD branch: Diljit's OBD-only classifier (D125, models/obd/sim_classifier.py).

DISABLED BY DEFAULT (D133). The classifier was trained on simulator OBD and failed sim-to-real on the
held-out carOBD files (D126: names a fault in 73 % of windows, 28.5 false faults/h, 2 two-witness ALARMs in
12.54 h), so it is NOT deployed: the REAL carOBD replay runs single-witness (ADVISORY / UNCLASSIFIED at most)
and says so in `state.replay`.

load_witness() is what backend/sources/carobd_replay.py calls. It returns None unless S720_OBD_CLASSIFIER=1
AND a checkpoint exists at CHECKPOINT (models/obd/sim_classifier.py doesn't save one yet). When it returns a
witness, it is a callable(OBD v1 window dict) -> models.witness.WitnessOut, and ALARM then follows the
normal two-witness rule. Enabling it is for experiments only, never for the judged demo, until a classifier
passes the real-data false-fault check.
"""

from __future__ import annotations

import logging
import os
import pickle
import time
from pathlib import Path

ENV = "S720_OBD_CLASSIFIER"
CHECKPOINT = Path(__file__).resolve().parents[1] / "checkpoints" / "obd_classifier.pkl"
log = logging.getLogger("backend")


def enabled() -> bool:
    return os.environ.get(ENV) == "1"


def load_witness(path: Path = CHECKPOINT):
    if not enabled():
        return None                      # D133: not deployed (sim-to-real failed, D126)
    if not path.exists():
        log.warning("%s=1 but no OBD classifier checkpoint at %s; running single-witness", ENV, path)
        return None
    from models.obd.sim_classifier import classifier_witness, xmat
    from models.witness import WitnessOut

    with open(path, "rb") as f:
        clf = pickle.load(f)
    log.warning("OBD classifier ENABLED from %s: NOT deployed (D126: 28.5 false faults/h on real carOBD)", path)

    def witness(w: dict) -> WitnessOut:
        t0 = time.perf_counter()
        models = classifier_witness(clf, xmat([w]))
        return WitnessOut(models, None, None, (time.perf_counter() - t0) * 1e3)

    return witness
