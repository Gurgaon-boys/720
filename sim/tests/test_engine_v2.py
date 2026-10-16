"""Sim v2: accelerometer, ECU/OBD layer, knock / injector clog / valve tick, regimes, held-out guard."""

import numpy as np
import pytest
from scipy import signal as sps

from sim import FS, EngineParams, EngineSim
from sim.engine import OBD_CHANNELS, HeldOutFaultError
from sim.guard import HeldOutError, check_chunk, check_condition
from sim.params import Regimes

P = EngineParams()


def _chunks(seconds, fault=None, at=2.0, seed=0, **kw):
    sim = EngineSim(P, seed=seed, **kw)
    out = []
    for i in range(int(round(seconds / 0.5))):
        if fault and abs(i * 0.5 - at) < 1e-9:
            sim.inject(*fault)
        out.append(sim.step(0.5))
    return sim, out


def _cat(chunks, key):
    return np.concatenate([c[key] for c in chunks])


def _band_energy(x, lo, hi):
    sos = sps.butter(4, [lo, hi], btype="band", fs=FS, output="sos")
    return float(np.mean(sps.sosfilt(sos, np.nan_to_num(x)) ** 2))


# ---------------------------------------------------------------- backward compatibility

def test_v1_api_and_keys_still_work():
    sim = EngineSim(P, 0)                               # positional (params, seed) as in v1
    sim.set_operating_point(rpm_target=900, load_nm=6.0)
    sim.inject("misfire", cylinder=2, severity=0.5)
    c = sim.step(0.5)
    v1_keys = {"t", "crank_angle_deg", "rpm", "tooth_times", "tooth_ids", "mic", "cylinder_pressure", "labels", "meta"}
    assert v1_keys <= set(c)
    assert {"accel", "ecu_fast", "obd", "dropouts"} <= set(c)
    assert c["accel"].dtype == np.float32 and c["accel"].shape == c["mic"].shape
    lab = c["labels"][0]
    assert {"cycle", "t_start", "fault", "cylinder", "severity", "misfired"} <= set(lab)
    assert c["meta"]["fault"] == {"fault": "misfire", "cylinder": 2, "severity": 0.5}
    assert c["meta"]["sim_version"].startswith("2.")


def test_regimes_default_off():
    sim = EngineSim(P, 0)
    assert not sim.regimes.any_on and Regimes.eval().any_on


# ---------------------------------------------------------------- determinism and p = 0

@pytest.mark.parametrize("regimes", [None, Regimes.eval(dropout_per_min=2.0)])
def test_same_seed_identical_with_v2_channels(regimes):
    _, a = _chunks(4.0, ("knock", 2, 0.5), seed=7, regimes=regimes)
    _, b = _chunks(4.0, ("knock", 2, 0.5), seed=7, regimes=regimes)
    for key in ("mic", "accel", "tooth_times", "rpm"):
        np.testing.assert_array_equal(_cat(a, key), _cat(b, key), err_msg=key)
    assert [c["obd"] for c in a] == [c["obd"] for c in b]
    _, d = _chunks(4.0, ("knock", 2, 0.5), seed=8, regimes=regimes)
    assert not np.array_equal(np.nan_to_num(_cat(a, "accel")), np.nan_to_num(_cat(d, "accel")))


@pytest.mark.parametrize("fault", ["misfire", "knock", "injector_clog", "valve_tick"])
def test_severity_zero_is_bit_identical_to_healthy(fault):
    _, h = _chunks(4.0, seed=5)
    _, z = _chunks(4.0, (fault, 3, 0.0), seed=5)
    for key in ("mic", "accel", "rpm", "tooth_times", "cylinder_pressure"):
        np.testing.assert_array_equal(_cat(h, key), _cat(z, key), err_msg=f"{fault}: {key}")
    assert [c["obd"] for c in h] == [c["obd"] for c in z]


# ---------------------------------------------------------------- slow channels

def test_obd_at_1hz_with_all_channels_and_sources():
    _, cs = _chunks(10.0)
    obd = [o for c in cs for o in c["obd"]]
    assert [o["t"] for o in obd] == [float(i) for i in range(10)]
    assert set(obd[0]) == {"t", *OBD_CHANNELS}
    last = obd[-1]
    assert abs(last["rpm"] - 800) < 20 and 20 < last["map_kpa"] < 50 and 0 < last["throttle_pct"] < 40
    assert abs(last["stft_pct"]) < 2 and abs(last["lambda"] - 1) < 0.02 and 80 < last["coolant_c"] < 95
    meta = cs[-1]["meta"]
    assert meta["sources"]["ecu_fast"] == "sim_internal" and "simulator only" in meta["sources"]["lambda"]
    assert len(cs[-1]["ecu_fast"]["t"]) > 5                          # sim-internal, once per firing


@pytest.mark.parametrize("s", [0.1, 0.2, 0.4])
def test_injector_clog_trim_is_s_over_4_minus_s(s):
    """One cylinder of four loses fraction s of its fuel; the bank trim settles where the mean
    equivalence ratio is 1: (1 + T)(1 - s/4) = 1, so T = s / (4 - s), i.e. about s/4."""
    _, cs = _chunks(40.0, ("injector_clog", 1, s), at=2.0)
    f = [c["ecu_fast"] for c in cs[-20:]]                            # last 10 s
    trim = np.mean((1 + np.concatenate([x["stft_pct"] for x in f]) / 100)
                   * (1 + np.concatenate([x["ltft_pct"] for x in f]) / 100)) - 1
    assert trim == pytest.approx(s / (4 - s), abs=0.006)
    assert abs(trim - s / 4) < 0.25 * s / 4 + 0.005                 # "about s/4"
    lam = np.concatenate([x["lambda"] for x in f])
    assert abs(np.mean(1 / lam) - 1) < 0.02                          # bank equivalence ratio back to 1


