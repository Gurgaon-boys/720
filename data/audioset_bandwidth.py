"""Effective audio bandwidth of every downloaded AudioSet clip -> eval/audioset_bandwidth.csv + .json

    python data/audioset_bandwidth.py

AudioSet audio went through YouTube's lossy codecs, so a clip stored at 48 kHz can carry nothing
above 16 kHz, or even 3 kHz. Knock energy sits at ~5-15 kHz (plan section 4), so the knock check and
any band-energy feature must know each clip's real bandwidth.

bandwidth_hz = highest frequency whose smoothed power spectral density (Welch, 4096-point, 0.5 kHz
smoothing done in linear power) is within DROP_DB of the clip's reference level (90th percentile of
the PSD in 0.3-5 kHz). Codec cut-offs are brick walls (40-100 dB drops), so the value is insensitive
to DROP_DB. The manifest is left untouched.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import welch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import EVAL, REPO, git_commit, utc_now_iso, write_json  # noqa: E402

DROP_DB = 50.0
KNOCK_BAND_HZ = (5000, 15000)
BINS = (3000, 5000, 8000, 12000, 15000, 18000, 20000, 22000, 24001)


def bandwidth(path: str) -> float:
    x, sr = sf.read(path, dtype="float32")
    f, p = welch(x, sr, nperseg=4096)
    k = max(1, int(round(500 / (f[1] - f[0]))))
    p = np.convolve(p, np.ones(k) / k, mode="same")  # zero padding in linear power can only lower the edges
    db = 10 * np.log10(p + 1e-30)
    ref = np.percentile(db[(f >= 300) & (f <= 5000)], 90)
    above = np.nonzero(db >= ref - DROP_DB)[0]
    return float(f[above.max()]) if len(above) else 0.0


def main() -> None:
    with open(EVAL / "audioset_manifest.csv", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["status"] == "ok" and (REPO / r["path"]).exists()]
    with ProcessPoolExecutor() as ex:
        bws = list(ex.map(bandwidth, [str(REPO / r["path"]) for r in rows], chunksize=32))

    out = EVAL / "audioset_bandwidth.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["clip_id", "video_id", "start", "target_class", "original_sr", "bandwidth_hz", "covers_knock_band"])
        for r, b in zip(rows, bws):
            w.writerow([f"{r['video_id']}_{r['start']}", r["video_id"], r["start"], r["target_class"],
                        r["original_sr"], int(round(b)), int(b >= KNOCK_BAND_HZ[1])])

    def hist(vals):
        c = Counter(next(e for e in BINS if v < e) for v in vals)
        lo = [0, *BINS[:-1]]
        return {f"{a // 1000}-{min(b, 24000) // 1000} kHz": c.get(b, 0) for a, b in zip(lo, BINS)}

    summary = {}
    for cls in sorted({r["target_class"] for r in rows}) + ["all"]:
        v = [b for r, b in zip(rows, bws) if cls in ("all", r["target_class"])]
        summary[cls] = {"clips": len(v), "median_hz": int(np.median(v)),
                        "covers_knock_band_15k": sum(b >= KNOCK_BAND_HZ[1] for b in v),
                        "below_5k": sum(b < KNOCK_BAND_HZ[0] for b in v), "histogram": hist(v)}
    write_json(EVAL / "audioset_bandwidth.json", {
        "created_at": utc_now_iso(), "git_commit": git_commit(), "produced_by": "data/audioset_bandwidth.py",
        "method": f"highest frequency within {DROP_DB:.0f} dB of the 0.3-5 kHz PSD reference (90th pct)",
        "knock_band_hz": list(KNOCK_BAND_HZ), "by_class": summary})

    print(f"{'class':7} {'clips':>5} {'median':>7} {'>=15 kHz':>9} {'<5 kHz':>7}")
    for cls, s in summary.items():
        print(f"{cls:7} {s['clips']:5d} {s['median_hz'] / 1000:6.1f}k {s['covers_knock_band_15k']:9d} {s['below_5k']:7d}")
    print("histogram (all):", summary["all"]["histogram"])
    print(f"Wrote {out.relative_to(REPO)} and eval/audioset_bandwidth.json")


if __name__ == "__main__":
    main()
