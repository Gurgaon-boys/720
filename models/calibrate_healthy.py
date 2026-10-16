"""Healthy-model threshold per rpm band from ENGINE-TO-ENGINE spread on VALIDATION engines (L3).

Before L3 the threshold was the 99.9 % quantile of pooled validation WINDOWS. Healthy distances are set
mostly per engine and operating point (a run sits at its own level), so pooled windows hide the engines
that sit high: on a fresh range two engines gave almost all UNCLASSIFIED windows (soak_l2_fresh2.json).

Statistic per validation engine-run and band: the run's largest 2-window persisted distance,
max over consecutive window pairs of min(d_t, d_t+1) (both windows valid and in the band), which is
exactly what UNCLASSIFIED (2 consecutive anomalous windows) responds to.
Threshold per band = the MAX of that statistic over every validation engine-run in the band, so no
validation engine-run would raise UNCLASSIFIED. A new engine-run exceeds it with probability about
1 / (n_band + 1).

Validation engine-runs (never test, soak or robustness engines):
  * the classifier dataset's val engines 760-774: their healthy runs (1 + 4 per engine, 36 s each)
  * 80 extra validation engines 1200-1279 (eval.common SEEDS "val_thr"), 2 runs each of 3 min,
    recorded through the full live chain (python -m eval.record_runs val_thr)
Also reports, on the val_thr recordings, the replayed UNCLASSIFIED episodes/h before and after, and the
trade-off family (threshold = q-quantile of the per-run maxima, q = 0.5 ... 1.0, and 1.1-1.5 x the max).

Writes models/thresholds/healthy_model.json (read by models/witness.py).
Usage:  python -m models.calibrate_healthy
"""

from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from eval.chain import CACHE, checkpoint_thresholds, levels, load_rec, replay, total
from models.classifier import BANDS, band_of, load
from models.make_dataset import MANIFEST, OUT_DIR
from models.witness import HEALTHY_THRESHOLDS

ROOT = Path(__file__).resolve().parents[1]
FAMILY_Q = (0.5, 0.75, 0.9, 0.95, 0.975, 1.0)
FAMILY_X = (1.1, 1.2, 1.5)


def persisted_max(d: np.ndarray, bands: np.ndarray, band: str, counted: np.ndarray) -> float | None:
    pair = np.minimum(d[:-1], d[1:])
    ok = counted[1:] & counted[:-1] & (bands[1:] == band) & (bands[:-1] == band) & ~np.isnan(pair)
    return float(pair[ok].max()) if ok.sum() >= 10 else None


def dataset_val_runs(model) -> list[dict]:
    man = json.loads(MANIFEST.read_text())
    out = []
    for j in man["runs"]:
        if j["split"] not in ("val", "val_healthy") or j["fault"] != "healthy":
            continue
        z = np.load(OUT_DIR / f"{j['run_id']}.npz")
        meta = z["meta"]
        order = np.argsort(meta[:, 0])
        X, meta = z["X"].astype(float)[order], meta[order]
        d, _ = model.anomaly(X, meta[:, 1])
        d = np.where((meta[:, 2] > 0) & (meta[:, 3] > 0), d, np.nan)
        out.append({"source": "dataset_val", "engine": j["engine"], "run": j["run_id"], "d": d,
                    "bands": np.array([band_of(float(r)) for r in meta[:, 1]]), "counted": np.ones(len(d), bool)})
    return out


def val_thr_runs() -> tuple[list[dict], list[dict]]:
    paths = sorted(glob.glob(str(CACHE / "soak" / "val_thr" / "*.npz")))
    if len(paths) < 160:
        raise SystemExit(f"{len(paths)} / 160 val_thr runs recorded: run python -m eval.record_runs val_thr")
    recs = [load_rec(Path(p)) for p in paths]
    runs = [{"source": "val_thr", "engine": r["meta"]["range"] + f"#{r['meta']['i']}", "run": r["meta"]["i"],
             "d": np.where(r["valid"], r["d"], np.nan), "bands": np.array([band_of(float(x)) for x in r["rpm"]]),
             "counted": r["t_start"] >= r["meta"]["warmup_s"]} for r in recs]
    return runs, recs


def main():
    model = load()
    vruns, recs = val_thr_runs()
    runs = dataset_val_runs(model) + vruns
    stats = {b: [m for r in runs if (m := persisted_max(r["d"], r["bands"], b, r["counted"])) is not None]
             for b, _, _ in BANDS}
    thr = {b: float(max(v)) for b, v in stats.items()}
    hours = sum(r["meta"]["run_s"] for r in recs) / 3600

    def val_rate(th, uw=2):
        t = total([levels(replay(r, th, uw)) for r in recs])
        return {"unclassified_episodes": t["unclassified"]["episodes"], "unclassified_windows": t["unclassified"]["windows"],
                "per_hour": round(t["unclassified"]["episodes"] / hours, 3),
                "anomaly_flag_windows": t["anomaly_flags"]["windows"]}

    family = {}
    for q in FAMILY_Q:
        th = {b: float(np.quantile(v, q)) for b, v in stats.items()}
        family[f"q{q:g}"] = {"thresholds": th, "val_thr": val_rate(th)}
    for x in FAMILY_X:
        th = {b: v * x for b, v in thr.items()}
        family[f"max_x{x:g}"] = {"thresholds": th, "val_thr": val_rate(th)}
    ck = checkpoint_thresholds()
    doc = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "per band: max over VALIDATION engine-runs of the run's largest 2-window persisted Mahalanobis "
                  "distance (max over consecutive valid window pairs in the band of min(d_t, d_t+1))",
        "bands": thr,
        "previous": {"method": "(1 - 0.001) quantile of pooled VAL healthy windows (models/fast_v1.json)", "bands": ck},
        "engine_runs_per_band": {b: len(v) for b, v in stats.items()},
        "per_run_statistic_quantiles": {b: {q: round(float(np.quantile(v, q)), 3) for q in (0.5, 0.9, 0.95, 1.0)}
                                        for b, v in stats.items()},
        "validation": {"dataset_val": "engines 760-774, healthy runs (75 x 36 s)",
                       "val_thr": f"engines 1200-1279, 160 runs x 3 min = {hours:.2f} h through the full live chain"},
        "val_thr_unclassified": {"before (pooled windows, 1 window)": val_rate(None, 1),
                                 "pooled windows, 2 windows": val_rate(None, 2),
                                 "after (engine spread, 2 windows)": val_rate(thr, 2)},
        "family_on_validation": family,
        "note": "SIM 2.1, column A, validation engines only. The third untouched range and the valve-tick cost are "
                "measured afterwards with these thresholds fixed (eval/soak_l3.py, eval/valve_tick_cost.py)",
    }
    HEALTHY_THRESHOLDS.write_text(json.dumps(doc, indent=1))
    print(json.dumps({k: doc[k] for k in ("bands", "previous", "engine_runs_per_band", "val_thr_unclassified")}, indent=1))
    for k, v in family.items():
        print(f"  {k:8s} {', '.join(f'{b} {t:.2f}' for b, t in v['thresholds'].items())}  val {v['val_thr']['per_hour']:.2f} episodes/h")


if __name__ == "__main__":
    main()
