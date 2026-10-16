"""Robustness to REAL ambient noise: ESC-50 mixed into P3 at fixed SNRs -> eval/noise_sweep_esc50.json

    python -m models.real_audio.noise_sweep

Plan section 13: "accuracy vs noise level ... public real ambient noise mixed at fixed levels: ESC-50 ...
split by source clip and named in the results". Question: does real background sound make the
real-audio pipeline raise false alarms on healthy engines?

* Engine audio: every clip of vetted pool P3 (held-out real test; ESC-50 is real noise, not the
  simulator, so P3's rule holds).
* Noise: ESC-50 `noise_test` files only (data/splits/esc50_split.json, split by source recording),
  excluding the engine-like categories. Each P3 clip gets two noise files chosen by a hash of the clip
  id, concatenated, resampled 44.1 -> 48 kHz and cut to the clip's length. The SAME noise files are
  used at every SNR, so levels are compared on paired data.
* SNR (dB, clip RMS vs scaled noise RMS over the whole clip): clean, 30, 20, 10, 5, 0.
* Detector: the real-audio healthy model as fitted for the P3 result (models/thresholds/audio_healthy.json),
  nothing refitted. Each window goes through backend/rule_layer.py.
* Reported per SNR: anomaly-flag windows (Wilson) and episodes per hour (exact Poisson bound),
  UNCLASSIFIED episodes, and the flag share per ESC-50 major group at the headline SNR (10 dB).
"""
from __future__ import annotations

import csv
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "data"))
from _common import git_commit, stable_hash, utc_now_iso, write_json  # noqa: E402

from backend.rule_layer import Evidence, RuleLayer  # noqa: E402
from eval.stats import episodes, proportion_summary, rate_summary  # noqa: E402
from models.obd.healthy import HealthyModel  # noqa: E402
from models.real_audio.features import FEATURES, HOP_S, SR, WIN_S, array_features, load  # noqa: E402

POOLS = REPO / "data" / "splits" / "audioset_pools.json"
ESC = REPO / "data" / "splits" / "esc50_split.json"
ESC_DIR = REPO / "data" / "raw" / "esc50"
MANIFEST = REPO / "eval" / "audioset_manifest.csv"
MODEL = REPO / "models" / "thresholds" / "audio_healthy.json"
OUT = REPO / "eval" / "noise_sweep_esc50.json"

SNRS = (None, 30.0, 20.0, 10.0, 5.0, 0.0)
HEADLINE_SNR = 10.0
NOISE_FILES_PER_CLIP = 2
GROUPS = {0: "animals", 1: "natural soundscapes & water", 2: "human non-speech", 3: "interior / domestic",
          4: "exterior / urban (engine excluded)"}


