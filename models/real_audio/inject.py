"""Additive fault injection onto real engine audio (column B: knock and valve tick ONLY, D9).

Uses sim 2.1's own physical models (sim/engine.py EngineParams), so injected and simulated faults
are the same events:
  knock       chamber acoustic modes f = rho_mn c / (pi bore) (~6.4 / 10.6 / 13.3 kHz), decays
              0.9 / 0.6 / 0.5 ms, gains 1 / 0.55 / 0.35, lognormal(0, 0.4) intensity, on one cylinder
              with probability p per engine cycle
  valve_tick  impact modes 6.5 / 9.5 / 13 kHz, 0.2 ms decay, once per cycle on one cylinder, amplitude
              x (1 + 10 severity) (excess lash); healthy seat impacts are already in the real audio
Real clips have no crank reference, so event times assume an engine speed drawn from RPM_RANGE
(one event slot per 720 deg cycle, small timing jitter).

Level: each event's nominal peak is `level_db` relative to the clip's own RMS in 5 kHz .. min(15 kHz,
clip bandwidth). The injected signal is then low-passed at the clip's measured bandwidth (D59), so
it passes through the same channel limit as the real audio around it.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import butter, sosfiltfilt

from sim.engine import EngineParams

SR = 48_000
RPM_RANGE = (700.0, 2000.0)
JITTER_S = 0.0003
_P = EngineParams()
KNOCK_FREQS = tuple(rho * _P.knock_sound_speed_mps / (math.pi * _P.bore_m) for rho in _P.knock_mode_rho)
KNOCK_TAUS, KNOCK_GAINS = _P.knock_mode_taus_s, _P.knock_mode_gains
VALVE_FREQS, VALVE_TAU = _P.valve_modes_hz, _P.valve_tau_s
VALVE_TICK_GAIN = _P.valve_tick_gain


def _burst(freqs, taus, gains, rng) -> np.ndarray:
    n = int(7 * max(taus) * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for f, tau, g in zip(freqs, taus, gains):
        out += g * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi)) * np.exp(-t / tau)
    return out / np.max(np.abs(out))


def band_rms(x: np.ndarray, bandwidth_hz: float) -> float:
    hi = min(15000.0, 0.95 * bandwidth_hz)
    if hi <= 5500:
        return float(np.sqrt(np.mean(x ** 2)))
    sos = butter(4, (5000.0, hi), btype="bandpass", fs=SR, output="sos")
    return float(np.sqrt(np.mean(sosfiltfilt(sos, x) ** 2)))


def inject(x: np.ndarray, fault: str, severity: float, level_db: float, bandwidth_hz: float,
           seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Returns (x + injected, event start times in s). fault is 'knock' or 'valve_tick'."""
    if fault not in ("knock", "valve_tick"):
        raise ValueError("column B injects additive faults only: knock, valve_tick (D9)")
    rng = np.random.default_rng(seed)
    period = 120.0 / rng.uniform(*RPM_RANGE)
    a0 = band_rms(x, bandwidth_hz) * 10 ** (level_db / 20)
    y = np.zeros_like(x)
    times = []
    for t in np.arange(rng.uniform(0, period), len(x) / SR, period):
        if fault == "knock":
            if rng.random() >= severity:
                continue
            ev = _burst(KNOCK_FREQS, KNOCK_TAUS, KNOCK_GAINS, rng) * rng.lognormal(0.0, 0.4)
        else:
            ev = _burst(VALVE_FREQS, (VALVE_TAU,) * len(VALVE_FREQS), (1.0,) * len(VALVE_FREQS), rng)
            ev *= (1 + VALVE_TICK_GAIN * severity) / (1 + VALVE_TICK_GAIN)  # severity 1 -> nominal level
        i = int(round((t + rng.normal(0, JITTER_S)) * SR))
        if i < 0 or i + len(ev) > len(y):
            continue
        y[i:i + len(ev)] += a0 * ev
        times.append(i / SR)
    if bandwidth_hz < 0.95 * SR / 2:
        y = sosfiltfilt(butter(8, 0.98 * bandwidth_hz, btype="lowpass", fs=SR, output="sos"), y)
    return x + y, np.asarray(times)
