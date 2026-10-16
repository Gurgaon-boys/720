"""Live simulator source: EngineSim (v2) -> 4-cycle windows -> features -> physics rule.

The simulator is stepped lazily, in 50 ms chunks, up to whatever stream time the runtime's ticks
ask for, so it runs in real time. Windows of 4 whole cycles with 50% overlap finish every
2 cycles (300 ms at idle); the rule layer is stepped once per finished window, and the state
ticks in between resend the last decision (`fresh=False`).

Witnesses (L2): the physics rules (misfire half-order energy, knock band energy, trim drift) and,
through models/witness.py, the fast classifier (SVM) and the healthy model (Mahalanobis). The rule
layer raises ALARM only when a physics rule and the classifier name the same fault (and cylinder)
for 2 consecutive windows. Without a trained checkpoint (models/checkpoints/fast_v1.pkl), or with
S720_CLASSIFIER=0, the source falls back to L1: `models` empty, `anomaly.score` null, ADVISORY at most.
`anomaly.per_location` carries the calibrated crank roughness for the bars; the anomaly score is
scaled so its limit is the same 0.5.

Channels: `signal` carries mic and accel (4 kHz display streams) and the mic STFT; `obd` carries
the simulator's ECU values at 1 Hz (rpm, load, throttle, MAP, coolant, STFT, LTFT, spark advance,
lambda; lambda is simulator-only, carOBD has no O2). With S720_SIM_REGIMES=eval, sensor dropouts
stop a channel's output, so the backend watchdog marks it FAILURE after 3 s (crank included).
"""

from __future__ import annotations

import os
from functools import lru_cache

import numpy as np
from scipy import signal as sps

from backend.rule_layer import Evidence
from backend.scenarios import SIGNAL_RATE_HZ, SIGNAL_S, WINDOW_S, Phase, Window
from dsp.features import fingerprint
from dsp.rules import PER_LOCATION_THRESHOLD, all_rules, load_all, per_location, trim_recovery
from dsp.windows import CycleWindower
from models.features import StreamFeatures
from models.witness import ClassifierWitness, WitnessOut
from sim import FS, EngineParams, EngineSim
from sim.engine import CHANNEL_CALIBRATION
from sim.params import Regimes

CHUNK_S = 0.05            # simulator step, s
DECIMATE = FS // SIGNAL_RATE_HZ
DISPLAY_GAIN = {"mic": 8.0, "accel": 6.0}   # the synthesised signals peak near 0.06-0.08; scale towards +-0.5
KEEP_S = 2.0              # 4 kHz display history kept for signal frames
DROPOUT_KEEP_S = 30.0
OBD_UNITS = {"rpm": "rev/min", "load_pct": "%", "throttle_pct": "%", "map_kpa": "kPa", "coolant_c": "degC",
             "stft_pct": "%", "ltft_pct": "%", "spark_adv_deg": "deg", "lambda": "1",
             "intake_air_temp_c": "degC", "battery_v": "V", "baro_kpa": "kPa"}
_RUNNING = Phase()        # the source itself never goes "offline"; sensors drop out individually
SABOTAGE_CHANNELS = ("mic", "accel", "crank", "cam")   # sensors an operator may switch off (Sabotage Lab, D137)
WARMUP_S = 20.0           # guidance "warming up" after a start or POST /reset_engine (classifier needs 20 OBD rows)


