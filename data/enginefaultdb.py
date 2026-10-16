"""Load EngineFaultDB and write its inventory -> eval/enginefaultdb_inventory.json.

    git clone https://github.com/Leo-Thomas/EngineFaultDB data/raw/enginefaultdb
    python data/enginefaultdb.py

Source: Vergara, Ramos, Rivera-Campoverde, Rivas-Echeverria, "EngineFaultDB: A Novel Dataset for
Automotive Engine Fault Classification and Baseline Results", IEEE Access 11 (2023) 126155-126171,
doi:10.1109/ACCESS.2023.3331316. GPL-3.0: the CSV stays in data/raw/ (git-ignored), never committed.
REAL lab engine (Opel C14NE, 1.4 L 4-cyl petrol) on a test bench, faults induced on purpose.

Traps in the file (EngineFaultDB_Final.csv, 55,999 rows, label column "Fault"):

1. Each class is ONE contiguous block of rows (0, then 1, then 2, then 3). Class = recording session,
   so any split that mixes rows of a block leaks the session into the test set.
2. Inside a block, rows come in segments of ~1000 at one operating point; between segments RPM
   jumps by up to ~2900 rpm (row-to-row median ~40 rpm). `segments()` finds them.
3. Class 3 rows 0..13,999 are row-for-row copies of class 2 rows 1,000..14,999: MAP and TPS are
   bit-identical and the other columns differ by small zero-mean noise. `copy_check()` measures it.
4. The README gives counts but not label names. LABELS below (rich / lean / low voltage) is the
   team's reading, unconfirmed against the paper (paywalled); classes 1 and 2 match rich / lean
   chemistry (see eval/enginefaultdb.py).
5. Units are as the README states, but MAP has mean ~1.8 with "kPa" (an idling 1.4 L engine sits
   near 30-40 kPa), so MAP is most likely a sensor voltage. Treat MAP and TPS as raw signals.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from ._common import EVAL, RAW, git_commit, utc_now_iso, write_json
else:
    from _common import EVAL, RAW, git_commit, utc_now_iso, write_json

EFDB_DIR = RAW / "enginefaultdb"
CSV = EFDB_DIR / "EngineFaultDB_Final.csv"
SOURCE = "https://github.com/Leo-Thomas/EngineFaultDB (EngineFaultDB_Final.csv)"
CITATION = ("Vergara M., Ramos L., Rivera-Campoverde N. D., Rivas-Echeverria F. EngineFaultDB: A Novel "
            "Dataset for Automotive Engine Fault Classification and Baseline Results. IEEE Access 11 (2023) "
            "126155-126171. doi:10.1109/ACCESS.2023.3331316. Data licensed GPL-3.0.")
LABEL = "Fault"
LABELS = {0: "normal", 1: "rich_mixture", 2: "lean_mixture", 3: "low_voltage"}
LABELS_CONFIRMED = False
VARIABLES = ["MAP", "TPS", "Force", "Power", "RPM", "Consumption L/H", "Consumption L/100KM", "Speed",
             "CO", "HC", "CO2", "O2", "Lambda", "AFR"]
UNITS = {"MAP": "kPa (as stated; values ~0.6-5 suggest volts)", "TPS": "% (as stated)", "Force": "N",
         "Power": "kW", "RPM": "rpm", "Consumption L/H": "L/h", "Consumption L/100KM": "L/100 km",
         "Speed": "km/h", "CO": "% vol", "HC": "ppm", "CO2": "% vol", "O2": "% vol", "Lambda": "-",
         "AFR": "-"}
OPERATING = ["RPM", "TPS", "MAP", "Speed", "Power", "Force"]
COMBUSTION = ["Lambda", "AFR", "O2", "CO", "HC", "CO2"]
JUMP_RPM = 500.0  # |dRPM| between consecutive rows above this starts a new segment (p99 is ~230)


def load(path: str | Path = CSV) -> pd.DataFrame:
    """The CSV as given, plus `row` (position in the file). Checks columns and the block layout."""
    df = pd.read_csv(path)
    missing = [c for c in [LABEL, *VARIABLES] if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    df[LABEL] = df[LABEL].astype(int)
    df.insert(0, "row", np.arange(len(df)))
    return df


def class_blocks(df: pd.DataFrame) -> dict[int, tuple[int, int]]:
    """{label: (start_row, end_row)} with end exclusive. Raises if a class is not one contiguous block."""
    out = {}
    y = df[LABEL].to_numpy()
    for k in sorted(set(y)):
        idx = np.flatnonzero(y == k)
        if idx[-1] - idx[0] + 1 != len(idx):
            raise ValueError(f"class {k} is not one contiguous block")
        out[int(k)] = (int(idx[0]), int(idx[-1]) + 1)
    return out


def segments(df: pd.DataFrame, jump: float = JUMP_RPM) -> np.ndarray:
    """Segment id per row: a new segment at each class change or |dRPM| > jump."""
    rpm = df["RPM"].to_numpy(float)
    y = df[LABEL].to_numpy()
    new = np.r_[True, (np.abs(np.diff(rpm)) > jump) | (np.diff(y) != 0)]
    return np.cumsum(new) - 1


def copy_check(df: pd.DataFrame, a: int = 2, b: int = 3, lag: int = 1000) -> dict:
    """How closely class b's rows match class a's rows shifted by `lag`."""
    blocks = class_blocks(df)
    xa = df.iloc[blocks[a][0] + lag: blocks[a][1]][VARIABLES].to_numpy(float)
    xb = df.iloc[blocks[b][0]: blocks[b][1]][VARIABLES].to_numpy(float)
    n = min(len(xa), len(xb))
    xa, xb = xa[:n], xb[:n]
    per = {}
    for i, v in enumerate(VARIABLES):
        sd = xa[:, i].std()
        per[v] = {"identical_frac": round(float(np.mean(xa[:, i] == xb[:, i])), 4),
                  "corr": round(float(np.corrcoef(xa[:, i], xb[:, i])[0, 1]), 4),
                  "median_abs_diff_over_sd": round(float(np.median(np.abs(xa[:, i] - xb[:, i])) / sd), 4),
                  "mean_diff": round(float((xb[:, i] - xa[:, i]).mean()), 4)}
    return {"class_a": a, "class_b": b, "lag_rows": lag, "rows_compared": int(n),
            "class_b_rows_total": int(blocks[b][1] - blocks[b][0]),
            "rows_with_identical_MAP_and_TPS": int(np.sum((xa[:, 0] == xb[:, 0]) & (xa[:, 1] == xb[:, 1]))),
            "per_variable": per}


def repo_commit(path: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def inventory(df: pd.DataFrame) -> dict:
    blocks = class_blocks(df)
    seg = segments(df)
    rpm = df["RPM"].to_numpy(float)
    d = np.abs(np.diff(rpm))
    per_class = {}
    for k, (s, e) in blocks.items():
        sub = df.iloc[s:e]
        sseg = seg[s:e]
        segs = []
        for g in np.unique(sseg):
            m = sub[sseg == g]
            segs.append({"start_row": int(m.row.iloc[0]), "rows": len(m), "rpm_mean": round(float(m.RPM.mean()), 1),
                         "speed_mean": round(float(m.Speed.mean()), 1),
                         "lambda_mean": round(float(m.Lambda.mean()), 4)})
        inner = np.abs(np.diff(sub.RPM.to_numpy(float)))
        per_class[str(k)] = {
            "label": LABELS[k], "rows": int(e - s), "start_row": s, "end_row_exclusive": e,
            "sessions": 1, "segments": len(segs),
            "stats": {v: {"mean": round(float(sub[v].mean()), 4), "sd": round(float(sub[v].std()), 4),
                          "min": round(float(sub[v].min()), 4), "max": round(float(sub[v].max()), 4)}
                      for v in VARIABLES},
            "rpm_row_jump": {"median": round(float(np.median(inner)), 1),
                             "p99": round(float(np.percentile(inner, 99)), 1),
                             "max": round(float(inner.max()), 1), f"n_over_{JUMP_RPM:.0f}": int(np.sum(inner > JUMP_RPM))},
            "segment_list": segs,
        }
    return {
        "created_at": utc_now_iso(), "git_commit": git_commit(), "produced_by": "data/enginefaultdb.py",
        "source": SOURCE, "source_commit": repo_commit(EFDB_DIR), "citation": CITATION,
        "data_kind": "REAL lab engine (C14NE), 4 sessions, faults induced on a test bench",
        "rows": len(df), "variables": VARIABLES, "units_as_given": UNITS,
        "labels": {str(k): v for k, v in LABELS.items()}, "labels_confirmed_against_paper": LABELS_CONFIRMED,
        "labels_note": "README gives counts per label but no names; mapping is the team's, chemistry agrees for 1 and 2",
        "layout": {"blocks": {str(k): list(v) for k, v in blocks.items()},
                   "each_class_one_contiguous_block": True,
                   "sessions_total": len(blocks),
                   "segment_rule": f"new segment at |dRPM| > {JUMP_RPM:.0f} rpm between consecutive rows",
                   "segments_total": int(seg.max() + 1)},
        "rpm_row_jump_all": {"median": round(float(np.median(d)), 1), "p99": round(float(np.percentile(d, 99)), 1),
                             "max": round(float(d.max()), 1), f"n_over_{JUMP_RPM:.0f}": int(np.sum(d > JUMP_RPM))},
        "duplicate_rows_exact": int(df[VARIABLES].duplicated().sum()),
        "class3_copies_class2": copy_check(df),
        "per_class": per_class,
    }


def main() -> None:
    if not CSV.exists():
        raise SystemExit(f"{CSV} missing; clone EngineFaultDB first (see docstring)")
    df = load()
    inv = inventory(df)
    path = EVAL / "enginefaultdb_inventory.json"
    write_json(path, inv)
    print(f"{'class':5} {'label':13} {'rows':>6} {'segs':>4} {'RPM mean':>8} {'Lambda':>7}")
    for k, c in inv["per_class"].items():
        print(f"{k:5} {c['label']:13} {c['rows']:6d} {c['segments']:4d} {c['stats']['RPM']['mean']:8.0f} "
              f"{c['stats']['Lambda']['mean']:7.3f}")
    cc = inv["class3_copies_class2"]
    print(f"class 3 vs class 2 (+{cc['lag_rows']} rows): {cc['rows_with_identical_MAP_and_TPS']}/"
          f"{cc['rows_compared']} rows with identical MAP and TPS")
    print(f"Wrote {path.relative_to(EVAL.parent)}")


if __name__ == "__main__":
    main()
