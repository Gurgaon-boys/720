"""Healthy UNCLASSIFIED episodes by operating point (RPM x load), from the committed long soak.

Reads eval/results/soak_l3_long.json only (no simulation, nothing refitted): each of its 400 healthy
SIM 2.1 runs holds one operating point (rpm, load_nm, drawn once per run in eval/chain.py) and its
UNCLASSIFIED episode count under each replayed setting (before / persist2 / after). This script bins
the runs on a fixed RPM x load grid and reports, per cell, the runs, engine-hours, episodes, runs
with any episode and the exact one-sided 95 % Poisson upper bound (eval/stats.py), for the
Results tab's operating map. The RPM edges include the healthy model's band edges (1,100 and
2,000 rpm, models/classifier.py BANDS); the grid was fixed before the counts were looked at.

Every run is one engine and one operating point, and one engine can produce several episodes in a
row, so episodes are not independent events: the Poisson bound is optimistic where one run holds
most of a cell's episodes. "runs_with_any" is reported beside it for that reason.

Writes eval/results/operating_map.json.
Usage (from the repo root):  python -m eval.operating_map
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from eval.stats import rate_summary

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "eval" / "results" / "soak_l3_long.json"
OUT = ROOT / "eval" / "results" / "operating_map.json"

RPM_EDGES = [700, 1100, 1500, 2000, 2500, 2950]   # soak draws rpm uniformly in [700, 2950]
LOAD_EDGES = [3, 13.5, 24, 34.5, 45]               # soak draws load_nm uniformly in [3, 45]
CONFIGS = ("before", "persist2", "after")


def bin_of(v: float, edges: list[float]) -> int:
    for i in range(len(edges) - 1):
        if edges[i] <= v < edges[i + 1] or (i == len(edges) - 2 and v == edges[-1]):
            return i
    raise ValueError(f"{v} outside {edges[0]}..{edges[-1]}")


def git_commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def main() -> None:
    src = json.loads(SRC.read_text())
    runs = src["runs_detail"]
    hours_per_run = src["run_s_counted"] / 3600
    if any(r["fault"] is not None for r in runs):
        raise ValueError("soak_l3_long.json holds a fault run; the map is for healthy runs only")
    per_run = {c: src["configs"][c]["unclassified_episodes_per_run"] for c in CONFIGS}
    for c, eps in per_run.items():
        if len(eps) != len(runs):
            raise ValueError(f"{c}: {len(eps)} episode counts for {len(runs)} runs")
        if sum(eps) != src["configs"][c]["levels"]["unclassified"]["episodes"]["events"]:
            raise ValueError(f"{c}: per-run episodes do not add up to the file's total")

    cells: dict[tuple[int, int], dict] = {}
    for i, r in enumerate(runs):
        key = (bin_of(r["rpm"], RPM_EDGES), bin_of(r["load_nm"], LOAD_EDGES))
        cell = cells.setdefault(key, {"runs": 0, **{c: {"episodes": 0, "runs_with_any": 0} for c in CONFIGS}})
        cell["runs"] += 1
        for c in CONFIGS:
            cell[c]["episodes"] += per_run[c][i]
            cell[c]["runs_with_any"] += per_run[c][i] > 0

    out_cells = []
    for (ri, li), cell in sorted(cells.items()):
        hours = cell["runs"] * hours_per_run
        out_cells.append({
            "rpm": [RPM_EDGES[ri], RPM_EDGES[ri + 1]],
            "load_nm": [LOAD_EDGES[li], LOAD_EDGES[li + 1]],
            "runs": cell["runs"],
            "engine_hours": round(hours, 4),
            **{c: {**rate_summary(cell[c]["episodes"], hours), "runs_with_any": int(cell[c]["runs_with_any"])} for c in CONFIGS},
        })

    total_hours = sum(c["engine_hours"] for c in out_cells)
    if abs(total_hours - src["configs"]["after"]["hours"]) > 1e-6:
        raise ValueError(f"cells hold {total_hours} h, the soak {src['configs']['after']['hours']} h")

    res = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "produced_by": "python -m eval.operating_map",
        "derived_from": {"file": "eval/results/soak_l3_long.json", "created_at": src["created_at"]},
        "sim_version": src["sim_version"],
        "label": src["label"],
        "column": src["column"],
        "split": src["split"],
        "metric": "healthy UNCLASSIFIED episodes per engine-hour (every one is a false alert: no fault is present)",
        "unit": "episodes per engine-hour",
        "bound_method": "poisson_exact_one_sided_95 (episodes within one run are not independent: optimistic)",
        "run_s_counted": src["run_s_counted"],
        "rpm_edges": RPM_EDGES,
        "load_nm_edges": LOAD_EDGES,
        "configs": {
            "before": "checkpoint healthy thresholds, 1-window UNCLASSIFIED (before L3)",
            "persist2": "checkpoint healthy thresholds, 2-window UNCLASSIFIED (D100)",
            "after": "D101 validation thresholds + 2-window UNCLASSIFIED: the committed setting (D106)",
        },
        "not_measured": "fuel use or efficiency: the simulator soak records no fuel flow, so this is not an efficiency map",
        "cells": out_cells,
    }
    OUT.write_text(json.dumps(res, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(out_cells)} cells, {total_hours:.2f} engine-h")


if __name__ == "__main__":
    main()
