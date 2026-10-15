"""Fake scenarios for demo level L0: scripted, selectable, contract-shaped evidence.

Nothing here is a result. Each scenario is a looping timeline of phases. A phase says what the
engine is doing (the truth, used to draw fake signals) and which witnesses see it (the evidence the
rule layer gets). The real simulator, DSP and models replace this module later; the rule layer and
the WebSocket code after it stay the same (plan §11.1, single code path).
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

import numpy as np

from backend.rule_layer import Evidence

WINDOW_S = 0.1  # one window (4 cycles at 2400 rpm, 50% overlap) per state message, ~10 Hz
SIGNAL_S = 0.05  # one signal frame per channel every 50 ms, ~20 Hz
OBD_S = 1.0  # OBD at its true ~1 Hz

SIGNAL_RATE_HZ = 4000  # waveform rate after server downsampling
FIRING_ORDER = (1, 3, 4, 2)

SIM_RULES = ("misfire", "knock", "injector_clog", "valve_tick")
REAL_RULES = ("misfire", "knock", "valve_tick")  # injector clog needs crank/O2: simulator only


@dataclass(frozen=True)
class Phase:
    profile: str = "healthy"  # healthy | both | classifier_only | conflict | unclassified
    fault: str | None = None  # the truth: misfire | knock | injector_clog | valve_tick
    cylinder: int | None = None
    severity: float | None = None
    conflict_rule: str | None = None  # the physics rule that fires in a `conflict` phase
    offline: bool = False  # the whole backend output stops (UI watchdog test)
    dead: frozenset = frozenset()  # sensor streams that stall (backend watchdog test)


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    timeline: tuple  # ((duration_s, Phase), ...), looped
    source: str = "simulated"
    sensors: tuple = ("mic", "accel", "obd")

    @property
    def period(self) -> float:
        return sum(d for d, _ in self.timeline)

    @property
    def real(self) -> bool:
        return self.source == "real"

    def phase_at(self, t: float) -> Phase:
        t = t % self.period
        for dur, phase in self.timeline:
            if t < dur - 1e-9:
                return phase
            t -= dur
        return self.timeline[-1][1]

    def live_sensors(self, t: float) -> set[str]:
        return set(self.sensors) - set(self.phase_at(t).dead)


H = Phase()
SCENARIOS = {
    s.name: s
    for s in (
        Scenario("healthy", "Healthy engine at cruise, nothing fires", ((60.0, H),)),
        Scenario("misfire", "Healthy 2 s, misfire on cyl 3 (both witnesses) 6 s, healthy 12 s",
                 ((2.0, H), (6.0, Phase("both", "misfire", 3, 0.12)), (12.0, H))),
        Scenario("knock", "Healthy 2 s, knock on cyl 2 (both witnesses) 6 s, healthy 12 s",
                 ((2.0, H), (6.0, Phase("both", "knock", 2, 0.3)), (12.0, H))),
        Scenario("injector_clog", "Healthy 2 s, injector clog on cyl 1 (both witnesses) 8 s, healthy 10 s",
                 ((2.0, H), (8.0, Phase("both", "injector_clog", 1, 0.3)), (10.0, H))),
        Scenario("advisory", "Healthy 2 s, classifier says knock but the physics rule is quiet 6 s, healthy 4 s",
                 ((2.0, H), (6.0, Phase("classifier_only", "knock", 2, 0.2)), (4.0, H))),
        Scenario("conflict", "Healthy 2 s, classifier says misfire while the knock rule fires 6 s, healthy 4 s",
                 ((2.0, H), (6.0, Phase("conflict", "misfire", 3, 0.1, conflict_rule="knock")), (4.0, H))),
        Scenario("unclassified", "Healthy 2 s, valve tick on cyl 2 (held out: anomaly high, no label, physics hint) 8 s, healthy 4 s",
                 ((2.0, H), (8.0, Phase("unclassified", "valve_tick", 2, 0.4)), (4.0, H))),
        Scenario("real_p3", "Healthy real P3 replay: no localisation, no fingerprint, firing_event rules, no OBD",
                 ((60.0, H),), source="real", sensors=("mic",)),
        Scenario("offline", "Healthy 4 s, then all output stops for 5 s so the UI watchdog trips; loops",
                 ((4.0, H), (5.0, Phase(offline=True)))),
        Scenario("mic_dropout", "Healthy, but the mic stream stalls for 8 s: backend watchdog sets mic FAILURE",
                 ((3.0, H), (8.0, Phase(dead=frozenset({"mic"}))), (3.0, H))),
    )
}


def _rng(*key) -> np.random.Generator:
    """Deterministic noise per (scenario, index, ...) so runs and tests repeat exactly."""
    return np.random.default_rng([zlib.crc32(k.encode()) if isinstance(k, str) else int(k) for k in key])


def _r(x: float, nd: int = 3) -> float:
    return round(float(x), nd)


# ---------------------------------------------------------------- state (one window)

@dataclass
class Window:
    t: float
    phase: Phase
    evidence: Evidence
    fields: dict = field(default_factory=dict)  # state message fields except sent_at, decision, health, latency


def window(sc: Scenario, k: int) -> Window:
    t = round(k * WINDOW_S, 6)
    ph = sc.phase_at(t)
    rng = _rng(sc.name, k)
    jit = lambda s: float(rng.normal(0, s))  # noqa: E731

    labels = {"healthy": "healthy", "both": ph.fault, "classifier_only": ph.fault, "conflict": ph.fault}
    if ph.profile == "unclassified":
        models = [{"model": "Baseline", "label": "healthy", "p": 0.55},
                  {"model": "SVM", "label": "knock", "p": 0.41},
                  {"model": "ANN", "label": "healthy", "p": 0.48},
                  {"model": "CNN", "label": "misfire", "p": 0.39}]
    else:
        label = labels[ph.profile]
        base = 0.93 if label == "healthy" else 0.88
        models = [{"model": "Baseline", "label": label, "p": 0.70}] + [
            {"model": m, "label": label, "p": base + d} for m, d in (("SVM", -0.02), ("ANN", 0.0), ("CNN", 0.03))
        ]
    if sc.real:
        models = [m for m in models if m["model"] != "Baseline"]
    for m in models:
        m["p"] = _r(min(0.99, max(0.01, m["p"] + jit(0.01))))

    fires = {"both": ph.fault, "conflict": ph.conflict_rule, "unclassified": ph.fault}.get(ph.profile)
    basis = "firing_event" if sc.real else "crank_angle"
    loc = None if sc.real else ph.cylinder
    rules = [{"rule": r, "fired": r == fires, "location": loc if r == fires else None, "rule_basis": basis}
             for r in (REAL_RULES if sc.real else SIM_RULES)]

    score = {"healthy": 0.2, "both": 0.82, "classifier_only": 0.5, "conflict": 0.78, "unclassified": 0.91}[ph.profile]
    score = _r(max(0.0, score + jit(0.02)))
    threshold = 0.60

    if sc.real:
        per_location, features = [], {"knock_band_ratio": _r(0.9 + jit(0.03)), "kurtosis": _r(3.2 + jit(0.1))}
        rpm, load, regime = None, None, "idle"
    else:
        per_location = [_r(0.12 + jit(0.01)) for _ in range(4)]
        if ph.profile != "healthy" and ph.cylinder:
            per_location[ph.cylinder - 1] = _r(score + 0.08)
        f = ph.fault if ph.profile != "healthy" else None
        features = {
            "half_order_ratio": _r((3.1 if f in ("misfire", "injector_clog") else 1.0) + jit(0.05)),
            "knock_band_ratio": _r((4.1 if f == "knock" else 1.0) + jit(0.05)),
            "cam_env_peak": _r((0.8 if f == "valve_tick" else 0.2) + jit(0.02)),
        }
        rpm, load, regime = _r(2400 + jit(8), 1), _r(38 + jit(0.5), 1), "cruise"

    fields = {
        "run_id": f"fake_{sc.name}",
        "source": sc.source,
        "audioset_pool": "P3" if sc.real else None,
        "engine_tag": "vetted_car" if sc.real else None,
        "mode": "replay",
        "t": t,
        "operating_point": {"rpm": rpm, "load_pct": load, "regime": regime},
        "localisation": "none" if sc.real else "cylinder",
        "features_summary": features,
        "anomaly": {"score": score, "threshold": threshold, "per_location": per_location},
        "models": models,
        "physics_rules": rules,
        "signature": signature(sc, ph, rng),
    }
    sev = ph.severity if ph.profile == "both" else None
    return Window(t, ph, Evidence(models, rules, score, threshold, sev), fields)


CRANK_DEG = np.arange(0, 720, 4, dtype=float)  # 180 points per 720° cycle
_LOBES = {cyl: i * 180.0 for i, cyl in enumerate(FIRING_ORDER)}  # TDC of each cylinder's firing


def _lobe(centre: float, width: float = 28.0) -> np.ndarray:
    d = (CRANK_DEG - centre + 360) % 720 - 360
    return np.exp(-0.5 * (d / width) ** 2)


BASELINE = 0.15 + 0.85 * sum(_lobe(c) for c in _LOBES.values())


def signature(sc: Scenario, ph: Phase, rng: np.random.Generator) -> dict:
    """The 720° fingerprint. Empty on real clips (contract rule 6)."""
    if sc.real:
        return {"crank_deg": [], "amplitude": [], "baseline_amplitude": []}
    amp = BASELINE.copy()
    if ph.profile != "healthy" and ph.cylinder:
        c = _LOBES[ph.cylinder]
        if ph.fault == "misfire":
            amp -= 0.75 * _lobe(c)
        elif ph.fault == "injector_clog":
            amp -= 0.35 * _lobe(c)
        elif ph.fault == "knock":
            amp += 0.45 * _lobe(c + 12, 6) * np.cos(np.radians(CRANK_DEG - c) * 18) ** 2
        elif ph.fault == "valve_tick":
            amp += 0.6 * _lobe(c + 470, 5)
    amp = amp + rng.normal(0, 0.015, amp.size)
    return {
        "crank_deg": CRANK_DEG.tolist(),
        "amplitude": np.round(amp, 3).tolist(),
        "baseline_amplitude": np.round(BASELINE, 3).tolist(),
    }


# ---------------------------------------------------------------- signal (one frame per channel)

def _engine_wave(t: np.ndarray, ph: Phase, rpm: float, ring_hz: float, tau: float, seed) -> np.ndarray:
    """Firing pulses as damped rings, one every 180°, with the phase's fault drawn on top."""
    fi = 30.0 / rpm  # seconds per firing (180°)
    k = np.floor(t / fi).astype(int)
    since = t - k * fi
    cyl = np.array(FIRING_ORDER)[k % 4]
    gain = np.ones_like(t)
    hit = (cyl == ph.cylinder) if ph.cylinder and ph.profile != "healthy" else np.zeros_like(t, bool)
    if ph.profile != "healthy":
        if ph.fault == "misfire":
            gain[hit] = 0.08
        elif ph.fault == "injector_clog":
            gain[hit] = 0.6
    y = gain * np.exp(-since / tau) * np.sin(2 * np.pi * ring_hz * since)
    if ph.profile != "healthy" and ph.fault == "knock":
        d = since - 0.0015
        y += np.where(hit & (d > 0), 0.7 * np.exp(-np.maximum(d, 0) / 0.0012) * np.sin(2 * np.pi * 1700 * d), 0)
    if ph.profile != "healthy" and ph.fault == "valve_tick":
        d = since - 0.6 * fi
        y += np.where(hit & (d > 0), 0.8 * np.exp(-np.maximum(d, 0) / 0.0006) * np.sin(2 * np.pi * 1900 * d), 0)
    y += _rng(*seed).normal(0, 0.04, t.size)
    return np.clip(y, -1, 1).astype(np.float32)


