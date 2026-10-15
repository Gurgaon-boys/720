"""ESC-50 ambient noise: download and split 50/50 -> data/splits/esc50_split.json.

    python data/esc50_split.py            # clones github.com/karolpiczak/ESC-50 into data/raw/esc50 if missing

Split by SOURCE CLIP (meta/esc50.csv `src_file`, the original Freesound recording),
within each of the 50 categories: several 5 s files cut from one recording never
land on both sides. Groups are shuffled with the seed and each goes to whichever
side currently has fewer files of that category, so each category splits ~20/20.

ESC-50 is 44.1 kHz mono, 5 s per file, licence CC BY-NC 3.0. The "engine"
category is listed under engine_like_categories: leave it out when mixing noise
into engine audio, or report it separately.
"""
from __future__ import annotations

import argparse
import csv
import random
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import RAW, REPO, SPLITS, git_commit, utc_now_iso, write_json  # noqa: E402

ESC_DIR = RAW / "esc50"
URL = "https://github.com/karolpiczak/ESC-50"
ENGINE_LIKE = ["engine"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=720)
    args = ap.parse_args()

    meta = ESC_DIR / "meta" / "esc50.csv"
    if not meta.exists():
        subprocess.run(["git", "clone", "--depth", "1", URL, str(ESC_DIR)], check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ESC_DIR, text=True).strip()
    with open(meta, newline="") as fh:
        rows = list(csv.DictReader(fh))
    missing = [r["filename"] for r in rows if not (ESC_DIR / "audio" / r["filename"]).exists()]
    if missing:
        raise SystemExit(f"{len(missing)} audio files missing, e.g. {missing[:3]}")

    groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        groups[r["category"]][r["src_file"]].append(r["filename"])
    rng = random.Random(args.seed)
    side: dict[str, list[str]] = {"noise_train": [], "noise_test": []}
    per_cat = {}
    for cat in sorted(groups):
        srcs = sorted(groups[cat])
        rng.shuffle(srcs)
        n = {"noise_train": 0, "noise_test": 0}
        for s in srcs:
            dest = min(n, key=lambda k: (n[k], k == "noise_test"))  # fewer files first, ties -> train
            side[dest].extend(sorted(groups[cat][s]))
            n[dest] += len(groups[cat][s])
        per_cat[cat] = n
    for k in side:
        side[k].sort()

    src_of = {r["filename"]: (r["category"], r["src_file"]) for r in rows}
    leak = {src_of[f] for f in side["noise_train"]} & {src_of[f] for f in side["noise_test"]}
    if leak:
        raise SystemExit(f"source clips on both sides: {sorted(leak)[:5]}")

    out = {
        "created_at": utc_now_iso(),
        "git_commit": git_commit(),
        "seed": args.seed,
        "produced_by": "data/esc50_split.py",
        "source": {"url": URL, "commit": commit, "licence": "CC BY-NC 3.0",
                   "format": "44.1 kHz mono WAV, 5 s", "audio_dir": str((ESC_DIR / "audio").relative_to(REPO))},
        "method": "by source clip (src_file) within category, balanced 50/50 per category",
        "engine_like_categories": ENGINE_LIKE,
        "counts": {k: {"files": len(v), "source_clips": len({src_of[f] for f in v}),
                       "categories": len({src_of[f][0] for f in v})} for k, v in side.items()},
        "per_category": per_cat,
        **side,
    }
    path = SPLITS / "esc50_split.json"
    write_json(path, out)
    imbalance = Counter(abs(v["noise_train"] - v["noise_test"]) for v in per_cat.values())
    for k, c in out["counts"].items():
        print(f"{k:12} {c['files']:5d} files  {c['source_clips']:5d} source clips  {c['categories']} categories")
    print(f"per-category |train - test| file counts: {dict(sorted(imbalance.items()))}")
    print(f"Wrote {path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
