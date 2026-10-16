"""The live simulator source (L1) through the real runtime ticks, on a fake clock."""

import numpy as np

from backend import messages
from backend.signal_frame import decode_frame
from backend.tests.test_scenarios import Run


def _run_with_misfire(healthy_s=4.0, faulty_s=6.0, severity=0.5):
    r = Run("sim").advance(healthy_s)
    n_healthy = len(r.states)
    r.rt.scenario.inject("misfire", cylinder=3, severity=severity)
    r.advance(faulty_s)
    return r, n_healthy


def test_sim_misfire_gives_advisory_on_cyl3_never_alarm():
    r, n_healthy = _run_with_misfire()
    healthy, faulty = r.states[:n_healthy], r.states[n_healthy:]
    assert healthy and all(m["decision"]["state"] == "NORMAL" for m in healthy)
    assert "ALARM" not in [m["decision"]["state"] for m in r.states]   # no classifier yet: one witness only
    adv = [m for m in faulty if m["decision"]["state"] == "ADVISORY"]
    assert len(adv) >= len(faulty) // 3
    assert all(m["decision"]["witnesses"] == ["physics"] for m in adv)
    assert np.mean([m["decision"]["location"] == 3 for m in adv]) >= 0.85
    assert all(m["models"] == [] for m in r.states)
    on3 = [m for m in adv if m["decision"]["location"] == 3]
    assert all(m["anomaly"]["per_location"][2] > m["anomaly"]["threshold"] for m in on3)


def test_continuous_misfire_collapses_fingerprint_lobe_and_lights_one_bar():
    r, n_healthy = _run_with_misfire(severity=1.0)
    adv = [m for m in r.states[n_healthy:] if m["decision"]["state"] == "ADVISORY"]
    assert adv and all(m["decision"]["location"] == 3 for m in adv)
    for m in adv:
        pl, thr = m["anomaly"]["per_location"], m["anomaly"]["threshold"]
        assert pl[2] > thr and max(pl[0], pl[1], pl[3]) <= thr      # only cylinder 3 over its line
    sig = adv[-1]["signature"]
    deg, amp, base = (np.array(sig[k]) for k in ("crank_deg", "amplitude", "baseline_amplitude"))
    lobe3 = (deg > 180) & (deg < 240)                                 # after cyl 3 TDC
    assert amp[lobe3].mean() < 0.2 * base[lobe3].mean()


def test_rule_layer_steps_once_per_window():
    r = Run("sim").advance(6.0)
    fresh_t = sorted({m["t"] for m in r.states})
    # 4-cycle windows with 50% overlap at ~800 rpm finish every ~0.3 s; state goes out at 10 Hz
    assert 15 <= len(fresh_t) <= 22
    assert len(r.states) > 2 * len(fresh_t)
    assert np.allclose(np.diff(fresh_t), 0.3, atol=0.03)


def test_every_sim_message_validates():
    r, _ = _run_with_misfire(3.0, 3.0)
    assert r.states and r.out["obd"] and r.out["signal"]
    for m in r.states:
        assert messages.errors("state", m) == []
        assert m["source"] == "simulated" and m["mode"] == "live" and m["localisation"] == "cylinder"
        assert m["physics_rules"][0]["rule_basis"] == "crank_angle"
    for m in r.out["obd"]:
        assert messages.errors("obd", m) == []
        assert set(m["channels"]) == {"rpm", "load_pct", "throttle_pct", "map_kpa", "coolant_c", "stft_pct",
                                      "ltft_pct", "spark_adv_deg", "lambda"}
        assert all(c["source"] == "simulator" for c in m["channels"].values())
    channels = set()
    for buf in r.out["signal"]:
        h, data = decode_frame(buf)
        assert messages.errors("signal_header", h) == []
        assert np.all(np.isfinite(data))
        channels.add(h["channel"])
    assert channels == {"mic", "mic_stft", "accel"}
    assert all(m["anomaly"]["score"] is None for m in r.states)     # schema 1.5: no model, no number


def test_inject_endpoints():
    from fastapi.testclient import TestClient

    from backend.main import create_app

    with TestClient(create_app("healthy")) as c:
        body = {"fault": "misfire", "cylinder": 3, "severity": 0.5}
        assert c.post("/inject", json=body).status_code == 409          # fake scenario: no simulator
        assert "sim" in [s["name"] for s in c.get("/scenarios").json()["scenarios"]]
        assert c.post("/scenario/sim").json() == {"current": "sim"}
        assert c.post("/inject", json=body).status_code == 200
        for fault in ("knock", "injector_clog", "valve_tick"):                # all in sim v2
            assert c.post("/inject", json={**body, "fault": fault}).status_code == 200
        assert c.post("/inject", json={**body, "fault": "banana"}).status_code == 422
        assert c.post("/inject", json={**body, "cylinder": 9}).status_code == 422
        assert c.post("/clear_faults").status_code == 200


def test_sim_dropout_shows_in_sensor_health():
    """A mic dropout longer than the 3 s watchdog turns health.sensors.mic to FAILURE, then back."""
    from backend.sources.sim_source import SimSource
    from sim.params import Regimes

    r = Run("healthy")
    src = SimSource(regimes=Regimes(dropout_per_min=8.0, dropout_s=(4.0, 5.0), dropout_channels=("mic", "crank")))
    r.rt.select("sim", src)
    r.advance(60.0)
    mic = [m["health"]["sensors"]["mic"] for m in r.states]
    crank = [m["health"]["sensors"]["crank"] for m in r.states]
    assert mic.count("FAILURE") < len(mic)                             # it recovers between dropouts
    assert "FAILURE" in mic and "FAILURE" in crank
    assert all(m["health"]["sensors"]["accel"] == "NORMAL" for m in r.states)
    assert all(messages.errors("state", m) == [] for m in r.states)


def test_knock_and_clog_reach_obd_through_the_backend():
    r = Run("sim").advance(3.0)
    r.rt.inject("injector_clog", 1, 0.4)
    r.advance(25.0)
    stft = [m["channels"]["stft_pct"]["v"][0] + m["channels"]["ltft_pct"]["v"][0] for m in r.out["obd"]]
    assert stft[1] < 2.0 and stft[-1] > 7.0                            # trim heads for s/(4-s) = 11 %
    assert r.states[-1]["injection"]["fault"] == "injector_clog"
