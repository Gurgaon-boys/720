"""Sabotage Lab sensor controls (D140): switching simulator sensors off and on, through the real runtime
ticks on a fake clock, the HTTP routes and the OPC UA methods."""

import pytest

from backend import messages
from backend.opcua_bridge import NE107
from backend.rule_layer import Evidence, RuleLayer
from backend.runtime import SensorControlError
from backend.sources.sim_source import SABOTAGE_CHANNELS, SimSource
from backend.tests.test_opcua import Rig, _ok, run, wait_for
from backend.tests.test_scenarios import Run
from models.witness import WitnessOut


class StubWitness:
    """Stands in for the classifier: always answers, and records the validity flag it was given."""
    available = True

    def __init__(self):
        self.calls = 0

    def __call__(self, vec, rpm, valid=True, obd_rows=None):
        self.calls += 1
        return WitnessOut([{"model": "SVM", "label": "healthy", "p": 0.9}], None, 0.1 if valid else None, 0.1)


def _sim_run(witness=None):
    r = Run("healthy")
    r.rt.select("sim", SimSource(witness=witness or StubWitness()))
    return r


def _first(states, pred, after_t=-1.0):
    return next((m for m in states if m["t"] > after_t and pred(m)), None)


# ---------------------------------------------------------------- the source API

def test_disable_enable_restore_and_validation():
    src = SimSource(witness=StubWitness())
    assert src.disabled_sensors() == []
    src.disable_sensor("accel")
    src.disable_sensor("cam")
    assert src.disabled_sensors() == ["accel", "cam"]          # always in SABOTAGE_CHANNELS order
    src.enable_sensor("cam")
    assert src.disabled_sensors() == ["accel"]
    src.restore_sensors()
    assert src.disabled_sensors() == []
    for bad in ("obd", "lidar", ""):
        with pytest.raises(ValueError):
            src.disable_sensor(bad)
    assert SABOTAGE_CHANNELS == ("mic", "accel", "crank", "cam")


def test_clearing_faults_never_restores_a_sensor():
    r = _sim_run().advance(1.0)
    r.rt.disable_sensor("crank")
    r.rt.inject("misfire", 3, 1.0)
    r.rt.clear_faults()
    assert r.rt.disabled_sensors() == ["crank"]


def test_simulator_unchanged_when_no_sensor_is_disabled():
    """No sabotage = bit-identical output; a sensor cut never touches the engine or its random streams."""
    a, b = _sim_run().advance(4.0), _sim_run()
    b.rt.restore_sensors()                                       # no-op commands
    b.rt.enable_sensor("mic")
    b.advance(4.0)
    strip = lambda ms: [{k: v for k, v in m.items() if k not in ("sent_at", "latency_ms")} for m in ms]   # noqa: E731
    assert strip(a.states) == strip(b.states)
    assert a.out["signal"] == b.out["signal"]

    # cut the accelerometer for 2 s: the engine itself (rpm, OBD) runs exactly as without the cut
    c, d = _sim_run().advance(2.0), _sim_run().advance(2.0)
    d.rt.disable_sensor("accel")
    c.advance(2.0), d.advance(2.0)
    d.rt.enable_sensor("accel")
    c.advance(4.0), d.advance(4.0)
    assert [m["operating_point"] for m in c.states] == [m["operating_point"] for m in d.states]
    obd = lambda r: [{k: ch["v"] for k, ch in m["channels"].items()} for m in r.out["obd"]]   # noqa: E731
    assert obd(c) == obd(d)


# ---------------------------------------------------------------- watchdog, gating, recovery

def test_watchdog_reports_failure_three_seconds_after_the_cut_and_recovers():
    r = _sim_run().advance(3.0)
    t_cut = r.t
    r.rt.disable_sensor("accel")
    r.advance(5.0)
    after = [m for m in r.states if m["t"] >= t_cut]
    fail = next(i for i, m in enumerate(after) if m["health"]["sensors"]["accel"] == "FAILURE")
    # the state's sent_at is the fake clock: measure the confirmation delay on it
    t0 = 1_760_000_000.0 + t_cut
    delay = after[fail]["sent_at"] - t0
    assert 2.95 <= delay <= 3.4, delay
    assert all(m["health"]["sensors"]["accel"] == "NORMAL" for m in after[:fail])   # never early
    assert all(m["health"]["sensors"][s] == "NORMAL" for m in r.states for s in ("mic", "crank", "cam", "obd"))

    t_on = 1_760_000_000.0 + r.t
    r.rt.enable_sensor("accel")
    r.advance(2.0)
    back = _first(r.states, lambda m: m["sent_at"] > t_on and m["health"]["sensors"]["accel"] == "NORMAL")
    assert back is not None and back["sent_at"] - t_on <= 0.3
    assert all(messages.errors("state", m) == [] for m in r.states)


