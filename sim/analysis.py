"""Thin re-export kept for the sim tests and demo. The helpers now live in dsp/features.py."""

from __future__ import annotations

import numpy as np

from dsp.features import amplitude_at, order_spectrum, speed_from_teeth, tsa_720, unwrap_720

__all__ = ["amplitude_at", "half_order_ratio", "order_spectrum", "run", "speed_from_teeth", "tsa_720", "unwrap_720"]


def run(sim, seconds: float, chunk_s: float = 0.5) -> dict:
    """Step `sim` for `seconds` and concatenate the chunks (labels are concatenated as a list)."""
    chunks = [sim.step(chunk_s) for _ in range(int(round(seconds / chunk_s)))]
    out = {}
    for k in ("t", "crank_angle_deg", "rpm", "tooth_times", "tooth_ids", "mic"):
        out[k] = np.concatenate([c[k] for c in chunks])
    out["cylinder_pressure"] = np.concatenate([c["cylinder_pressure"] for c in chunks], axis=1)
    out["labels"] = [lab for c in chunks for lab in c["labels"]]
    out["meta"] = chunks[-1]["meta"]
    return out


def half_order_ratio(healthy: dict, faulty: dict, wheel_first_tooth_deg: float) -> float:
    """Half-order amplitude of tooth-derived crank speed over a whole record, faulty / healthy."""
    amps = []
    for r in (healthy, faulty):
        _, ang, rpm = speed_from_teeth(r["tooth_times"], r["tooth_ids"], first_tooth_deg=wheel_first_tooth_deg)
        o, s = order_spectrum(rpm, ang)
        amps.append(amplitude_at(o, s, 0.5))
    return amps[1] / amps[0]
