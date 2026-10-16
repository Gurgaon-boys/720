"""EngineFaultDB loader and split: python -m pytest data/tests/test_enginefaultdb.py -q

Synthetic CSVs only, so these run without the (GPL-3, git-ignored) dataset. One test checks the
committed split file against the real CSV and skips when the CSV is absent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enginefaultdb as efdb  # noqa: E402
import enginefaultdb_split as efsplit  # noqa: E402

SPLIT_FILE = Path(__file__).resolve().parents[1] / "splits" / "enginefaultdb_split.json"


def _csv(tmp_path, sizes=(40, 30, 50, 20), seed=0, shuffle=False) -> Path:
    rng = np.random.default_rng(seed)
    y = np.concatenate([np.full(n, k) for k, n in enumerate(sizes)])
    if shuffle:
        rng.shuffle(y)
    df = pd.DataFrame({"Fault": y})
    for v in efdb.VARIABLES:
        df[v] = rng.normal(size=len(y)).round(3)
    # two operating points per class: RPM steps by 2000 halfway through each block
    rpm, start = np.empty(len(y)), 0
    for n in sizes:
        rpm[start:start + n] = 1500 + rng.normal(0, 20, n) + np.where(np.arange(n) >= n // 2, 2000, 0)
        start += n
    df["RPM"] = rpm
    p = tmp_path / "EngineFaultDB_Final.csv"
    df.to_csv(p, index=False)
    return p


def test_load_columns_row_and_blocks(tmp_path):
    df = efdb.load(_csv(tmp_path))
    assert list(df.columns[:2]) == ["row", "Fault"] and set(efdb.VARIABLES) <= set(df.columns)
    assert df["row"].tolist() == list(range(140))
    assert efdb.class_blocks(df) == {0: (0, 40), 1: (40, 70), 2: (70, 120), 3: (120, 140)}


def test_load_rejects_missing_column(tmp_path):
    p = _csv(tmp_path)
    pd.read_csv(p).drop(columns="Lambda").to_csv(p, index=False)
    with pytest.raises(ValueError, match="Lambda"):
        efdb.load(p)


def test_class_blocks_rejects_interleaved_labels(tmp_path):
    with pytest.raises(ValueError, match="contiguous"):
        efdb.class_blocks(efdb.load(_csv(tmp_path, shuffle=True)))


def test_segments_split_on_rpm_jump_and_class_change(tmp_path):
    df = efdb.load(_csv(tmp_path))
    seg = efdb.segments(df)
    assert seg.max() + 1 == 8  # 4 classes x 2 operating points
    assert len(np.unique(seg[:20])) == 1 and seg[20] != seg[19] and seg[40] != seg[39]


def test_copy_check_finds_shifted_noised_copy(tmp_path):
    df = efdb.load(_csv(tmp_path, sizes=(10, 10, 30, 20)))
    b2, b3 = efdb.class_blocks(df)[2], efdb.class_blocks(df)[3]
    src = df.iloc[b2[0] + 5: b2[0] + 25][efdb.VARIABLES].to_numpy()
    noisy = src + np.random.default_rng(1).normal(0, 1e-3, src.shape)
    noisy[:, :2] = src[:, :2]  # MAP, TPS bit-identical, as in the real file
    df.loc[b3[0]:b3[1] - 1, efdb.VARIABLES] = noisy
    cc = efdb.copy_check(df, lag=5)
    assert cc["rows_compared"] == 20 and cc["rows_with_identical_MAP_and_TPS"] == 20
    assert cc["per_variable"]["Lambda"]["corr"] > 0.99


def test_temporal_split_order_gap_and_fractions():
    blocks = {0: (0, 16000), 1: (16000, 26998), 2: (26998, 41998), 3: (41998, 55999)}
    for k, r in efsplit.temporal(blocks).items():
        s, e = blocks[k]
        assert r["train"][0] == s and r["test"][1] == e
        assert r["train"][1] == r["gap"][0] and r["gap"][1] == r["test"][0]
        n = e - s
        assert abs((r["train"][1] - s) / n - 0.70) < 1e-3
        assert abs((e - r["test"][0]) / n - 0.25) < 1e-3
        assert r["gap"][1] - r["gap"][0] >= 0.05 * n - 1


def test_random_split_deterministic_stratified_and_hashed():
    y = np.repeat([0, 1, 2, 3], [400, 300, 200, 100])
    a, b = efsplit.random_test_rows(y, 720), efsplit.random_test_rows(y, 720)
    assert np.array_equal(a, b) and not np.array_equal(a, efsplit.random_test_rows(y, 721))
    assert np.bincount(y[a]).tolist() == [100, 75, 50, 25]
    assert efsplit.rows_hash(a) == efsplit.rows_hash(a[::-1])


def test_masks_disjoint_and_check_hash():
    y = np.repeat([0, 1, 2, 3], [40, 30, 50, 20])
    blocks = {0: (0, 40), 1: (40, 70), 2: (70, 120), 3: (120, 140)}
    split = {"rows": 140, "seed": 3,
             "temporal": {"ranges": {str(k): v for k, v in efsplit.temporal(blocks).items()}},
             "random": {"test_rows_sha256": efsplit.rows_hash(efsplit.random_test_rows(y, 3))}}
    m = efsplit.masks(split, y)
    for tr, te in m.values():
        assert not np.any(tr & te)
    assert m["random"][0].sum() + m["random"][1].sum() == 140
    split["random"]["test_rows_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        efsplit.masks(split, y)


def test_committed_split_matches_real_csv():
    if not efdb.CSV.exists():
        pytest.skip("EngineFaultDB CSV not cloned")
    split = json.loads(SPLIT_FILE.read_text())
    df = efdb.load()
    assert split["rows"] == len(df) == 55999
    blocks = efdb.class_blocks(df)
    assert [e - s for s, e in blocks.values()] == [16000, 10998, 15000, 14001]
    assert split["temporal"]["ranges"] == {str(k): v for k, v in efsplit.temporal(blocks).items()}
    efsplit.masks(split, df[efdb.LABEL].to_numpy())  # raises if the random split no longer reproduces