def test_signal_frames_stop_for_a_disabled_waveform():
    from backend.signal_frame import decode_frame

    r = _sim_run().advance(2.0)
    r.rt.disable_sensor("mic")
    n0 = len(r.out["signal"])
    r.advance(2.0)
    channels = {decode_frame(f)[0]["channel"] for f in r.out["signal"][n0 + 4:]}
    assert "mic" not in channels and "mic_stft" not in channels and "accel" in channels


@pytest.mark.parametrize("sensor", ["accel", "crank"])
def test_classifier_witness_gated_while_its_inputs_are_missing(sensor):
    """Crank and accelerometer feed the classifier's vector: without either, no model output at all."""
    wit = StubWitness()
    r = _sim_run(wit).advance(3.0)
    assert r.states[-1]["models"]                                   # answering while inputs are valid
    t_cut = r.t
    r.rt.disable_sensor(sensor)
    r.advance(3.0)
    gone = _first(r.states, lambda m: not m["models"], after_t=t_cut)
    assert gone is not None and gone["t"] - t_cut <= 0.7             # the first window that holds the gap
    tail = [m for m in r.states if m["t"] > gone["t"]]
    assert tail and all(m["models"] == [] and m["anomaly"]["score"] is None for m in tail)
    calls = wit.calls
    r.advance(1.0)
    assert wit.calls == calls                                        # the model isn't even run

    t_on = r.t
    r.rt.enable_sensor(sensor)
    r.advance(2.0)
    back = _first(r.states, lambda m: bool(m["models"]), after_t=t_on)
    assert back is not None and back["t"] - t_on <= 1.2              # inference resumes on valid windows only


def test_mic_and_cam_cuts_leave_the_classifier_running():
    r = _sim_run().advance(2.0)
    r.rt.disable_sensor("mic")
    r.rt.disable_sensor("cam")
    r.advance(4.0)
    assert all(m["models"] for m in r.states[-10:])
    assert r.states[-1]["health"]["sensors"]["mic"] == "FAILURE"
    assert r.states[-1]["health"]["sensors"]["cam"] == "FAILURE"


def _ev(fault_rule=None, classifier=None, loc=3):
    rules = [{"rule": r, "fired": r == fault_rule, "location": loc if r == fault_rule else None, "rule_basis": "crank_angle"}
             for r in ("misfire", "knock", "injector_clog")]
    models = [] if classifier is False else [{"model": "SVM", "label": classifier or "healthy", "p": 0.95}]
    return Evidence(models=models, physics_rules=rules, anomaly_score=None, anomaly_threshold=0.5,
                    classifier_location=loc if classifier not in (None, False, "healthy") else None)


def test_alarm_stays_latched_while_the_classifier_is_gone_until_acknowledged():
    """Rule layer unchanged: losing a witness never clears or downgrades an unacknowledged alarm."""
    rl = RuleLayer()
    for _ in range(2):
        d = rl.step(_ev("misfire", "misfire"))
    assert d["state"] == "ALARM" and d["latched"]
    for _ in range(20):                                              # accelerometer cut: physics only
        d = rl.step(_ev("misfire", False))
    assert d["state"] == "ALARM" and d["latched"] and not d["acked"]
    assert rl.ack(d["alarm_id"], "judge", 0.0).ok
    for _ in range(2):
        d = rl.step(_ev("misfire", False))
    assert d["state"] == "ADVISORY" and d["witnesses"] == ["physics"]   # reset once its own match is gone (D124)
    for _ in range(2):                                                 # sensor back, both witnesses agree
        d = rl.step(_ev("misfire", "misfire"))
    assert d["state"] == "ALARM" and d["alarm_id"] != "a_0001"


