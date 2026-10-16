"""Split EngineFaultDB -> data/splits/enginefaultdb_split.json (written and committed before training).

    python data/enginefaultdb_split.py [--seed 720]

Two splits, both recorded here so eval/enginefaultdb.py cannot pick its own:

  temporal  inside each class block: first 70% of rows train, next 5% dropped (gap), last 25% test.
            Rows are never shuffled. Because each class is one session, this still cannot separate
            fault from session; it only stops neighbouring rows of one segment landing on both sides.
  random    rows shuffled with --seed, 25% test stratified by class (what most published numbers use;
            kept to show the leak). Stored as the seed plus a sha256 of the sorted test rows.
"""
from __future__ import annotations

import argparse
import hashlib
import math

import numpy as np

if __package__:
    from . import enginefaultdb as efdb
    from ._common import SPLITS, git_commit, utc_now_iso, write_json
else:
    import enginefaultdb as efdb
    from _common import SPLITS, git_commit, utc_now_iso, write_json

TRAIN_FRAC, GAP_FRAC, TEST_FRAC = 0.70, 0.05, 0.25


def temporal(blocks: dict[int, tuple[int, int]]) -> dict[int, dict]:
    out = {}
    for k, (s, e) in blocks.items():
        n = e - s
        tr_end = s + math.floor(TRAIN_FRAC * n)
        te_start = s + math.ceil((TRAIN_FRAC + GAP_FRAC) * n)
        out[k] = {"train": [s, tr_end], "gap": [tr_end, te_start], "test": [te_start, e]}
    return out


def random_test_rows(y: np.ndarray, seed: int, frac: float = TEST_FRAC) -> np.ndarray:
    rng = np.random.default_rng(seed)
    test = []
    for k in np.unique(y):
        idx = np.flatnonzero(y == k)
        test.append(rng.choice(idx, size=round(frac * len(idx)), replace=False))
    return np.sort(np.concatenate(test))


def rows_hash(rows: np.ndarray) -> str:
    return hashlib.sha256(",".join(map(str, np.sort(rows))).encode()).hexdigest()


def masks(split: dict, y: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """{"temporal"|"random": (train_mask, test_mask)} from a loaded split JSON; checks the random hash."""
    n = len(y)
    if split["rows"] != n:
        raise ValueError(f"split is for {split['rows']} rows, data has {n}")
    t_tr, t_te = np.zeros(n, bool), np.zeros(n, bool)
    for r in split["temporal"]["ranges"].values():
        t_tr[r["train"][0]:r["train"][1]] = True
        t_te[r["test"][0]:r["test"][1]] = True
    rt = random_test_rows(y, split["seed"])
    if rows_hash(rt) != split["random"]["test_rows_sha256"]:
        raise ValueError("random split does not reproduce the committed hash")
    r_te = np.zeros(n, bool)
    r_te[rt] = True
    return {"temporal": (t_tr, t_te), "random": (~r_te, r_te)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=720)
    args = ap.parse_args()
    if not efdb.CSV.exists():
        raise SystemExit(f"{efdb.CSV} missing; clone EngineFaultDB first")
    df = efdb.load()
    blocks = efdb.class_blocks(df)
    y = df[efdb.LABEL].to_numpy()
    tr = temporal(blocks)
    rt = random_test_rows(y, args.seed)
    out = {
        "created_at": utc_now_iso(), "git_commit": git_commit(), "seed": args.seed,
        "produced_by": "data/enginefaultdb_split.py", "source": efdb.SOURCE, "rows": len(df),
        "temporal": {
            "method": f"inside each class block: first {TRAIN_FRAC:.0%} train, {GAP_FRAC:.0%} gap (unused), "
                      f"last {TEST_FRAC:.0%} test; row ranges [start, end) in file order",
            "ranges": {str(k): v for k, v in tr.items()},
            "rows": {s: int(sum(r[s][1] - r[s][0] for r in tr.values())) for s in ("train", "gap", "test")},
        },
        "random": {
            "method": f"rows shuffled (numpy default_rng(seed)), {TEST_FRAC:.0%} test per class; rest train",
            "test_rows": int(len(rt)), "test_rows_sha256": rows_hash(rt),
        },
        "warning": "each class is one recording session: neither split separates fault from session",
    }
    path = SPLITS / "enginefaultdb_split.json"
    write_json(path, out)
    for k, r in tr.items():
        print(f"class {k}: train {r['train']}  gap {r['gap']}  test {r['test']}")
    print(f"random: {len(rt)} test rows, sha256 {out['random']['test_rows_sha256'][:12]}\n"
          f"Wrote {path.relative_to(SPLITS.parent.parent)}")


if __name__ == "__main__":
    main()
