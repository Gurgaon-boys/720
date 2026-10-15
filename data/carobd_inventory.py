"""Inventory of the carOBD dataset -> eval/carobd_inventory.json.

    git clone https://github.com/eron93br/carOBD data/raw/carobd
    python data/carobd_inventory.py

Per file: type, rows, columns, and duration two ways:
  * duration_s:        from the ENGINE_RUN_TIME column (the honest number)
  * duration_s_if_1hz: rows x 1 s, the README's "1 Hz" claim (~2x too long)
"""
from __future__ import annotations

import subprocess

import numpy as np

import carobd
from _common import EVAL, git_commit, utc_now_iso, write_json


def repo_commit(path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> None:
    files = carobd.list_files()
    if not files:
        raise SystemExit(f"no CSVs in {carobd.CAROBD_DIR}; clone carOBD first (see docstring)")
    rows_out, columns = [], None
    for p in files:
        df = carobd.load(p)
        cols = [c for c in df.columns if c != "t_s"]
        columns = columns or cols
        dur = carobd.duration_s(df)
        dt = np.diff(df[carobd.TIME_COL].to_numpy(float))
        rpm = df["ENGINE_RPM"]
        rows_out.append({
            "file": p.name,
            "type": carobd.file_type(p),
            "rows": len(df),
            "n_columns": len(cols),
            "columns_match_first_file": cols == columns,
            "has_timestamp_column": True,
            "duration_s": round(dur, 1),
            "duration_s_if_1hz": len(df),
            "rows_per_s": round(len(df) / dur, 3) if dur else None,
            "time_breaks": int(((dt < 0) | (dt > carobd.MAX_GAP_S)).sum()),
            # how often RPM actually takes a new value: the per-PID refresh rate, below the row rate
            "rpm_updates_per_s": round(float((rpm.diff() != 0).sum() / dur), 3) if dur else None,
            "rpm_median": round(float(rpm.median()), 1),
            "rpm_min": round(float(rpm.min()), 1),
            "rpm_max": round(float(rpm.max()), 1),
            "speed_kmh_max": round(float(df["VEHICLE_SPEED"].max()), 1),
        })

    by_type = {}
    for t in carobd.TYPES:
        sel = [r for r in rows_out if r["type"] == t]
        by_type[t] = {
            "description": carobd.TYPE_NOTES[t],
            "files": len(sel),
            "rows": sum(r["rows"] for r in sel),
            "hours": round(sum(r["duration_s"] for r in sel) / 3600, 3),
            "hours_if_1hz": round(sum(r["rows"] for r in sel) / 3600, 3),
        }
    total_s = sum(r["duration_s"] for r in rows_out)
    moving = [r["rpm_updates_per_s"] for r in rows_out if r["type"] != "idle" and r["rpm_updates_per_s"]]
    total_rows = sum(r["rows"] for r in rows_out)
    out = {
        "created_at": utc_now_iso(),
        "git_commit": git_commit(),
        "source": {"url": "https://github.com/eron93br/carOBD", "commit": repo_commit(carobd.CAROBD_DIR.parent),
                   "vehicle": "2014 Toyota Etios, 1496 cc"},
        "produced_by": "data/carobd_inventory.py",
        "notes": [
            "Data rows end with a trailing comma (28 fields vs 27 header names); parsed with index_col=False. "
            "A plain pd.read_csv shifts every column by one. Use data/carobd.py:load().",
            "Header spells the run-time column ENGINE_RUN_TINE; renamed to ENGINE_RUN_TIME.",
            "Hours come from the ENGINE_RUN_TIME column (monotonic, whole seconds). The logger writes "
            f"~{total_rows / total_s:.2f} rows/s, so rows/3600 (the README's 1 Hz) overstates the duration about 2x.",
            "Many consecutive rows repeat values: PIDs are polled round-robin, so each variable refreshes "
            f"at ~{float(np.median(moving)):.2f} Hz (median RPM value changes per second in non-idle files), "
            "not 1 Hz. A 30 s window holds ~15 fresh samples per PID.",
        ],
        "columns": columns,
        "totals": {
            "files": len(rows_out),
            "rows": total_rows,
            "hours": round(total_s / 3600, 3),
            "hours_if_1hz": round(total_rows / 3600, 3),
            "rows_per_s": round(total_rows / total_s, 3),
            "pid_refresh_hz_median_nonidle": round(float(np.median(moving)), 3),
        },
        "by_type": by_type,
        "files": rows_out,
    }
    path = EVAL / "carobd_inventory.json"
    write_json(path, out)

    print(f"{'type':6} {'files':>5} {'rows':>7} {'hours':>7} {'(if 1 Hz)':>10}")
    for t, s in by_type.items():
        print(f"{t:6} {s['files']:5d} {s['rows']:7d} {s['hours']:7.2f} {s['hours_if_1hz']:10.2f}")
    print(f"{'total':6} {len(rows_out):5d} {total_rows:7d} {total_s / 3600:7.2f} {total_rows / 3600:10.2f}")
    print(f"\nTotal carOBD duration: {total_s / 3600:.2f} h (from ENGINE_RUN_TIME; "
          f"{total_rows / total_s:.2f} rows/s, not 1 Hz). Each PID refreshes at ~{float(np.median(moving)):.2f} Hz.\n"
          f"Wrote {path.relative_to(EVAL.parent)}")
    mismatched = [r["file"] for r in rows_out if not r["columns_match_first_file"]]
    if mismatched:
        print("WARNING: column sets differ in", mismatched)


if __name__ == "__main__":
    main()
