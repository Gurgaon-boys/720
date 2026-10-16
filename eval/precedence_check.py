"""D131 check: does the corroboration rule (an anomaly flag within 3 windows of a trained-fault physics rule
is that fault's ADVISORY, not UNCLASSIFIED) change any simulator number?

Replays every recorded L3 run (eval/record_runs.py: val_thr, soak_fresh2, soak_fresh3, soak_long, and the
robustness fault runs) through the real rule layer twice, with the L3 thresholds (models/thresholds/
healthy_model.json, 2-window UNCLASSIFIED): corroborate_windows = 0 (before D131) and 3 (now). Reports the
level totals of both and every window whose decision state or fault differs. Nothing is re-thresholded.
SIM 2.1, column A. Writes eval/results/precedence_check.json.

    python -m eval.precedence_check
"""

from __future__ import annotations

import glob
import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from eval.chain import CACHE, first_time, levels, load_rec, replay, total
from models.witness import HEALTHY_THRESHOLDS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "precedence_check.json"
RANGES = {"val_thr": "soak/val_thr", "soak_fresh2": "soak/soak_fresh2", "soak_fresh3": "soak/soak_fresh3",
          "soak_long": "soak/soak_long", "faults": "faults/robustness"}


def states(rep) -> list:
    st = np.where(rep["alarm"], "ALARM", np.where(rep["unclassified"], "UNCLASSIFIED",
                                                 np.where(rep["advisory"], "ADVISORY", "NORMAL")))
    return [f"{s}:{f}" if s != "NORMAL" else s for s, f in zip(st, rep["fault"])]


def one(path: str) -> dict:
    th = json.loads(HEALTHY_THRESHOLDS.read_text())["bands"]
    rec = load_rec(Path(path))
    old, new = replay(rec, th, 2, corroborate_windows=0), replay(rec, th, 2, corroborate_windows=3)
    so, sn = states(old), states(new)
    diff = Counter(f"{a} -> {b}" for a, b, c in zip(so, sn, old["counted"]) if a != b and c)
    out = {"path": path, "meta": rec["meta"], "hours": rec["meta"]["run_s"] / 3600,
           "old": levels(old), "new": levels(new), "diff": dict(diff), "windows": int(old["counted"].sum())}
    if "fault" in rec["meta"] and rec["meta"].get("fault"):
        f = rec["meta"]["fault"]
        key = "unclassified" if f == "valve_tick" else "alarm_raised"
        out["first"] = {"old": first_time(old, rec, key), "new": first_time(new, rec, key)}
    return out


def main():
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "label": "SIM 2.1", "column": "A",
           "what": __doc__.split("\n\n")[0], "ranges": {}}
    workers = int(os.environ.get("S720_WORKERS", "10"))
    with ProcessPoolExecutor(workers) as ex:
        for name, sub in RANGES.items():
            paths = sorted(glob.glob(str(CACHE / sub / "*.npz")))
            rs = list(ex.map(one, paths, chunksize=4))
            diff = Counter()
            for r in rs:
                diff.update(r["diff"])
            entry = {"runs": len(rs), "engine_hours": round(sum(r["hours"] for r in rs), 3),
                     "windows": sum(r["windows"] for r in rs),
                     "levels_before": total([r["old"] for r in rs]), "levels_after": total([r["new"] for r in rs]),
                     "windows_changed": sum(diff.values()), "changes": dict(diff.most_common()),
                     "runs_changed": sum(1 for r in rs if r["diff"])}
            if name == "faults":
                by = {}
                for r in rs:
                    m = r["meta"]
                    k = f"{m['fault']} s{m['severity']:g}"
                    b = by.setdefault(k, {"n": 0, "detected_before": 0, "detected_after": 0, "latency_changed": 0})
                    b["n"] += 1
                    b["detected_before"] += r["first"]["old"] is not None
                    b["detected_after"] += r["first"]["new"] is not None
                    b["latency_changed"] += r["first"]["old"] != r["first"]["new"]
                entry["detection"] = {"metric": "ALARM raised (trained faults) / UNCLASSIFIED reached (valve tick)",
                                      **by}
            res["ranges"][name] = entry
            lb, la = entry["levels_before"], entry["levels_after"]
            print(f"{name:12s} {entry['runs']:4d} runs {entry['engine_hours']:6.2f} h  windows changed "
                  f"{entry['windows_changed']} in {entry['runs_changed']} runs  "
                  f"UNCL ep {lb['unclassified']['episodes']} -> {la['unclassified']['episodes']}  "
                  f"ADV ep {lb['advisory']['episodes']} -> {la['advisory']['episodes']}  "
                  f"ALARM ep {lb['alarm']['episodes']} -> {la['alarm']['episodes']}")
            if entry["changes"]:
                print("   ", dict(list(entry["changes"].items())[:8]))
            if name == "faults":
                for k, b in entry["detection"].items():
                    if isinstance(b, dict):
                        print(f"    {k}: {b}")
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
