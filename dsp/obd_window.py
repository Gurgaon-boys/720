"""30 s OBD windows (plan section 8: OBD branch, 30 s, 50% overlap) for the trim-drift rule and the
classifier's slow features. The same code reads simulator OBD rows and carOBD rows.

A row is a dict with at least t (s), stft_pct, ltft_pct, spark_adv_deg (and rpm for carOBD filtering).
"""

from __future__ import annotations

import numpy as np

WINDOW_S = 30.0
RETARD_LOOKBACK_S = 60.0


def total_trim_pct(stft_pct, ltft_pct):
    """Total fuel correction of the bank, %: (1 + STFT)(1 + LTFT) - 1."""
    return 100.0 * ((1 + np.asarray(stft_pct) / 100.0) * (1 + np.asarray(ltft_pct) / 100.0) - 1.0)


def obd_window(t: np.ndarray, stft: np.ndarray, ltft: np.ndarray, spark: np.ndarray, t_end: float) -> dict:
    """Trim and spark summary of the OBD rows in (t_end - 30 s, t_end].

    spark_retard_deg: median spark advance over the 30 s before the window minus its median inside
    the window (knock control pulls spark; positive = retarded now). 0 when there's no history.
    """
    t = np.asarray(t, dtype=float)
    cur = (t > t_end - WINDOW_S) & (t <= t_end)
    prev = (t > t_end - RETARD_LOOKBACK_S) & (t <= t_end - WINDOW_S)
    n = int(cur.sum())
    if n == 0:
        return {"n": 0, "trim_mean_pct": 0.0, "trim_sd_pct": 0.0, "ltft_pct": 0.0, "spark_retard_deg": 0.0}
    trim = total_trim_pct(np.asarray(stft)[cur], np.asarray(ltft)[cur])
    sp = np.asarray(spark, dtype=float)
    retard = float(np.median(sp[prev]) - np.median(sp[cur])) if prev.sum() >= 5 else 0.0
    return {"n": n, "trim_mean_pct": float(trim.mean()), "trim_sd_pct": float(trim.std()),
            "ltft_pct": float(np.mean(np.asarray(ltft)[cur])), "spark_retard_deg": retard}


class ObdHistory:
    """Rolling store of OBD rows (simulator chunks or a live stream) answering obd_window(t_end)."""

    def __init__(self, keep_s: float = 90.0):
        self.keep_s = keep_s
        self.rows: list[dict] = []

    def push(self, rows) -> None:
        self.rows.extend(rows)
        if self.rows:
            t_last = self.rows[-1]["t"]
            self.rows = [r for r in self.rows if r["t"] > t_last - self.keep_s]

    def window(self, t_end: float) -> dict:
        if not self.rows:
            return obd_window(np.zeros(0), [], [], [], t_end)
        g = lambda k: np.array([r[k] for r in self.rows], dtype=float)
        return obd_window(g("t"), g("stft_pct"), g("ltft_pct"), g("spark_adv_deg"), t_end)
