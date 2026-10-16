"""Every fake scenario, run through the real runtime ticks on a fake clock (no sockets, no waiting).

Checks the decision sequence each scenario should produce, the backend watchdog, ACK handling,
and that every JSON message and signal header validates against contract 1.3.
"""

import json

import pytest

from backend import messages
from backend.runtime import Runtime
from backend.scenarios import OBD_S, SCENARIOS, SIGNAL_S, WINDOW_S
from backend.signal_frame import decode_frame


class Run:
    """Drives the three ticks in time order on a fake clock and records what each hub got."""

    def __init__(self, scenario):
        self.now = 1_760_000_000.0
        self.rt = Runtime(scenario, clock=lambda: self.now)
        self.qs = {name: getattr(self.rt, f"{name}_hub").subscribe() for name in ("state", "obd", "signal")}
        self.out = {name: [] for name in self.qs}
        self.t = 0.0

    def advance(self, seconds):
        events = []
        for period, tick in ((WINDOW_S, self.rt._tick_state), (OBD_S, self.rt._tick_obd), (SIGNAL_S, self.rt._tick_signal)):
            i = round(self.t / period)
            while i * period < self.t + seconds - 1e-9:
                if i * period >= self.t - 1e-9:
                    events.append((i * period, tick, i))
                i += 1
        for t, tick, i in sorted(events, key=lambda e: e[0]):
            self.now = 1_760_000_000.0 + t
            tick(i)
            self._drain()
        self.t += seconds
        return self

    def _drain(self):
        for name, q in self.qs.items():
            while not q.empty():
                msg = q.get_nowait()
                self.out[name].append(msg if isinstance(msg, (bytes, dict)) else json.loads(msg))

    @property
    def states(self):
        return [m for m in self.out["state"] if "type" not in m]

    def seq(self):
        """Decision states with consecutive repeats collapsed."""
        out = []
        for m in self.states:
            if not out or out[-1] != m["decision"]["state"]:
                out.append(m["decision"]["state"])
        return out


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_every_message_validates(name):
    r = Run(name).advance(SCENARIOS[name].period if SCENARIOS[name].period < 30 else 6)
    assert r.states, "no state messages"
    for m in r.states:
        assert messages.errors("state", m) == [], m["decision"]
    for m in r.out["obd"]:
        assert messages.errors("obd", m) == []
    for buf in r.out["signal"]:
        header, data = decode_frame(buf)
        assert messages.errors("signal_header", header) == []
        assert data.size == header["n_samples"]


def test_healthy_is_normal():
    r = Run("healthy").advance(5)
    assert r.seq() == ["NORMAL"]
    assert all(m["health"] == {"asset_ne107": "NORMAL", "sensors": {"mic": "NORMAL", "accel": "NORMAL", "obd": "NORMAL"}} for m in r.states)
    assert len(r.states) == 50 and len(r.out["obd"]) == 5 and len(r.out["signal"]) == 100 * 3


def test_misfire_raises_after_two_windows_and_stays_latched():
    r = Run("misfire").advance(19)
    assert r.seq() == ["NORMAL", "ADVISORY", "ALARM"]
    first_alarm = next(m for m in r.states if m["decision"]["state"] == "ALARM")
    assert first_alarm["t"] == pytest.approx(2.1)  # fault starts at 2.0; 2nd matching window is 2.1
    assert first_alarm["decision"]["alarm_id"] == "a_0001" and first_alarm["health"]["asset_ne107"] == "OFF_SPEC"
    last = r.states[-1]  # fault cleared at 8 s, never acknowledged
    assert last["decision"]["state"] == "ALARM" and last["decision"]["latched"] and not last["decision"]["acked"]


def test_ack_flow_through_runtime():
    r = Run("misfire").advance(3)
    alarm_id = r.states[-1]["decision"]["alarm_id"]

    def ack(aid, op="judge"):
        return json.dumps({"schema_version": "1.5", "type": "ack", "alarm_id": aid, "operator": op, "sent_at": r.now})

    before = r.states[-1]["decision"]
    for bad, reason in ((ack("a_9999"), "unknown_alarm_id"), ("not json", "invalid_message"),
                        (json.dumps({"type": "ack", "alarm_id": alarm_id}), "invalid_message")):
        err = r.rt.handle_ack(bad)
        assert err["type"] == "ack_error" and err["reason"] == reason
        assert {**r.rt.rules.decision(), "guidance": before["guidance"]} == before   # the broadcast adds guidance

    assert r.rt.handle_ack(ack(alarm_id)) is None
    r._drain()
    assert r.states[-1]["decision"]["acked"] and r.states[-1]["decision"]["state"] == "ALARM"  # broadcast at once
    assert r.rt.handle_ack(ack(alarm_id, "other"))["reason"] == "duplicate_ack"

    r.advance(4)  # fault still active until 8 s: ACK doesn't clear it
    assert r.states[-1]["decision"]["state"] == "ALARM"
    r.advance(1.3)  # healthy from 8.0; reset on the 2nd healthy window
    reset = next(m for m in r.states if m["decision"]["state"] == "NORMAL" and m["t"] > 7)
    assert reset["t"] == pytest.approx(8.1)


