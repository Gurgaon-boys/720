import numpy as np
import pytest

from dsp.calibrate_misfire import BANDS, CAL_ENGINES, TARGET_FPR, VAL_ENGINES
from dsp.features import TDC_DEG, window_features
from dsp.rules import (MISFIRE_HALF_ORDER, MISFIRE_HALF_ORDER_V1, band_for, load_thresholds, misfire_firing_event,
                       misfire_half_order, per_location)
from dsp.windows import CycleWindower, cycle_windows
from eval.common import SEEDS
from sim import EngineParams, EngineSim
from sim.params import Regimes


def _windows(seconds, chunk_s=0.5, seed=0, params=None, rpm=None, inject_at=None, misfire=(3, 0.5), regimes=None):
    sim = EngineSim(params or EngineParams(), seed=seed, regimes=regimes)
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

def test_threshold_file_v2_per_band_and_seed_split():
    assert MISFIRE_HALF_ORDER.exists() and MISFIRE_HALF_ORDER_V1.exists()
    th = load_thresholds()
    assert th["version"] == 2 and th["feature"] == "half_order_energy" and th["rule_basis"] == "crank_angle"
    assert th["source"].startswith("simulated (healthy")
    bands = th["bands"]
    assert [b["name"] for b in bands] == [b[0] for b in BANDS]
    assert all(a["rpm_hi"] == b["rpm_lo"] for a, b in zip(bands, bands[1:])) and bands[-1]["rpm_hi"] <= 3000
    for b in bands:
        assert b["half_order_energy"]["threshold"] > b["half_order_energy"]["healthy_quantile"] > 0
        assert b["roughness"]["threshold"] > 0
        assert b["validation"]["windows"] > 1000 and b["validation"]["fpr_upper_95"] <= TARGET_FPR
    cal = set(th["calibration"]["engine_seeds"])
    assert cal == set(CAL_ENGINES) and not cal & set(VAL_ENGINES)
    for kind in ("soak", "robustness", "held_out_region"):
        assert not cal & set(SEEDS[kind][0])
    assert th["held_out_never_used"]["faults"] == ["valve_tick"]


def test_v1_threshold_file_still_works():
    v1 = load_thresholds(MISFIRE_HALF_ORDER_V1)
    assert "bands" not in v1 and band_for(v1, 900.0) is v1
    w = _windows(3.0)[-1]
    assert misfire_half_order(window_features(w), v1)["fired"] is False


def test_unseen_healthy_engines_stay_quiet_with_regimes_on():
    """A slice of the soak's engines (never used for calibration), regimes on: no false positives."""
    th = load_thresholds()
    fired = n = 0
    engines, off = SEEDS["soak"]
    for e in engines[:2]:
        for rpm in (800, 2500):
            for w in _windows(12.0, seed=off + e, params=EngineParams.randomised(e, "train"), rpm=rpm,
                              regimes=Regimes.eval()):
                if w.t_start < 3.0:
                    continue
                n += 1
                fired += misfire_half_order(window_features(w), th)["fired"]
    assert n > 150
    assert fired / n <= TARGET_FPR      # with n < 1000 this means zero false positives


def test_crank_dropout_window_cannot_fire():
    f = {"half_order_energy": 1.0, "half_order_ratio": 9.0, "roughness": np.ones(4), "rpm_mean": 800.0,
         "crank_valid": False}
    assert misfire_half_order(f, load_thresholds())["fired"] is False


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
    # v1's half-order ratio needed <= 3 windows here; v2's half-order energy (D50) is slower on a
    # p = 0.5 misfire at idle (it is not divided by a 2nd order that can vanish); eval/ reports the stats
    assert n <= 5

    # it keeps locating cylinder 3 while the fault lasts
    rules = [misfire_half_order(window_features(w), th) for w in wins[hit:]]
    fired = [r for r in rules if r["fired"]]
    assert len(fired) >= len(rules) // 2
    assert np.mean([r["location"] == 3 for r in fired]) >= 0.85


def test_rule_shape_and_per_location():
    th = load_thresholds()
    f = {"half_order_energy": 0.0, "half_order_ratio": 0.0, "roughness": np.zeros(4), "rpm_mean": 800.0, "map_kpa": 35.0}
    assert misfire_half_order(f, th) == {"rule": "misfire", "fired": False, "location": None, "rule_basis": "crank_angle"}
    lim = band_for(th, 800.0)["roughness"]["threshold"]
    assert per_location(np.array([0.0, lim, 2 * lim, 10 * lim]), th, 800.0) == [0.0, 0.5, 1.0, 1.0]
    assert band_for(th, 800.0)["name"] == "idle" and band_for(th, 2500.0)["name"] == "high"
    assert band_for(th, 3300.0)["name"] == "high"                          # held-out rpm: nearest band
    with pytest.raises(NotImplementedError):
        misfire_firing_event(np.zeros(3), np.zeros(3), th)


def test_tdc_table_matches_simulator():
    meta = EngineSim(EngineParams(), seed=0).step(0.1)["meta"]
    assert {int(k): v for k, v in meta["tdc_deg"].items()} == TDC_DEG
