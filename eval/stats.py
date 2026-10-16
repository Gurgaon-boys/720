"""Statistics every reported number uses (CONVENTIONS "Evidence", plan section 4).

    wilson(k, n)                 two-sided 95% interval for a detection rate
    poisson_upper(k, hours)      one-sided 95% upper bound on an event rate (k = 0 -> ~3/T, rule of three)
    rate_summary(k, hours)       {count, hours, per_hour, upper95_per_hour}
    episodes(flags)              number of runs of consecutive True values (overlapping windows
                                 that flag one event count once)
"""
from __future__ import annotations

from math import sqrt

from scipy.stats import chi2

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo = 0.0 if k == 0 else max(0.0, (c - h) / d)
    hi = 1.0 if k == n else min(1.0, (c + h) / d)
    return (lo, hi)


def poisson_upper(k: int, exposure: float, conf: float = 0.95) -> float:
    """Exact one-sided upper bound on a Poisson rate (events per unit of exposure)."""
    if exposure <= 0:
        return float("inf")
    return float(chi2.ppf(conf, 2 * (k + 1)) / 2 / exposure)


def rate_summary(k: int, hours: float) -> dict:
    return {"count": int(k), "hours": round(hours, 3),
            "per_hour": round(k / hours, 4) if hours else None,
            "upper95_per_hour": round(poisson_upper(k, hours), 4)}


def proportion_summary(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "rate": round(k / n, 5) if n else None,
            "wilson95": [round(lo, 5), round(hi, 5)]}


def episodes(flags) -> int:
    n, prev = 0, False
    for f in flags:
        f = bool(f)
        n += f and not prev
        prev = f
    return n
