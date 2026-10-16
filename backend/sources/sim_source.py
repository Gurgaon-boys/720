"""Live simulator source (demo level L1): EngineSim -> 4-cycle windows -> features -> physics rule.

The simulator is stepped lazily, in 50 ms chunks, up to whatever stream time the runtime's ticks
ask for, so it runs in real time. Windows of 4 whole cycles with 50% overlap finish every
2 cycles (300 ms at idle); the rule layer is stepped once per finished window, and the state
ticks in between resend the last decision (`fresh=False`).

Witnesses in L1: the physics rule only. There is no classifier yet, so `models` is empty and the
rule layer can raise ADVISORY but never ALARM. There is no healthy (anomaly) model yet either, so
`anomaly.score` is a placeholder 0.0; `anomaly.per_location` carries the calibrated crank
roughness for the per-cylinder bars, and `anomaly.threshold` is the bars' limit.
"""

from __future__ import annotations

import os
from functools import lru_cache

import numpy as np
from scipy import signal as sps

from backend.rule_layer import Evidence
from backend.scenarios import SIGNAL_RATE_HZ, SIGNAL_S, WINDOW_S, Phase, Window
from dsp.features import fingerprint, window_features
from dsp.rules import PER_LOCATION_THRESHOLD, load_thresholds, misfire_half_order, per_location
from dsp.windows import CycleWindower
from sim import FS, EngineParams, EngineSim

CHUNK_S = 0.05            # simulator step, s
DECIMATE = FS // SIGNAL_RATE_HZ
MIC_DISPLAY_GAIN = 8.0    # the synthesised mic peaks near 0.06; scale the display stream towards +-0.5
KEEP_S = 2.0              # 4 kHz mic history kept for signal frames
_RUNNING = Phase()        # the simulator is never "offline"; sensor dropouts come later


