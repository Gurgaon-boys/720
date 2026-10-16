"""Real-audio false positives on vetted AudioSet P3, and the weak knock check -> eval/audio_p3.json

    python -m models.real_audio.run_p3

Protocol (plan section 4 "AudioSet rules", section 13; CONVENTIONS "Splits"):
  * Pools come from data/splits/audioset_pools.json (vetted, single rater: D60/D64/D65), assigned by
    video id and pre-registered before vetting (D61).
  * The healthy model (Mahalanobis, Ledoit-Wolf, one regime: RPM is unknown on real clips) is
    fitted on P2 "fit" videos; its threshold is the q0.99 of P2 "validation" windows (P2 split by
    video hash). q0.99 rather than the OBD branch's q0.995 because validation has only a few hundred
    windows. P1 is never used (simulator texture only). P3 is evaluated once.
  * Each window goes through backend/rule_layer.py. On real audio no physics rule can run yet (the
    firing_event rules are not built) and the classifier needs crank angle and the accelerometer,
    so advisories and alarms are structurally zero; the honest number is the anomaly-flag rate.
  * Every P3 result is also reported on the strict subset (plausibly_4cyl = y, D65).

Weak knock check (column C, never training), method fixed before it was run on knock clips:
  * only clips whose measured bandwidth reaches 15 kHz (D59);
  * knock score = median 5-15 kHz envelope kurtosis (models/real_audio/features.py);
  * threshold = q0.95 of the knock score over healthy P2 clips (fit + validation);
  * detection rate on knock_check clips and false-positive rate on healthy P3 clips, Wilson 95%.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "data"))
from _common import git_commit, stable_hash, utc_now_iso, write_json  # noqa: E402

from backend.rule_layer import Evidence, RuleLayer  # noqa: E402
from eval.stats import episodes, proportion_summary, rate_summary  # noqa: E402
from models.obd.healthy import HealthyModel  # noqa: E402
from models.real_audio.features import FEATURES, HOP_S, WIN_S, clip_features, knock_score  # noqa: E402

POOLS = REPO / "data" / "splits" / "audioset_pools.json"
MANIFEST = REPO / "eval" / "audioset_manifest.csv"
BANDWIDTH = REPO / "eval" / "audioset_bandwidth.csv"
OUT = REPO / "eval" / "audio_p3.json"
THRESHOLDS = REPO / "models" / "thresholds" / "audio_healthy.json"

SEED = 720
VAL_FRAC = 0.30
ANOMALY_QUANTILE = 0.99
KNOCK_QUANTILE = 0.95
KNOCK_MIN_BW_HZ = 15000


def read_csv(path: Path, key: str = "clip_id") -> dict[str, dict]:
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if key == "clip_id" and "clip_id" not in rows[0]:
        for r in rows:
            r["clip_id"] = f"{r['video_id']}_{r['start']}"
    return {r[key]: r for r in rows}


def as_windows(x: np.ndarray) -> list[dict]:
    return [{**dict(zip(FEATURES, row)), "regime": "all"} for row in x]


def evaluate(clips: list[dict], feats: dict[str, np.ndarray], dur: dict[str, float], model: HealthyModel) -> dict:
    n_win = flags = uncl = adv = 0
    ep_flags = ep_uncl = ep_adv = alarms = 0
    clips_flagged = 0
    for c in clips:
        ws = as_windows(feats[c["clip_id"]])
        rl = RuleLayer()
        f, u, a, ids = [], [], [], set()
        for w in ws:
            d, thr = model.distance(w), model.threshold(w)
            dec = rl.step(Evidence(models=[], physics_rules=[], anomaly_score=d, anomaly_threshold=thr))
            f.append(d > thr)
            u.append(dec["state"] == "UNCLASSIFIED")
            a.append(dec["state"] == "ADVISORY")
            if dec["state"] == "ALARM":
                ids.add(dec["alarm_id"])
        n_win += len(ws)
        flags, uncl, adv = flags + sum(f), uncl + sum(u), adv + sum(a)
        ep_flags, ep_uncl, ep_adv, alarms = ep_flags + episodes(f), ep_uncl + episodes(u), ep_adv + episodes(a), alarms + len(ids)
        clips_flagged += any(f)
    H = sum(dur[c["clip_id"]] for c in clips) / 3600
    return {
        "clips": len(clips), "videos": len({c["video_id"] for c in clips}), "hours": round(H, 4), "windows": n_win,
        "levels": {
            "1_anomaly_flags": {"windows": proportion_summary(flags, n_win), "episodes": rate_summary(ep_flags, H),
                                "clips_with_any_flag": proportion_summary(clips_flagged, len(clips))},
            "2_unclassified": {"windows": proportion_summary(uncl, n_win), "episodes": rate_summary(ep_uncl, H)},
            "3_advisories": {**rate_summary(ep_adv, H), "note": "structurally zero: no physics rule runs on real audio "
                                                                "yet (firing_event versions not built)"},
            "4_classifier_false_faults": "not available: the classifier needs crank angle and the accelerometer",
            "5_alarms": {**rate_summary(alarms, H), "note": "structurally zero on audio-only data (no second witness)"},
        },
    }


def main() -> None:
    pools = json.loads(POOLS.read_text())
    if not pools.get("vetted"):
        raise SystemExit("audioset_pools.json is not the vetted pools file")
    man = read_csv(MANIFEST)
    bw = {k: int(v["bandwidth_hz"]) for k, v in read_csv(BANDWIDTH).items()}
    P2, P3, KN = pools["pools"]["P2"], pools["pools"]["P3"], pools["pools"]["knock_check"]
    all_clips = P2 + P3 + KN
    paths = {c["clip_id"]: REPO / man[c["clip_id"]]["path"] for c in all_clips}
    dur = {c["clip_id"]: float(man[c["clip_id"]]["duration_s"]) for c in all_clips}

    t0 = time.time()
    ids = [c["clip_id"] for c in P2 + P3]
    with ProcessPoolExecutor() as ex:
        feats = dict(zip(ids, ex.map(clip_features, [paths[i] for i in ids], chunksize=4)))
        kscore = dict(zip([c["clip_id"] for c in all_clips],
                          ex.map(knock_score, [paths[c["clip_id"]] for c in all_clips], chunksize=4)))
    print(f"features for {len(ids)} clips, knock scores for {len(all_clips)} in {time.time() - t0:.0f} s")

    val_v = {c["video_id"] for c in P2 if stable_hash(c["video_id"], SEED) < VAL_FRAC}
    fit_c = [c for c in P2 if c["video_id"] not in val_v]
    val_c = [c for c in P2 if c["video_id"] in val_v]
    model = HealthyModel(FEATURES).fit([w for c in fit_c for w in as_windows(feats[c["clip_id"]])])
    model.set_thresholds([w for c in val_c for w in as_windows(feats[c["clip_id"]])], ANOMALY_QUANTILE)

    p3 = evaluate(P3, feats, dur, model)
    p3_strict = evaluate([c for c in P3 if c.get("plausibly_4cyl") == "y"], feats, dur, model)
    val = evaluate(val_c, feats, dur, model)

    # weak knock check
    wide = lambda cs: [c for c in cs if bw[c["clip_id"]] >= KNOCK_MIN_BW_HZ]
    ref = [kscore[c["clip_id"]] for c in wide(P2)]
    k_thr = float(np.quantile(ref, KNOCK_QUANTILE))
    kn, hp3 = wide(KN), wide(P3)
    det = sum(kscore[c["clip_id"]] > k_thr for c in kn)
    fp = sum(kscore[c["clip_id"]] > k_thr for c in hp3)
    knock = {
        "column": "C (real audio, weak check, never training)",
        "detector": "median 5-15 kHz envelope kurtosis > threshold (stand-in: the system's knock rule needs crank angle)",
        "bandwidth_filter_hz": KNOCK_MIN_BW_HZ,
        "threshold": round(k_thr, 4), "threshold_from": f"q{KNOCK_QUANTILE} of {len(ref)} healthy P2 clips (>= 15 kHz)",
        "detection": proportion_summary(int(det), len(kn)),
        "false_positive_healthy_P3": proportion_summary(int(fp), len(hp3)),
        "excluded_band_limited": {"knock_check": len(KN) - len(kn), "P3": len(P3) - len(hp3)},
        "scores": {"knock_check_median": round(float(np.median([kscore[c["clip_id"]] for c in kn])), 3) if kn else None,
                   "healthy_P3_median": round(float(np.median([kscore[c["clip_id"]] for c in hp3])), 3) if hp3 else None},
    }

    meta = {"created_at": utc_now_iso(), "git_commit": git_commit(), "seed": SEED,
            "produced_by": "python -m models.real_audio.run_p3"}
    write_json(THRESHOLDS, {**meta, "features": FEATURES, "window": {"seconds": WIN_S, "hop_seconds": HOP_S},
                            "fit_clips": [c["clip_id"] for c in fit_c], "val_clips": [c["clip_id"] for c in val_c],
                            "anomaly": {"method": f"Mahalanobis, Ledoit-Wolf, threshold = validation q{ANOMALY_QUANTILE}",
                                        **model.to_json()},
                            "knock_score_threshold": knock["threshold"]})
    write_json(OUT, {
        **meta,
        "what": "False positives on real healthy engine audio (secondary bound) and the weak knock check. Column C.",
        "data": "AudioSet (HF mirror), vetted by ONE rater (D60, D64, D65); pools by video id (D61)",
        "fit": {"pool": "P2", "fit_clips": len(fit_c), "val_clips": len(val_c)},
        "episode_definition": f"run of consecutive {WIN_S:.0f} s windows ({HOP_S} s hop) in the same state within one clip",
        "stats": "rates: exact Poisson one-sided 95% upper bound; proportions: Wilson 95%",
        "P3": p3, "P3_strict_4cyl_yes": p3_strict, "validation_P2": val, "knock_check": knock,
    })

    for name, r in (("validation (P2)", val), ("P3 held-out", p3), ("P3 strict 4-cyl", p3_strict)):
        L = r["levels"]
        e = L["1_anomaly_flags"]["episodes"]
        print(f"{name:16} {r['clips']:3d} clips {r['hours'] * 60:5.1f} min  flags {e['count']:3d} episodes = "
              f"{e['per_hour']:.1f}/h (95% <= {e['upper95_per_hour']:.1f}/h); "
              f"{100 * L['1_anomaly_flags']['windows']['rate']:.1f}% of windows; "
              f"{L['1_anomaly_flags']['clips_with_any_flag']['k']} clips flagged")
    d, f_ = knock["detection"], knock["false_positive_healthy_P3"]
    print(f"knock check: detected {d['k']}/{d['n']} = {d['rate']:.2f} (95% {d['wilson95']}); "
          f"healthy P3 false positives {f_['k']}/{f_['n']} (95% {f_['wilson95']}); threshold {k_thr:.3f}")
    print(f"Wrote {OUT.relative_to(REPO)} and {THRESHOLDS.relative_to(REPO)}")


if __name__ == "__main__":
    main()