@pytest.mark.skipif(not SimSource().witness.available, reason="classifier checkpoint not installed")
def test_live_chain_misfire_alarm_survives_accel_cut_and_returns_after_restore():
    r = Run("healthy")
    r.rt.select("sim", SimSource())
    r.advance(22.0)                                                  # past the classifier's OBD warm-up
    r.rt.inject("misfire", 3, 1.0)
    r.advance(4.0)
    assert r.states[-1]["decision"]["state"] == "ALARM" and r.states[-1]["decision"]["location"] == 3
    t_cut = r.t
    r.rt.disable_sensor("accel")
    r.advance(6.0)
    after = [m for m in r.states if m["t"] > t_cut + 1.0]
    assert all(m["models"] == [] for m in after)
    assert all(m["decision"]["state"] == "ALARM" and not m["decision"]["acked"] for m in after)
    alarm = after[-1]["decision"]["alarm_id"]
    r.rt.ack(alarm, "judge", via="test")
    r.advance(2.0)
    assert r.states[-1]["decision"]["state"] == "ADVISORY"            # one witness left: never ALARM
    r.rt.enable_sensor("accel")
    r.advance(4.0)
    d = r.states[-1]["decision"]
    assert d["state"] == "ALARM" and d["alarm_id"] != alarm and r.states[-1]["models"]


# ---------------------------------------------------------------- source lifecycle, HTTP, OPC UA

def test_source_switch_resets_sensor_state_and_fake_sources_refuse():
    r = _sim_run().advance(1.0)
    r.rt.disable_sensor("accel")
    r.rt.select("sim", SimSource(witness=StubWitness()))             # a new run starts with every sensor on
    assert r.rt.disabled_sensors() == []
    r.rt.select("healthy")
    assert r.rt.disabled_sensors() is None
    with pytest.raises(SensorControlError):
        r.rt.disable_sensor("accel")


def test_sensor_http_routes():
    from fastapi.testclient import TestClient

    from backend.main import create_app

    with TestClient(create_app("healthy", opcua=False)) as c:
        assert c.get("/sensors").json()["controllable"] is False
        assert c.post("/sensor/accel/off").status_code == 409          # fake scenario: no simulator
        assert c.post("/sensors/restore").status_code == 409
        assert c.post("/scenario/sim").status_code == 200
        body = c.post("/sensor/accel/off").json()
        assert body["controllable"] is True and body["disabled"] == ["accel"]
        assert c.post("/sensor/crank/off").json()["disabled"] == ["accel", "crank"]
        assert c.post("/sensor/obd/off").status_code == 422             # not switchable
        assert c.post("/sensor/banana/on").status_code == 422
        assert c.post("/sensor/accel/on").json()["disabled"] == ["crank"]
        assert c.post("/clear_faults").status_code == 200
        assert c.get("/sensors").json()["disabled"] == ["crank"]        # clearing faults keeps it off
        assert c.post("/sensors/restore").json()["disabled"] == []


def test_opcua_disable_enable_sensor_methods():
    async def go():
        async with Rig("misfire") as r:
            ok, reason = await r.call("DisableSensor", "accel")
            assert ok is False and "switch to sim" in reason           # not on a fake scenario
        async with Rig("sim") as r:
            ok, reason = await r.call("DisableSensor", "lidar")
            assert ok is False and "unknown sensor" in reason
            ok, reason = await r.call("DisableSensor", "accel")
            assert ok is True and reason == ""
            assert r.runtime.disabled_sensors() == ["accel"]             # the same state HTTP sees
            # confirmed by the watchdog-driven health node, not by the method's reply
            await wait_for(lambda: _ok(r.read("EngineAsset.SensorHealth.accel"), lambda v: v == NE107.index("FAILURE")),
                           timeout=8)
            ok, reason = await r.call("EnableSensor", "accel")
            assert ok is True
            await wait_for(lambda: _ok(r.read("EngineAsset.SensorHealth.accel"), lambda v: v == NE107.index("NORMAL")),
                           timeout=5)
            assert r.runtime.disabled_sensors() == []
    run(go())


def test_address_space_has_sensor_methods():
    async def go():
        async with Rig("sim") as r:
            names = {(await m.read_browse_name()).Name for m in await r.node("EngineAsset").get_methods()}
            assert {"DisableSensor", "EnableSensor", "Inject", "Acknowledge"} <= names
    run(go())
