import numpy as np

from models.table_a import misfire as M


def test_grouped_folds_never_share_an_engine():
    groups = np.repeat(np.arange(12), 50)
    seen = []
    for tr, te in M.folds("grouped", len(groups), groups, seed=3):
        assert not set(groups[tr]) & set(groups[te])
        seen += list(te)
    assert sorted(seen) == list(range(len(groups)))  # every window tested exactly once


def test_runs_cover_both_regions_and_severities():
    rs = M.runs()
    assert {r["region"] for r in rs} == {"train", "test"}
    assert {r["severity"] for r in rs} == {0.0, *M.SEVERITIES}
    assert len({r["run_seed"] for r in rs}) == len(rs)


def test_baseline_learns_thresholds_on_training_data():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, len(M.FEATURES)))
    y = (X[:, M.FEATURES.index("half_order_ratio")] > 0.5).astype(int)
    b = M.Baseline().fit(X, y)
    assert (b.predict(X) == y).mean() > 0.95
