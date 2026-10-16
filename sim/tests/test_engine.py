import numpy as np
import pytest

from sim import FS, EngineParams, EngineSim
from sim.analysis import half_order_ratio, run, tsa_720

P = EngineParams()


def _sim(seed=0, misfire=None, cylinder=3, params=P):
    sim = EngineSim(params, seed=seed)
    if misfire is not None:
        sim.inject("misfire", cylinder=cylinder, severity=misfire)
    return sim


def _settled(misfire=None, seed=0, warmup=3.0, record=4.0):
    sim = _sim(seed, misfire)
    run(sim, warmup)
    return run(sim, record)


# ---------------------------------------------------------------- determinism

def test_same_seed_identical():
    a, b = run(_sim(seed=1), 1.5), run(_sim(seed=1), 1.5)
    for k in ("t", "crank_angle_deg", "rpm", "tooth_times", "tooth_ids", "mic", "cylinder_pressure"):
        np.testing.assert_array_equal(a[k], b[k], err_msg=k)
    assert a["labels"] == b["labels"]


def test_different_seed_differs():
    a, b = run(_sim(seed=1), 1.5), run(_sim(seed=2), 1.5)
    assert not np.array_equal(a["mic"], b["mic"])
    assert not np.array_equal(a["rpm"], b["rpm"])


# ---------------------------------------------------------------- shapes and continuity

def test_chunk_shapes_and_continuity():
    sim = _sim()
    c1, c2 = sim.step(0.5), sim.step(0.5)
    n = int(0.5 * FS)
    for c in (c1, c2):
        assert c["t"].shape == c["crank_angle_deg"].shape == c["rpm"].shape == c["mic"].shape == (n,)
        assert c["cylinder_pressure"].shape == (4, n)
        assert c["mic"].dtype == np.float32
        assert c["tooth_times"].shape == c["tooth_ids"].shape
        assert np.all((c["crank_angle_deg"] >= 0) & (c["crank_angle_deg"] < 720))
        assert np.all(np.diff(c["tooth_times"]) > 0)
        assert c["tooth_times"].min() >= c["t"][0] and c["tooth_times"].max() < c["t"][-1] + 1 / FS
        assert np.all(np.isfinite(c["mic"])) and np.all(np.isfinite(c["cylinder_pressure"]))
        for lab in c["labels"]:
            assert {"cycle", "t_start", "fault", "cylinder", "severity", "misfired"} <= set(lab)
        assert c["meta"]["sim_version"] and c["meta"]["seed"] == 0
    assert c2["t"][0] == pytest.approx(c1["t"][-1] + 1 / FS)
    assert c2["tooth_times"][0] > c1["tooth_times"][-1]
    assert c2["labels"][0]["cycle"] == c1["labels"][-1]["cycle"] + 1


def test_trigger_wheel_has_gap():
    r = run(_sim(), 1.0)
    dt = np.diff(r["tooth_times"])
    gaps = np.diff(r["tooth_ids"]) < 0
    # the missing-teeth interval spans 3 pitches, so it is ~3x a normal tooth interval
    assert np.median(dt[gaps]) / np.median(dt[~gaps]) == pytest.approx(3.0, rel=0.1)
    assert set(np.unique(r["tooth_ids"])) == set(range(58))


# ---------------------------------------------------------------- idle control

def test_idle_settles_near_800():
    r = run(_sim(), 4.0)
    last = r["rpm"][-FS:]
    assert abs(last.mean() - 800) < 15
    assert last.max() - last.min() < 100


def test_continuous_misfire_does_not_stall():
    sim = _sim(misfire=1.0)
    r = run(sim, 5.0)
    assert not sim.stalled
    assert abs(r["rpm"][-FS:].mean() - 800) < 30


# ---------------------------------------------------------------- misfire signatures

def test_half_order_ratio_misfire_vs_healthy():
    ratio = half_order_ratio(_settled(), _settled(misfire=1.0), P.wheel_first_tooth_deg)
    assert ratio > 5


