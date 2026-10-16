"""Injector-clog detectability through the full live chain at the system's fixed operating point
(the thresholds as committed: trim rule from carOBD calibration, classifier cut p 0.8, rule layer).

Engines from the robustness evaluation range (never trained or tuned on), regimes on, 30 s healthy
warm-up (full OBD history), then injector_clog on a random cylinder at severity s for 90 s.
Per s: fraction of runs reaching ALARM (two witnesses, right cylinder) and ADVISORY, with Wilson
intervals, and the latency from injection to the first ADVISORY / ALARM window.
The smallest detectable s = the smallest s whose ALARM detection rate is >= 0.9 (and >= 0.5).

Resumable per run (data/generated/clog_sweep/). Writes eval/results/clog_sweep.json.
Usage:  python -m eval.clog_sweep
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from backend.rule_layer import Evidence, RuleLayer
from dsp.rules import PER_LOCATION_THRESHOLD, all_rules, load_all
from eval.common import eval_engine, wilson
from models.features import StreamFeatures
from models.witness import ClassifierWitness
from sim import SIM_VERSION, EngineSim
from sim.params import Regimes

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "generated" / "clog_sweep"
OUT = ROOT / "eval" / "results" / "clog_sweep.json"
WORKERS = int(os.environ.get("S720_WORKERS", 10))
SEVERITIES = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6)
RUNS_PER_S = 10
WARMUP_S, FAULT_S = 30.0, 90.0


def one(job) -> dict:
    s, k = job
    path = CACHE / f"s{s:.2f}_{k:02d}.json"
    if path.exists():
        return json.loads(path.read_text())
    i = int(round(s * 100)) * 7 + k          # spread over the robustness engines
    params, seed = eval_engine("robustness", i)
    rng = np.random.default_rng([seed, 0xC106])
    rpm, load, cyl = float(rng.uniform(700, 2950)), float(rng.uniform(3, 45)), int(rng.integers(1, 5))
    sim = EngineSim(params, seed=seed, regimes=Regimes.eval(), purpose="eval")
    sim.set_operating_point(rpm_target=rpm, load_nm=load)
    sf, th, wit, layer = StreamFeatures(), load_all(), ClassifierWitness(), RuleLayer()
    first = {"ADVISORY": None, "ALARM": None}
    alarm_cyl = None
    injected = False
    for n in range(int((WARMUP_S + FAULT_S) / 0.5)):
        if not injected and n * 0.5 >= WARMUP_S:
            sim.inject("injector_clog", cyl, s)
            injected = True
        for w in sf.push(sim.step(0.5)):
            rules = all_rules(w.features, w.obd, th)
            o = wit(w.vector, w.features["rpm_mean"], w.features["crank_valid"] and w.features["accel_valid"], w.obd["n"])
            d = layer.step(Evidence(o.models, rules, o.score, PER_LOCATION_THRESHOLD, classifier_location=o.location))
            if w.win.t_start < WARMUP_S:
                continue
            st = d["state"]
            for lv in ("ADVISORY", "ALARM"):
                hit = st == lv and (d["fault"] == "injector_clog")
                if hit and first[lv] is None:
                    first[lv] = round(w.win.t_end - WARMUP_S, 2)
                    if lv == "ALARM":
                        alarm_cyl = d["location"]
    out = {"s": s, "k": k, "seed": seed, "rpm": round(rpm, 1), "load_nm": round(load, 2), "cylinder": cyl,
           "advisory_s": first["ADVISORY"], "alarm_s": first["ALARM"], "alarm_cylinder": alarm_cyl}
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def main():
    t0 = time.perf_counter()
    jobs = [(s, k) for s in SEVERITIES for k in range(RUNS_PER_S)]
    with Pool(WORKERS) as pool:
        runs = pool.map(one, jobs)
    per = {}
    for s in SEVERITIES:
        rs = [r for r in runs if r["s"] == s]
        n = len(rs)
        res = {"expected_trim_pct": round(100 * s / (4 - s), 2), "n_runs": n}
        for lv, key in (("advisory", "advisory_s"), ("alarm", "alarm_s")):
            lat = [r[key] for r in rs if r[key] is not None]
            k = len(lat)
            lo, hi = wilson(k, n)
            res[lv] = {"kind": "proportion", "successes": k, "n": n, "value": k / n, "ci_95": [lo, hi], "ci_method": "wilson_95",
                       "latency_s_median": float(np.median(lat)) if lat else None,
                       "latency_s_p95": float(np.quantile(lat, 0.95)) if lat else None}
        res["alarm_right_cylinder"] = sum(r["alarm_cylinder"] == r["cylinder"] for r in rs if r["alarm_s"] is not None)
        per[f"{s:.2f}"] = res
    smallest = {thr: next((float(s) for s in SEVERITIES if per[f"{s:.2f}"]["alarm"]["value"] >= thr), None) for thr in (0.5, 0.9)}
    doc = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sim_version": SIM_VERSION,
           "column": "A", "split": "robustness engines (unseen), regimes on",
           "operating_point": "committed thresholds: trim rule (carOBD calibration), classifier p >= 0.8, rule layer 2 windows",
           "fault_s": FAULT_S, "per_severity": per,
           "smallest_clog_alarm_rate_ge_0.5": smallest[0.5], "smallest_clog_alarm_rate_ge_0.9": smallest[0.9],
           "runs": runs, "wall_s": round(time.perf_counter() - t0, 1)}
    OUT.write_text(json.dumps(doc, indent=1))
    for s, r in per.items():
        print(f"  s={s}  trim {r['expected_trim_pct']:5.2f}%  ADVISORY {r['advisory']['successes']}/{r['n_runs']} "
              f"(median {r['advisory']['latency_s_median']} s)  ALARM {r['alarm']['successes']}/{r['n_runs']} "
              f"(median {r['alarm']['latency_s_median']} s, right cyl {r['alarm_right_cylinder']})")
    print(f"smallest s with ALARM rate >= 0.5: {smallest[0.5]}, >= 0.9: {smallest[0.9]}  ({doc['wall_s']:.0f} s)")


if __name__ == "__main__":
    main()
