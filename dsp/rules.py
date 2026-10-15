"""Physics rules: the rule layer's physics witness (plan section 8, step 5).

Every rule has two versions (DECISIONS.md D13):
  crank_angle   simulator / injected data, which carry a crank reference
  firing_event  real audio, relative to detected firing pulses (not built yet)
Each rule returns a contract `physics_rules` entry; the `rule` field is the fault name.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

THRESHOLDS_DIR = Path(__file__).resolve().parents[1] / "models" / "thresholds"
MISFIRE_HALF_ORDER = THRESHOLDS_DIR / "misfire_half_order.json"


def load_thresholds(path: Path = MISFIRE_HALF_ORDER) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def misfire_half_order(features: dict, thresholds: dict) -> dict:
    """Misfire rule, crank-angle version.

    Fires when the window's half-order ratio of crank speed exceeds the threshold calibrated on
    healthy simulator runs. Location = the cylinder with the largest crank roughness.
    """
    fired = bool(features["half_order_ratio"] > thresholds["half_order_ratio"]["threshold"])
    location = int(np.argmax(features["roughness"]) + 1) if fired else None
    return {"rule": "misfire", "fired": fired, "location": location, "rule_basis": "crank_angle"}


def misfire_firing_event(pulse_times: np.ndarray, pulse_strengths: np.ndarray, thresholds: dict) -> dict:
    """Misfire rule, firing-event version for real audio: a missing or weak pulse in the sequence
    of detected firing pulses. Reports no location (real clips have no cam/TDC reference).

    Returns {"rule": "misfire", "fired": ..., "location": None, "rule_basis": "firing_event"}.
    """
    raise NotImplementedError("firing-event misfire rule: needs the firing-pulse detector (dsp, later)")


def per_location(roughness: np.ndarray, thresholds: dict) -> list[float]:
    """Roughness scaled for the dashboard bars: 0..1, with the calibrated limit at 0.5."""
    lim = thresholds["roughness"]["threshold"]
    return [round(float(np.clip(r / (2 * lim), 0.0, 1.0)), 3) for r in roughness]


PER_LOCATION_THRESHOLD = 0.5
