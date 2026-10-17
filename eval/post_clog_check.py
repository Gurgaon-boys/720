"""Does the post-clog recovery window change any recorded simulator number?

Replays every recorded L3 run (the same ranges as eval/post_misfire_check.py: healthy soaks, and the 120 robustness
fault runs in their `faults_trims` recording) through the real rule layer with the post-clog window off
(`clog_recovery=False`, the behaviour before) and on; the post-misfire and post-knock gates are on in both. Reports
level totals, windows whose decision changed, and per-fault detection. SIM 2.1, column A. Nothing is re-thresholded.

    python -m eval.post_clog_check         -> eval/results/post_clog_check.json
"""

from __future__ import annotations

from eval.post_misfire_check import ROOT, check, compare

OUT = ROOT / "eval" / "results" / "post_clog_check.json"


def one(path: str) -> dict:
    return compare(path, {"clog_recovery": False}, {"clog_recovery": True})


def main():
    check(one, OUT, __doc__)


if __name__ == "__main__":
    main()
