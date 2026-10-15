"""Split carOBD BY FILE into calibration and held-out -> data/splits/carobd_split.json.

    python data/carobd_split.py [--seed 720] [--heldout-frac 0.30]

Stratified by file type (idle / drive / live / ufpe / long): within each type the
files are shuffled with the seed and added to held-out while that brings the
type's held-out rows closer to --heldout-frac, with at least one held-out file per
type. Deterministic for a given seed and file list. Never split by window.
"""
from __future__ import annotations

import argparse
import random

import carobd
from _common import SPLITS, git_commit, utc_now_iso, write_json


def split(sizes: dict[str, int], types: dict[str, str], seed: int, frac: float) -> set[str]:
    rng = random.Random(seed)
    held: set[str] = set()
    for t in carobd.TYPES:
        files = sorted(f for f in sizes if types[f] == t)
        if len(files) < 2:
            raise SystemExit(f"type {t} has {len(files)} file(s); need >= 2 to hold one out")
        rng.shuffle(files)
        target = frac * sum(sizes[f] for f in files)
        got = 0
        for f in files:
            if not held.intersection(files) or abs(got + sizes[f] - target) < abs(got - target):
                held.add(f)
                got += sizes[f]
        if len(held.intersection(files)) == len(files):
            raise SystemExit(f"type {t}: every file ended up held out")
    return held


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=720)
    ap.add_argument("--heldout-frac", type=float, default=0.30)
    args = ap.parse_args()

    paths = carobd.list_files()
    if not paths:
        raise SystemExit(f"no CSVs in {carobd.CAROBD_DIR}; clone carOBD first")
    sizes, hours, types = {}, {}, {}
    for p in paths:
        df = carobd.load(p)
        sizes[p.name], hours[p.name], types[p.name] = len(df), carobd.duration_s(df) / 3600, carobd.file_type(p)

    held = split(sizes, types, args.seed, args.heldout_frac)
    order = {p.name: i for i, p in enumerate(paths)}
    calib = sorted((f for f in sizes if f not in held), key=order.get)
    heldout = sorted(held, key=order.get)

    summary = {}
    for t in carobd.TYPES:
        def agg(files):
            fs = [f for f in files if types[f] == t]
            return {"files": len(fs), "rows": sum(sizes[f] for f in fs), "hours": round(sum(hours[f] for f in fs), 3)}
        c, h = agg(calib), agg(heldout)
        summary[t] = {"calibration": c, "heldout": h, "heldout_row_frac": round(h["rows"] / (c["rows"] + h["rows"]), 3)}
    tot_rows = sum(sizes.values())
    held_rows = sum(sizes[f] for f in heldout)
    out = {
        "created_at": utc_now_iso(),
        "git_commit": git_commit(),
        "seed": args.seed,
        "produced_by": "data/carobd_split.py",
        "method": f"by file, stratified by type, greedy toward {args.heldout_frac:.0%} of each type's rows held out, "
                  ">= 1 held-out file per type",
        "source": "https://github.com/eron93br/carOBD (obdiidata/*.csv)",
        "calibration": calib,
        "heldout": heldout,
        "totals": {
            "calibration": {"files": len(calib), "rows": tot_rows - held_rows,
                            "hours": round(sum(hours[f] for f in calib), 3)},
            "heldout": {"files": len(heldout), "rows": held_rows, "hours": round(sum(hours[f] for f in heldout), 3)},
            "heldout_row_frac": round(held_rows / tot_rows, 3),
        },
        "by_type": summary,
    }
    path = SPLITS / "carobd_split.json"
    write_json(path, out)
    print(f"{'type':6} {'calib files':>11} {'held files':>10} {'held h':>7} {'held row %':>10}")
    for t, s in summary.items():
        print(f"{t:6} {s['calibration']['files']:11d} {s['heldout']['files']:10d} "
              f"{s['heldout']['hours']:7.2f} {100 * s['heldout_row_frac']:9.1f}%")
    T = out["totals"]
    print(f"{'total':6} {T['calibration']['files']:11d} {T['heldout']['files']:10d} "
          f"{T['heldout']['hours']:7.2f} {100 * T['heldout_row_frac']:9.1f}%")
    print(f"held out: {', '.join(heldout)}\nWrote {path.relative_to(SPLITS.parent.parent)}")


if __name__ == "__main__":
    main()
