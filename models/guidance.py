"""Operator guidance: one deterministic line per (fault, NE 107 state), from models/guidance.json.

guidance(decision, health, capability) is a pure function of the contract's `decision` and `health` objects. Precedence
(one line, the most urgent): a latched ALARM, then UNCLASSIFIED, then any sensor in FAILURE (diagnostics
from the remaining sensors can't be trusted), then a single-witness ADVISORY. NORMAL gives None.

capability names what the SOURCE can do (D135), e.g. "single_witness_real_obd" for a REAL carOBD replay with no
OBD classifier: there, ALARM is impossible, so its ADVISORY line must not say "watch for ALARM". A capability
line replaces the generic line with the same key; keys it doesn't define fall back to the generic ones.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

GUIDANCE = Path(__file__).resolve().parent / "guidance.json"


@lru_cache(maxsize=1)
def table() -> dict:
    return json.loads(GUIDANCE.read_text(encoding="utf-8"))


def guidance(decision: dict, health: dict, capability: str | None = None) -> str | None:
    g = table()
    if capability is not None:
        if capability not in g["capability"]:
            raise KeyError(f"unknown guidance capability {capability!r}")
        g = {**g, **g["capability"][capability]}
    state, ne107 = decision["state"], health["asset_ne107"]
    cyl = decision.get("location")
    if state == "ALARM":
        line = g["alarm"].get(f"{decision['fault']}|{ne107}")
        if line is None:                      # an unknown pairing must never pass silently
            raise KeyError(f"no guidance for ALARM {decision['fault']} / {ne107}")
        return line.format(cyl=cyl if cyl is not None else "?")
    if state == "UNCLASSIFIED":
        return g["unclassified|OFF_SPEC"]
    failed = sorted(s for s, v in health.get("sensors", {}).items() if v == "FAILURE")
    if failed:
        return g["sensor|FAILURE"].format(sensor=", ".join(failed))
    if state == "ADVISORY":
        return g["advisory|NORMAL"].format(fault=decision.get("fault") or "unknown")
    return g["normal|NORMAL"]
