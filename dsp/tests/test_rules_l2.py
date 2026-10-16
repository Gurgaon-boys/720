"""L2 physics rules: knock band energy, trim drift, and the half-order energy under a load change."""

import json

import numpy as np
import pytest

from dsp.features import window_features
from dsp.obd_window import obd_window, total_trim_pct
from dsp.rules import (KNOCK_BAND_ENERGY, TRIM_DRIFT, band_for, knock_band_energy, load_thresholds, misfire_half_order,
                       trim_drift)
from dsp.windows import CycleWindower
from sim import EngineParams, EngineSim
from sim.params import Regimes


def _windows(seconds, fault=None, at=4.0, rpm=None, engine=None, seed=0, regimes=None, step=0.5):
    sim = EngineSim(EngineParams.randomised(engine) if engine is not None else EngineParams(), seed=seed,
                    regimes=regimes)
    if rpm:
        sim.set_operating_point(rpm_target=rpm)
    from models.features import StreamFeatures      # the live pipeline: the knock gate follows the ECU retard
    sf, out = StreamFeatures(), []
    for i in range(int(round(seconds / step))):
        if fault and abs(i * step - at) < 1e-9:
            sim.inject(*fault)
        for w in sf.push(sim.step(step)):
            out.append((w.win, w.features))
    return out


@pytest.mark.skipif(not KNOCK_BAND_ENERGY.exists(), reason="run python -m dsp.calibrate_knock")
@pytest.mark.parametrize("rpm", [800, 2500])
def test_knock_rule_fires_on_the_knocking_cylinder_only_when_it_knocks(rpm):
    th = load_thresholds(KNOCK_BAND_ENERGY)
    healthy = [knock_band_energy(f, th)["fired"] for win, f in _windows(10.0, rpm=rpm) if win.t_start > 3]
    assert sum(healthy) == 0
    ws = [(win, f) for win, f in _windows(20.0, ("knock", 2, 0.8), rpm=rpm) if win.t_start > 4.5]
    knocked = [(knock_band_energy(f, th)) for win, f in ws if any(lab["knocked"][1] for lab in win.labels)]
    hits = [r for r in knocked if r["fired"]]
    assert knocked and len(hits) >= 0.7 * len(knocked)
    assert all(r["location"] == 2 and r["rule_basis"] == "crank_angle" for r in hits)


def test_trim_drift_rule_logic():
    th = {"threshold_pct": 8.0, "min_samples": 20, "retard_exclude_deg": 1.0}
    base = {"n": 30, "trim_mean_pct": 11.0, "spark_retard_deg": 0.0}
    r = trim_drift(base, th)
    assert r == {"rule": "injector_clog", "fired": True, "location": None, "rule_basis": "obd"}
    assert not trim_drift({**base, "trim_mean_pct": 5.0}, th)["fired"]
    assert not trim_drift({**base, "spark_retard_deg": 2.0}, th)["fired"]      # knock control active
    assert not trim_drift({**base, "n": 10}, th)["fired"]                       # not a full 30 s yet


def test_obd_window_total_trim_and_retard():
    t = np.arange(0, 60.0)
    stft, ltft = np.full(60, 10.0), np.full(60, 5.0)
    spark = np.where(t < 30, 14.0, 11.0)
    w = obd_window(t, stft, ltft, spark, 59.0)
    assert w["n"] == 30 and w["trim_mean_pct"] == pytest.approx(15.5) and w["spark_retard_deg"] == pytest.approx(3.0)
    assert total_trim_pct(0.0, 0.0) == 0.0


@pytest.mark.skipif(not TRIM_DRIFT.exists(), reason="run python -m dsp.calibrate_trim")
def test_trim_threshold_comes_from_carobd_calibration_and_catches_large_clogs():
    th = json.loads(TRIM_DRIFT.read_text())
    assert th["rule_basis"] == "obd" and "carOBD calibration" in th["source"]
    assert th["carobd"]["windows"] > 1000
    assert th["threshold_pct"] < 100 * 0.6 / (4 - 0.6)          # the largest clog (s = 0.6) is above it


@pytest.mark.parametrize("rpm", [1500, 2500])
def test_half_order_energy_ignores_a_healthy_load_change(rpm):
    """Load 30 % -> 70 % (calculated load) over 1 s on a healthy engine: the misfire rule stays quiet.
    The crank-speed trend is removed before the half order, so the ramp doesn't leak into it."""
    th = load_thresholds()
    sim = EngineSim(EngineParams.randomised(55), seed=1, regimes=Regimes.eval())
    sim.set_operating_point(rpm_target=rpm, load_nm=2.0)
    w, fired, loads, vals = CycleWindower(), 0, [], []
    for i in range(200):
        t = i * 0.1
        if t >= 10.0:
            sim.set_operating_point(load_nm=2.0 + 38.0 * min(1.0, t - 10.0))
        c = sim.step(0.1)
        loads += [o["load_pct"] for o in c["obd"]]
        for win in w.push(c):
            if win.t_start < 4:
                continue
            f = window_features(win)
            fired += misfire_half_order(f, th)["fired"]
            vals.append(f["half_order_energy"] / band_for(th, f["rpm_mean"])["half_order_energy"]["threshold"])
    assert np.median(loads[4:9]) < 40 and np.median(loads[-4:]) > 65
    assert fired == 0, f"max value / threshold {max(vals):.2f}"
