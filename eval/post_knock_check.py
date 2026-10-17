"""Does the post-knock recovery gate change any recorded simulator number?

Replays every recorded L3 run (the same ranges as eval/post_misfire_check.py: healthy soaks, and the 120 robustness
fault runs in their `faults_trims` recording) through the real rule layer with the post-knock gate off
(`knock_recovery=False`, the behaviour before) and on; the post-misfire gate is on in both. The recordings carry no
spark advance, so every post-knock window runs its full 90 s here: an upper bound on what the gate changes (live, it
ends once spark timing has settled). Reports level totals, windows whose decision changed, and per-fault detection.
SIM 2.1, column A. Nothing is re-thresholded.

    python -m eval.post_knock_check        -> eval/results/post_knock_check.json
"""

from __future__ import annotations

from pathlib import Path

from eval.post_misfire_check import ROOT, check, compare

OUT = ROOT / "eval" / "results" / "post_knock_check.json"


def one(path: str) -> dict:
    return compare(path, {"recovery": True, "knock_recovery": False}, {"recovery": True, "knock_recovery": True})


def main():
    check(one, OUT, __doc__)


if __name__ == "__main__":
    main()
