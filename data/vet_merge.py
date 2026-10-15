"""Merge two raters' vetting answers -> surviving clip list + agreement report.

    python data/vet_merge.py                       # the two CSVs in data/vetting/
    python data/vet_merge.py --raters diljit dipen

A clip survives only if BOTH raters said:
    is_passenger_car = y, plausibly_4cyl = y, speech_or_music = n
and, for knock clips, also is_detonation_knock = y. "unsure" never survives.

Writes
    data/vetting/vetted_clips.csv     clip_id, video_id, start, target_class (input to make_pools.py)
    eval/vetting_agreement.json       Cohen's kappa per question, raw agreement, surviving counts
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import EVAL, git_commit, utc_now_iso, write_json  # noqa: E402
from vet import QUESTIONS, VET_DIR  # noqa: E402

KEEP = {"is_passenger_car": "y", "plausibly_4cyl": "y", "speech_or_music": "n"}
KEEP_KNOCK = {**KEEP, "is_detonation_knock": "y"}
OUT_CLIPS = VET_DIR / "vetted_clips.csv"


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's kappa for two raters over the same items; None if undefined (no items or no variation)."""
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / n**2
    return None if pe == 1 else (po - pe) / (1 - pe)


def read_rater(path: Path) -> dict[str, dict]:
    with open(path, newline="") as fh:
        return {r["clip_id"]: r for r in csv.DictReader(fh)}  # last answer per clip wins


def survives(r: dict) -> bool:
    rule = KEEP_KNOCK if r["target_class"] == "knock" else KEEP
    return all(r[q] == v for q, v in rule.items())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raters", nargs=2, metavar=("A", "B"))
    args = ap.parse_args()

    if args.raters:
        paths = [VET_DIR / f"{r}.csv" for r in args.raters]
    else:
        paths = sorted(p for p in VET_DIR.glob("*.csv") if p != OUT_CLIPS)
        if len(paths) != 2:
            raise SystemExit(f"expected 2 rater CSVs in {VET_DIR}, found {[p.name for p in paths]}; use --raters")
    a, b = (read_rater(p) for p in paths)
    names = [p.stem for p in paths]
    both = sorted(set(a) & set(b))
    if not both:
        raise SystemExit("the two raters have no clips in common")

    agreement = {}
    for q in QUESTIONS:
        ids = [c for c in both if q != "is_detonation_knock" or a[c]["target_class"] == "knock"]
        xa, xb = [a[c][q] for c in ids], [b[c][q] for c in ids]
        k = cohen_kappa(xa, xb)
        agreement[q] = {"n": len(ids), "kappa": None if k is None else round(k, 3),
                        "raw_agreement": round(sum(x == y for x, y in zip(xa, xb)) / len(ids), 3) if ids else None,
                        "confusion": {f"{x}|{y}": n for (x, y), n in sorted(Counter(zip(xa, xb)).items())}}

    kept = [c for c in both if survives(a[c]) and survives(b[c])]
    classes = sorted({a[c]["target_class"] for c in both})
    counts = {cls: {"rated_by_both": sum(a[c]["target_class"] == cls for c in both),
                    "kept_by_" + names[0]: sum(a[c]["target_class"] == cls and survives(a[c]) for c in both),
                    "kept_by_" + names[1]: sum(b[c]["target_class"] == cls and survives(b[c]) for c in both),
                    "surviving": sum(a[c]["target_class"] == cls for c in kept)} for cls in classes}

    OUT_CLIPS.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CLIPS, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["clip_id", "video_id", "start", "target_class"])
        for c in kept:
            w.writerow([c, a[c]["video_id"], a[c]["start"], a[c]["target_class"]])

    report = {
        "created_at": utc_now_iso(), "git_commit": git_commit(), "produced_by": "data/vet_merge.py",
        "raters": names, "rated_by_each": {names[0]: len(a), names[1]: len(b)}, "rated_by_both": len(both),
        "rule": {"idling/engine": KEEP, "knock": KEEP_KNOCK, "note": "both raters must match the rule; unsure fails"},
        "agreement": agreement, "counts": counts, "surviving_total": len(kept),
    }
    write_json(EVAL / "vetting_agreement.json", report)

    print(f"raters {names[0]} ({len(a)}) and {names[1]} ({len(b)}): {len(both)} clips rated by both")
    print(f"\n{'question':22} {'n':>5} {'kappa':>6} {'agree':>6}")
    for q, s in agreement.items():
        k = "  n/a" if s["kappa"] is None else f"{s['kappa']:6.2f}"
        ag = "" if s["raw_agreement"] is None else f"{s['raw_agreement']:6.2f}"
        print(f"{q:22} {s['n']:5d} {k:>6} {ag:>6}")
    print(f"\n{'class':8} {'both':>5} {'surviving':>9}")
    for cls, s in counts.items():
        print(f"{cls:8} {s['rated_by_both']:5d} {s['surviving']:9d}")
    print(f"total surviving: {len(kept)} -> {OUT_CLIPS.relative_to(VET_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
