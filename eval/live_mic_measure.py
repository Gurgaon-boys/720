"""Live-mic feasibility, measured offline FIRST: the existing real-audio path, unchanged, on the vetted clips.

    python -m eval.live_mic_measure          -> eval/results/live_mic_measure.json

Path (the one used for AudioSet P3 and the ESC-50 sweep, D96 / D127): models/real_audio/features.py
(1 s windows, 0.5 s hop, 0-4 kHz anomaly features, firing-event basis, no localisation) with the committed
healthy model and thresholds in models/thresholds/audio_healthy.json (NOT refitted), and the knock-band score
(median 5-15 kHz envelope kurtosis) against its committed threshold. Clips: the 25 knock_check and the 56 P3
clips of data/splits/audioset_pools.json (48 kHz). Every number is REAL AudioSet, column C, Wilson 95 %.
These clips were already read for the pre-registered weak knock check (eval/audio_p3.json); this re-reads
them to decide what the live-mic tab may claim. Nothing is fitted or tuned here.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "data"))

from backend.rule_layer import Evidence, RuleLayer  # noqa: E402
from eval.stats import proportion_summary  # noqa: E402
from models.obd.healthy import HealthyModel  # noqa: E402
from models.real_audio.features import FEATURES, clip_features, knock_score  # noqa: E402
from models.real_audio.run_p3 import BANDWIDTH, KNOCK_MIN_BW_HZ, MANIFEST, POOLS, THRESHOLDS, read_csv  # noqa: E402

OUT = REPO / "eval" / "results" / "live_mic_measure.json"


def per_clip(path: str) -> dict:
    th = json.loads(THRESHOLDS.read_text())
    model = HealthyModel.from_json(th["anomaly"])
    x = clip_features(path)
    ws = [{**dict(zip(FEATURES, r)), "regime": "all"} for r in x]
    rl, flags, uncl = RuleLayer(), [], []
    for w in ws:
        d, thr = model.distance(w), model.threshold(w)
        flags.append(d > thr)
        uncl.append(rl.step(Evidence([], [], d, thr))["state"] == "UNCLASSIFIED")
    k = knock_score(path)
    return {"windows": len(ws), "flags": int(sum(flags)), "any_flag": bool(any(flags)), "unclassified": bool(any(uncl)),
            "knock_score": round(k, 3), "knock_flag": bool(k > th["knock_score_threshold"])}


def summary(rows: list[dict]) -> dict:
    n, w = len(rows), sum(r["windows"] for r in rows)
    return {"clips": n, "windows": w,
            "knock_band_flag_clips": proportion_summary(sum(r["knock_flag"] for r in rows), n),
            "anomaly_flag_windows": proportion_summary(sum(r["flags"] for r in rows), w),
            "clips_with_any_anomaly_flag": proportion_summary(sum(r["any_flag"] for r in rows), n),
            "clips_reaching_unclassified": proportion_summary(sum(r["unclassified"] for r in rows), n),
            "knock_score_median": round(float(np.median([r["knock_score"] for r in rows])), 3) if rows else None}


def main():
    pools = json.loads(POOLS.read_text())
    man, bw = read_csv(MANIFEST), {k: int(v["bandwidth_hz"]) for k, v in read_csv(BANDWIDTH).items()}
    sets = {"knock_check": pools["pools"]["knock_check"], "P3_healthy": pools["pools"]["P3"]}
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "label": "REAL AudioSet, column C",
           "what": __doc__.split("\n\n")[0], "thresholds": "models/thresholds/audio_healthy.json (committed, not refitted)",
           "knock_threshold": json.loads(THRESHOLDS.read_text())["knock_score_threshold"], "sets": {}}
    with ProcessPoolExecutor(10) as ex:
        for name, clips in sets.items():
            rows = list(ex.map(per_clip, [str(REPO / man[c["clip_id"]]["path"]) for c in clips]))
            wide = [r for r, c in zip(rows, clips) if bw[c["clip_id"]] >= KNOCK_MIN_BW_HZ]
            res["sets"][name] = {"all": summary(rows), f"bandwidth_ge_{KNOCK_MIN_BW_HZ}": summary(wide)}
    OUT.write_text(json.dumps(res, indent=1))
    for name, s in res["sets"].items():
        for sub, v in s.items():
            f = lambda k: f"{v[k]['k'] if 'k' in v[k] else v[k].get('successes')}/{v[k].get('n')} = {v[k].get('rate', v[k].get('value'))}"
            print(name, sub, json.dumps({k: v[k] for k in v}, default=str)[:900])
    print(f"wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
