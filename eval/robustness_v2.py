"""Robustness of the crank-angle misfire rule at the recalibrated thresholds (sim v2.x, regimes on; the figure title carries the exact sim_version).

Evaluation engines only (disjoint from calibration). For each condition, runs start healthy, the
misfire is injected after warm-up, and every window that holds a TRUE misfired cycle (ground truth)
counts: detection = the rule fired; localisation = it named the cylinder that actually misfired.

Sweeps
  p        misfire probability 0.1..1.0 x rpm (800/1500/2300/2800) x load (5, 25 N m)
  snr      mic/accel SNR 40..0 dB at p = 0.5 (the rule reads the crank only: flat by construction)
  crank    crank-side noise: torsional flex x tooth jitter at p = 0.5, 2300 rpm
  held     held-out region: test-region engines at 3100/3400 rpm (thresholds extrapolated)
Latency    first TRUE misfired combustion -> first ADVISORY window end, per rpm band (p = 1.0 and 0.5);
           the backend publishes it on its next 10 Hz state tick (+ <= 100 ms, not included).

Writes eval/results/robustness_v2.json and robustness_v2.png (also docs/figures/robustness_v2.png).
Usage (repo root):  python -m eval.robustness_v2 [--engines 6]
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from dataclasses import replace
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from dsp.rules import MISFIRE_HALF_ORDER, load_thresholds
from eval.common import SEEDS, eval_engine, rpm_band, run, wilson
from sim import SIM_VERSION
from sim.params import Regimes

RESULTS = Path(__file__).resolve().parent / "results"
FIG = Path(__file__).resolve().parents[1] / "docs" / "figures" / "robustness_v2.png"
P_GRID = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0)
RPMS = (800.0, 1500.0, 2300.0, 2800.0)
LOADS = (5.0, 25.0)
HELD_RPMS = (3100.0, 3400.0)
SNRS = (40.0, 25.0, 15.0, 5.0, 0.0)
CRANK = ((0.0, 2e-6), (0.02, 2e-6), (0.04, 2e-6), (0.08, 2e-6), (0.02, 5e-6), (0.02, 10e-6))
FAULTY_S = 40.0


def job(args):
    sweep, kind, i, rpm, load, p, extra = args
    params, seed = eval_engine(kind, i)
    cyl = 1 + (i % 4)
    regimes = Regimes.eval()
    kw = {}
    if sweep == "snr":
        kw = {"mic_snr_db": extra, "accel_snr_db": extra}
    elif sweep == "crank":
        regimes = replace(regimes, torsional_deg_at_1000=extra[0], tooth_jitter_s=extra[1])
    wins = run(params, seed, rpm, load, FAULTY_S, regimes=regimes, fault=("misfire", cyl, p), inject_at=0.0, **kw)
    bad = [w for w in wins if cyl in w.true_misfire]
    fired = [w for w in bad if w.fired]
    first_true = min((w.first_misfire_t for w in wins if w.first_misfire_t is not None), default=None)
    adv = next((w.t_end for w in wins if w.decision == "ADVISORY" and first_true is not None and w.t_end >= first_true), None)
    return {"sweep": sweep, "kind": kind, "engine": i, "cyl": cyl, "rpm": rpm, "load": load, "p": p,
            "extra": extra, "bad_windows": len(bad), "detected": len(fired),
            "located": sum(1 for w in fired if w.location == cyl),
            "latency_s": (adv - first_true) if adv is not None else None,
            "compute_ms": float(np.median([w.compute_ms for w in wins])) if wins else None,
            "value_median": float(np.median([w.value for w in bad])) if bad else None,
            "threshold": float(np.median([w.threshold for w in wins])) if wins else None}


def agg(rows):
    n = sum(r["bad_windows"] for r in rows)
    d = sum(r["detected"] for r in rows)
    loc = sum(r["located"] for r in rows)
    vals = [r["value_median"] / r["threshold"] for r in rows if r["value_median"] is not None]
    return {"windows_with_misfire": n, "detected": d, "detection": d / n if n else None,
            "detection_ci95": wilson(d, n), "located": loc, "localisation": loc / d if d else None,
            "localisation_ci95": wilson(loc, d), "separation_median_x_threshold": float(np.median(vals)) if vals else None,
            "runs": len(rows)}


def latency_stats(rows):
    lat = [r["latency_s"] for r in rows if r["latency_s"] is not None]
    miss = sum(1 for r in rows if r["latency_s"] is None)
    if not lat:
        return {"n": 0, "missed": miss}
    return {"n": len(lat), "missed_within_run": miss, "median_ms": 1e3 * float(np.median(lat)),
            "p95_ms": 1e3 * float(np.quantile(lat, 0.95)), "max_ms": 1e3 * float(np.max(lat))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", type=int, default=6)
    a = ap.parse_args()
    E = a.engines
    jobs = [("p", "robustness", i, rpm, load, p, None) for rpm in RPMS for load in LOADS for p in P_GRID for i in range(E)]
    jobs += [("snr", "robustness", i, 1500.0, 25.0, 0.5, s) for s in SNRS for i in range(E)]
    jobs += [("crank", "robustness", i, 2300.0, 25.0, 0.5, c) for c in CRANK for i in range(E)]
    jobs += [("held", "held_out_region", i, rpm, load, p, None) for rpm in HELD_RPMS for load in LOADS
             for p in (0.3, 0.5, 1.0) for i in range(E)]
    jobs.sort(key=lambda j: -j[3])
    t0 = time.perf_counter()
    rows = []
    with Pool(int(__import__("os").environ.get("S720_WORKERS", 10))) as pool:
        for k, r in enumerate(pool.imap_unordered(job, jobs), 1):
            rows.append(r)
            if k % 50 == 0 or k == len(jobs):
                print(f"  {k}/{len(jobs)} runs  ({time.perf_counter() - t0:.0f} s)", flush=True)

    sel = lambda **kv: [r for r in rows if all(r[k] == v for k, v in kv.items())]  # noqa: E731
    p_table = {f"{rpm:.0f}rpm_{load:.0f}Nm": {f"{p}": agg(sel(sweep="p", rpm=rpm, load=load, p=p)) for p in P_GRID}
               for rpm in RPMS for load in LOADS}
    held_table = {f"{rpm:.0f}rpm_{load:.0f}Nm": {f"{p}": agg(sel(sweep="held", rpm=rpm, load=load, p=p)) for p in (0.3, 0.5, 1.0)}
                  for rpm in HELD_RPMS for load in LOADS}
    snr_table = {f"{s:.0f}dB": agg([r for r in rows if r["sweep"] == "snr" and r["extra"] == s]) for s in SNRS}
    crank_table = {f"flex{c[0]}_jit{c[1]*1e6:.0f}us": agg([r for r in rows if r["sweep"] == "crank" and tuple(r["extra"]) == c])
                   for c in CRANK}
    lat = {}
    for p in (1.0, 0.5):
        for band in ("idle", "mid", "high"):
            lat[f"p{p}_{band}"] = latency_stats([r for r in rows if r["sweep"] == "p" and r["p"] == p and rpm_band(r["rpm"]) == band])
        lat[f"p{p}_held-out"] = latency_stats(sel(sweep="held", p=p))

    seps = [v["separation_median_x_threshold"] for t in p_table.values() for v in t.values()
            if v["separation_median_x_threshold"] is not None]
    th = load_thresholds()
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sim_version": SIM_VERSION, "source": "simulated", "column": "A (simulated)",
        "thresholds": {"file": MISFIRE_HALF_ORDER.name, "created_at": th["created_at"], "feature": th.get("feature")},
        "regimes": Regimes.eval().__dict__,
        "seeds": {k: {"engines": [SEEDS[k][0][0], SEEDS[k][0][-1]], "run_seed_offset": SEEDS[k][1]}
                  for k in ("robustness", "held_out_region")},
        "engines_per_condition": E, "faulty_seconds_per_run": FAULTY_S,
        "definitions": {"detection": "windows holding a true misfire of the injected cylinder in which the rule fired",
                        "localisation": "of those detected windows, the share naming the right cylinder",
                        "separation": "median feature value of misfire windows / band threshold",
                        "latency": "first true misfired combustion -> end of the first ADVISORY window (+ <=100 ms tick)"},
        "p_sweep": p_table, "held_out_region": held_table, "snr_sweep": snr_table, "crank_noise_sweep": crank_table,
        "latency": lat,
        "separation_range": {"min": min(seps), "max": max(seps),
                             "everywhere_above_4x": bool(min(seps) > 4)},
        "compute_ms_per_window_median": float(np.median([r["compute_ms"] for r in rows if r["compute_ms"]])),
        "runs": rows,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "robustness_v2.json").write_text(json.dumps(result, indent=1, default=float) + "\n", encoding="utf-8")
    plot(result)
    report(result)
    print(f"({time.perf_counter() - t0:.0f} s)")


def plot(res):
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))
    colors = dict(zip(RPMS + HELD_RPMS, ("#5f6368", "#1a73e8", "#e37400", "#c5221f", "#9334e6", "#7b1fa2")))
    for key, tab in res["p_sweep"].items():
        rpm, load = float(key.split("rpm")[0]), float(key.split("_")[1][:-2])
        ps = [float(p) for p in tab]
        style = "-" if load == 25.0 else "--"
        ax[0, 0].plot(ps, [tab[p]["detection"] for p in tab], style, marker="o", ms=3, color=colors[rpm],
                      label=f"{rpm:.0f} rpm, {load:.0f} N m")
        ax[0, 1].plot(ps, [tab[p]["localisation"] for p in tab], style, marker="o", ms=3, color=colors[rpm])
    for key, tab in res["held_out_region"].items():
        rpm, load = float(key.split("rpm")[0]), float(key.split("_")[1][:-2])
        ps = [float(p) for p in tab]
        ax[0, 0].plot(ps, [tab[p]["detection"] for p in tab], ":", marker="s", ms=4, color=colors[rpm],
                      label=f"HELD-OUT {rpm:.0f} rpm, {load:.0f} N m")
        ax[0, 1].plot(ps, [tab[p]["localisation"] for p in tab], ":", marker="s", ms=4, color=colors[rpm])
    ax[0, 0].set(title="Detection vs misfire probability (windows holding a true misfire)", xlabel="misfire p per cycle",
                 ylabel="detection rate", ylim=(0, 1.02))
    ax[0, 0].legend(fontsize=7, ncol=2)
    ax[0, 1].set(title="Localisation vs p (detected windows naming the right cylinder)", xlabel="misfire p per cycle",
                 ylabel="localisation accuracy", ylim=(0, 1.02))
    snr = res["snr_sweep"]
    ax[1, 0].plot([float(k[:-2]) for k in snr], [v["detection"] for v in snr.values()], "o-", color="#5f6368",
                  label="mic/accel SNR sweep (rule reads the crank only)")
    crank = res["crank_noise_sweep"]
    ax[1, 0].set(title="Detection at p = 0.5 vs SNR (1500 rpm): flat by construction", xlabel="mic / accel SNR (dB)",
                 ylabel="detection rate", ylim=(0, 1.02))
    ax[1, 0].invert_xaxis()
    ax2 = ax[1, 0].inset_axes([0.08, 0.08, 0.5, 0.42])
    ax2.bar(range(len(crank)), [v["detection"] for v in crank.values()], color="#e37400")
    ax2.set_xticks(range(len(crank)), [k.replace("_", "\n") for k in crank], fontsize=6)
    ax2.set_title("crank noise, p=0.5, 2300 rpm", fontsize=7)
    ax2.set_ylim(0, 1)
    lat = res["latency"]
    keys = [k for k in lat if lat[k].get("n")]
    ax[1, 1].bar(range(len(keys)), [lat[k]["median_ms"] for k in keys], color="#1a73e8", label="median")
    ax[1, 1].scatter(range(len(keys)), [lat[k]["p95_ms"] for k in keys], color="#c5221f", zorder=3, label="p95")
    ax[1, 1].set_xticks(range(len(keys)), [k.replace("_", "\n") for k in keys], fontsize=7)
    ax[1, 1].set(title="Latency: first true misfire -> ADVISORY window end", ylabel="ms")
    ax[1, 1].legend()
    fig.suptitle(f"720 misfire rule, sim {SIM_VERSION} (SIMULATED, column A), evaluation engines only, regimes on", fontsize=11)
    fig.tight_layout()
    fig.savefig(RESULTS / "robustness_v2.png", dpi=110)
    FIG.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(RESULTS / "robustness_v2.png", FIG)


def report(res):
    print("detection / localisation (n windows with a true misfire)")
    for key, tab in list(res["p_sweep"].items()) + [("HELD " + k, v) for k, v in res["held_out_region"].items()]:
        print(f"  {key:22s} " + "  ".join(f"p{p}: {v['detection']:.2f}/{(v['localisation'] or 0):.2f}" for p, v in tab.items()))
    print("snr:", {k: round(v["detection"], 2) for k, v in res["snr_sweep"].items()})
    print("crank noise:", {k: round(v["detection"], 2) for k, v in res["crank_noise_sweep"].items()})
    for k, v in res["latency"].items():
        if v.get("n"):
            print(f"  latency {k:14s} n={v['n']:3d} median {v['median_ms']:.0f} ms  p95 {v['p95_ms']:.0f} ms  missed {v['missed_within_run']}")
    print("separation (median misfire value / threshold):", res["separation_range"])


if __name__ == "__main__":
    main()
