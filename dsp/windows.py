"""Cut a stream of simulator chunks into windows of whole engine cycles (plan section 8.1).

A window is `n_cycles` complete 720 deg cycles (default 4) with 50% overlap, so a new window
starts every `n_cycles / 2` cycles. Cycle boundaries come from the crank angle (cylinder 1 firing
TDC, crank angle 0), never from wall time, so a window always holds whole cycles whatever the rpm.

Cycle phase (L3): a crank wheel only knows the angle within one revolution (its missing tooth repeats every
360 deg); which of the two revolutions of a 720 deg cycle we are in comes from the cam sensor, one pulse per
cycle at CAM_OFFSET_DEG. When chunks carry `cam_times`, the windower takes the crank angle modulo 360 and
derives the revolution from the cam: revolution = (360 deg wraps since the last cam pulse) mod 2. Chunks
without `cam_times` (v1-style) keep using the given 0-720 deg angle. `phase_source` says which was used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

_STREAM_KEYS = ("t", "crank_angle_deg", "rpm", "mic")
_OPTIONAL_KEYS = ("accel",)


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
    accel: np.ndarray | None = None   # sim v2 block accelerometer (None for v1-style chunks)

    @property
    def t_start(self) -> float:
        return float(self.t[0])

    @property
    def t_end(self) -> float:
        return float(self.t[-1])

    @property
    def theta_start(self) -> float:
        return 720.0 * self.first_cycle


CAM_OFFSET_DEG = 90.0       # crank angle of the cam pulse (sim.engine.CAM_OFFSET_DEG); a sensor-mounting constant


class CamPhase:
    """0-720 deg cycle phase from a 0-360 deg crank angle plus cam pulses (one per 720 deg)."""

    def __init__(self):
        self._wraps = 0                 # 360 deg wraps of the crank angle seen so far
        self._last_a = None
        self._cam_wraps = None          # wrap count at the last cam pulse

    def __call__(self, t, angle_deg, cam_times) -> np.ndarray | None:
        a = np.mod(np.asarray(angle_deg, dtype=float), 360.0)
        prev = a[:1] if self._last_a is None else np.array([self._last_a])
        w = self._wraps + np.cumsum(np.diff(np.concatenate([prev, a])) < -180.0)
        self._wraps, self._last_a = int(w[-1]), float(a[-1])
        cams = np.asarray(cam_times, dtype=float)
        if cams.size:
            idx = np.searchsorted(t, cams, side="right") - 1          # last sample at or before each pulse
            cam_w = w[np.clip(idx, 0, len(w) - 1)]
            j = np.searchsorted(cams, t, side="right") - 1             # last pulse at or before each sample
            ref = np.where(j >= 0, cam_w[np.clip(j, 0, None)], cam_w[0] if self._cam_wraps is None else self._cam_wraps)
            self._cam_wraps = int(cam_w[-1])
        elif self._cam_wraps is not None:
            ref = np.full(len(w), self._cam_wraps)
        else:
            return None                                                # no cam seen yet: phase unknown
        rev = np.mod(w - ref, 2)
        return a + 360.0 * rev


class CycleWindower:
    """Incremental windower: push simulator chunks in order, get finished windows back."""

    def __init__(self, n_cycles: int = 4, overlap: float = 0.5):
        hop = n_cycles * (1 - overlap)
        if n_cycles < 1 or hop < 1 or abs(hop - round(hop)) > 1e-9:
            raise ValueError("n_cycles * (1 - overlap) must be a whole number of cycles >= 1")
        self.n_cycles = n_cycles
        self.hop = int(round(hop))
        self._buf = {k: np.zeros(0, dtype=np.float32 if k in ("mic", "accel") else float) for k in _STREAM_KEYS + _OPTIONAL_KEYS}
        self._has = set(_STREAM_KEYS)
        self._theta = np.zeros(0)
        self._teeth_t = np.zeros(0)
        self._teeth_id = np.zeros(0, dtype=int)
        self._labels = {}
        self._meta = {}
        self._wraps = 0
        self._last_wrapped = None
        self._next_cycle = None   # first cycle of the next window
        self._index = 0
        self._cam = CamPhase()
        self.phase_source = None  # "cam" | "given": where the 720 deg phase of the last chunk came from

    def _phase(self, chunk: dict) -> np.ndarray:
        given = np.asarray(chunk["crank_angle_deg"], dtype=float)
        if "cam_times" not in chunk:
            self.phase_source = "given"
            return given
        cam = self._cam(np.asarray(chunk["t"], dtype=float), given, chunk["cam_times"])
        if cam is None:
            self.phase_source = "given"
            return given
        self.phase_source = "cam"
        # keep the given value where it agrees with the cam (bit-identical), else the cam-derived one
        return np.where(np.floor(given / 360.0) == np.floor(cam / 360.0), given, cam)

    def push(self, chunk: dict) -> list[CycleWindow]:
        wrapped = self._phase(chunk)
        if "cam_times" in chunk:
            chunk = {**chunk, "crank_angle_deg": wrapped}
        prev = wrapped[:1] if self._last_wrapped is None else np.array([self._last_wrapped])
        steps = np.diff(np.concatenate([prev, wrapped])) < -360.0
        theta = wrapped + 720.0 * (self._wraps + np.cumsum(steps))
        self._wraps += int(steps.sum())
        self._last_wrapped = float(wrapped[-1])

        for k in _STREAM_KEYS + _OPTIONAL_KEYS:
            if k in chunk:
                self._has.add(k)
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
            accel=self._buf["accel"][i0:i1] if "accel" in self._has else None,
        )
        self._index += 1
        return win

    def _trim(self):
        """Drop samples before the start of the next window's first cycle."""
        i = int(np.searchsorted(self._theta, 720.0 * self._next_cycle, side="left"))
        if i <= 0:
            return
        for k in _STREAM_KEYS + _OPTIONAL_KEYS:
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
