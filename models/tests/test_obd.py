import numpy as np
import pandas as pd

from models.obd import features as F
from models.obd import rules
from models.obd.healthy import HealthyModel


def frame(n=200, rpm=1500.0, load=50.0, timing=12.0, stft=0.0, ltft=0.0, coolant=88.0, seed=0) -> pd.DataFrame:
    """carOBD-like log: 2 rows/s, each value repeated over two rows (PID polling)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) * 0.5
    fresh = lambda base, sd: np.repeat(base + rng.normal(0, sd, n // 2 + 1), 2)[:n]
    return pd.DataFrame({
        "t_s": t, F.RPM: fresh(rpm, 20), F.LOAD: fresh(load, 1), F.MAP: fresh(40, 1),
        F.STFT: fresh(stft, 1), F.LTFT: np.full(n, ltft), F.TIMING: fresh(timing, 0.5),
        F.COOLANT: np.full(n, coolant), F.SPEED: np.full(n, 40.0),
    })


def test_windows_30s_half_overlap():
    ws = F.windows(frame(n=240))  # rows at t = 0 .. 119.5 s; a window must fit inside the log
    assert [w["t0"] for w in ws] == [0, 15, 30, 45, 60, 75]
    assert all(w["regime"] == "mid" for w in ws)


def test_regimes():
    assert F.regime(800, 90) == "idle" and F.regime(1500, 90) == "mid"
    assert F.regime(2500, 90) == "high" and F.regime(2500, 40) == "warmup"


def test_retard_events_need_load_and_steady_rpm():
    d = frame(n=60, load=60)
    d[F.TIMING] = np.where((np.arange(60) // 6) % 2 == 0, 20.0, 10.0)  # 10 deg drops every 6 rows
    assert F.retard_events(d) == 5
    d[F.LOAD] = 20.0  # light load: pull-backs are normal, not knock control
    assert F.retard_events(d) == 0


def test_engine_off_window_skipped():
    assert F.window_features(frame(n=60, rpm=0.0)) is None


def test_healthy_model_flags_shifted_trim_and_roundtrips():
    fit = [w for s in range(6) for w in F.windows(frame(n=400, seed=s))]
    val = [w for s in range(6, 9) for w in F.windows(frame(n=400, seed=s))]
    for w in fit + val:
        w["map_resid_mean"] = w["map_resid_std"] = 0.0
    m = HealthyModel().fit(fit)
    m.set_thresholds(val, 0.99)
    bad = F.windows(frame(n=80, stft=8.0, seed=50))[0]
    bad["map_resid_mean"] = bad["map_resid_std"] = 0.0
    assert m.distance(bad) > m.threshold(bad)
    m2 = HealthyModel.from_json(m.to_json())
    assert m2.distance(bad) == m.distance(bad)


def test_rules():
    th = {"total_trim_max": 7.0, "retard_events_min": 4}
    w = {"regime": "mid", "total_trim": 9.0, "retard_events": 1}
    trim, ret = rules.evaluate(w, th)
    assert trim["fired"] and trim["rule"] == "injector_clog" and trim["location"] is None
    assert not ret["fired"] and ret["rule"] == "knock"
    assert not rules.trim_drift({**w, "regime": "warmup"}, th)["fired"]  # open loop: rule off


def test_obd_classifier_jobs_respect_held_out_rules():
    from eval.common import SEEDS
    from models.obd import sim_classifier as C
    js = C.jobs()
    tr, va = set(SEEDS["obd_clf_train"][0]), set(SEEDS["obd_clf_val"][0])
    assert {j["engine"] for j in js if j["split"] == "train"} == tr and {j["engine"] for j in js if j["split"] == "val"} == va
    assert not tr & va
    assert {j["fault"] for j in js} == set(C.CLASSES)  # never valve_tick
    assert all(not 3000 <= rpm <= 3500 for j in js for rpm, _ in j["schedule"])  # held-out rpm band
    assert set(C.SIM_TO_CAROBD.values()) <= {F.RPM, F.LOAD, F.MAP, F.STFT, F.LTFT, F.TIMING, F.COOLANT}
