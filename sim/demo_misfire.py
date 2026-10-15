"""Proof that sim v1 produces a physically consistent misfire.

Runs a healthy engine and the same engine (same seed) with a continuous misfire on cylinder 3
at idle, then writes docs/figures/sim_v1_misfire.png with four panels:
  1) instantaneous crank speed from the 60-2 wheel tooth times
  2) microphone waveform: one combustion pulse missing per 720 deg cycle
  3) order spectrum of crank speed: the half-order (0.5x) line
  4) 720 deg time-synchronous average of mic energy (the fingerprint)
and prints the half-order ratio and the step() timing.

Usage (from the repo root):  python -m sim.demo_misfire
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim import FS, EngineParams, EngineSim
from sim.analysis import amplitude_at, order_spectrum, run, speed_from_teeth, tsa_720, unwrap_720

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures" / "sim_v1_misfire.png"
SEED = 0
WARMUP_S, RECORD_S = 3.0, 6.0
GREY, RED = "#5f6368", "#c62828"


def simulate(params, misfire):
    sim = EngineSim(params, seed=SEED)
    if misfire:
        sim.inject("misfire", cylinder=3, severity=1.0)
    run(sim, WARMUP_S)
    return run(sim, RECORD_S)


def tdc_times(r, tdc_deg):
    ang = unwrap_720(r["crank_angle_deg"])
    k = np.arange(np.ceil((ang[0] - tdc_deg) / 720), np.floor((ang[-1] - tdc_deg) / 720) + 1)
    return np.interp(tdc_deg + 720 * k, ang, r["t"])


def timing(params, n=40):
    sim = EngineSim(params, seed=SEED)
    sim.inject("misfire", cylinder=3, severity=1.0)
    sim.step(0.5)
    t0 = time.perf_counter()
    for _ in range(n):
        sim.step(0.5)
    return (time.perf_counter() - t0) / n


def main():
    params = EngineParams()
    h, m = simulate(params, False), simulate(params, True)
    tdc = {cyl: deg for cyl, deg in h["meta"]["tdc_deg"].items()}

    fig, ax = plt.subplots(4, 1, figsize=(11, 14))
    fig.suptitle("720 sim v1: healthy vs continuous misfire on cylinder 3 (idle, 800 rpm target, seed 0)\n"
                 "SIMULATED: physics-informed engine simulator", fontsize=12)

    # 1) crank speed from tooth times
    t_lo = h["t"][0] + 1.0
    for r, col, lab in ((h, GREY, "healthy"), (m, RED, "misfire cyl 3, p = 1.0")):
        tm, _, rpm = speed_from_teeth(r["tooth_times"], r["tooth_ids"], first_tooth_deg=params.wheel_first_tooth_deg)
        sel = (tm >= t_lo) & (tm < t_lo + 1.0)
        ax[0].plot(tm[sel] - t_lo, rpm[sel], color=col, lw=1, label=lab)
    for t3 in tdc_times(m, tdc[3]):
        if t_lo <= t3 < t_lo + 1.0:
            ax[0].axvline(t3 - t_lo, color=RED, ls=":", lw=0.8)
    ax[0].set(title="1) Instantaneous crank speed from 60-2 tooth times (dotted: cyl 3 firing TDC, misfire run)",
              xlabel="time (s)", ylabel="rpm")
    ax[0].legend(loc="upper right")

    # 2) microphone over two cycles
    for r, col, off, lab in ((h, GREY, 0.0, "healthy"), (m, RED, -0.25, "misfire cyl 3")):
        t_start = tdc_times(r, 0)[3]
        sel = (r["t"] >= t_start - 0.01) & (r["t"] < t_start + 0.31)
        ax[1].plot(r["t"][sel] - t_start, r["mic"][sel] + off, color=col, lw=0.5, label=lab)
        for cyl, deg in tdc.items():
            for tt in tdc_times(r, deg):
                if t_start - 0.01 <= tt < t_start + 0.31:
                    ax[1].axvline(tt - t_start, color=col, ls=":", lw=0.6)
                    ax[1].text(tt - t_start + 0.002, off + 0.07, f"c{cyl}", color=col, fontsize=8)
    ax[1].set(title="2) Synthesised microphone, two 720° cycles (dotted: firing TDCs). Misfire: one pulse missing per cycle",
              xlabel="time from cyl 1 TDC (s)", ylabel="mic (offset)", ylim=(-0.36, 0.1))
    ax[1].legend(loc="center right")

    # 3) order spectrum of tooth-derived crank speed
    amps = {}
    for r, col, lab in ((h, GREY, "healthy"), (m, RED, "misfire cyl 3")):
        _, ang, rpm = speed_from_teeth(r["tooth_times"], r["tooth_ids"], first_tooth_deg=params.wheel_first_tooth_deg)
        o, s = order_spectrum(rpm, ang)
        amps[lab] = amplitude_at(o, s, 0.5)
        sel = o <= 4.25
        ax[2].plot(o[sel], s[sel], color=col, lw=1.2, label=lab)
    ratio = amps["misfire cyl 3"] / amps["healthy"]
    ax[2].axvline(0.5, color="k", ls=":", lw=0.8)
    ax[2].set(title=f"3) Order spectrum of crank speed: half-order amplitude misfire / healthy = {ratio:.1f}",
              xlabel="engine order (1 = once per revolution)", ylabel="rpm amplitude")
    ax[2].set_xticks(np.arange(0, 4.5, 0.5))
    ax[2].legend(loc="upper right")

    # 4) 720 deg fingerprint
    for r, col, lab in ((h, GREY, "healthy"), (m, RED, "misfire cyl 3")):
        c, e = tsa_720(r["mic"].astype(float) ** 2, r["crank_angle_deg"], bin_deg=2.0)
        ax[3].plot(c, e, color=col, lw=1.2, label=lab)
    for cyl, deg in tdc.items():
        ax[3].axvline(deg, color="k", ls=":", lw=0.6)
        ax[3].text(deg + 4, ax[3].get_ylim()[1] * 0.9, f"cyl {cyl} TDC", fontsize=8)
    ax[3].set(title="4) 720° fingerprint: time-synchronous average of mic energy, 2° bins",
              xlabel="crank angle (deg, 0 = cyl 1 firing TDC)", ylabel="mean mic²", xlim=(0, 720))
    ax[3].set_xticks(np.arange(0, 721, 90))
    ax[3].legend(loc="upper right")

    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=110)

    per_step = timing(params)
    n_cycles = len(m["labels"])
    print(f"figure: {OUT.relative_to(ROOT)}")
    print(f"recorded {RECORD_S:.0f} s after {WARMUP_S:.0f} s warm-up; {n_cycles} cycles; "
          f"misfired cyl 3 in {sum(l['misfired'][2] for l in m['labels'])}/{n_cycles}")
    print(f"mean rpm healthy {h['rpm'].mean():.1f}, misfire {m['rpm'].mean():.1f} "
          f"(MAP healthy {h['meta']['map_pa'] / 1e3:.1f} kPa, misfire {m['meta']['map_pa'] / 1e3:.1f} kPa)")
    print(f"half-order amplitude: healthy {amps['healthy']:.3f} rpm, misfire {amps['misfire cyl 3']:.3f} rpm, "
          f"ratio {ratio:.1f}")
    print(f"step(0.5): {per_step * 1e3:.1f} ms per call = {0.5 / per_step:.0f}x faster than real time")


if __name__ == "__main__":
    main()