def noise_pool() -> tuple[list[str], dict[str, int]]:
    split = json.loads(ESC.read_text())
    with open(ESC_DIR / "meta" / "esc50.csv", newline="") as fh:
        meta = {r["filename"]: r for r in csv.DictReader(fh)}
    files = sorted(f for f in split["noise_test"] if meta[f]["category"] not in split["engine_like_categories"])
    return files, {f: int(meta[f]["target"]) // 10 for f in files}


def noise_for(clip_id: str, n: int, files: list[str]) -> tuple[np.ndarray, list[str]]:
    picks = [files[int(stable_hash(f"esc:{clip_id}:{k}", 720) * len(files))] for k in range(NOISE_FILES_PER_CLIP)]
    parts = []
    for f in picks:
        y, sr = sf.read(ESC_DIR / "audio" / f, dtype="float32")
        parts.append(resample_poly(y.astype(float), 160, 147) if sr == 44100 else y.astype(float))
    noise = np.concatenate(parts)
    noise = np.pad(noise, (0, max(0, n - len(noise))))[:n]
    return noise, picks


def clip_job(args) -> dict:
    clip_id, path, files = args
    x = load(path)
    noise, picks = noise_for(clip_id, len(x), files)
    px, pn = np.mean(x ** 2), np.mean(noise ** 2)
    out = {"clip_id": clip_id, "noise_files": picks, "seconds": len(x) / SR, "features": {}}
    for snr in SNRS:
        y = x if snr is None else x + noise * np.sqrt(px / (pn * 10 ** (snr / 10)))
        out["features"]["clean" if snr is None else f"{snr:.0f}"] = array_features(y)
    return out


def main() -> None:
    pools = json.loads(POOLS.read_text())
    with open(MANIFEST, newline="") as fh:
        man = {f"{r['video_id']}_{r['start']}": r for r in csv.DictReader(fh)}
    files, group = noise_pool()
    P3 = pools["pools"]["P3"]
    model = HealthyModel.from_json(json.loads(MODEL.read_text())["anomaly"])
    with ProcessPoolExecutor() as ex:
        res = list(ex.map(clip_job, [(c["clip_id"], REPO / man[c["clip_id"]]["path"], files) for c in P3], chunksize=2))

    hours = sum(r["seconds"] for r in res) / 3600
    levels, by_group = {}, {}
    for key in ["clean"] + [f"{s:.0f}" for s in SNRS if s is not None]:
        n_win = flags = 0
        ep_flags = ep_uncl = 0
        g_counts: dict[int, list[int]] = {}
        for r in res:
            rl, f_, u_ = RuleLayer(), [], []
            for row in r["features"][key]:
                w = {**dict(zip(FEATURES, row)), "regime": "all"}
                d, thr = model.distance(w), model.threshold(w)
                dec = rl.step(Evidence(models=[], physics_rules=[], anomaly_score=d, anomaly_threshold=thr))
                f_.append(d > thr)
                u_.append(dec["state"] == "UNCLASSIFIED")
            n_win, flags = n_win + len(f_), flags + sum(f_)
            ep_flags, ep_uncl = ep_flags + episodes(f_), ep_uncl + episodes(u_)
            for nf in r["noise_files"][:1]:  # attribute a clip to its first noise file's group
                g = g_counts.setdefault(group[nf], [0, 0])
                g[0], g[1] = g[0] + sum(f_), g[1] + len(f_)
        levels[key] = {"anomaly_flag_windows": proportion_summary(flags, n_win),
                       "anomaly_flag_episodes": rate_summary(ep_flags, hours),
                       "unclassified_episodes": rate_summary(ep_uncl, hours)}
        if key == f"{HEADLINE_SNR:.0f}":
            by_group = {GROUPS[g]: proportion_summary(k, n) for g, (k, n) in sorted(g_counts.items())}

    write_json(OUT, {
        "created_at": utc_now_iso(), "git_commit": git_commit(), "produced_by": "python -m models.real_audio.noise_sweep",
        "column": "C (real engine audio + real ambient noise)",
        "engine_audio": f"vetted P3, {len(P3)} clips, {hours * 60:.1f} min (single rater, D60)",
        "noise": {"source": "ESC-50 noise_test (data/splits/esc50_split.json), engine category excluded",
                  "files_available": len(files), "files_per_clip": NOISE_FILES_PER_CLIP,
                  "paired": "the same noise files at every SNR"},
        "detector": "models/thresholds/audio_healthy.json (P3 result, not refitted); backend/rule_layer.py at this commit",
        "snr_db": [s for s in SNRS if s is not None], "headline_snr_db": HEADLINE_SNR,
        "levels": levels, "flags_by_noise_group_at_headline": by_group,
        "window": {"seconds": WIN_S, "hop_seconds": HOP_S},
    })
    print(f"P3 {len(P3)} clips ({hours * 60:.1f} min) x {len(levels)} noise levels")
    for k, v in levels.items():
        w, e, u = v["anomaly_flag_windows"], v["anomaly_flag_episodes"], v["unclassified_episodes"]
        print(f"  {k:>5}{'' if k == 'clean' else ' dB'}: flags {100 * w['rate']:5.1f}% of windows "
              f"(Wilson {[round(100 * x, 1) for x in w['wilson95']]}), {e['per_hour']:6.1f} episodes/h "
              f"(<= {e['upper95_per_hour']:.1f}); UNCLASSIFIED {u['per_hour']:.1f}/h")
    print("  by noise group @10 dB:", {g: round(v["rate"], 3) for g, v in by_group.items()})
    print(f"Wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
