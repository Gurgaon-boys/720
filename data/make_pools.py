"""Assign vetted AudioSet clips to pools P1 / P2 / P3 BY VIDEO ID -> data/splits/audioset_pools.json.

    python data/make_pools.py                  # after vetting (reads data/vetting/vetted_clips.csv)
    python data/make_pools.py --unvetted       # dev only: every downloaded clip -> audioset_pools_unvetted.json

Pools (plan section 4), for Idling / Engine clips:
    P1  simulator background texture only            ~40%
    P2  base audio for additive injection (training) ~30%
    P3  held-out real test, never touched by training or the simulator  ~30%
Knock clips never train: they go to a separate "knock_check" list.

A video's pool is a deterministic hash of (seed, video_id), so every segment of one
video lands in the same pool, and a video keeps its pool no matter which other
clips survive vetting. Any video that also has a knock clip is forced into P3 so
no training pool shares a video with the knock check.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import EVAL, REPO, SPLITS, git_commit, stable_hash, utc_now_iso, write_json  # noqa: E402

POOLS = (("P1", 0.40), ("P2", 0.30), ("P3", 0.30))
POOL_USE = {
    "P1": "simulator background texture only (high-passed residual, ambient, mic character); never firing",
    "P2": "real base clips for additive injection (knock, valve tick): training + validation",
    "P3": "held-out real test: false alarms per hour, demo replay; never touched by training or the simulator",
    "knock_check": "AudioSet 'Engine knocking' clips both raters agreed are detonation: weak out-of-domain check, never training",
}
VETTED = REPO / "data" / "vetting" / "vetted_clips.csv"
MANIFEST = EVAL / "audioset_manifest.csv"


def pool_for(video_id: str, seed: int) -> str:
    u, edge = stable_hash(video_id, seed), 0.0
    for name, frac in POOLS:
        edge += frac
        if u < edge:
            return name
    return POOLS[-1][0]


def read_clips(unvetted: bool) -> list[dict]:
    if unvetted:
        with open(MANIFEST, newline="") as fh:
            return [{"clip_id": f"{r['video_id']}_{r['start']}", "video_id": r["video_id"], "start": r["start"],
                     "target_class": r["target_class"]} for r in csv.DictReader(fh) if r["status"] == "ok"]
    if not VETTED.exists():
        raise SystemExit(f"{VETTED.relative_to(REPO)} not found: run data/vet_merge.py first (or --unvetted for dev)")
    with open(VETTED, newline="") as fh:
        return list(csv.DictReader(fh))


def assign(clips: list[dict], seed: int) -> tuple[dict[str, list[dict]], int]:
    knock_videos = {c["video_id"] for c in clips if c["target_class"] == "knock"}
    out: dict[str, list[dict]] = defaultdict(list)
    forced = 0
    for c in sorted(clips, key=lambda c: c["clip_id"]):
        entry = {"clip_id": c["clip_id"], "video_id": c["video_id"], "start": c["start"], "engine_tag": c["target_class"]}
        if c["target_class"] == "knock":
            out["knock_check"].append(entry)
        elif c["video_id"] in knock_videos:
            out["P3"].append(entry)
            forced += 1
        else:
            out[pool_for(c["video_id"], seed)].append(entry)
    return out, forced


def check_disjoint(pools: dict[str, list[dict]]) -> None:
    """No video in two of P1/P2/P3, and no knock-check video in a training pool (P1/P2)."""
    seen: dict[str, str] = {}
    for name in ("P1", "P2", "P3"):
        for e in pools.get(name, []):
            if seen.setdefault(e["video_id"], name) != name:
                raise SystemExit(f"video {e['video_id']} in both {seen[e['video_id']]} and {name}")
    clash = {e["video_id"] for e in pools.get("knock_check", [])} & {
        v for v, n in seen.items() if n in ("P1", "P2")}
    if clash:
        raise SystemExit(f"knock-check videos in training pools: {sorted(clash)[:5]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=720)
    ap.add_argument("--unvetted", action="store_true", help="dev only: pool every downloaded clip")
    args = ap.parse_args()

    clips = read_clips(args.unvetted)
    pools, forced = assign(clips, args.seed)
    check_disjoint(pools)

    names = [p for p, _ in POOLS] + ["knock_check"]
    counts = {n: {"clips": len(pools[n]), "videos": len({e["video_id"] for e in pools[n]}),
                  "by_engine_tag": dict(Counter(e["engine_tag"] for e in pools[n]))} for n in names}
    out = {
        "created_at": utc_now_iso(),
        "git_commit": git_commit(),
        "seed": args.seed,
        "produced_by": "data/make_pools.py",
        "input": "eval/audioset_manifest.csv (status ok, UNVETTED: dev only)" if args.unvetted
                 else "data/vetting/vetted_clips.csv",
        "vetted": not args.unvetted,
        "method": "pool = sha256(seed:video_id) mapped to [0,1) with cut points 0.40 / 0.70; "
                  "videos that also have a knock clip forced to P3",
        "fractions": dict(POOLS),
        "use": POOL_USE,
        "forced_to_P3": forced,
        "counts": counts,
        "pools": {n: pools[n] for n in names},
    }
    path = SPLITS / ("audioset_pools_unvetted.json" if args.unvetted else "audioset_pools.json")
    write_json(path, out)
    print(f"{'pool':12} {'clips':>6} {'videos':>6}  by tag")
    for n in names:
        print(f"{n:12} {counts[n]['clips']:6d} {counts[n]['videos']:6d}  {counts[n]['by_engine_tag']}")
    if args.unvetted:
        print("UNVETTED: development only, do not report numbers from these pools.")
    print(f"Wrote {path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
