"""How much do the features depend on the simulator's chunk size with the hard regimes on? (D99 follow-up)

The live source steps 0.05 s chunks, the dataset and every evaluation 0.5 s. With regimes off the stream is
identical (tested); with regimes on, per-chunk regime draws differ. Same engine, seed and operating point,
stepped both ways; windows matched by engine cycle; for every feature of the classifier / healthy-model
vector: relative change |a - b| / |a| (median, p95) and the change in units of that feature's spread across
the run's windows. Downstream: physics-rule agreement, classifier-label agreement, healthy-distance change.

VALIDATION engines only (SEEDS "val_thr", engines 1200-1204), 30 s warm-up then 60 s.
Run twice: "before_fix" generates every step directly (EngineSim._step, the 2.1.0 behaviour); "after_fix" uses
EngineSim.step, which serves steps as slices of 0.5 s blocks when regimes are on.
Writes eval/results/chunk_size_check.json.   Usage:  python -m eval.chunk_size_check
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from dsp.rules import all_rules, load_all
from eval.common import eval_engine
from models.features import FEATURE_NAMES, StreamFeatures
from models.witness import ClassifierWitness
from sim import SIM_VERSION, EngineSim
from sim.params import Regimes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "chunk_size_check.json"
ENGINES, WARMUP_S, RUN_S = range(5), 30.0, 60.0


def run(i: int, step: float, legacy: bool = False) -> dict:
    params, seed = eval_engine("val_thr", i)
    rng = np.random.default_rng([seed, 0x50AC])
    sim = EngineSim(params, seed=seed, regimes=Regimes.eval(), purpose="eval")
    sim.set_operating_point(rpm_target=float(rng.uniform(700, 2950)), load_nm=float(rng.uniform(3, 45)))
    sf, th, wit = StreamFeatures(), load_all(), ClassifierWitness()
    out = {}
    gen = sim._step if legacy else sim.step       # legacy = before the block fix: each step generated directly
    for _ in range(int(round((WARMUP_S + RUN_S) / step))):
        for w in sf.push(gen(step)):
            if w.win.t_start < WARMUP_S:
                continue
            valid = bool(w.features["crank_valid"] and w.features["accel_valid"])
            o = wit(w.vector, w.features["rpm_mean"], valid, w.obd["n"])
            out[w.win.first_cycle] = {"v": w.vector, "valid": valid, "rules": [r["fired"] for r in all_rules(w.features, w.obd, th)],
                                      "label": o.models[0]["label"] if o.models else None, "d": o.distance}
    return out


def compare(legacy: bool) -> dict:
    rel, z, rules_same, label_same, dd, n_valid = [], [], [], [], [], 0
    per_engine = []
    for i in ENGINES:
        a, b = run(i, 0.5, legacy), run(i, 0.05, legacy)
        keys = sorted(k for k in set(a) & set(b) if a[k]["valid"] and b[k]["valid"])
        A = np.array([a[k]["v"] for k in keys])
        B = np.array([b[k]["v"] for k in keys])
        sd = A.std(0) + 1e-12
        r = np.abs(A - B) / np.maximum(np.abs(A), 1e-9)
        rel.append(r)
        z.append(np.abs(A - B) / sd)
        rules_same += [a[k]["rules"] == b[k]["rules"] for k in keys]
        label_same += [a[k]["label"] == b[k]["label"] for k in keys]
        dd += [abs(a[k]["d"] - b[k]["d"]) / a[k]["d"] for k in keys]
        n_valid += len(keys)
        per_engine.append({"i": i, "windows_matched": len(keys), "windows_0.5": len(a), "windows_0.05": len(b)})
    R, Z = np.vstack(rel), np.vstack(z)
    feats = {f: {"rel_median": float(np.median(R[:, j])), "rel_p95": float(np.quantile(R[:, j], 0.95)),
                 "sd_units_median": float(np.median(Z[:, j])), "sd_units_p95": float(np.quantile(Z[:, j], 0.95))}
             for j, f in enumerate(FEATURE_NAMES)}
    over = {f: v for f, v in feats.items() if v["rel_median"] > 0.01}
    res = {"windows_matched": n_valid, "per_engine": per_engine,
           "features": feats, "features_median_rel_change_over_1pct": sorted(over),
           "max_rel_change_any_feature_any_window": float(R.max()),
           "physics_rules_agree": float(np.mean(rules_same)), "classifier_label_agrees": float(np.mean(label_same)),
           "healthy_distance_rel_change": {"median": float(np.median(dd)), "p95": float(np.quantile(dd, 0.95))}}
    print(f"{'before fix (legacy)' if legacy else 'after fix'}: {n_valid} matched windows on 5 validation engines")
    for f, v in sorted(feats.items(), key=lambda kv: -kv[1]["rel_median"])[:12]:
        print(f"  {f:20s} rel median {v['rel_median']:.4f} p95 {v['rel_p95']:.4f}  sd-units median {v['sd_units_median']:.3f} p95 {v['sd_units_p95']:.3f}")
    print("over 1 % (median):", sorted(over))
    print("rules agree", res["physics_rules_agree"], "label agrees", res["classifier_label_agrees"], "distance", res["healthy_distance_rel_change"])
    return res


def main():
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sim_version": SIM_VERSION,
           "label": "SIM 2.1", "engines": "val_thr 1200-1204 (validation)", "regimes": "Regimes.eval()",
           "chunks_s": [0.5, 0.05], "before_fix": compare(True), "after_fix": compare(False)}
    OUT.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
