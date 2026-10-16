"""Per-window features, crank-angle versions (plan section 8). Simulator and injected data only:
real AudioSet clips have no crank reference and use the firing-event versions instead.

Crank speed comes from the 60-2 tooth times at the wheel's NOMINAL tooth angles, so the wheel's
per-tooth spacing errors show up as speed noise, as on a real engine. The cylinder phase (which
of the two revolutions in a 720 deg cycle) comes from the simulator's crank angle, standing in
for the cam/TDC reference every real engine has.
"""

from __future__ import annotations

import numpy as np

FIRING_ORDER = (1, 3, 4, 2)
TDC_DEG = {cyl: 180.0 * i for i, cyl in enumerate(FIRING_ORDER)}   # firing TDC per cylinder (DECISIONS.md D24)
SEGMENT_OFFSET_DEG = -60.0   # roughness segments run [TDC - 60, TDC + 120); chosen on simulator runs, see roughness()


# ---------------------------------------------------------------- generic helpers (from sim/analysis.py)

def unwrap_720(crank_angle_deg: np.ndarray) -> np.ndarray:
    """Unwrap a 0..720 crank angle into a monotonic absolute angle (deg)."""
    return np.unwrap(crank_angle_deg, period=720.0)


def speed_from_teeth(tooth_times, tooth_ids, pitch_deg=6.0, first_tooth_deg=0.0):
    """Crank speed (rpm) between consecutive teeth, from tooth times and ids only (no cam phase).

    Returns (t_mid, angle_mid_deg, rpm); the angle starts at an arbitrary revolution.
    """
    ids = np.asarray(tooth_ids)
    rev = np.concatenate([[0], np.cumsum(np.diff(ids) < 0)])
    ang = first_tooth_deg + pitch_deg * ids + 360.0 * rev
    dt = np.diff(tooth_times)
    rpm = np.diff(ang) / dt / 6.0
    return 0.5 * (tooth_times[1:] + tooth_times[:-1]), 0.5 * (ang[1:] + ang[:-1]), rpm