class SimSource:
    name = "sim"
    description = "Physics-informed simulator v2, live"
    source = "simulated"
    source_label = "simulated"      # contract 1.6 (optional), also on obd messages
    sensors = ("mic", "accel", "obd", "crank", "cam")
    polled_sensors = ("crank", "cam")   # no stream of their own: the runtime feeds the watchdog on state ticks
    channel_calibration = CHANNEL_CALIBRATION   # obd channel -> real_calibrated | sim_only (contract 1.5)
    real = False
    period = None

    def __init__(self, params: EngineParams | None = None, seed: int = 0, thresholds: dict | None = None,
                 regimes: Regimes | None = None, witness: ClassifierWitness | None = None, reset_n: int = 0):
        if FS % SIGNAL_RATE_HZ or round(CHUNK_S * FS) % DECIMATE:
            raise ValueError("chunk length must be a whole number of display samples")
        self.params = params or EngineParams()
        self.seed = seed
        self.reset_n = reset_n          # POST /reset_engine count: a fresh engine, same seed, its own run_id
        self.thresholds = thresholds or load_all()
        self.sim = EngineSim(self.params, seed, regimes=regimes, purpose="demo")
        self.features = StreamFeatures()
        self.witness = witness if witness is not None else default_witness()
        self.classifier_ms: list[float] = []
        self.stream_t = 0.0
        self._ready: list[Window] = []
        self._last: Window | None = None
        self._lp = sps.butter(8, 0.45 * SIGNAL_RATE_HZ, fs=FS, output="sos")
        self._lp_zi = {ch: np.zeros((self._lp.shape[0], 2)) for ch in DISPLAY_GAIN}
        self._disp = {ch: np.zeros(0, dtype=np.float32) for ch in DISPLAY_GAIN}   # 4 kHz display streams
        self._disp_t0 = 0.0                                                       # stream time of index 0
        self._obd: list[dict] = []
        self._drops: list[tuple] = []                                            # (channel, t0, t1)
        self._disabled: set[str] = set()                                         # operator-disabled sensors
        self._centres, self._baseline = healthy_fingerprint(self.params, self.seed)

    @property
    def warming_up(self) -> bool:
        """The first WARMUP_S of a run: the classifier abstains until its OBD window is full (MIN_OBD_ROWS)."""
        return self.stream_t < WARMUP_S

    @classmethod
    def from_env(cls, reset_n: int = 0) -> "SimSource":
        """S720_SIM_SEED: run seed (default 0). S720_SIM_ENGINE: engine seed for a randomised engine
        (default: the reference EngineParams). S720_SIM_REGIMES=eval: harder regimes incl. dropouts."""
        engine = os.environ.get("S720_SIM_ENGINE")
        params = EngineParams.randomised(int(engine)) if engine else None
        regimes = Regimes.eval() if os.environ.get("S720_SIM_REGIMES") == "eval" else None
        return cls(params, seed=int(os.environ.get("S720_SIM_SEED", "0")), regimes=regimes, reset_n=reset_n)

    # ------------------------------------------------------------ control (the injection console)

    def inject(self, fault: str, cylinder: int, severity: float) -> None:
        """One injection at a time, like the console: a new request replaces the previous one.
        Raises ValueError for an unknown fault or out-of-range values (the sim validates).
        The runtime records the accepted request for state.injection (D46)."""
        self.sim.clear_faults()
        self.sim.inject(fault, cylinder=cylinder, severity=severity)

    def clear_faults(self) -> None:
        self.sim.clear_faults()   # never restores a disabled sensor (D140)

    # ------------------------------------------------------------ sensor sabotage (D140)

    def disable_sensor(self, channel: str) -> None:
        """Switch a sensor off from the next simulator chunk on. Uses the simulator's own dropout
        semantics (mic/accel samples NaN, crank teeth removed) plus cam pulses removed, and records the
        span like a regime dropout, so live_sensors() drops it and the watchdog reports FAILURE after 3 s.
        Nothing about the engine or the random streams changes."""
        if channel not in SABOTAGE_CHANNELS:
            raise ValueError(f"unknown sensor {channel!r}; allowed: {', '.join(SABOTAGE_CHANNELS)}")
        self._disabled.add(channel)

    def enable_sensor(self, channel: str) -> None:
        if channel not in SABOTAGE_CHANNELS:
            raise ValueError(f"unknown sensor {channel!r}; allowed: {', '.join(SABOTAGE_CHANNELS)}")
        self._disabled.discard(channel)

    def restore_sensors(self) -> None:
        self._disabled.clear()

    def disabled_sensors(self) -> list[str]:
        return [ch for ch in SABOTAGE_CHANNELS if ch in self._disabled]

    # ------------------------------------------------------------ source interface

    def phase_at(self, t: float) -> Phase:
        return _RUNNING

    def live_sensors(self, t: float) -> set[str]:
        """Sensors with data at stream time t: a channel inside a dropout span is not live.
        Advances just past t, so the chunk that starts at t (and its dropout spans) exists."""
        self._advance(t + 1e-6)
        down = {ch for ch, a, b in self._drops if a <= t < b}
        return set(self.sensors) - down

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
        n = int(SIGNAL_RATE_HZ * SIGNAL_S)
        i0 = int(round((t0 - self._disp_t0) * SIGNAL_RATE_HZ))
        out = []
        for ch in ("mic", "accel"):
            if ch not in live:
                continue
            x = np.zeros(n, dtype=np.float32)
            # i0 < 0 before the display buffer starts (start-up): clamp both ends, or a negative stop
            # index slices from the end of the buffer and the frame no longer fits
            lo, hi = max(i0, 0), max(i0 + n, 0)
            seg = self._disp[ch][lo:hi]
            x[lo - i0: lo - i0 + seg.size] = seg
            out.append(({"channel": ch, "kind": "waveform", "t0": t0, "sample_rate_hz": SIGNAL_RATE_HZ, "n_samples": n}, x))
            if ch == "mic":
                spec = np.abs(np.fft.rfft(x * np.hanning(n))) / (n / 4)
                db = np.clip(20 * np.log10(spec + 1e-6), -90, 0).astype(np.float32)
                out.append(({"channel": "mic_stft", "kind": "spectrogram", "t0": t0, "sample_rate_hz": 1 / SIGNAL_S,
                             "n_samples": db.size, "n_bins": db.size, "n_frames": 1, "f_max_hz": SIGNAL_RATE_HZ / 2}, db))
        return out

    def obd_sample(self, i: int) -> dict:
        """The simulator's ECU values sampled at 1 Hz (sample and hold), name -> (value, unit)."""
        t = i * 1.0
        self._advance(t + CHUNK_S)
        rec = next((o for o in reversed(self._obd) if o["t"] <= t + 1e-9), self._obd[0] if self._obd else None)
        if rec is None:
            return {}
        return {k: (round(float(rec[k]), 3 if k == "lambda" else 2), u) for k, u in OBD_UNITS.items()}

    # ------------------------------------------------------------ internals

    def _advance(self, t: float) -> None:
        while self.stream_t < t - 1e-9:
            chunk = self.sim.step(CHUNK_S)
            t_end = float(chunk["t"][-1]) + 1 / FS
            if self._disabled:
                chunk = self._sabotage(chunk, float(chunk["t"][0]), t_end)
            for w in self.features.push(chunk):
                self._ready.append(self._process(w))
            for ch in DISPLAY_GAIN:
                y, self._lp_zi[ch] = sps.sosfilt(self._lp, np.nan_to_num(chunk[ch]), zi=self._lp_zi[ch])
                self._disp[ch] = np.concatenate([self._disp[ch], (DISPLAY_GAIN[ch] * y[::DECIMATE]).astype(np.float32)])
            drop = max(self._disp["mic"].size - int(KEEP_S * SIGNAL_RATE_HZ), 0)
            if drop:
                self._disp = {ch: v[drop:] for ch, v in self._disp.items()}
                self._disp_t0 += drop / SIGNAL_RATE_HZ
            self._obd = [o for o in self._obd if o["t"] > t_end - 5.0] + chunk["obd"]
            self._drops = [d for d in self._drops if d[2] > t_end - DROPOUT_KEEP_S]
            self._drops += [(d["channel"], d["t0"], d["t1"]) for d in chunk["dropouts"]]
            self.stream_t = t_end

    def _sabotage(self, chunk: dict, t0: float, t1: float) -> dict:
        """Apply the operator-disabled sensors to one chunk (a copy; the simulator's state is untouched)."""
        chunk = dict(chunk)
        for ch in self._disabled:
            if ch in ("mic", "accel"):
                chunk[ch] = np.full_like(chunk[ch], np.nan)
            elif ch == "crank":
                chunk["tooth_times"] = chunk["tooth_times"][:0]
                chunk["tooth_ids"] = chunk["tooth_ids"][:0]
            elif ch == "cam":
                chunk["cam_times"] = chunk["cam_times"][:0]
            self._drops.append((ch, t0, t1))
        return chunk

    def _process(self, w) -> Window:
        win, f = w.win, w.features
        rules = all_rules(f, w.obd, self.thresholds)
        valid = f["crank_valid"] and f["accel_valid"]
        if valid:
            wit = self.witness(w.vector, f["rpm_mean"], True, w.obd["n"])
        else:
            # the classifier's inputs include crank and accelerometer features: with either stream missing
            # the vector is not a measurement, so the witness gives no output at all (D140)
            wit = WitnessOut([], None, None, 0.0)
        if wit.cpu_ms:
            self.classifier_ms = (self.classifier_ms + [wit.cpu_ms])[-500:]
        _, fp = fingerprint(win)
        scale = self._baseline.max()
        map_pa = float(win.meta["map_pa"])
        fields = {
            "run_id": f"sim_{self.seed:04d}" + (f"_r{self.reset_n}" if self.reset_n else ""),
            "source": "simulated",
            "source_label": "simulated",
            "audioset_pool": None,
            "engine_tag": None,
            "mode": "live",
            "t": round(win.t_end, 3),
            "operating_point": {"rpm": round(f["rpm_mean"], 1), "load_pct": round(100 * map_pa / 101_325, 1),
                                "regime": f["regime"]},
            "localisation": "cylinder",
            "features_summary": {"half_order_energy": round(f["half_order_energy"], 5),
                                 "half_order_ratio_v1": round(f["half_order_ratio"], 4),
                                 "roughness_max": round(float(f["roughness"].max()), 5),
                                 "knock_db_max": round(float(np.max(f["knock_db"])), 2),
                                 "trim_mean_pct": round(w.obd["trim_mean_pct"], 2),
                                 **({"classifier_cylinder": wit.location} if wit.location else {})},
            "anomaly": {"score": wit.score, "threshold": PER_LOCATION_THRESHOLD,
                        "per_location": per_location(f["roughness"], self.thresholds["misfire"], f["rpm_mean"],
                                                     f["map_kpa"])},
            "models": wit.models,
            "physics_rules": rules,
            "signature": {"crank_deg": [round(float(c), 1) for c in self._centres],
                          "amplitude": [round(float(v), 3) for v in fp / scale],
                          "baseline_amplitude": [round(float(v), 3) for v in self._baseline / scale]},
        }
        # warm-up: like the classifier (MIN_OBD_ROWS), the anomaly witness waits WARMUP_S, so the start transient of
        # a fresh engine (POST /reset_engine) can't read as UNCLASSIFIED; the score is still shown
        score = None if win.t_end < WARMUP_S else wit.score
        evidence = Evidence(models=wit.models, physics_rules=rules, anomaly_score=score,
                            anomaly_threshold=PER_LOCATION_THRESHOLD, severity=None, classifier_location=wit.location,
                            t=round(win.t_end, 3), trims=trim_recovery(w.obd, self.thresholds["injector_clog"]))
        return Window(round(win.t_end, 3), _RUNNING, evidence, fields)


_WITNESS: ClassifierWitness | None = None


def default_witness() -> ClassifierWitness:
    """One loaded checkpoint shared by every SimSource in the process. S720_CLASSIFIER=0 disables it."""
    global _WITNESS
    if os.environ.get("S720_CLASSIFIER") == "0":
        return ClassifierWitness(path=None)
    if _WITNESS is None:
        _WITNESS = ClassifierWitness()
    return _WITNESS


@lru_cache(maxsize=8)
def healthy_fingerprint(params: EngineParams, seed: int):
    """Healthy reference fingerprint for this engine: a separate healthy run, 3 s warm-up + 3 s.
    Cached per (engine, seed), so re-selecting the simulator is quick."""
    ref = EngineSim(params, seed=seed + 10_000, purpose="demo")
    w = CycleWindower(4, 0.5)
    fps = []
    for _ in range(12):
        for win in w.push(ref.step(0.5)):
            if win.t_start >= 3.0:
                c, fp = fingerprint(win)
                fps.append(fp)
    return c, np.mean(fps, axis=0)
