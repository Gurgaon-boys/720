"""Alarm budget: what an operator would see, per engine and for a 50-engine fleet (L3).

Episodes per engine-hour on healthy data, at the committed configuration, for each witness alone and for
what reaches the operator:
  physics rule alone          any physics rule fired (episodes of consecutive windows)
  classifier alone (p >= 0.8) the classifier witness names a fault (it abstains below p 0.8)
  two-witness ALARM           ALARM latch episodes
  UNCLASSIFIED                2 consecutive anomalous, unnamed windows
on (i) SIM 2.1: the third untouched soak (engines 1100-1139, eval/results/soak_l3_fresh3.json, "after") and
(ii) REAL carOBD: the held-out files (eval/results/column_c_detail.json, the OBD branch through the same rule
layer; there is no classifier on OBD, so ALARM cannot occur there).
Detection rate (Wilson 95 %) and median latency for the same levels on recorded fault runs (robustness
engines; misfire p 0.5, knock 0.5, injector clog s 0.5, valve tick 0.3 / 0.6 / 1.0; 20 runs each, 90 s).

Fleet projection: one operator watching 50 engines; operator-facing notifications = ALARM + UNCLASSIFIED
episodes (NE 107 not NORMAL); ADVISORY stays in the event log. Compared with alarm-rate guidance per operator
(EEMUA 191 / ANSI/ISA-18.2): about 6 per hour (1 per 10 min) "very likely acceptable", about 12 per hour
(2 per 10 min; ~300 per day) "maximum manageable". Assumptions are printed on the figure.

Writes eval/results/alarm_budget.json and docs/figures/alarm_budget.png.   Usage:  python -m eval.alarm_budget
"""

from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import chi2

from eval.chain import CACHE, RULES, load_rec, replay
from eval.common import wilson
from models.make_dataset import CLASSES
from models.witness import HEALTHY_THRESHOLDS, WITNESS_P
from sim import SIM_VERSION

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "alarm_budget.json"
FIG = ROOT / "docs" / "figures" / "alarm_budget.png"
ENGINES = 50
GUIDANCE = {"very likely acceptable (1 per 10 min)": 6.0, "maximum manageable (2 per 10 min)": 12.0}
FAULTS = (("misfire", 0.5), ("knock", 0.5), ("injector_clog", 0.5), ("valve_tick", 0.3), ("valve_tick", 0.6),
          ("valve_tick", 1.0))


def poisson(k, hours):
    return {"events": int(k), "exposure_hours": round(hours, 4), "per_hour": k / hours,
            "upper_95_per_hour": float(chi2.ppf(0.95, 2 * (k + 1)) / (2 * hours)), "bound_method": "poisson_exact_one_sided_95"}


def sim_levels(name: str) -> dict:
    s3 = json.loads((ROOT / "eval" / "results" / f"soak_l3_{name}.json").read_text())
    a = s3["configs"]["after"]
    L, h = a["levels"], a["hours"]
    lv = {"physics_alone": L["physics_windows"]["episodes"], "classifier_alone": L["classifier_false_faults"]["episodes"],
          "alarm": L["alarms_raised"]["episodes"], "unclassified": L["unclassified"]["episodes"],
          "advisory": L["advisory"]["episodes"]}
    return {"label": "SIM 2.1", "column": "A", "split": s3["split"], "hours": h, "runs": s3["runs"],
            "source": f"eval/results/soak_l3_{name}.json (after)", "levels": lv}


def healthy_levels() -> dict:
    c = json.loads((ROOT / "eval" / "results" / "column_c_detail.json").read_text())["overall"]
    ch = c["hours"]
    real = {"physics_alone": c["physics_trim_windows"], "classifier_alone": None,
            "alarm": {**c["alarms"], "structurally_limited": True}, "unclassified": c["unclassified"],
            "advisory": c["advisories"]}
    real_src = {"label": "REAL carOBD", "column": "C", "split": "carOBD held-out files", "hours": ch, "files": c["files"],
                "source": "eval/results/column_c_detail.json (OBD branch, 2-window UNCLASSIFIED; secondary analysis)",
                "note": "no classifier on OBD: classifier alone is not measured and ALARM needs two witnesses"}
    # sim = the 20 engine-hour soak (main); sim_2h = the 2 h third-range soak, kept beside it
    return {"sim": sim_levels("long"), "sim_2h": sim_levels("fresh3"), "real": {**real_src, "levels": real}}