def signal_frames(sc: Scenario, j: int, live: set[str]) -> list[tuple[dict, np.ndarray]]:
    """Frames for one 50 ms tick: (header fields without sent_at, payload), one per live channel."""
    t0 = round(j * SIGNAL_S, 6)
    ph = sc.phase_at(t0)
    n = int(SIGNAL_RATE_HZ * SIGNAL_S)
    t = t0 + np.arange(n) / SIGNAL_RATE_HZ
    rpm = 850.0 if sc.real else 2400.0
    out = []
    mic = None
    if "mic" in live:
        mic = _engine_wave(t, ph, rpm, 310.0, 0.003, (sc.name, j, 1))
        out.append(({"channel": "mic", "kind": "waveform", "t0": t0, "sample_rate_hz": SIGNAL_RATE_HZ, "n_samples": n}, mic))
    if "accel" in live:
        acc = _engine_wave(t, ph, rpm, 880.0, 0.002, (sc.name, j, 2))
        out.append(({"channel": "accel", "kind": "waveform", "t0": t0, "sample_rate_hz": SIGNAL_RATE_HZ, "n_samples": n}, acc))
    if mic is not None:
        spec = np.abs(np.fft.rfft(mic * np.hanning(n))) / (n / 4)
        db = np.clip(20 * np.log10(spec + 1e-6), -90, 0).astype(np.float32)
        out.append(({"channel": "mic_stft", "kind": "spectrogram", "t0": t0, "sample_rate_hz": 1 / SIGNAL_S,
                     "n_samples": db.size, "n_bins": db.size, "n_frames": 1, "f_max_hz": SIGNAL_RATE_HZ / 2}, db))
    return out


