"""Input stage: where windows of evidence come from. Everything after it (rule layer, messages,
sockets) is the same for every source (plan section 11.1, single code path).

A source provides: name, description, source ("simulated" | "real"), sensors, phase_at(t),
live_sensors(t), window(k) -> Window | None, signal_frames(j, live), obd_sample(i).
The simulator source also accepts inject(fault, cylinder, severity) and clear_faults().
The carOBD replay (REAL, mode "replay") accepts inject("lean_drift", ...) and has obd_channels(i), which
the runtime uses instead of obd_sample(i) so each value goes out at its own file time.
"""

from __future__ import annotations

from backend.scenarios import SCENARIOS

SIM = "sim"
SIM_DESCRIPTION = "Physics-informed simulator v1 at idle, live; inject faults with POST /inject"
REPLAY = "carobd_replay"
REPLAY_DESCRIPTION = "REAL carOBD held-out log, replayed (not live); pick file and speed with POST /replay"


def source_names() -> list[str]:
    return [*SCENARIOS, SIM, REPLAY]


def make_source(name: str):
    """A fresh source by name: a fake L0 scenario, or the live simulator. KeyError if unknown."""
    if name in SCENARIOS:
        return SCENARIOS[name]
    if name == SIM:
        from backend.sources.sim_source import SimSource  # numpy/scipy/sim only when used

        return SimSource.from_env()
    if name == REPLAY:
        from backend.sources.carobd_replay import make

        return make()
    raise KeyError(name)


def describe() -> list[dict]:
    """The /scenarios listing: every fake scenario plus the simulator."""
    out = [{"name": s.name, "description": s.description, "source": s.source, "loop_s": s.period}
           for s in SCENARIOS.values()]
    out.append({"name": SIM, "description": SIM_DESCRIPTION, "source": "simulated", "loop_s": None})
    out.append({"name": REPLAY, "description": REPLAY_DESCRIPTION, "source": "real", "loop_s": None})
    return out
