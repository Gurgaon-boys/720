"""Table B: knock and valve tick injected onto real AudioSet audio -> eval/table_b.json

    python -m models.real_audio.table_b

Column B = sim-to-real for ADDITIVE faults only (plan section 4, D9). Misfire and injector clog are
never injected onto real audio.

Base audio: vetted pool P2 only (P3 is never touched by the simulator; P1 is texture only), clips
whose measured bandwidth reaches 8 kHz (the first knock mode is ~6.4 kHz, D59).
Injection: models/real_audio/inject.py (sim 2.1's knock and valve models), knock p and valve lash
severity in {0.3, 0.6, 1.0}, level {0, +6, +12} dB re the clip's own 5-15 kHz RMS.

Evaluation: 5 folds BY SOURCE CLIP x 3 seeds (every window of a clip, healthy or injected, on one side).
  knock       RBF SVM on 1 s windows (FEATURES_B), trained on the training clips' healthy windows (0)
              and knock-injected windows (1 if the window holds a knock burst, else 0). Reported on
              the held-out clips: detection per severity x level, false positives on healthy windows.
  valve_tick  HELD OUT (D19, D71): never in any training set (asserted). A healthy model
              (Mahalanobis, Ledoit-Wolf) is fitted on the training clips' healthy windows minus a
              validation quarter of those clips, threshold = q0.99 of the validation healthy windows.
              Valve-tick windows of held-out clips are "unknown" if anomalous and not called knock,
              "mislabelled" if the knock SVM calls them knock, otherwise "missed".
Headline (pre-registered): level 0 dB, severities pooled, seed 0, Wilson 95% over windows
(windows of one clip are correlated: also read the clip counts).

--posthoc-levels (D98, chosen AFTER the pre-registered run was seen): the same method with levels
0..+30 dB, written to eval/table_b_levels_posthoc.json as a detectability curve. The pre-registered
eval/table_b.json stays the headline.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "data"))
from _common import git_commit, stable_hash, utc_now_iso, write_json  # noqa: E402

from eval.stats import proportion_summary  # noqa: E402
from models.obd.healthy import HealthyModel  # noqa: E402
from models.real_audio.features import FEATURES_B, SR, WIN_S, features_b, load  # noqa: E402
from models.real_audio.inject import inject  # noqa: E402

POOLS = REPO / "data" / "splits" / "audioset_pools.json"
MANIFEST = REPO / "eval" / "audioset_manifest.csv"
BANDWIDTH = REPO / "eval" / "audioset_bandwidth.csv"
OUT = REPO / "eval" / "table_b.json"

MIN_BW_HZ = 8000
SEVERITIES = (0.3, 0.6, 1.0)
LEVELS_DB = (0.0, 6.0, 12.0)
SEEDS = (0, 1, 2)
N_FOLDS = 5
VAL_FRAC = 0.25
ANOMALY_QUANTILE = 0.99
HEADLINE_LEVEL = 0.0


POSTHOC_LEVELS_DB = (0.0, 6.0, 12.0, 18.0, 24.0, 30.0)


def versions(levels=LEVELS_DB) -> list[tuple[str, float, float]]:
    return [("healthy", 0.0, 0.0)] + [(f, s, lv) for f in ("knock", "valve_tick") for s in SEVERITIES for lv in levels]


def _seed(*parts) -> int:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def clip_rows(args) -> list[dict]:
    clip_id, path, bw, levels = args
    x = load(path)
    out = []
    for fault, sev, lv in versions(levels):
        if fault == "healthy":
            y, times = x, np.zeros(0)
        else:
            y, times = inject(x, fault, sev, lv, bw, _seed(clip_id, fault, sev, lv))
        F, starts = features_b(y, bw)
        for row, s in zip(F, starts):
            has_event = bool(((times >= s) & (times < s + WIN_S)).any())
            out.append({"clip": clip_id, "fault": fault, "severity": sev, "level": lv, "x": row,
                        "event": has_event})
    return out


def run_seed(rows: list[dict], clips: list[str], seed: int) -> dict:
    rng = np.random.default_rng(seed)
    order = list(clips)
    rng.shuffle(order)
    X = np.array([r["x"] for r in rows])
    clip = np.array([r["clip"] for r in rows])
    fault = np.array([r["fault"] for r in rows])
    event = np.array([r["event"] for r in rows])
    knock_pred = np.full(len(rows), -1)
    anomalous = np.zeros(len(rows), bool)
    anomaly_score = np.full(len(rows), np.nan)
    for k in range(N_FOLDS):
        test_clips = set(order[k::N_FOLDS])
        te = np.isin(clip, list(test_clips))
        tr = ~te & (fault != "valve_tick")
        assert not (fault[tr] == "valve_tick").any(), "valve_tick must never be in training (D19, D71)"
        y_tr = (fault[tr] == "knock") & event[tr]
        svm = make_pipeline(StandardScaler(), SVC(C=1.0, gamma="scale", class_weight="balanced"))
        svm.fit(X[tr], y_tr.astype(int))
        knock_pred[te] = svm.predict(X[te])
        # healthy model: fit / validation split of the TRAINING clips, healthy windows only
        train_clips = [c for c in order if c not in test_clips]
        val_clips = {c for c in train_clips if stable_hash(f"b-val:{seed}:{c}", 720) < VAL_FRAC} or {train_clips[0]}
        fit_m = ~te & (fault == "healthy") & ~np.isin(clip, list(val_clips))
        val_m = ~te & (fault == "healthy") & np.isin(clip, list(val_clips))
        as_w = lambda m: [{**dict(zip(FEATURES_B, r)), "regime": "all"} for r in X[m]]
        hm = HealthyModel(FEATURES_B).fit(as_w(fit_m))
        hm.set_thresholds(as_w(val_m), ANOMALY_QUANTILE)
        idx = np.nonzero(te)[0]
        for i, w in zip(idx, as_w(te)):
            anomaly_score[i] = hm.distance(w)
            anomalous[i] = anomaly_score[i] > hm.threshold(w)
    return {"knock_pred": knock_pred, "anomalous": anomalous, "score": anomaly_score,
            "fault": fault, "event": event, "clip": clip,
            "severity": np.array([r["severity"] for r in rows]), "level": np.array([r["level"] for r in rows])}


def summarise(r: dict, levels=LEVELS_DB) -> dict:
    f, ev, kp, an = r["fault"], r["event"], r["knock_pred"], r["anomalous"]
    healthy = f == "healthy"
    out = {"knock_false_positive_on_healthy": proportion_summary(int(kp[healthy].sum()), int(healthy.sum())),
           "anomaly_flag_on_healthy": proportion_summary(int(an[healthy].sum()), int(healthy.sum())),
           "knock_detection": {}, "valve_tick": {}}
    for lv in levels:
        for s in (*SEVERITIES, "pooled"):
            m = (f == "knock") & ev & (r["level"] == lv) & (True if s == "pooled" else r["severity"] == s)
            out["knock_detection"][f"{lv:+.0f}dB_p{s}"] = proportion_summary(int(kp[m].sum()), int(m.sum()))
            v = (f == "valve_tick") & (r["level"] == lv) & (True if s == "pooled" else r["severity"] == s)
            unknown, mislab = an & (kp == 0), kp == 1
            out["valve_tick"][f"{lv:+.0f}dB_s{s}"] = {
                "unknown": proportion_summary(int((v & unknown).sum()), int(v.sum())),
                "mislabelled_knock": proportion_summary(int((v & mislab).sum()), int(v.sum())),
                "missed": proportion_summary(int((v & ~unknown & ~mislab).sum()), int(v.sum())),
            }
    vt = f == "valve_tick"
    out["valve_tick_anomaly_auroc_vs_healthy"] = round(float(roc_auc_score(
        np.r_[np.ones(vt.sum()), np.zeros(healthy.sum())], np.r_[r["score"][vt], r["score"][healthy]])), 3)
    # clip level at the headline: a held-out clip counts as detected if most of its knock windows are
    m = (f == "knock") & ev & (r["level"] == HEADLINE_LEVEL)
    det_clips = [np.mean(kp[m & (r["clip"] == c)]) >= 0.5 for c in np.unique(r["clip"][m])]
    out["headline_clip_level"] = proportion_summary(int(sum(det_clips)), len(det_clips))
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--posthoc-levels", action="store_true", help="D98 detectability curve (post-hoc)")
    args = ap.parse_args()
    levels = POSTHOC_LEVELS_DB if args.posthoc_levels else LEVELS_DB
    out_path = OUT.with_name("table_b_levels_posthoc.json") if args.posthoc_levels else OUT
    pools = json.loads(POOLS.read_text())
    with open(MANIFEST, newline="") as fh:
        man = {f"{r['video_id']}_{r['start']}": r for r in csv.DictReader(fh)}
    with open(BANDWIDTH, newline="") as fh:
        bw = {r["clip_id"]: int(r["bandwidth_hz"]) for r in csv.DictReader(fh)}
    clips = sorted(c["clip_id"] for c in pools["pools"]["P2"] if bw[c["clip_id"]] >= MIN_BW_HZ)

    t0 = time.time()
    with ProcessPoolExecutor() as ex:
        rows = [r for rs in ex.map(clip_rows, [(c, REPO / man[c]["path"], bw[c], levels) for c in clips]) for r in rs]
    print(f"{len(clips)} P2 clips, {len(rows)} windows ({len(versions(levels))} versions per clip) in {time.time() - t0:.0f} s")

    per_seed = {s: summarise(run_seed(rows, clips, s), levels) for s in SEEDS}
    hk = f"{HEADLINE_LEVEL:+.0f}dB_ppooled"
    hv = f"{HEADLINE_LEVEL:+.0f}dB_spooled"
    across = {
        "knock_detection_headline": [per_seed[s]["knock_detection"][hk]["rate"] for s in SEEDS],
        "knock_false_positive": [per_seed[s]["knock_false_positive_on_healthy"]["rate"] for s in SEEDS],
        "valve_tick_unknown_headline": [per_seed[s]["valve_tick"][hv]["unknown"]["rate"] for s in SEEDS],
    }
    s0 = per_seed[SEEDS[0]]
    write_json(out_path, {
        "created_at": utc_now_iso(), "git_commit": git_commit(),
        "produced_by": "python -m models.real_audio.table_b" + (" --posthoc-levels" if args.posthoc_levels else ""),
        "status": ("POST-HOC detectability curve (D98): levels extended after the pre-registered run; "
                   "eval/table_b.json is the headline") if args.posthoc_levels else "pre-registered (D97)",
        "column": "B (simulated additive faults injected onto real audio): knock and valve tick only",
        "base": {"pool": "P2 (single-rater vetted)", "min_bandwidth_hz": MIN_BW_HZ, "clips": len(clips),
                 "windows_per_version": "1 s, 0.5 s hop"},
        "injection": {"model": "sim 2.1 EngineParams knock / valve modes (models/real_audio/inject.py)",
                      "severities": list(SEVERITIES), "levels_db_re_clip_5_15k_rms": list(levels)},
        "split": f"{N_FOLDS} folds by source clip x seeds {list(SEEDS)}",
        "headline": {"level_db": HEADLINE_LEVEL, "severities": "pooled", "seed": SEEDS[0],
                     "knock_detection": s0["knock_detection"][hk],
                     "knock_false_positive_on_healthy": s0["knock_false_positive_on_healthy"],
                     "valve_tick_unknown": s0["valve_tick"][hv]["unknown"],
                     "valve_tick_mislabelled_knock": s0["valve_tick"][hv]["mislabelled_knock"],
                     "knock_clip_level": s0["headline_clip_level"]},
        "across_seeds": {k: {"per_seed": [round(v, 4) for v in vs], "mean": round(float(np.mean(vs)), 4),
                             "sd": round(float(np.std(vs, ddof=1)), 4)} for k, vs in across.items()},
        "per_seed": {str(s): v for s, v in per_seed.items()},
    })
    h = s0
    print(f"knock detection @0 dB pooled: {h['knock_detection'][hk]['rate']:.3f} {h['knock_detection'][hk]['wilson95']} "
          f"(n {h['knock_detection'][hk]['n']}); healthy FP {h['knock_false_positive_on_healthy']['rate']:.3f}")
    for lv in levels:
        print(f"  {lv:+.0f} dB: knock det " + " ".join(f"p{s}={h['knock_detection'][f'{lv:+.0f}dB_p{s}']['rate']:.2f}"
                                                    for s in SEVERITIES) +
              f" | valve unknown {h['valve_tick'][f'{lv:+.0f}dB_spooled']['unknown']['rate']:.2f}"
              f" mislabelled {h['valve_tick'][f'{lv:+.0f}dB_spooled']['mislabelled_knock']['rate']:.2f}")
    print(f"valve tick anomaly AUROC {h['valve_tick_anomaly_auroc_vs_healthy']}; across seeds: "
          f"{ {k: v['mean'] for k, v in json.loads(out_path.read_text())['across_seeds'].items()} }")
    print(f"Wrote {out_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
