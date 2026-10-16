"""OBD-II-style misfire counter (plan stage 3): per-cylinder misfire counts over the last 200 and
1000 crankshaft revolutions, like Mode $06 misfire monitor data.

Each firing's relative crank-speed drop (dsp.features roughness, neighbour-relative) is compared with the
calibrated per-band roughness threshold (models/thresholds/misfire_half_order.json, healthy calibration
engines only). Windows overlap by 50 %, so firings are de-duplicated by their absolute index.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from dsp.features import _drops, crank_valid
from dsp.rules import band_for

WINDOWS_REVS = (200, 1000)


class MisfireCounter:
    def __init__(self, thresholds: dict):
        self.th = thresholds
        self.events: deque = deque()          # (firing index, cylinder, misfired)
        self.last_index = None

    def push(self, win, rpm_mean: float, map_kpa: float | None = None) -> None:
        if not crank_valid(win):
            return
        rel, cyl, idx = _drops(win, with_index=True)
        thr = band_for(self.th, rpm_mean, map_kpa)["roughness"]["threshold"]
        for r, c, j in zip(rel, cyl, idx):
            if self.last_index is not None and j <= self.last_index:
                continue
            self.events.append((int(j), int(c), bool(r > thr)))
            self.last_index = int(j)
        while self.events and self.events[0][0] < self.last_index - 2 * max(WINDOWS_REVS):
            self.events.popleft()

    def counts(self, revs: int) -> list[int]:
        """Misfires per cylinder (index 0 = cylinder 1) over the last `revs` revolutions (2 firings per rev)."""
        if self.last_index is None:
            return [0, 0, 0, 0]
        lo = self.last_index - 2 * revs
        out = [0, 0, 0, 0]
        for j, c, m in self.events:
            if j > lo and m:
                out[c - 1] += 1
        return out

    def revs_seen(self) -> int:
        return 0 if not self.events else (self.events[-1][0] - self.events[0][0] + 1) // 2
