import numpy as np
import pytest

from dsp.calibrate_misfire import CAL_ENGINES, HELD_OUT_ENGINES, TARGET_FPR
from dsp.features import TDC_DEG, window_features
from dsp.rules import MISFIRE_HALF_ORDER, load_thresholds, misfire_firing_event, misfire_half_order, per_location
from dsp.windows import CycleWindower, cycle_windows
from sim import EngineParams, EngineSim


def _windows(seconds, chunk_s=0.5, seed=0, params=None, rpm=None, inject_at=None, misfire=(3, 0.5)):
    sim = EngineSim(params or EngineParams(), seed=seed)
    if rpm:
        sim.set_operating_point(rpm_target=rpm)
    w = CycleWindower()
    out = []
    for i in range(int(round(seconds / chunk_s))):
        if inject_at is not None and abs(i * chunk_s - inject_at) < 1e-9:
            sim.inject("misfire", cylinder=misfire[0], severity=misfire[1])
        out += w.push(sim.step(chunk_s))
    return out


# ---------------------------------------------------------------- windowing

@pytest.mark.parametrize("rpm", [800, 2500])
def test_windows_are_whole_cycles_with_50pct_overlap(rpm):
    wins = _windows(4.0, rpm=rpm)
    assert len(wins) > 5
    for a, b in zip(wins, wins[1:]):
        assert b.first_cycle == a.first_cycle + 2                          # hop = 2 cycles
    for w in wins:
        assert w.n_cycles == 4
        step = np.diff(w.theta).max()
        assert 0 <= w.theta[0] - w.theta_start < step + 1e-9               # starts on a cycle boundary
        assert 0 < 720 * (w.first_cycle + 4) - w.theta[-1] <= step + 1e-9  # ends just before the 5th
        assert [lab["cycle"] for lab in w.labels] == list(range(w.first_cycle, w.first_cycle + 4))
        assert abs(len(w.tooth_times) - 8 * 58) <= 1                      # 8 revolutions of a 60-2 wheel
        assert np.all(np.diff(w.theta) > 0)


def test_windowing_independent_of_chunk_size():
    a, b = _windows(3.0, chunk_s=0.5), _windows(3.0, chunk_s=0.05)
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert x.first_cycle == y.first_cycle
        np.testing.assert_array_equal(x.t, y.t)
        np.testing.assert_array_equal(x.tooth_times, y.tooth_times)


def test_cycle_windows_batch_helper():
    sim = EngineSim(EngineParams(), seed=0)
    wins = cycle_windows([sim.step(0.5) for _ in range(4)])
    assert [w.first_cycle for w in wins] == list(range(0, 2 * len(wins), 2))


# ---------------------------------------------------------------- thresholds

def test_threshold_file_and_calibration_split():
    assert MISFIRE_HALF_ORDER.exists()
    th = load_thresholds()
    assert th["rule_basis"] == "crank_angle" and th["source"].startswith("simulated (healthy")
    assert th["half_order_ratio"]["threshold"] > th["half_order_ratio"]["healthy_quantile"] > 0
    assert th["roughness"]["threshold"] > 0
    assert th["calibration"]["engine_seeds"] == CAL_ENGINES
    assert not set(th["held_out"]["engine_seeds"]) & set(th["calibration"]["engine_seeds"])
    ho = th["held_out"]
    assert ho["n_windows"] >= 3000
    assert ho["fpr"] <= TARGET_FPR and ho["fpr_upper_95"] <= TARGET_FPR


def test_held_out_healthy_engines_stay_quiet():
    """Re-run part of the held-out set here: unseen healthy engines must not fire the rule."""
    th = load_thresholds()
    fired = n = 0
    for e in HELD_OUT_ENGINES[:2]:
        for rpm in (800, 2500):
            for w in _windows(12.0, seed=5000 + e, params=EngineParams.randomised(e, "train"), rpm=rpm):
                if w.t_start < 3.0:
                    continue
                n += 1
                fired += misfire_half_order(window_features(w), th)["fired"]
    assert n > 150
    assert fired / n <= TARGET_FPR      # with n < 1000 this means zero false positives


# ---------------------------------------------------------------- rule

def test_misfire_cyl3_fires_on_cyl3_quickly(capsys):
    """N counts windows from the first one that contains a misfired cycle (ground truth): a fault
    applies from the next engine cycle the simulator hasn't planned yet, so the first windows that
    end after the injection hold no misfire at all."""
    th = load_thresholds()
    inject_t = 3.0
    wins = [w for w in _windows(9.0, inject_at=inject_t) if w.t_end > inject_t]
    first_bad = next(i for i, w in enumerate(wins) if any(lab["misfired"][2] for lab in w.labels))
    hit = None
    for i, w in enumerate(wins):
        r = misfire_half_order(window_features(w), th)
        if r["fired"] and r["location"] == 3:
            hit = i
            break
    assert hit is not None and hit >= first_bad
    n = hit - first_bad + 1
    latency_ms = (wins[hit].t_end - inject_t) * 1000
    with capsys.disabled():
        print("\n" f"  misfire cyl 3 p=0.5 injected at {inject_t:.1f} s: rule fired on cyl 3 in window {n} counted from "
              f"the first window holding a misfire ({hit + 1} windows after injection); {latency_ms:.0f} ms from "
              f"injection to the end of that window")
    assert n <= 3

    # it keeps locating cylinder 3 while the fault lasts
    rules = [misfire_half_order(window_features(w), th) for w in wins[hit:]]
    fired = [r for r in rules if r["fired"]]
    assert len(fired) >= len(rules) // 2
    assert np.mean([r["location"] == 3 for r in fired]) >= 0.85


def test_rule_shape_and_per_location():
    th = load_thresholds()
    r = misfire_half_order({"half_order_ratio": 0.0, "roughness": np.zeros(4)}, th)
    assert r == {"rule": "misfire", "fired": False, "location": None, "rule_basis": "crank_angle"}
    lim = th["roughness"]["threshold"]
    assert per_location(np.array([0.0, lim, 2 * lim, 10 * lim]), th) == [0.0, 0.5, 1.0, 1.0]
    with pytest.raises(NotImplementedError):
        misfire_firing_event(np.zeros(3), np.zeros(3), th)


def test_tdc_table_matches_simulator():
    meta = EngineSim(EngineParams(), seed=0).step(0.1)["meta"]
    assert {int(k): v for k, v in meta["tdc_deg"].items()} == TDC_DEG