# ---------------------------------------------------------------- obd (one sample per channel)

OBD_UNITS = {"rpm": "rev/min", "load_pct": "%", "throttle_pct": "%", "map_kpa": "kPa", "coolant_c": "degC",
             "stft_pct": "%", "ltft_pct": "%", "spark_adv_deg": "deg"}


def obd_sample(sc: Scenario, i: int) -> dict:
    """One ~1 Hz sample of every simulated OBD channel: name -> (value, unit)."""
    t = i * OBD_S
    ph = sc.phase_at(t)
    rng = _rng(sc.name, i, 3)
    active = ph.profile != "healthy"
    stft = 1.5 + (3.8 if active and ph.fault == "injector_clog" else 0.0)
    spark = 24.0 - (5.0 if active and ph.fault == "knock" else 0.0)
    v = {
        "rpm": 2400 + rng.normal(0, 8),
        "load_pct": 38 + rng.normal(0, 0.5),
        "throttle_pct": 21 + rng.normal(0, 0.3),
        "map_kpa": 52 + rng.normal(0, 0.4),
        "coolant_c": 90 + 0.5 * math.sin(t / 30),
        "stft_pct": stft + rng.normal(0, 0.3),
        "ltft_pct": 2.1 + rng.normal(0, 0.05),
        "spark_adv_deg": spark + rng.normal(0, 0.2),
    }
    return {k: (_r(x, 2), OBD_UNITS[k]) for k, x in v.items()}
