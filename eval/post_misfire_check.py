"""Does the post-misfire recovery gate (and the bank-wide ALARM location) change any recorded simulator number?

Replays every recorded L3 run through the real rule layer with the gate off (`recovery=False`, the behaviour
before) and on, with the committed thresholds (models/thresholds/healthy_model.json, 2-window UNCLASSIFIED, D134
corroboration). Healthy soaks (val_thr, fresh2, fresh3, long) use their existing recordings. Fault runs use
`faults_trims`, the same 120 robustness runs re-recorded with the trims the gate reads (python -m eval.record_runs
faults_trims); every column they share with the original `faults` recording is checked to be identical first.
Reports level totals, windows whose decision changed, and per-fault detection (ALARM of the injected fault for the
trained faults, UNCLASSIFIED for valve tick) with latency. SIM 2.1, column A. Nothing is re-thresholded.

    python -m eval.post_misfire_check        -> eval/results/post_misfire_check.json
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

from eval.chain import CACHE, levels, load_rec, replay, total
from eval.precedence_check import states
from models.witness import HEALTHY_THRESHOLDS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "post_misfire_check.json"
RANGES = {"val_thr": "soak/val_thr", "soak_fresh2": "soak/soak_fresh2", "soak_fresh3": "soak/soak_fresh3",
          "soak_long": "soak/soak_long", "faults": "faults_trims/robustness"}


def same_as_original(path: str) -> bool:
    """A faults_trims recording must equal the original faults recording on every shared column."""
    orig = Path(path.replace("faults_trims", "faults"))
    if not orig.exists():
        return False
    a, b = load_rec(Path(path)), load_rec(orig)
    return a["meta"] == b["meta"] and all(np.array_equal(a[k], b[k], equal_nan=True) for k in b if k != "meta")


def first(rep, rec, mask) -> float | None:
    inj = rec["meta"]["inject_t"]
    i = np.flatnonzero(mask & rep["counted"] & (rec["t_start"] >= inj))
    return round(float(rec["t_end"][i[0]] - inj), 3) if len(i) else None


def one(path: str) -> dict:
    th = json.loads(HEALTHY_THRESHOLDS.read_text())["bands"]
    rec = load_rec(Path(path))
    old, new = replay(rec, th, 2, recovery=False), replay(rec, th, 2, recovery=True)
    so, sn = states(old), states(new)
    diff = Counter(f"{a} -> {b}" for a, b, c in zip(so, sn, old["counted"]) if a != b and c)
    out = {"meta": rec["meta"], "hours": rec["meta"]["run_s"] / 3600, "old": levels(old), "new": levels(new),
           "diff": dict(diff), "windows": int(old["counted"].sum())}
    f = rec["meta"].get("fault")
    if f:
        out["identical_to_original"] = same_as_original(path)
        def det(rep):
            if f == "valve_tick":
                return first(rep, rec, rep["unclassified"])
            return first(rep, rec, rep["alarm"] & np.array([x == f for x in rep["fault"]]))
        out["first"] = {"old": det(old), "new": det(new)}
        out["named_other_alarm"] = {k: int(any(a and x not in (None, f) for a, x in zip(r["alarm"], r["fault"])))
                                    for k, r in (("old", old), ("new", new))}
    return out


def main():
    res = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "label": "SIM 2.1", "column": "A",
           "what": __doc__.split("\n\n")[0], "ranges": {}}
    with ProcessPoolExecutor(int(os.environ.get("S720_WORKERS", "10"))) as ex:
        for name, sub in RANGES.items():
            paths = sorted(glob.glob(str(CACHE / sub / "*.npz")))
            rs = list(ex.map(one, paths, chunksize=4))
            diff = Counter()
            for r in rs:
                diff.update(r["diff"])
            lb, la = total([r["old"] for r in rs]), total([r["new"] for r in rs])
            entry = {"runs": len(rs), "engine_hours": round(sum(r["hours"] for r in rs), 3),
                     "levels_before": lb, "levels_after": la, "windows_changed": sum(diff.values()),
                     "changes": dict(diff.most_common()), "runs_changed": sum(1 for r in rs if r["diff"])}
            print(f"{name:12s} {len(rs):4d} runs {entry['engine_hours']:6.2f} h  windows changed {entry['windows_changed']}"
                  f" in {entry['runs_changed']} runs  UNCL ep {lb['unclassified']['episodes']} -> {la['unclassified']['episodes']}"
                  f"  ADV ep {lb['advisory']['episodes']} -> {la['advisory']['episodes']}"
                  f"  ALARM ep {lb['alarm']['episodes']} -> {la['alarm']['episodes']}")
            if name == "faults":
                entry["recordings_identical_to_original"] = sum(r["identical_to_original"] for r in rs)
                by = {}
                for r in rs:
                    m = r["meta"]
                    b = by.setdefault(f"{m['fault']} s{m['severity']:g}", {"n": 0, "detected_before": 0, "detected_after": 0,
                                                                         "latency_changed": 0, "lat_before": [], "lat_after": [],
                                                                         "other_fault_alarm_before": 0, "other_fault_alarm_after": 0})
                    b["n"] += 1
                    for k, key in (("old", "before"), ("new", "after")):
                        if r["first"][k] is not None:
                            b[f"detected_{key}"] += 1
                            b[f"lat_{key}"].append(r["first"][k])
                        b[f"other_fault_alarm_{key}"] += r["named_other_alarm"][k]
                    b["latency_changed"] += r["first"]["old"] != r["first"]["new"]
                for b in by.values():
                    for key in ("before", "after"):
                        lat = b.pop(f"lat_{key}")
                        b[f"latency_median_s_{key}"] = float(np.median(lat)) if lat else None
                entry["detection"] = {"metric": "first ALARM of the injected fault (trained faults) / UNCLASSIFIED (valve "
                                                "tick) after injection; other_fault_alarm = a run that raised an ALARM "
                                                "naming a different fault", **by}
                print(f"    recordings identical to the original 'faults' recording: "
                      f"{entry['recordings_identical_to_original']}/{len(rs)}")
                for k, b in by.items():
                    print(f"    {k}: {b}")
            if entry["changes"]:
                print("   ", dict(list(entry["changes"].items())[:8]))
            res["ranges"][name] = entry
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
