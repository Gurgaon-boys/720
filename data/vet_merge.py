"""Vetting answers -> surviving clip list + agreement report.

    python data/vet_merge.py --raters diljit dipen       # two raters: both must agree (plan section 4)
    python data/vet_merge.py --raters diljit             # single rater (deviation, DECISIONS D60)
    python data/vet_merge.py --raters diljit --audit x   # single rater + x re-rated a sample: kappa on overlap

A clip survives only if the rater(s) said:
    is_passenger_car = y, plausibly_4cyl = y, speech_or_music = n
and, for knock clips, also is_detonation_knock = y. "unsure" never survives.

Writes
    data/vetting/vetted_clips.csv     clip_id, video_id, start, target_class (input to make_pools.py)
    eval/vetting_agreement.json       Cohen's kappa per question, raw agreement, surviving counts
"""
from __future__ import annotations

import argparse
import csv
import json
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


CORRECTIONS: dict[str, dict] = {}  # rater -> corrections applied (goes into the report)
FLIP = {"y": "n", "n": "y"}


def read_rater(path: Path) -> dict[str, dict]:
    """Answers per clip (last answer wins), with the rater's documented corrections applied.

    <rater>_corrections.json next to the CSV may flip a question's y/n answers given up to a time,
    e.g. when the rater confirms they read the question inverted. The raw CSV is never edited."""
    with open(path, newline="") as fh:
        rows = {r["clip_id"]: r for r in csv.DictReader(fh)}
    corr_path = path.with_name(f"{path.stem}_corrections.json")
    if corr_path.exists():
        corr = json.loads(corr_path.read_text())
        applied = {}
        for q, spec in corr.get("flip", {}).items():
            n = 0
            for r in rows.values():
                if r["rated_at"] <= spec["rated_at_lte"] and r.get(q) in FLIP:
                    r[q] = FLIP[r[q]]
                    n += 1
            applied[q] = {**spec, "answers_flipped": n}
        CORRECTIONS[path.stem] = {"file": corr_path.name, "flip": applied}
    return rows


def survives(r: dict, accept_unsure_4cyl: bool = False) -> bool:
    rule = KEEP_KNOCK if r["target_class"] == "knock" else KEEP
    for q, v in rule.items():
        ok = r[q] == v or (q == "plausibly_4cyl" and accept_unsure_4cyl and r[q] == "unsure")
        if not ok:
            return False
    return True