class SimSource:
    name = "sim"
    description = "Physics-informed simulator v1 at idle, live"
    source = "simulated"
    sensors = ("mic", "obd")
    real = False
    period = None

    def __init__(self, params: EngineParams | None = None, seed: int = 0, thresholds: dict | None = None):
        if FS % SIGNAL_RATE_HZ or round(CHUNK_S * FS) % DECIMATE:
            raise ValueError("chunk length must be a whole number of display samples")
        self.params = params or EngineParams()
        self.seed = seed
        self.thresholds = thresholds or load_thresholds()
        self.sim = EngineSim(self.params, seed)
        self.windower = CycleWindower(4, 0.5)
        self.stream_t = 0.0
        self.injections: list[dict] = []
        self._ready: list[Window] = []
        self._last: Window | None = None
        self._lp = sps.butter(8, 0.45 * SIGNAL_RATE_HZ, fs=FS, output="sos")
        self._lp_zi = np.zeros((self._lp.shape[0], 2))
        self._mic = np.zeros(0, dtype=np.float32)   # 4 kHz display mic
        self._mic_t0 = 0.0                          # stream time of self._mic[0]
        self._obd = []                              # (t_end, mean rpm, MAP Pa) per chunk
        self._centres, self._baseline = healthy_fingerprint(self.params, self.seed)

    @classmethod
    def from_env(cls) -> "SimSource":
        """S720_SIM_SEED: run seed (default 0). S720_SIM_ENGINE: engine seed for a randomised engine
        (default: the reference EngineParams)."""
        engine = os.environ.get("S720_SIM_ENGINE")
        params = EngineParams.randomised(int(engine)) if engine else None
        return cls(params, seed=int(os.environ.get("S720_SIM_SEED", "0")))

    # ------------------------------------------------------------ control

    def inject(self, fault: str, cylinder: int, severity: float) -> None:
        self.sim.inject(fault, cylinder=cylinder, severity=severity)
        self.injections.append({"t": self.stream_t, "fault": fault, "cylinder": cylinder, "severity": severity})

    def clear_faults(self) -> None:
        self.sim.clear_faults()
        self.injections.append({"t": self.stream_t, "fault": None})

    # ------------------------------------------------------------ source interface

    def phase_at(self, t: float) -> Phase:
        return _RUNNING

    def live_sensors(self, t: float) -> set[str]:
        return set(self.sensors)

    def window(self, k: int) -> Window | None:
        self._advance(k * WINDOW_S)
        if self._ready:
            *earlier, last = self._ready
            self._ready = []
            last.backlog = [w.evidence for w in earlier]
            self._last = last
            return last
        if self._last is None:
            return None
        return Window(self._last.t, self._last.phase, self._last.evidence, self._last.fields, fresh=False)

    def signal_frames(self, j: int, live: set[str]) -> list[tuple[dict, np.ndarray]]:
        t0 = round(j * SIGNAL_S, 6)
        self._advance(t0 + SIGNAL_S)
        if "mic" not in live:
            return []
        n = int(SIGNAL_RATE_HZ * SIGNAL_S)
        i0 = int(round((t0 - self._mic_t0) * SIGNAL_RATE_HZ))
        mic = np.zeros(n, dtype=np.float32)
        seg = self._mic[max(i0, 0): i0 + n]
        mic[: seg.size] = seg
        spec = np.abs(np.fft.rfft(mic * np.hanning(n))) / (n / 4)
        db = np.clip(20 * np.log10(spec + 1e-6), -90, 0).astype(np.float32)
        return [
            ({"channel": "mic", "kind": "waveform", "t0": t0, "sample_rate_hz": SIGNAL_RATE_HZ, "n_samples": n}, mic),
            ({"channel": "mic_stft", "kind": "spectrogram", "t0": t0, "sample_rate_hz": 1 / SIGNAL_S,
              "n_samples": db.size, "n_bins": db.size, "n_frames": 1, "f_max_hz": SIGNAL_RATE_HZ / 2}, db),
        ]

    def obd_sample(self, i: int) -> dict:
        """The simulator's own engine values at ~1 Hz (no ECU/OBD loops in sim v1 yet)."""
        t = i * 1.0
        self._advance(max(t, CHUNK_S))
        recent = [o for o in self._obd if o[0] > t - 1.0] or self._obd[-1:]
        rpm = float(np.mean([o[1] for o in recent]))
        map_pa = recent[-1][2]
        return {"rpm": (round(rpm, 1), "rev/min"), "map_kpa": (round(map_pa / 1e3, 2), "kPa"),
                "load_pct": (round(100 * map_pa / 101_325, 1), "%")}

    # ------------------------------------------------------------ internals

    def _advance(self, t: float) -> None:
        while self.stream_t < t - 1e-9:
            chunk = self.sim.step(CHUNK_S)
            t_end = float(chunk["t"][-1]) + 1 / FS
            for win in self.windower.push(chunk):
                self._ready.append(self._process(win))
            y, self._lp_zi = sps.sosfilt(self._lp, chunk["mic"], zi=self._lp_zi)
            self._mic = np.concatenate([self._mic, (MIC_DISPLAY_GAIN * y[::DECIMATE]).astype(np.float32)])
            drop = max(self._mic.size - int(KEEP_S * SIGNAL_RATE_HZ), 0)
            self._mic, self._mic_t0 = self._mic[drop:], self._mic_t0 + drop / SIGNAL_RATE_HZ
            self._obd = [o for o in self._obd if o[0] > t_end - 2.0]
            self._obd.append((t_end, float(chunk["rpm"].mean()), float(chunk["meta"]["map_pa"])))
            self.stream_t = t_end

    def _process(self, win) -> Window:
        f = window_features(win)
        rule = misfire_half_order(f, self.thresholds)
        _, fp = fingerprint(win)
        scale = self._baseline.max()
        map_pa = float(win.meta["map_pa"])
        fields = {
            "run_id": f"sim_{self.seed:04d}",
            "source": "simulated",
            "audioset_pool": None,
            "engine_tag": None,
            "mode": "live",
            "t": round(win.t_end, 3),
            "operating_point": {"rpm": round(f["rpm_mean"], 1), "load_pct": round(100 * map_pa / 101_325, 1),
                                "regime": f["regime"]},
            "localisation": "cylinder",
            "features_summary": {"half_order_ratio": round(f["half_order_ratio"], 4),
                                 "roughness_max": round(float(f["roughness"].max()), 5)},
            "anomaly": {"score": 0.0, "threshold": PER_LOCATION_THRESHOLD,
                        "per_location": per_location(f["roughness"], self.thresholds)},
            "models": [],
            "physics_rules": [rule],
            "signature": {"crank_deg": [round(float(c), 1) for c in self._centres],
                          "amplitude": [round(float(v), 3) for v in fp / scale],
                          "baseline_amplitude": [round(float(v), 3) for v in self._baseline / scale]},
        }
        evidence = Evidence(models=[], physics_rules=[rule], anomaly_score=0.0,
                            anomaly_threshold=PER_LOCATION_THRESHOLD, severity=None)
        return Window(round(win.t_end, 3), _RUNNING, evidence, fields)


@lru_cache(maxsize=8)
def healthy_fingerprint(params: EngineParams, seed: int):
    """Healthy reference fingerprint for this engine: a separate healthy run, 3 s warm-up + 3 s.
    Cached per (engine, seed), so re-selecting the simulator is quick."""
    ref = EngineSim(params, seed=seed + 10_000)
    w = CycleWindower(4, 0.5)
    fps = []
    for _ in range(12):
        for win in w.push(ref.step(0.5)):
            if win.t_start >= 3.0:
                c, fp = fingerprint(win)
                fps.append(fp)
    return c, np.mean(fps, axis=0)