def test_advisory_and_conflict_never_alarm():
    assert Run("advisory").advance(12).seq() == ["NORMAL", "ADVISORY", "NORMAL"]
    r = Run("conflict").advance(12)
    assert r.seq() == ["NORMAL", "ADVISORY", "NORMAL"]
    assert all(m["decision"]["fault"] is None for m in r.states if m["decision"]["state"] == "ADVISORY")


def test_unclassified_scenario():
    r = Run("unclassified").advance(14)
    # the first anomalous window is not yet persistent: the physics hint alone shows as ADVISORY
    assert r.seq() == ["NORMAL", "ADVISORY", "UNCLASSIFIED", "NORMAL"]
    u = next(m for m in r.states if m["decision"]["state"] == "UNCLASSIFIED")
    assert u["decision"]["fault"] == "valve_tick" and u["health"]["asset_ne107"] == "OFF_SPEC"


def test_injector_clog_is_maintenance_required():
    r = Run("injector_clog").advance(5)
    assert r.states[-1]["decision"]["state"] == "ALARM"
    assert r.states[-1]["health"]["asset_ne107"] == "MAINTENANCE_REQUIRED"


def test_real_p3_has_no_localisation_fingerprint_or_obd():
    r = Run("real_p3").advance(3)
    assert r.seq() == ["NORMAL"] and not r.out["obd"]
    for m in r.states:
        assert m["source"] == "real" and m["localisation"] == "none" and m["signature"]["amplitude"] == []
        assert m["operating_point"]["rpm"] is None and m["health"]["sensors"] == {"mic": "NORMAL"}
        assert all(p["rule_basis"] == "firing_event" for p in m["physics_rules"])


def test_offline_scenario_goes_silent_on_every_channel():
    r = Run("offline").advance(9)
    times = [m["t"] for m in r.states]
    assert max(times) < 4.0 and len(times) == 40  # nothing at all from 4 s to 9 s
    assert all(m["channels"]["rpm"]["t"][0] < 4.0 for m in r.out["obd"])
    assert all(decode_frame(b)[0]["t0"] < 4.0 for b in r.out["signal"])
    r.advance(1)  # loop restarts: back online
    assert r.states[-1]["t"] >= 9.0


def test_backend_watchdog_flags_stalled_mic():
    r = Run("mic_dropout").advance(10)
    by_t = {round(m["t"], 1): m["health"]["sensors"] for m in r.states}
    assert by_t[2.9]["mic"] == "NORMAL"
    assert by_t[5.9]["mic"] == "NORMAL"  # last mic frame at 2.95 s; not yet 3 s
    assert by_t[6.1]["mic"] == "FAILURE" and by_t[6.1]["accel"] == "NORMAL"
    assert all(m["health"]["asset_ne107"] == "NORMAL" for m in r.states)  # sensor health stays separate
    r.advance(2)  # mic is back at 11 s
    assert r.states[-1]["health"]["sensors"]["mic"] == "NORMAL"
    assert not any(decode_frame(b)[0]["channel"] == "mic" and 3.0 <= decode_frame(b)[0]["t0"] < 11.0 for b in r.out["signal"])


def test_alarm_ids_never_repeat_across_scenario_switches():
    r = Run("misfire").advance(3)
    assert r.states[-1]["decision"]["alarm_id"] == "a_0001"
    r.rt.select("knock")
    r.t = 0.0
    r.advance(3)
    assert r.states[-1]["decision"]["alarm_id"] == "a_0002"
    assert r.rt.handle_ack(json.dumps({"schema_version": "1.5", "type": "ack", "alarm_id": "a_0001",
                                       "operator": "judge", "sent_at": 1.0}))["reason"] == "unknown_alarm_id"
