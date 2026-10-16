"""Held-out guard: nothing held out may reach calibration or training (plan section 10).

Held out: the valve_tick fault (tests "unknown"), the 3000-3500 rpm band and the "test" parameter
region (tests generalisation). Calibration and training code calls these checks; a violation
raises HeldOutError instead of silently mixing held-out data in.
"""

from __future__ import annotations

from sim.params import HELD_OUT_FAULTS, HELD_OUT_RPM


class HeldOutError(RuntimeError):
    pass


def check_labels(labels) -> None:
    """Raise if any cycle label carries a held-out fault."""
    for lab in labels:
        held = lab.get("held_out") or any(f.get("fault") in HELD_OUT_FAULTS for f in lab.get("faults", []))
        if held or lab.get("fault") in HELD_OUT_FAULTS:
            raise HeldOutError(f"cycle {lab.get('cycle')} carries a held-out fault ({lab.get('fault')})")


def check_chunk(chunk: dict) -> None:
    if chunk.get("meta", {}).get("held_out"):
        raise HeldOutError("chunk comes from a simulator with a held-out fault active")
    check_labels(chunk.get("labels", []))


def check_condition(rpm_target: float, region: str) -> None:
    """Raise for an operating condition or parameter region that is held out."""
    lo, hi = HELD_OUT_RPM
    if lo <= rpm_target <= hi:
        raise HeldOutError(f"{rpm_target:.0f} rpm is in the held-out band {lo:.0f}-{hi:.0f}")
    if region != "train":
        raise HeldOutError(f"parameter region {region!r} is held out")