def test_misfire_is_a_missing_pulse():
    h, m = _settled(), _settled(misfire=1.0)
    assert all(lab["misfired"] == [False, False, True, False] for lab in m["labels"])
    # cylinder 3 is only motored: its peak pressure falls well below the cylinders that still fire
    # (compare within the run: the idle controller raises MAP, which lifts the motored peak too)
    peaks = m["cylinder_pressure"].max(axis=1)
    assert peaks[2] < 0.8 * min(peaks[0], peaks[1], peaks[3])
    # the 720 deg mic-energy fingerprint loses the cylinder 3 lobe (TDC at 180 deg)
    for r, expect_lobe in ((h, True), (m, False)):
        c, e = tsa_720(r["mic"].astype(float) ** 2, r["crank_angle_deg"])
        lobe3 = e[(c > 180) & (c < 240)].mean()
        lobe1 = e[(c > 0) & (c < 60)].mean()
        assert (lobe3 > 0.5 * lobe1) == expect_lobe


def test_misfire_probability_matches_severity():
    sim = _sim(misfire=0.5)
    r = run(sim, 30.0)
    frac = np.mean([lab["misfired"][2] for lab in r["labels"]])
    assert 0.35 < frac < 0.65


def test_p0_misfire_equals_healthy():
    h, z = run(_sim(seed=4), 3.0), run(_sim(seed=4, misfire=0.0), 3.0)
    for k in ("rpm", "mic", "cylinder_pressure", "tooth_times"):
        np.testing.assert_array_equal(h[k], z[k], err_msg=k)
    assert all(lab["fault"] == "misfire" and lab["severity"] == 0.0 for lab in z["labels"])
    assert not any(any(lab["misfired"]) for lab in z["labels"])
    assert h["rpm"].mean() == z["rpm"].mean() and h["mic"].std() == z["mic"].std()


def test_fault_takes_effect_at_cycle_boundary_and_clears():
    sim = _sim()
    run(sim, 1.0)
    sim.inject("misfire", cylinder=3, severity=1.0)
    r = run(sim, 1.0)
    flags = [lab["misfired"][2] for lab in r["labels"]]
    assert flags[-1]                                          # on after the next cycle boundary
    assert flags == sorted(flags)                             # off -> on once, never back
    sim.clear_faults()
    r = run(sim, 1.0)
    assert not r["labels"][-1]["misfired"][2]


# ---------------------------------------------------------------- API guards and hooks

def test_unknown_faults_rejected():
    # v1 also asserted knock / injector_clog / valve_tick raised NotImplementedError; sim v2 implements
    # them (tests in test_engine_v2.py), so only the validation part of this test still applies.
    sim = _sim()
    with pytest.raises(ValueError):
        sim.inject("banana", cylinder=1, severity=0.5)
    with pytest.raises(ValueError):
        sim.inject("misfire", cylinder=5, severity=0.5)
    with pytest.raises(ValueError):
        sim.inject("misfire", cylinder=1, severity=1.5)


def test_background_hook():
    n = int(0.5 * FS)
    a, b = _sim(seed=3), _sim(seed=3)
    np.testing.assert_array_equal(a.step(0.5)["mic"], b.step(0.5, background=np.zeros(n))["mic"])
    c = b.step(0.5, background=np.random.default_rng(0).standard_normal(n))
    assert not np.array_equal(a.step(0.5)["mic"], c["mic"])
    with pytest.raises(ValueError):
        b.step(0.5, background=np.zeros(n - 1))


def test_randomised_params_regions():
    train = [EngineParams.randomised(i, "train") for i in range(20)]
    test = [EngineParams.randomised(i, "test") for i in range(20)]
    def scale(p):
        return np.mean(np.array(p.mode_freqs_hz) / np.array(P.mode_freqs_hz))

    # the held-out region is a higher structural-mode frequency scale
    assert np.mean([scale(p) for p in train]) < 1.0 < np.mean([scale(p) for p in test]) - 0.05
    assert all(1000 < f < 7000 for p in train + test for f in p.mode_freqs_hz)
    assert EngineParams.randomised(5) == EngineParams.randomised(5)
    assert EngineParams.randomised(5) != EngineParams.randomised(6)
    with pytest.raises(ValueError):
        EngineParams.randomised(0, "validation")
