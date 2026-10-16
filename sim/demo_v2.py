"""Sanity figures for sim v2 (all SIMULATED, seed 0, reference engine, regimes off):

  docs/figures/sim_v2_knock.png       knock on cyl 2: accel around a burst, its spectrogram (chamber
                                      modes in 5-15 kHz) and the ECU's spark-advance trace
  docs/figures/sim_v2_clog_trim.png   injector clog: steady fuel trim vs s against s/(4-s) and s/4,
                                      and the once-per-cycle lambda ripple (simulator only)
  docs/figures/sim_v2_valve_tick.png  valve tick (HELD OUT): accel over one cycle, healthy vs tick

Usage (repo root):  python -m sim.demo_v2
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal as sps

from sim import FS, SIM_VERSION, EngineParams, EngineSim

OUT = Path(__file__).resolve().parents[1] / "docs" / "figures"
GREY, RED, BLUE = "#5f6368", "#c62828", "#1a73e8"


def run(fault=None, at=2.0, seconds=20.0, seed=0):
    sim = EngineSim(EngineParams(), seed=seed, purpose="demo")
    cs = []
    for i in range(int(round(seconds / 0.5))):
        if fault and abs(i * 0.5 - at) < 1e-9:
            sim.inject(*fault)
        cs.append(sim.step(0.5))
    return sim, cs


def cat(cs, k):
    return np.concatenate([c[k] for c in cs])


def knock_figure():
    _, cs = run(("knock", 2, 0.8), at=4.0, seconds=40.0)
    meta = cs[-1]["meta"]
    t, acc, ca = cat(cs, "t"), cat(cs, "accel"), cat(cs, "crank_angle_deg")
    lab = next(lab for c in cs for lab in c["labels"] if lab["knocked"][1])
    # cyl 2 fires at 540 deg into its cycle: find that crossing after the cycle start
    i0 = np.searchsorted(t, lab["t_start"])
    tk = t[i0 + int(np.argmax(ca[i0:i0 + FS // 4] >= 540.0))]
    sel = (t > tk - 0.03) & (t < tk + 0.05)
    fig, ax = plt.subplots(3, 1, figsize=(11, 10))
    ax[0].plot(t[sel] - tk, acc[sel], color=GREY, lw=0.5)
    ax[0].set(title="Block accelerometer (= the knock sensor) around the first knock on cyl 2", xlabel="s from cyl 2 TDC",
              ylabel="accel (a.u.)")
    f, tt, S = sps.spectrogram(acc[sel], fs=FS, nperseg=256, noverlap=240)
    ax[1].pcolormesh(tt + (t[sel][0] - tk), f / 1e3, 10 * np.log10(S + 1e-14), shading="auto", cmap="Greys")
    for fk in meta["knock_freqs_hz"]:
        ax[1].axhline(fk / 1e3, color=RED, lw=0.7, ls=":")
    ax[1].set(title="Spectrogram: the knock burst rings at the chamber modes (dotted, Draper) inside 5-15 kHz",
              xlabel="s from cyl 2 TDC", ylabel="kHz", ylim=(0, 20))
    obd = [o for c in cs for o in c["obd"]]
    ax[2].step([o["t"] for o in obd], [o["spark_adv_deg"] for o in obd], where="post", color=BLUE,
               label="spark advance (OBD, 1 Hz, cylinder mean)")
    ax[2].axvline(4.0, color=RED, ls=":", lw=0.8)
    n_k = sum(lab["knocked"][1] for c in cs for lab in c["labels"])
    ax[2].set(title=f"ECU knock control: retard per detected knock, slow recovery; knock suppressed ({n_k} bursts in 36 s)",
              xlabel="s (knock injected at 4 s, cyl 2, p = 0.8)", ylabel="deg BTDC")
    ax[2].legend(loc="lower right")
    fig.suptitle(f"720 sim {SIM_VERSION} (SIMULATED): knock", fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "sim_v2_knock.png", dpi=110)
    plt.close(fig)
    return n_k


def clog_figure():
    ss = (0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
    trims = []
    for s in ss:
        _, cs = run(("injector_clog", 1, s), seconds=40.0)
        f = [c["ecu_fast"] for c in cs[-20:]]
        trims.append(100 * (np.mean((1 + np.concatenate([x["stft_pct"] for x in f]) / 100)
                                    * (1 + np.concatenate([x["ltft_pct"] for x in f]) / 100)) - 1))
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))
    sx = np.linspace(0, 0.6, 50)
    ax[0].plot(sx, 100 * sx / (4 - sx), color=GREY, label="s / (4 - s): bank mean phi back to 1")
    ax[0].plot(sx, 25 * sx, ":", color=GREY, label="s / 4")
    ax[0].plot(ss, trims, "o", color=BLUE, label="simulated STFT + LTFT (mean of the last 10 s of 40 s)")
    ax[0].set(title="Injector clog on 1 of 4 cylinders: bank fuel trim", xlabel="clog s (fraction of fuel lost)",
              ylabel="trim (%)")
    ax[0].legend(fontsize=8)
    _, cs = run(("injector_clog", 1, 0.4), seconds=12.0)
    e = [c["ecu_fast"] for c in cs[-4:]]
    ax[1].plot(np.concatenate([x["t"] for x in e]), np.concatenate([x["lambda"] for x in e]), ".-", color=BLUE, ms=3)
    ax[1].set(title="Lambda, one sample per firing: ripple once per 720 deg (s = 0.4)\nsimulator only: carOBD has no O2",
              xlabel="s", ylabel="lambda")
    fig.suptitle(f"720 sim {SIM_VERSION} (SIMULATED): injector clog", fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "sim_v2_clog_trim.png", dpi=110)
    plt.close(fig)
    return list(zip(ss, trims))


def valve_figure():
    fig, ax = plt.subplots(1, 1, figsize=(11, 4))
    for fault, off, col, lab in ((None, 0.0, GREY, "healthy"), (("valve_tick", 4, 1.0), -0.15, RED, "valve tick on cyl 4, severity 1.0")):
        _, cs = run(fault, at=1.0, seconds=4.0)
        t, a, ca = cat(cs, "t"), cat(cs, "accel"), cat(cs, "crank_angle_deg")
        start = np.flatnonzero((np.diff(ca) < -360) & (t[1:] > 2.5))[0] + 1
        sel = slice(start, start + int(0.16 * FS))
        ax.plot(t[sel] - t[start], a[sel] + off, color=col, lw=0.5, label=lab)
    ax.axvline(220.0 / (6 * 800), color=RED, ls=":", lw=0.8)
    ax.text(220.0 / (6 * 800) + 0.002, 0.03, "cyl 4 valve closing", color=RED, fontsize=8)
    ax.set(title="Valve tick, HELD OUT (never in calibration or training): a sharper, earlier seat impact once per 720 deg",
           xlabel="s from cyl 1 TDC (~800 rpm)", ylabel="accel (offset)")
    ax.legend(loc="lower right")
    fig.suptitle(f"720 sim {SIM_VERSION} (SIMULATED, held-out fault): valve tick", fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "sim_v2_valve_tick.png", dpi=110)
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    n_k = knock_figure()
    trims = clog_figure()
    valve_figure()
    print(f"knock bursts in 36 s: {n_k}")
    print("clog trim % (s, simulated vs s/(4-s)):", [(s, round(t, 2), round(100 * s / (4 - s), 2)) for s, t in trims])
    print("wrote sim_v2_knock.png, sim_v2_clog_trim.png, sim_v2_valve_tick.png in", OUT)


if __name__ == "__main__":
    main()
