"""Input stage: where windows of evidence come from. Everything after it (rule layer, messages,
sockets) is the same for every source (plan section 11.1, single code path).

A source provides: name, description, source ("simulated" | "real"), sensors, phase_at(t),
live_sensors(t), window(k) -> Window | None, signal_frames(j, live), obd_sample(i).
The simulator source also accepts inject(fault, cylinder, severity) and clear_faults().
"""

from __future__ import annotations

from backend.scenarios import SCENARIOS

SIM = "sim"
SIM_DESCRIPTION = "Physics-informed simulator v1 at idle, live; inject faults with POST /inject"


def source_names() -> list[str]:
    return [*SCENARIOS, SIM]


def make_source(name: str):
    """A fresh source by name: a fake L0 scenario, or the live simulator. KeyError if unknown."""
    if name in SCENARIOS:
        return SCENARIOS[name]
    if name == SIM:
        from backend.sources.sim_source import SimSource  # numpy/scipy/sim only when used

        return SimSource.from_env()
    raise KeyError(name)


def describe() -> list[dict]:
    """The /scenarios listing: every fake scenario plus the simulator."""
    out = [{"name": s.name, "description": s.description, "source": s.source, "loop_s": s.period}
           for s in SCENARIOS.values()]
    out.append({"name": SIM, "description": SIM_DESCRIPTION, "source": "simulated", "loop_s": None})
    return out