def order_spectrum(x, angle_deg, step_deg=1.0, period_deg=720.0, start_deg=None, n_periods=None):
    """Order spectrum of x(angle): resample to a uniform crank-angle grid over whole cycles, FFT.

    Returns (orders, amplitude) with order 1 = once per revolution; the mean is removed.
    """
    a0 = angle_deg[0] if start_deg is None else start_deg
    if n_periods is None:
        n_periods = int((angle_deg[-1] - a0) // period_deg)
    if n_periods < 1:
        raise ValueError("need at least one full cycle")
    grid = a0 + np.arange(int(round(n_periods * period_deg / step_deg))) * step_deg
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


# ---------------------------------------------------------------- per-window features

def tooth_angles(win) -> np.ndarray:
    """Nominal absolute crank angle of each tooth in the window (deg, same frame as win.theta)."""
    wheel = win.meta["wheel"]
    base = wheel["first_tooth_deg"] + wheel["pitch_deg"] * win.tooth_ids
    true = np.interp(win.tooth_times, win.t, win.theta)      # cam/TDC phase reference
    return base + 360.0 * np.round((true - base) / 360.0)


def tooth_speed(win):
    """(angle_mid_deg, rpm) between consecutive teeth, at nominal tooth angles."""
    ang = tooth_angles(win)
    rpm = np.diff(ang) / np.diff(win.tooth_times) / 6.0
    return 0.5 * (ang[1:] + ang[:-1]), rpm


def half_order_ratio(win) -> float:
    """Half-order amplitude of crank speed over the firing-order (2nd order) amplitude.

    A window of whole cycles puts orders 0.5 and 2 exactly on FFT bins. Dimensionless, so it
    doesn't scale with load or rpm the way an absolute amplitude would.
    """
    ang, rpm = tooth_speed(win)
    o, s = order_spectrum(rpm, ang, step_deg=1.0, start_deg=win.theta_start, n_periods=win.n_cycles)
    return amplitude_at(o, s, 0.5) / max(amplitude_at(o, s, 2.0), 1e-9)


def half_order_energy(win) -> float:
    """Half-order crank-speed amplitude x mean speed / (1000 rpm)^2 (sim v2 misfire feature).

    A missing firing takes energy out once per cycle: J * omega * d_omega is proportional to the lost
    torque, so A_0.5 * rpm_mean barely depends on rpm for a given torque deficit. Unlike the v1
    half-order ratio it isn't divided by the 2nd order, which gas and reciprocating-inertia torque
    can cancel at some speeds (the ratio then explodes on a healthy engine).
    """
    ang, rpm = tooth_speed(win)
    o, s = order_spectrum(rpm, ang, step_deg=1.0, start_deg=win.theta_start, n_periods=win.n_cycles)
    return amplitude_at(o, s, 0.5) * float(np.mean(rpm)) / 1e6


def roughness(win) -> np.ndarray:
    """Per-cylinder crank roughness: the speed drop after each cylinder's TDC. Index i = cylinder i + 1.

    The crank angle is cut into 180 deg segments [TDC_k - 60, TDC_k + 120), one per firing, and the
    mean speed of each segment is taken from tooth times (averaging 30 teeth beats down the wheel's
    tooth errors). The drop from cylinder k's segment to the next, relative to mean speed, is k's
    deceleration. A missing firing also lowers the segment before it a little, so each drop is
    taken relative to the mean of its two neighbours (a second difference), which keeps the next
    cylinder in firing order from lighting up too. Each cylinder reports its worst firing in the
    window, so one misfire in four cycles still shows.

    On simulator runs (p = 0.5, cylinders 1/2/3, 800/1500/2500 rpm) this located the misfiring
    cylinder in 88-94% of windows containing a misfire; TDC-aligned segments blame the next cylinder.
    """
    ang = tooth_angles(win)
    off = SEGMENT_OFFSET_DEG
    j0 = int(np.ceil((ang[0] - off) / 180.0))
    j1 = int(np.floor((ang[-1] - off) / 180.0)) - 1
    js = np.arange(j0, j1 + 1)
    out = np.zeros(4)
    if js.size < 4:
        return out
    t_start = np.interp(180.0 * js + off, ang, win.tooth_times)
    t_end = np.interp(180.0 * js + off + 180.0, ang, win.tooth_times)
    speed = 180.0 / (t_end - t_start)
    drop = -np.diff(speed) / speed.mean()                 # drop[i]: segment js[i] -> js[i + 1]
    rel = drop[1:-1] - 0.5 * (drop[:-2] + drop[2:])
    cyl = np.array([FIRING_ORDER[j % 4] for j in js[1:-2]])
    for k in (1, 2, 3, 4):
        v = rel[cyl == k]
        out[k - 1] = v.max() if v.size else 0.0
    return out


def fingerprint(win, bin_deg: float = 2.0):
    """720 deg fingerprint: RMS of the mic per crank-angle bin, averaged over the window's cycles.
    Samples lost to a sensor dropout (NaN) are left out; a bin with no samples reads 0.

    Returns (crank_deg_centres, rms_per_bin).
    """
    ok = np.isfinite(win.mic)
    centres, energy = tsa_720(win.mic[ok].astype(float) ** 2, win.crank_angle_deg[ok], bin_deg)
    return centres, np.sqrt(energy)


def crank_valid(win, min_fraction: float = 0.97) -> bool:
    """True when the window has (nearly) all its teeth: a crank dropout leaves gaps that would
    otherwise read as huge speed swings."""
    wheel = win.meta.get("wheel", {"teeth": 60, "missing": 2})
    expected = win.n_cycles * 2 * (wheel["teeth"] - wheel["missing"])
    return len(win.tooth_times) >= min_fraction * expected


def regime(rpm: float) -> str:
    return "idle" if rpm < 1100 else ("cruise" if rpm < 3500 else "high")


def window_features(win) -> dict:
    """Everything the rule and the state message need from one window."""
    rpm = float(win.rpm.mean())
    valid = crank_valid(win)
    return {
        "half_order_ratio": half_order_ratio(win) if valid else 0.0,     # v1 feature, kept for comparison
        "half_order_energy": half_order_energy(win) if valid else 0.0,   # v2 rule feature
        "roughness": roughness(win) if valid else np.zeros(4),
        "rpm_mean": rpm,
        "map_kpa": float(win.meta["map_pa"]) / 1e3 if "map_pa" in win.meta else None,
        "regime": regime(rpm),
        "crank_valid": valid,
    }