def detection() -> dict:
    th = json.loads(HEALTHY_THRESHOLDS.read_text())["bands"]
    out = {}
    for fault, sev in FAULTS:
        paths = sorted(glob.glob(str(CACHE / "faults" / "robustness" / f"{fault}_s{sev:.2f}_*.npz")))
        lat = {k: [] for k in ("physics_alone", "classifier_alone", "alarm", "unclassified")}
        for p in paths:
            r = load_rec(Path(p))
            rep = replay(r, th, 2)
            inj = r["meta"]["inject_t"]
            after = rep["counted"] & (r["t_start"] >= inj)
            t = r["t_end"] - inj

            def first(mask):
                i = np.flatnonzero(mask & after)
                return float(t[i[0]]) if len(i) else None
            j = RULES.index(fault) if fault in RULES else None
            lat["physics_alone"].append(first(r["rf"][:, j]) if j is not None else None)
            cls = (r["label"] == CLASSES.index(fault)) & (r["p"] >= WITNESS_P) if fault in CLASSES else np.zeros(len(t), bool)
            lat["classifier_alone"].append(first(cls) if fault in CLASSES else None)
            lat["alarm"].append(first(rep["alarm"] & np.array([f == fault for f in rep["fault"]])))
            lat["unclassified"].append(first(rep["unclassified"]))
        res = {}
        for k, v in lat.items():
            if fault == "valve_tick" and k in ("physics_alone", "classifier_alone", "alarm"):
                res[k] = {"applicable": False, "reason": "valve tick is held out: no rule, no trained class",
                          "named_alarms": sum(x is not None for x in v) if k == "alarm" else None}
                continue
            hit = [x for x in v if x is not None]
            lo, hi = wilson(len(hit), len(v))
            res[k] = {"kind": "proportion", "successes": len(hit), "n": len(v), "value": len(hit) / len(v),
                      "ci_95": [lo, hi], "ci_method": "wilson_95", "latency_s_median": float(np.median(hit)) if hit else None}
        out[f"{fault} {sev:g}"] = res
    return out


