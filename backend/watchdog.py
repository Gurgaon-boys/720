"""Backend watchdog (plan §11.1, DECISIONS.md D15): a sensor stream with no data for 3 s is FAILURE.

This is sensor health under `health.sensors`, kept separate from engine (asset) health.
The UI runs its own watchdog on `sent_at`, because a crashed backend can't report its own crash.
"""

from __future__ import annotations

STALL_S = 3.0


class Watchdog:
    def __init__(self, sensors, now: float, stall_s: float = STALL_S) -> None:
        self.stall_s = stall_s
        self._last = {s: now for s in sensors}  # every stream starts healthy

    def watches(self, sensor: str) -> bool:
        return sensor in self._last

    def feed(self, sensor: str, now: float) -> None:
        self._last[sensor] = now

    def status(self, now: float) -> dict[str, str]:
        return {s: "FAILURE" if now - last > self.stall_s else "NORMAL" for s, last in self._last.items()}
