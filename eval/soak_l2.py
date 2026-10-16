"""L2 healthy soak: the full live chain (sim v2 -> StreamFeatures -> 3 physics rules + fast classifier +
healthy model -> the real rule layer) on HEALTHY evaluation engines (soak seeds, never trained or tuned on).

Counts per hour, at every level (CONVENTIONS: false positives at every level):
  anomaly flags            anomaly score over its threshold
  classifier false faults  the classifier witness names a fault (majority, p >= P_MIN)
  physics rule windows     any physics rule fired
  ADVISORY / UNCLASSIFIED / ALARM   the rule layer's decision
and, for comparison, ADVISORY with optional 2-window persistence: a single-witness advisory counts only
when the same witness named the same fault in the previous window too (not the default; reported only).

Resumable: one JSON per run in data/generated/soak_l2/ (finished runs are skipped).
Writes eval/results/soak_l2.json.    Usage:  python -m eval.soak_l2 [--hours 2]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from backend.rule_layer import P_MIN, Evidence, RuleLayer, assess, classifier_label
from dsp.rules import PER_LOCATION_THRESHOLD, all_rules, load_all
from eval.common import SEEDS, eval_engine, rate_per_hour
from models.features import StreamFeatures
from models.witness import ClassifierWitness
from sim import SIM_VERSION, EngineSim
from sim.params import Regimes

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "generated" / "soak_l2"
OUT = ROOT / "eval" / "results" / "soak_l2.json"
WORKERS = int(os.environ.get("S720_WORKERS", 10))
RUN_S, WARMUP_S = 180.0, 30.0
LEVELS = ("windows", "anomaly_flags", "classifier_false_faults", "physics_windows", "advisory", "unclassified",
          "alarm_windows", "alarms_raised", "advisory_persist2")


def one_run(i: int) -> dict:
    path = CACHE / f"run_{i:03d}.json"
    if path.exists():
        return json.loads(path.read_text())
    params, seed = eval_engine("soak", i)
    rng = np.random.default_rng([seed, 0x50AC])
    rpm, load = float(rng.uniform(700, 2950)), float(rng.uniform(3, 45))
    sim = EngineSim(params, seed=seed, regimes=Regimes.eval(), purpose="eval")
    sim.set_operating_point(rpm_target=rpm, load_nm=load)
    sf, th, wit, layer = StreamFeatures(), load_all(), ClassifierWitness(), RuleLayer()
    c = {k: 0 for k in LEVELS}
    prev_single = None
    ms = []
    events = []
    for _ in range(int((WARMUP_S + RUN_S) / 0.5)):
        for w in sf.push(sim.step(0.5)):
            rules = all_rules(w.features, w.obd, th)
            t0 = time.perf_counter()
            o = wit(w.vector, w.features["rpm_mean"], w.features["crank_valid"] and w.features["accel_valid"])
            ms.append((time.perf_counter() - t0) * 1e3)
            ev = Evidence(o.models, rules, o.score, PER_LOCATION_THRESHOLD, classifier_location=o.location)
            had_alarm = layer.alarm is not None
            d = layer.step(ev)
            if w.win.t_start < WARMUP_S:
                continue
            a = assess(ev)
            c["windows"] += 1
            c["anomaly_flags"] += o.score is not None and o.score > PER_LOCATION_THRESHOLD
            cls = classifier_label(o.models)
            c["classifier_false_faults"] += cls not in (None, "healthy")
            c["physics_windows"] += any(r["fired"] for r in rules)
            c["advisory"] += d["state"] == "ADVISORY"
            c["unclassified"] += d["state"] == "UNCLASSIFIED"
            c["alarm_windows"] += d["state"] == "ALARM"
            c["alarms_raised"] += d["state"] == "ALARM" and not had_alarm
            single = (tuple(a.witnesses), a.fault) if a.kind == "advisory" else None
            c["advisory_persist2"] += d["state"] == "ADVISORY" and (single is None or single == prev_single)
            prev_single = single
            if d["state"] != "NORMAL":
                events.append([round(w.win.t_end, 2), d["state"], d["fault"], d["location"], a.kind,
                               [r["rule"] for r in rules if r["fired"]], cls])
            if layer.alarm is not None and not layer.alarm.acked:     # an operator would ACK; keep counting episodes
                layer.ack(layer.alarm.alarm_id, "soak", w.win.t_end)
    out = {"i": i, "seed": seed, "rpm": round(rpm, 1), "load_nm": round(load, 2), "hours": RUN_S / 3600, **c,
           "classifier_ms_median": float(np.median(ms)) if ms else None, "events": events[:50]}
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=2.0)
    args = ap.parse_args()
    n = int(round(args.hours * 3600 / RUN_S))
    t0 = time.perf_counter()
    with Pool(WORKERS) as pool:
        runs = pool.map(one_run, range(n))
    hours = sum(r["hours"] for r in runs)
    tot = {k: sum(r[k] for r in runs) for k in LEVELS}
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sim_version": SIM_VERSION,
           "source": "simulated healthy runs, soak engine seeds (never trained/tuned on), regimes on",
           "column": "A", "split": "soak engines (unseen)", "engines": SEEDS["soak"][0][:n] if n <= 40 else "soak, cycled",
           "runs": n, "hours": hours, "windows": tot["windows"],
           "per_hour": {k: rate_per_hour(tot[k], tot["windows"], hours) for k in LEVELS if k != "windows"},
           "note": "advisory_persist2 is the optional 2-window persistence for single-witness advisories, "
                   "measured only (the default is unchanged). alarms_raised counts latch episodes "
                   "(each auto-acknowledged so later ones can be counted).",
           "classifier_ms_median": float(np.median([r["classifier_ms_median"] for r in runs if r["classifier_ms_median"]])),
           "p_min": P_MIN, "runs_detail": runs, "wall_s": round(time.perf_counter() - t0, 1)}
    OUT.write_text(json.dumps(res, indent=1))
    for k in LEVELS[1:]:
        r = res["per_hour"][k]
        print(f"  {k:25s} {r['events']:6d}  {r['per_hour']:.2f}/h  (95% upper {r['upper_95']:.2f}/h)")
    print(f"{n} runs, {hours:.2f} h, {tot['windows']} windows ({res['wall_s']:.0f} s) -> {OUT}")


if __name__ == "__main__":
    main()