def main():
    h = healthy_levels()
    sim = h["sim"]

    def project(src):
        op = src["levels"]["alarm"]["events"] + src["levels"]["unclassified"]["events"]
        pe = poisson(op, src["hours"])
        return {"label": src["label"], "split": src["split"], "hours": src["hours"], "per_engine_operator_notifications": pe,
                "fleet_per_hour": ENGINES * pe["per_hour"], "fleet_upper_95_per_hour": ENGINES * pe["upper_95_per_hour"]}
    by_source = {"sim_20h": project(sim), "sim_2h": project(h["sim_2h"]),
                 "real_carobd": {**project(h["real"]), "note": "ALARM is structurally 0 on carOBD (no classifier on OBD): "
                                                               "notifications are UNCLASSIFIED episodes only"}}
    per_engine = by_source["sim_20h"]["per_engine_operator_notifications"]
    proj = {"engines": ENGINES, "main": "sim_20h", "per_engine_operator_notifications": per_engine,
            "fleet_per_hour": ENGINES * per_engine["per_hour"], "fleet_upper_95_per_hour": ENGINES * per_engine["upper_95_per_hour"],
            "by_source": by_source, "guidance_per_operator_hour": GUIDANCE,
            "assumptions": ["50 engines like the healthy test fleet (SIM 2.1 long soak, or the REAL carOBD held-out drives), independent, steady operation",
                            "every ALARM and every UNCLASSIFIED episode is one operator notification; ADVISORY is logged, not annunciated",
                            "nuisance load on a healthy fleet only (true fault alarms come on top)",
                            "guidance: EEMUA 191 / ANSI/ISA-18.2 average alarm rate per operator (6/h acceptable, 12/h max manageable)"]}
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sim_version": SIM_VERSION,
           "healthy": h, "operator_projection": proj, "fault_runs": {"label": "SIM 2.1", "column": "A",
                                                                    "split": "robustness engines (unseen), 90 s of fault, 20 runs each",
                                                                    "detection": detection()}}
    OUT.write_text(json.dumps(res, indent=1))
    plot(res)
    for k, v in proj["by_source"].items():
        print(f"  projection {k:12s} {v['fleet_per_hour']:.1f}/h (95 % upper {v['fleet_upper_95_per_hour']:.1f}) from "
              f"{v['per_engine_operator_notifications']['events']} episodes in {v['hours']:.2f} h")
    print(json.dumps({"sim": {k: (v or {}).get("per_hour") for k, v in sim["levels"].items()},
                      "real": {k: (v or {}).get("per_hour") for k, v in h["real"]["levels"].items()},
                      "fleet_per_hour": proj["fleet_per_hour"], "fleet_upper_95": proj["fleet_upper_95_per_hour"]}, indent=1))
    for f, d in res["fault_runs"]["detection"].items():
        print(f"  {f:16s} " + "  ".join(f"{k}: {v['successes']}/{v['n']} ({v['latency_s_median']} s)" if v.get("kind") else f"{k}: n/a"
                                          for k, v in d.items()))