def test_clog_lambda_ripples_once_per_cycle():
    _, cs = _chunks(20.0, ("injector_clog", 1, 0.4), at=2.0)
    lam = np.concatenate([c["ecu_fast"]["lambda"] for c in cs[-10:]])   # one sample per firing
    x = lam - lam.mean()
    ac = [np.dot(x[:-k], x[k:]) / np.dot(x, x) for k in (1, 2, 3, 4)]
    assert ac[3] > 0.5 and ac[3] == max(ac)                          # period 4 firings = 720 deg


def test_knock_band_energy_and_spark_retard():
    sim, cs = _chunks(16.0, ("knock", 2, 0.8), at=4.0)
    knocked = [lab for c in cs for lab in c["labels"] if lab["knocked"][1]]
    assert knocked, "no knock events"
    assert all(5000 < f < 15000 for f in cs[-1]["meta"]["knock_freqs_hz"])
    healthy = _band_energy(_cat(cs[1:8], "accel"), 5000, 15000)
    # energy in the half-second chunks that hold a knock event
    t_k = [lab["t_start"] for lab in knocked]
    bursts = [c for c in cs[8:] if any(c["t"][0] <= t < c["t"][-1] for t in t_k)]
    assert _band_energy(_cat(bursts, "accel"), 5000, 15000) > 1.3 * healthy
    adv = [o["spark_adv_deg"] for c in cs for o in c["obd"]]
    assert adv[2] - min(adv[8:]) >= 2.0                              # the ECU pulled timing
    sim.clear_faults()
    later = [o["spark_adv_deg"] for _ in range(20) for o in sim.step(0.5)["obd"]]
    assert later[-1] > min(adv[8:]) + 1.0                            # and recovers slowly afterwards


# ---------------------------------------------------------------- valve tick: held out

def test_valve_tick_is_labelled_held_out_and_audible_in_accel():
    _, cs = _chunks(8.0, ("valve_tick", 4, 1.0), at=2.0)
    assert cs[-1]["meta"]["held_out"] is True and cs[-1]["labels"][-1]["held_out"] is True
    assert cs[0]["meta"]["held_out"] is False
    assert _band_energy(_cat(cs[-6:], "accel"), 8000, 15000) > 3 * _band_energy(_cat(cs[:3], "accel"), 8000, 15000)


def test_held_out_guard_blocks_calibration_and_training():
    for purpose in ("calibration", "training"):
        with pytest.raises(HeldOutFaultError):
            EngineSim(P, 0, purpose=purpose).inject("valve_tick", 1, 0.5)
    EngineSim(P, 0, purpose="eval").inject("valve_tick", 1, 0.5)      # explicit requests elsewhere are fine
    _, cs = _chunks(4.0, ("valve_tick", 1, 0.5), at=0.0)
    with pytest.raises(HeldOutError):
        check_chunk(cs[-1])
    check_chunk(_chunks(1.0)[1][-1])
    with pytest.raises(HeldOutError):
        check_condition(3200.0, "train")
    with pytest.raises(HeldOutError):
        check_condition(1500.0, "test")
    check_condition(2900.0, "train")


def test_calibration_script_refuses_held_out_conditions():
    from dsp.calibrate_misfire import healthy_windows
    with pytest.raises(HeldOutError):
        healthy_windows((0, 1000, 3200.0, 5.0))


# ---------------------------------------------------------------- regimes

def test_dropouts_blank_the_channel_and_are_reported():
    _, cs = _chunks(30.0, regimes=Regimes(dropout_per_min=4.0, dropout_s=(1.0, 2.0), dropout_channels=("mic", "crank")))
    spans = [d for c in cs for d in c["dropouts"]]
    assert {d["channel"] for d in spans} == {"mic", "crank"}
    for c in cs:
        for d in c["dropouts"]:
            sel = (c["t"] >= d["t0"]) & (c["t"] < d["t1"])
            if d["channel"] == "mic":
                assert np.all(np.isnan(c["mic"][sel]))
            else:
                assert not np.any((c["tooth_times"] >= d["t0"]) & (c["tooth_times"] < d["t1"]))
    assert not np.isnan(_cat(cs, "accel")).any()


def test_torsional_flex_size_and_rpm_scaling():
    """Flex only moves the tooth timestamps (the crank itself is unchanged), so the same run with and
    without it shows the angle error directly: rms ~ 0.02 deg x (rpm / 1000) ** 1.5."""
    def angle_error_rms(rpm):
        teeth = []
        for flex in (0.0, 0.02):
            sim = EngineSim(P, 1, regimes=Regimes(torsional_deg_at_1000=flex))
            sim.set_operating_point(rpm_target=rpm)
            teeth.append((_cat([sim.step(0.5) for _ in range(10)][4:], "tooth_times")))
        omega_deg = rpm * 6.0
        return float(np.sqrt(np.mean((teeth[1] - teeth[0]) ** 2)) * omega_deg)
    e900, e2500 = angle_error_rms(900), angle_error_rms(2500)
    assert e2500 == pytest.approx(0.02 * 2.5 ** 1.5, rel=0.35)
    assert e2500 / e900 == pytest.approx((2500 / 900) ** 1.5, rel=0.35)
