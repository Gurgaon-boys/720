"""OBD physics rules: the advisory-level witness of the OBD branch (plan section 10.1).

Both return a contract-shaped `physics_rules` entry. OBD has no cylinder reference, so
`location` is always None. NOTE: contract 1.4 only allows rule_basis crank_angle | firing_event;
"obd_window" is used offline here and needs a contract bump before these rules go live.
"""
from __future__ import annotations

RULE_BASIS = "obd_window"


def trim_drift(w: dict, th: dict) -> dict:
    """Injector clog -> lean mixture -> the ECU adds fuel: total fuel trim (STFT + LTFT, window
    mean) above the healthy band. Disabled in warm-up, when the loop may be open."""
    fired = w["regime"] != "warmup" and w["total_trim"] > th["total_trim_max"]
    return {"rule": "injector_clog", "fired": bool(fired), "location": None, "rule_basis": RULE_BASIS}


def timing_retard(w: dict, th: dict) -> dict:
    """Knock -> the ECU pulls spark timing and then recovers: repeated retard events under
    steady load within one window."""
    fired = w["retard_events"] >= th["retard_events_min"]
    return {"rule": "knock", "fired": bool(fired), "location": None, "rule_basis": RULE_BASIS}


def evaluate(w: dict, th: dict) -> list[dict]:
    return [trim_drift(w, th), timing_retard(w, th)]
