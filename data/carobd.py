"""Load carOBD CSVs correctly. Import this instead of calling pd.read_csv directly.

Two traps in the raw files (eron93br/carOBD, obdiidata/*.csv):

1. Every data row ends with a trailing comma, so rows have 28 fields against 27
   header names. Plain pd.read_csv then uses the first column as the index and
   shifts every column left by one (RPM lands in ENGINE_RUN_TIME, and so on).
   We parse with index_col=False.
2. The logger writes ~2 rows per second, not the 1 Hz the README states. The
   ENGINE_RUN_TIME column (spelled ENGINE_RUN_TINE in the header) is monotonic in
   whole seconds and is the time base; many consecutive rows repeat values
   because PIDs are polled round-robin.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

CAROBD_DIR = Path(__file__).resolve().parent / "raw" / "carobd" / "obdiidata"
TYPES = ("idle", "drive", "live", "ufpe", "long")
TYPE_NOTES = {
    "idle": "parked, engine on",
    "drive": "high-speed roads",
    "live": "fixed commute (work -> home)",
    "ufpe": "university campus, low speed",
    "long": "long trips",
}
TIME_COL = "ENGINE_RUN_TIME"
MAX_GAP_S = 30  # run-time steps larger than this (or negative) are treated as breaks


def file_type(path: str | Path) -> str:
    m = re.match(r"([a-z]+)\d+\.csv$", Path(path).name)
    if not m or m.group(1) not in TYPES:
        raise ValueError(f"unknown carOBD file type: {path}")
    return m.group(1)


def list_files(root: Path = CAROBD_DIR) -> list[Path]:
    def key(p: Path):
        m = re.match(r"([a-z]+)(\d+)", p.stem)
        return (m.group(1), int(m.group(2)))

    return sorted(root.glob("*.csv"), key=key)


def load(path: str | Path) -> pd.DataFrame:
    """DataFrame with clean column names (no ' ()', RUN_TINE typo fixed) and t_s (s since file start)."""
    df = pd.read_csv(path, skipinitialspace=True, index_col=False)
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    df.columns = [c.replace(" ()", "").strip().replace("RUN_TINE", "RUN_TIME") for c in df.columns]
    rt = df[TIME_COL].to_numpy(float)
    df["t_s"] = rt - rt[0]
    return df


def duration_s(df: pd.DataFrame) -> float:
    """Logged time from the run-time column, summing only normal forward steps."""
    dt = np.diff(df[TIME_COL].to_numpy(float))
    return float(dt[(dt > 0) & (dt <= MAX_GAP_S)].sum())
