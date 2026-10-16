"""Physics rules: the rule layer's physics witness (plan section 8, step 5).

Every rule has two versions (DECISIONS.md D13):
  crank_angle   simulator / injected data, which carry a crank reference
  firing_event  real audio, relative to detected firing pulses (not built yet)
Each rule returns a contract `physics_rules` entry; the `rule` field is the fault name.

Thresholds come from models/thresholds/misfire_half_order.json. From sim v2 they are per rpm band
(normal at idle is not normal at 3000 rpm, plan section 10); the v1 file format (one global
threshold, kept as misfire_half_order_v1.json) is still accepted.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

THRESHOLDS_DIR = Path(__file__).resolve().parents[1] / "models" / "thresholds"
MISFIRE_HALF_ORDER = THRESHOLDS_DIR / "misfire_half_order.json"
MISFIRE_HALF_ORDER_V1 = THRESHOLDS_DIR / "misfire_half_order_v1.json"
PER_LOCATION_THRESHOLD = 0.5


def load_thresholds(path: Path = MISFIRE_HALF_ORDER) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def band_for(thresholds: dict, rpm: float | None, map_kpa: float | None = None) -> dict:
    """The threshold set for an operating point: the rpm x load (MAP) band containing it, else the
    nearest one. An rpm outside every calibrated band (the held-out 3000-3500 rpm) uses the closest
    band: extrapolation, reported separately. A missing MAP uses the low-load band."""
    bands = thresholds.get("bands")
    if not bands:
        return thresholds                                   # v1 format: one global threshold
    rpm = bands[0]["rpm_lo"] if rpm is None else rpm
    m = 0.0 if map_kpa is None else map_kpa

    def dist(b):
        dr = 0.0 if b["rpm_lo"] <= rpm < b["rpm_hi"] else min(abs(rpm - b["rpm_lo"]), abs(rpm - b["rpm_hi"]))
        dm = 0.0 if b.get("map_lo", -1e9) <= m < b.get("map_hi", 1e9) else 1.0
        return (dr, dm)

    return min(bands, key=dist)


def misfire_half_order(features: dict, thresholds: dict) -> dict:
    """Misfire rule, crank-angle version.

    Fires when the window's half-order ratio of crank speed exceeds the threshold calibrated on
    healthy simulator runs for that rpm band. Location = the cylinder with the largest crank roughness.
    """
    if not features.get("crank_valid", True):
        # crank data missing (sensor dropout): the rule can't run, so it can't be a witness
        return {"rule": "misfire", "fired": False, "location": None, "rule_basis": "crank_angle"}
    band = band_for(thresholds, features.get("rpm_mean"), features.get("map_kpa"))
    feature = thresholds.get("feature", "half_order_ratio")     # v1 files predate the key
    fired = bool(features[feature] > band[feature]["threshold"])
    location = int(np.argmax(features["roughness"]) + 1) if fired else None
    return {"rule": "misfire", "fired": fired, "location": location, "rule_basis": "crank_angle"}


def misfire_firing_event(pulse_times: np.ndarray, pulse_strengths: np.ndarray, thresholds: dict) -> dict:
    """Misfire rule, firing-event version for real audio: a missing or weak pulse in the sequence
    of detected firing pulses. Reports no location (real clips have no cam/TDC reference).

    Returns {"rule": "misfire", "fired": ..., "location": None, "rule_basis": "firing_event"}.
    """
    raise NotImplementedError("firing-event misfire rule: needs the firing-pulse detector (dsp, later)")


def per_location(roughness: np.ndarray, thresholds: dict, rpm: float | None = None,
                 map_kpa: float | None = None) -> list[float]:
    """Roughness scaled for the dashboard bars: 0..1, with the band's calibrated limit at 0.5."""
    lim = band_for(thresholds, rpm, map_kpa)["roughness"]["threshold"]
    return [round(float(np.clip(r / (2 * lim), 0.0, 1.0)), 3) for r in roughness]
