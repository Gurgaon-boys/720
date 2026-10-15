"""Small analysis helpers used to prove the simulator (demo and tests).

These are minimal reference versions. The production order tracking, 720 deg averaging and
physics rules belong in dsp/ (plan section 8).
"""

from __future__ import annotations

import numpy as np


def run(sim, seconds: float, chunk_s: float = 0.5) -> dict:
    """Step `sim` for `seconds` and concatenate the chunks (labels are concatenated as a list)."""
    chunks = [sim.step(chunk_s) for _ in range(int(round(seconds / chunk_s)))]
    out = {}
    for k in ("t", "crank_angle_deg", "rpm", "tooth_times", "tooth_ids", "mic"):
        out[k] = np.concatenate([c[k] for c in chunks])
    out["cylinder_pressure"] = np.concatenate([c["cylinder_pressure"] for c in chunks], axis=1)
    out["labels"] = [lab for c in chunks for lab in c["labels"]]
    out["meta"] = chunks[-1]["meta"]
    return out


def unwrap_720(crank_angle_deg: np.ndarray) -> np.ndarray:
    """Unwrap a 0..720 crank angle into a monotonic absolute angle (deg)."""
    return np.unwrap(crank_angle_deg, period=720.0)


def speed_from_teeth(tooth_times, tooth_ids, pitch_deg=6.0, first_tooth_deg=0.0):
    """Crank speed (rpm) between consecutive teeth, from tooth times and ids only.

    Uses nominal tooth angles, so the wheel's per-tooth spacing errors show up as speed noise,
    as they do on a real engine. Returns (t_mid, angle_mid_deg, rpm).
    """
    ids = np.asarray(tooth_ids)
    rev = np.concatenate([[0], np.cumsum(np.diff(ids) < 0)])
    ang = first_tooth_deg + pitch_deg * ids + 360.0 * rev
    dt = np.diff(tooth_times)
    rpm = np.diff(ang) / dt / 6.0
    return 0.5 * (tooth_times[1:] + tooth_times[:-1]), 0.5 * (ang[1:] + ang[:-1]), rpm


def order_spectrum(x, angle_deg, step_deg=1.0, period_deg=720.0):
    """Order spectrum of x(angle): resample to a uniform crank-angle grid over whole cycles, FFT.

    Returns (orders, amplitude), where order 1 = once per revolution. The mean is removed.
    """
    a0 = angle_deg[0]
    n_cycles = int((angle_deg[-1] - a0) // period_deg)
    if n_cycles < 1:
        raise ValueError("need at least one full cycle")
    grid = a0 + np.arange(int(n_cycles * period_deg / step_deg)) * step_deg
    y = np.interp(grid, angle_deg, x)
    y = y - y.mean()
    spec = np.abs(np.fft.rfft(y)) * 2 / len(y)
    orders = np.fft.rfftfreq(len(y), d=step_deg / 360.0)
    return orders, spec


def amplitude_at(orders, spec, order):
    return float(spec[np.argmin(np.abs(orders - order))])


def tsa_720(x, crank_angle_deg, bin_deg=2.0):
    """Time-synchronous average of x over the 720 deg cycle in bins of `bin_deg`.

    Returns (bin_centres_deg, mean_per_bin).
    """
    nb = int(round(720 / bin_deg))
    b = np.minimum((np.mod(crank_angle_deg, 720) / bin_deg).astype(int), nb - 1)
    total = np.bincount(b, weights=x, minlength=nb)
    count = np.bincount(b, minlength=nb)
    return (np.arange(nb) + 0.5) * bin_deg, total / np.maximum(count, 1)


def half_order_ratio(healthy: dict, faulty: dict, wheel_first_tooth_deg: float) -> float:
    """Half-order amplitude of tooth-derived crank speed, faulty / healthy."""
    amps = []
    for r in (healthy, faulty):
        _, ang, rpm = speed_from_teeth(r["tooth_times"], r["tooth_ids"], first_tooth_deg=wheel_first_tooth_deg)
        o, s = order_spectrum(rpm, ang)
        amps.append(amplitude_at(o, s, 0.5))
    return amps[1] / amps[0]