def plot(res):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, ink2, grid = "#0b0b0b", "#52514e", "#e4e3df"
    plt.rcParams.update({"font.size": 8, "axes.edgecolor": ink2, "axes.labelcolor": ink, "xtick.color": ink2,
                         "ytick.color": ink2})
    fig = plt.figure(figsize=(11, 6.4), dpi=130)
    gs = fig.add_gridspec(2, 2, height_ratios=[3, 2], width_ratios=[3, 2])
    ax = fig.add_subplot(gs[0, 0])
    levels = [("physics_alone", "physics rule alone"), ("classifier_alone", "classifier alone (p ≥ 0.8)"),
              ("alarm", "two-witness ALARM"), ("unclassified", "UNCLASSIFIED")]
    src = [("sim", f"SIM 2.1 long soak ({res['healthy']['sim']['hours']:.0f} engine-h, engines 1300-1399)", "#2a78d6"),
           ("sim_2h", f"SIM 2.1 third range ({res['healthy']['sim_2h']['hours']:.0f} engine-h, engines 1100-1139)", "#1baf7a"),
           ("real", "REAL carOBD held-out files", "#eb6834")]
    y = np.arange(len(levels))
    for k, (key, lab, col) in enumerate(src):
        L = res["healthy"][key]["levels"]
        for i, (lv, _) in enumerate(levels):
            r = L[lv]
            yy = y[i] + (k - 1) * 0.27
            if r is None:
                ax.text(0.02, yy, "not measured (no OBD classifier)", va="center", color=ink2, fontsize=7)
                continue
            ax.barh(yy, r["per_hour"], height=0.24, color=col, edgecolor="white", linewidth=1.5)
            ax.errorbar(r["per_hour"], yy, xerr=[[0], [r["upper_95_per_hour"] - r["per_hour"]]], color=ink2, lw=0.8, capsize=2)
            ax.text(r["upper_95_per_hour"] + 0.05, yy, f"{r['per_hour']:.2f} (≤{r['upper_95_per_hour']:.2f})"
                    + (" structural" if r.get("structurally_limited") else ""), va="center", color=ink, fontsize=7)
    ax.set_yticks(y, [l for _, l in levels])
    ax.invert_yaxis()
    ax.set_xlabel("episodes per engine-hour, healthy (bar = rate, whisker = exact Poisson 95 % upper)")
    ax.grid(axis="x", color=grid, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in src], labels=[l for _, l, _ in src],
              loc="lower right", fontsize=7, frameon=False)
    ax.set_title("Healthy data, committed configuration", fontsize=9, color=ink, loc="left")

    ax2 = fig.add_subplot(gs[0, 1])
    p = res["operator_projection"]
    bars = [("sim_20h", "SIM 2.1\n20 engine-h", "#2a78d6"), ("sim_2h", "SIM 2.1\n2 engine-h", "#1baf7a"),
            ("real_carobd", "REAL carOBD\n(UNCLASSIFIED only)", "#eb6834")]
    top = max(p["by_source"][k]["fleet_upper_95_per_hour"] for k, _, _ in bars)
    for x, (k, lab, col) in enumerate(bars):
        v = p["by_source"][k]
        ax2.bar([x], [v["fleet_per_hour"]], width=0.55, color=col)
        ax2.errorbar([x], [v["fleet_per_hour"]], yerr=[[0], [v["fleet_upper_95_per_hour"] - v["fleet_per_hour"]]], color=ink2, capsize=4)
        ax2.text(x, v["fleet_upper_95_per_hour"] + top * 0.02, f"{v['fleet_per_hour']:.1f}\n(≤{v['fleet_upper_95_per_hour']:.1f})",
                 ha="center", va="bottom", color=ink, fontsize=7)
    for (lab, v), ls in zip(GUIDANCE.items(), ("--", "-")):
        ax2.axhline(v, color=ink2, ls=ls, lw=1)
        ax2.text(2.45, v, f"{v:g}/h {lab.split(' (')[0]}", va="bottom", ha="right", color=ink2, fontsize=6.5)
    ax2.set_xlim(-0.5, 2.5)
    ax2.set_ylim(0, top * 1.25)
    ax2.set_xticks(range(len(bars)), [b[1] for b in bars], fontsize=7)
    ax2.set_ylabel("notifications / h for 1 operator, 50 engines")
    ax2.set_title("50-engine projection (ALARM + UNCLASSIFIED)", fontsize=9, color=ink, loc="left")
    for sp in ("top", "right"):
        ax2.spines[sp].set_visible(False)

    ax3 = fig.add_subplot(gs[1, :])
    ax3.axis("off")
    det = res["fault_runs"]["detection"]
    cols = ["fault run", "physics alone", "classifier alone (p ≥ 0.8)", "two-witness ALARM", "UNCLASSIFIED"]
    rows = []
    for f, d in det.items():
        cell = lambda v: "n/a (held out)" if not v.get("kind") else (
            f"{v['successes']}/{v['n']}  {v['value']:.2f} [{v['ci_95'][0]:.2f}–{v['ci_95'][1]:.2f}]"
            + (f"  {v['latency_s_median']:.1f} s" if v["latency_s_median"] is not None else ""))
        rows.append([f] + [cell(d[k]) for k in ("physics_alone", "classifier_alone", "alarm", "unclassified")])
    t = ax3.table(cellText=rows, colLabels=cols, loc="upper center", cellLoc="left", colLoc="left")
    t.auto_set_font_size(False)
    t.set_fontsize(7)
    t.scale(1, 1.25)
    for (r_, c_), cell in t.get_celld().items():
        cell.set_edgecolor(grid)
        if r_ == 0:
            cell.set_text_props(color=ink, weight="bold")
    ax3.set_title("Fault runs (SIM 2.1, regimes on, robustness engines, 90 s of fault, injected at a 0.5 s block boundary): detected / runs, rate [Wilson 95 %], median latency",
                  fontsize=9, color=ink, loc="left")
    fig.text(0.01, 0.01, "Assumptions: " + "; ".join(p["assumptions"]), fontsize=6.5, color=ink2, wrap=True)
    fig.suptitle(f"Alarm budget · sim {res['sim_version']} (column A) and carOBD held-out (column C)", fontsize=11, color=ink, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.04, 1, 0.97))
    fig.savefig(FIG)
    print(f"-> {OUT}, {FIG}")


if __name__ == "__main__":
    main()
