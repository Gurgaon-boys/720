"""The cost of a higher healthy-model threshold: held-out valve-tick detection and latency (L3).

Valve tick is never trained, so only the healthy model can catch it (UNCLASSIFIED). Recorded fault runs
(eval/record_runs.py "faults"): robustness engines (never trained or tuned on), regimes on, 30 s healthy,
then valve tick on a random cylinder for 90 s, at severity 0.3 / 0.6 / 1.0, 20 runs each.

For each threshold configuration (before / persist2 / after, and the trade-off family from
models/thresholds/healthy_model.json) the real rule layer is replayed over the same windows:
  detected   the run reaches UNCLASSIFIED within the 90 s of fault
  latency    injection -> end of the first UNCLASSIFIED window (s)
  named      a named-fault ALARM during the fault (must stay 0: valve tick is unknown)
Detection rates carry Wilson 95 % intervals. The trade-off curve pairs each configuration's validation
UNCLASSIFIED episodes/h (from the calibration) with its valve-tick detection; the third soak's rate
(eval/results/soak_l3_fresh3.json) is added for the committed configurations only, as a report.

Writes eval/results/valve_tick_cost.json and docs/figures/unclassified_tradeoff.png.
Usage:  python -m eval.valve_tick_cost
"""

from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from eval.chain import CACHE, first_time, load_rec, replay
from eval.common import wilson
from models.witness import HEALTHY_THRESHOLDS
from sim import SIM_VERSION

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "valve_tick_cost.json"
FIG = ROOT / "docs" / "figures" / "unclassified_tradeoff.png"
SEVERITIES = (0.3, 0.6, 1.0)


def evaluate(recs, th, uw) -> dict:
    out = {}
    for s in SEVERITIES:
        rs = [r for r in recs if abs(r["meta"]["severity"] - s) < 1e-9]
        lat, named = [], 0
        for r in rs:
            rep = replay(r, th, uw)
            t = first_time(rep, r, "unclassified")
            if t is not None:
                lat.append(t)
            named += int(rep["alarm_raised"][rep["counted"]].sum())
        k, n = len(lat), len(rs)
        lo, hi = wilson(k, n)
        out[f"{s:g}"] = {"kind": "proportion", "successes": k, "n": n, "value": k / n if n else None, "ci_95": [lo, hi],
                         "ci_method": "wilson_95", "latency_s_median": float(np.median(lat)) if lat else None,
                         "latency_s_p90": float(np.quantile(lat, 0.9)) if lat else None, "named_fault_alarms": named}
    allk = sum(v["successes"] for v in out.values())
    alln = sum(v["n"] for v in out.values())
    lo, hi = wilson(allk, alln)
    out["all"] = {"successes": allk, "n": alln, "value": allk / alln, "ci_95": [lo, hi]}
    return out


def main():
    paths = sorted(glob.glob(str(CACHE / "faults" / "robustness" / "valve_tick_*.npz")))
    if len(paths) < 60:
        raise SystemExit(f"{len(paths)} / 60 valve-tick runs recorded: run python -m eval.record_runs faults")
    recs = [load_rec(Path(p)) for p in paths]
    cal = json.loads(HEALTHY_THRESHOLDS.read_text())
    soak3 = ROOT / "eval" / "results" / "soak_l3_fresh3.json"
    s3 = json.loads(soak3.read_text())["configs"] if soak3.exists() else {}
    v = cal["val_thr_unclassified"]
    rows = {"before": (None, 1, v["before (pooled windows, 1 window)"]["per_hour"]),
            "persist2": (None, 2, v["pooled windows, 2 windows"]["per_hour"]),
            "after": (cal["bands"], 2, v["after (engine spread, 2 windows)"]["per_hour"])}
    for k, f in cal["family_on_validation"].items():
        rows[f"family {k}"] = (f["thresholds"], 2, f["val_thr"]["per_hour"])
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sim_version": SIM_VERSION,
           "label": "SIM 2.1", "column": "A", "split": "robustness engines (unseen), valve tick held out of training",
           "runs_per_severity": len(recs) // len(SEVERITIES), "fault_s": recs[0]["meta"]["run_s"], "configs": {}}
    for name, (th, uw, val_rate) in rows.items():
        r = {"thresholds": th or "checkpoint (pooled val windows)", "unclassified_windows_required": uw,
             "val_unclassified_episodes_per_hour": val_rate, "valve_tick": evaluate(recs, th, uw)}
        if name in s3:
            r["fresh3_unclassified_episodes_per_hour"] = s3[name]["levels"]["unclassified"]["episodes"]["per_hour"]
        res["configs"][name] = r
        vt = r["valve_tick"]
        print(f"  {name:16s} val {val_rate:6.2f}/h  valve tick " + "  ".join(
            f"s{s}: {vt[s]['successes']}/{vt[s]['n']} (med {vt[s]['latency_s_median']} s)" for s in map(lambda x: f"{x:g}", SEVERITIES))
            + (f"  fresh3 {r['fresh3_unclassified_episodes_per_hour']:.2f}/h" if "fresh3_unclassified_episodes_per_hour" in r else ""))
    OUT.write_text(json.dumps(res, indent=1))
    plot(res)


def plot(res):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fam = [(k, c) for k, c in res["configs"].items() if k.startswith("family") or k in ("persist2", "after")]
    fam.sort(key=lambda kc: -kc[1]["val_unclassified_episodes_per_hour"])
    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=130)
    colors = {"0.3": "#86b6ef", "0.6": "#2a78d6", "1": "#104281"}     # sequential blue: severity is magnitude
    for s, c in colors.items():
        x = [max(cfg["val_unclassified_episodes_per_hour"], 0.02) for _, cfg in fam]
        y = [cfg["valve_tick"][s]["value"] for _, cfg in fam]
        ax.plot(x, y, "o-", color=c, label=f"valve tick severity {s}")
    a = res["configs"]["after"]
    ax.axvline(1.0, color="#d03b3b", ls="--", lw=1)
    ax.text(1.05, 0.03, "target 1 episode/h", color="#52514e", fontsize=8)
    ax.scatter([max(a["val_unclassified_episodes_per_hour"], 0.02)] * 3, [a["valve_tick"][s]["value"] for s in colors],
               s=120, facecolors="none", edgecolors="#d03b3b", zorder=5, label="committed (after)")
    ax.set_xscale("log")
    ax.set_xlabel("UNCLASSIFIED episodes / h on validation engines (healthy; 0 plotted at 0.02)")
    ax.set_ylabel("valve-tick runs reaching UNCLASSIFIED (90 s)")
    ax.set_ylim(-0.02, 1.02)
    ax.set_title(f"SIM {res['sim_version']} · healthy-model threshold trade-off (2-window UNCLASSIFIED)", fontsize=10)
    ax.legend(fontsize=8, loc="lower right")
    fig.text(0.01, 0.01, f"valve tick: robustness engines, {res['runs_per_severity']} runs per severity; "
             "thresholds: q-quantiles / multiples of per-engine-run maxima on validation engines", fontsize=7)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG)
    print(f"-> {OUT}, {FIG}")


if __name__ == "__main__":
    main()
