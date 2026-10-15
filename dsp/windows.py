"""Cut a stream of simulator chunks into windows of whole engine cycles (plan section 8.1).

A window is `n_cycles` complete 720 deg cycles (default 4) with 50% overlap, so a new window
starts every `n_cycles / 2` cycles. Cycle boundaries come from the crank angle (cylinder 1 firing
TDC, crank angle 0), never from wall time, so a window always holds whole cycles whatever the rpm.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

_STREAM_KEYS = ("t", "crank_angle_deg", "rpm", "mic")


@dataclass
class CycleWindow:
    index: int                 # window number in this stream
    first_cycle: int           # engine cycle index of the first cycle (cycle c starts at theta = 720 c)
    n_cycles: int
    t: np.ndarray              # s
    theta: np.ndarray          # unwrapped crank angle, deg; spans [720 first_cycle, 720 (first_cycle + n_cycles))
    crank_angle_deg: np.ndarray
    rpm: np.ndarray
    mic: np.ndarray
    tooth_times: np.ndarray
    tooth_ids: np.ndarray
    labels: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def t_start(self) -> float:
        return float(self.t[0])

    @property
    def t_end(self) -> float:
        return float(self.t[-1])

    @property
    def theta_start(self) -> float:
        return 720.0 * self.first_cycle


class CycleWindower:
    """Incremental windower: push simulator chunks in order, get finished windows back."""

    def __init__(self, n_cycles: int = 4, overlap: float = 0.5):
        hop = n_cycles * (1 - overlap)
        if n_cycles < 1 or hop < 1 or abs(hop - round(hop)) > 1e-9:
            raise ValueError("n_cycles * (1 - overlap) must be a whole number of cycles >= 1")
        self.n_cycles = n_cycles
        self.hop = int(round(hop))
        self._buf = {k: np.zeros(0, dtype=np.float32 if k == "mic" else float) for k in _STREAM_KEYS}
        self._theta = np.zeros(0)
        self._teeth_t = np.zeros(0)
        self._teeth_id = np.zeros(0, dtype=int)
        self._labels = {}
        self._meta = {}
        self._wraps = 0
        self._last_wrapped = None
        self._next_cycle = None   # first cycle of the next window
        self._index = 0

    def push(self, chunk: dict) -> list[CycleWindow]:
        wrapped = np.asarray(chunk["crank_angle_deg"], dtype=float)
        prev = wrapped[:1] if self._last_wrapped is None else np.array([self._last_wrapped])
        steps = np.diff(np.concatenate([prev, wrapped])) < -360.0
        theta = wrapped + 720.0 * (self._wraps + np.cumsum(steps))
        self._wraps += int(steps.sum())
        self._last_wrapped = float(wrapped[-1])

        for k in _STREAM_KEYS:
            self._buf[k] = np.concatenate([self._buf[k], np.asarray(chunk[k], dtype=self._buf[k].dtype)])
        self._theta = np.concatenate([self._theta, theta])
        self._teeth_t = np.concatenate([self._teeth_t, chunk["tooth_times"]])
        self._teeth_id = np.concatenate([self._teeth_id, chunk["tooth_ids"]]).astype(int)
        for lab in chunk.get("labels", []):
            self._labels[lab["cycle"]] = lab
        self._meta = chunk.get("meta", self._meta)

        if self._next_cycle is None:
            # the first whole cycle starts at the first boundary seen (or at sample 0 if it is one)
            th0 = self._theta[0]
            self._next_cycle = int(np.ceil(th0 / 720.0 - 1e-9))

        out = []
        while True:
            c0, c1 = self._next_cycle, self._next_cycle + self.n_cycles
            if self._theta[-1] < 720.0 * c1:
                break
            out.append(self._cut(c0, c1))
            self._next_cycle += self.hop
        self._trim()
        return out

    def _cut(self, c0: int, c1: int) -> CycleWindow:
        th = self._theta
        i0 = int(np.searchsorted(th, 720.0 * c0, side="left"))
        i1 = int(np.searchsorted(th, 720.0 * c1, side="left"))
        t = self._buf["t"][i0:i1]
        tt = self._teeth_t
        keep = (tt >= t[0]) & (tt <= t[-1])
        win = CycleWindow(
            index=self._index,
            first_cycle=c0,
            n_cycles=c1 - c0,
            t=t,
            theta=th[i0:i1],
            crank_angle_deg=self._buf["crank_angle_deg"][i0:i1],
            rpm=self._buf["rpm"][i0:i1],
            mic=self._buf["mic"][i0:i1],
            tooth_times=tt[keep],
            tooth_ids=self._teeth_id[keep],
            labels=[self._labels[c] for c in range(c0, c1) if c in self._labels],
            meta=self._meta,
        )
        self._index += 1
        return win

    def _trim(self):
        """Drop samples before the start of the next window's first cycle."""
        i = int(np.searchsorted(self._theta, 720.0 * self._next_cycle, side="left"))
        if i <= 0:
            return
        for k in _STREAM_KEYS:
            self._buf[k] = self._buf[k][i:]
        self._theta = self._theta[i:]
        t0 = self._buf["t"][0] if self._buf["t"].size else np.inf
        keep = self._teeth_t >= t0
        self._teeth_t, self._teeth_id = self._teeth_t[keep], self._teeth_id[keep]
        for c in [c for c in self._labels if c < self._next_cycle]:
            del self._labels[c]


def cycle_windows(chunks, n_cycles: int = 4, overlap: float = 0.5) -> list[CycleWindow]:
    """Window a finished list of chunks (or a single concatenated record)."""
    if isinstance(chunks, dict):
        chunks = [chunks]
    w = CycleWindower(n_cycles, overlap)
    return [win for c in chunks for win in w.push(c)]