def agreement_table(a: dict, b: dict, ids: list[str]) -> dict:
    out = {}
    for q in QUESTIONS:
        qi = [c for c in ids if q != "is_detonation_knock" or a[c]["target_class"] == "knock"]
        xa, xb = [a[c][q] for c in qi], [b[c][q] for c in qi]
        k = cohen_kappa(xa, xb)
        out[q] = {"n": len(qi), "kappa": None if k is None else round(k, 3),
                  "raw_agreement": round(sum(x == y for x, y in zip(xa, xb)) / len(qi), 3) if qi else None,
                  "confusion": {f"{x}|{y}": n for (x, y), n in sorted(Counter(zip(xa, xb)).items())}}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raters", nargs="+", metavar="NAME",
                    help="one name (single-rater mode) or two (both must agree); default: CSVs in data/vetting/")
    ap.add_argument("--accept-unsure-4cyl", action="store_true",
                    help="also keep clips whose 4-cylinder answer is 'unsure' (DECISIONS D65); each kept clip "
                         "records its answer so results can be split strict / relaxed")
    ap.add_argument("--audit", metavar="NAME",
                    help="single-rater mode plus a second rater who re-rated a SAMPLE: their answers only "
                         "measure agreement (kappa) on the overlap; the primary rater decides survival")
    args = ap.parse_args()

    names = args.raters or sorted(p.stem for p in VET_DIR.glob("*.csv") if p != OUT_CLIPS)
    if args.audit:
        names = [n for n in names if n != args.audit]
    if len(names) not in (1, 2) or (args.audit and len(names) != 1):
        raise SystemExit(f"need one primary rater (+ optional --audit) or two raters; got {names}")
    data = {n: read_rater(VET_DIR / f"{n}.csv") for n in names}

    def keep(r: dict) -> bool:
        return survives(r, args.accept_unsure_4cyl)
    a = data[names[0]]

    if len(names) == 2:
        mode = "two_raters"
        b = data[names[1]]
        rated = sorted(set(a) & set(b))
        if not rated:
            raise SystemExit("the two raters have no clips in common")
        agreement = agreement_table(a, b, rated)
        kept = [c for c in rated if keep(a[c]) and keep(b[c])]
        rule_note = "both raters must match the rule; unsure fails"
    else:
        mode = "single_rater_with_audit" if args.audit else "single_rater"
        rated = sorted(a)
        kept = [c for c in rated if keep(a[c])]
        rule_note = ("ONE rater decides (deviation from plan section 4, which asks for two independent raters; "
                     "DECISIONS D60); unsure fails")
        agreement = None
        if args.audit:
            b = read_rater(VET_DIR / f"{args.audit}.csv")
            overlap = sorted(set(a) & set(b))
            if not overlap:
                raise SystemExit(f"audit rater {args.audit} shares no clips with {names[0]}")
            agreement = agreement_table(a, b, overlap)
            agreement["_audit"] = {"rater": args.audit, "overlap_clips": len(overlap),
                                   "survival_agreement": round(sum(keep(a[c]) == keep(b[c])
                                                                   for c in overlap) / len(overlap), 3)}

    classes = sorted({a[c]["target_class"] for c in rated})
    counts = {cls: {"rated": sum(a[c]["target_class"] == cls for c in rated),
                    "surviving": sum(a[c]["target_class"] == cls for c in kept),
                    "surviving_strict_4cyl_yes": sum(a[c]["target_class"] == cls and a[c]["plausibly_4cyl"] == "y"
                                                     for c in kept)} for cls in classes}

    OUT_CLIPS.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CLIPS, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["clip_id", "video_id", "start", "target_class", "plausibly_4cyl"])
        for c in kept:
            w.writerow([c, a[c]["video_id"], a[c]["start"], a[c]["target_class"], a[c]["plausibly_4cyl"]])

    report = {
        "created_at": utc_now_iso(), "git_commit": git_commit(), "produced_by": "data/vet_merge.py",
        "mode": mode, "raters": names, "audit_rater": args.audit,
        "rated_by_each": {n: len(d) for n, d in data.items()}, "rated": len(rated),
        "rule": {"idling/engine": KEEP, "knock": KEEP_KNOCK, "note": rule_note,
                 "accept_unsure_4cyl": args.accept_unsure_4cyl},
        "corrections": CORRECTIONS or None,
        "agreement": agreement, "counts": counts, "surviving_total": len(kept),
    }
    write_json(EVAL / "vetting_agreement.json", report)

    print(f"mode {mode}: raters {names}{' + audit ' + args.audit if args.audit else ''}, {len(rated)} clips rated")
    if agreement:
        print(f"\n{'question':22} {'n':>5} {'kappa':>6} {'agree':>6}")
        for q, s in agreement.items():
            if q.startswith("_"):
                continue
            k = "  n/a" if s["kappa"] is None else f"{s['kappa']:6.2f}"
            ag = "" if s["raw_agreement"] is None else f"{s['raw_agreement']:6.2f}"
            print(f"{q:22} {s['n']:5d} {k:>6} {ag:>6}")
    else:
        print("no agreement measured (single rater): report vetted counts as single-rater")
    for r, c in CORRECTIONS.items():
        print(f"corrections for {r}: " + ", ".join(f"{q} flipped on {v['answers_flipped']} answers" for q, v in c["flip"].items()))
    print(f"\n{'class':8} {'rated':>6} {'surviving':>9} {'(4-cyl yes)':>11}")
    for cls, s in counts.items():
        print(f"{cls:8} {s['rated']:6d} {s['surviving']:9d} {s['surviving_strict_4cyl_yes']:11d}")
    print(f"total surviving: {len(kept)} -> {OUT_CLIPS.relative_to(VET_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
