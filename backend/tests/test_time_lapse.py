"""Simulator time-lapse and the post-clog recovery window (pending T1-T2, docs/decisions_pending/time_lapse.md).

The time-lapse maps the runtime's tick (wall) time to stream time at 1, 5 or 10x; every decision is made in stream
time, so it shows the same decisions sooner. Auto mode runs at 10x only while warming up, recovering after a cleared
fault, or waiting for a slow fault to build, and plays at 1x when there is something to read.
"""

import pytest

import backend.runtime as runtime_mod
from backend import messages
from backend.runtime import Runtime
from backend.tests.test_post_misfire import alarms, evidence

pytest.importorskip("scipy")


def test_sim_source_maps_wall_to_stream_at_its_speed():
    from backend.sources.sim_source import SimSource

    s = SimSource()
    s.window(10)                                    # 1 s wall at 1x
    assert s.stream_t == pytest.approx(1.0, abs=0.06)
    s.set_speed(10)
    s.window(20)                                    # 1 s more wall at 10x
    assert s.stream_t == pytest.approx(11.0, abs=0.06)
    s.set_speed(1)
    s.window(30)                                    # never jumps back: rate changes only
    assert s.stream_t == pytest.approx(12.0, abs=0.06)
    with pytest.raises(ValueError):
        s.set_speed(3)


class Clock:
    def __init__(self):
        self.wall = 0.0


@pytest.fixture
def driven(monkeypatch):
    """A Runtime on the simulator, driven tick by tick: tick() runs all three loops for 0.1 s of wall time."""
    clock = Clock()
    monkeypatch.setattr(runtime_mod.time, "monotonic", lambda: 1000.0 + clock.wall)
    rt = Runtime("sim", clock=lambda: 1000.0 + clock.wall)
    rt.set_time_lapse("auto")                                       # what the server does at startup (main.py)
    rt.validate = True
    k = [0]

    def tick():
        i = k[0]
        rt._tick_state(i)
        rt._tick_signal(2 * i)
        rt._tick_signal(2 * i + 1)
        if i % 10 == 0:
            rt._tick_obd(i // 10)
        k[0] += 1
        clock.wall = k[0] * 0.1
        return rt.last_state

    def until(pred, max_s):
        t0 = clock.wall
        seen = []
        while clock.wall - t0 < max_s:
            m = tick()
            if m:
                seen.append(m)
                if pred(m):
                    return round(clock.wall - t0, 1), seen
        return None, seen

    return rt, until


def settled(m):
    return m["decision"]["state"] == "NORMAL" and not m["decision"]["guidance"] and m["time_lapse"]["speed"] == 1


def test_auto_time_lapse_warm_up_and_misfire_recovery_take_seconds(driven):
    rt, until = driven
    wall, seen = until(settled, 10)
    assert wall is not None and wall < 4.0                          # 20 s warm-up in ~2-3 s of wall time
    assert {m["time_lapse"]["reason"] for m in seen} >= {"warming_up"}
    assert all(not messages.errors("state", m) for m in seen)       # the contract's optional time_lapse field

    rt.inject("misfire", 3, 1.0)
    wall, seen = until(lambda m: m["decision"]["state"] == "ALARM", 5)
    assert wall is not None and seen[-1]["decision"]["fault"] == "misfire"
    assert all(m["time_lapse"]["speed"] == 1 for m in seen)         # the fault itself plays at 1x
    rt.ack(seen[-1]["decision"]["alarm_id"], "test", "test")
    rt.clear_faults()
    wall, seen = until(settled, 15)
    assert wall is not None and wall < 6.0                          # ~31 s of stream recovery in a few seconds
    assert not any(m["decision"]["state"] in ("ALARM", "UNCLASSIFIED") for m in seen[3:])
    assert "recovering" in {m["time_lapse"]["reason"] for m in seen}


def test_an_unacknowledged_alarm_plays_at_1x(driven):
    rt, until = driven
    until(settled, 10)
    rt.inject("knock", 2, 1.0)
    until(lambda m: m["decision"]["state"] == "ALARM", 5)
    rt.clear_faults()                                               # cleared but never ACKed: the latch holds
    _, seen = until(lambda m: False, 3)
    assert all(m["time_lapse"]["speed"] == 1 for m in seen if m["decision"]["state"] == "ALARM"
               and not m["decision"]["acked"])


def test_manual_1x_is_real_time(driven):
    rt, until = driven
    rt.set_time_lapse("manual", 1)
    wall, seen = until(lambda m: not m["decision"]["guidance"], 30)
    assert wall is not None and wall >= 19.0                        # the full 20 s warm-up in wall time
    assert all(m["time_lapse"] == {"mode": "manual", "speed": 1, "reason": None} for m in seen)
    with pytest.raises(ValueError):
        rt.set_time_lapse("manual", 3)


def test_time_lapse_survives_an_engine_reset(driven):
    rt, until = driven
    rt.set_time_lapse("manual", 5)
    rt.reset_engine()
    _, seen = until(lambda m: True, 1)
    assert seen[-1]["time_lapse"]["speed"] == 5 and seen[-1]["run_id"].endswith("_r1")


# ---------------------------------------------------------------- post-clog recovery window

@pytest.fixture(scope="module")
def clog_then_clear():
    # injector clog cyl 1 s = 0.5 at 25 s (ALARM at ~48 s), cleared at 60 s, then 90 s more
    return evidence({25.0: ("injector_clog", 1, 0.5), 60.0: None}, 150.0)


def test_cleared_clog_recovers_instead_of_reading_unclassified(clog_then_clear):
    from backend.rule_layer import RuleLayer

    def run(clog_recovery):
        rl = RuleLayer(clog_recovery=clog_recovery)
        out = []
        for t, batch in clog_then_clear:
            for ev in batch:
                d = rl.step(ev)
            out.append((t, d, rl.recovering_from))
            if d["state"] == "ALARM" and not d["acked"]:
                rl.ack(d["alarm_id"], "test", t)
        return out

    before, after = run(False), run(True)
    assert alarms(before) == alarms(after) and alarms(after)[0][1] == "injector_clog"   # ALARMs unchanged
    assert any(d["state"] == "UNCLASSIFIED" for t, d, _ in before if t > 60.0)          # reproduced
    assert not any(d["state"] == "UNCLASSIFIED" for t, d, _ in after if t > 60.0)
    assert any(r == "injector_clog" for t, d, r in after if t > 60.0)
    assert after[-1][1]["state"] == "NORMAL"
    # before the clear the window changes nothing: the clog is still there
    assert [d["state"] for t, d, _ in before if t <= 60.0] == [d["state"] for t, d, _ in after if t <= 60.0]


def test_recovery_guidance_after_clog():
    from models.guidance import guidance

    d = {"state": "ADVISORY", "fault": None, "location": None}
    h = {"asset_ne107": "NORMAL", "sensors": {}}
    assert guidance(d, h, None, "recovering_after_injector_clog") == \
        "Recovering after injector clog: fuel trims re-adapting"



def test_obd_keeps_its_1_hz_stream_rate_during_a_time_lapse(driven):
    rt, until = driven
    sent = []
    rt._listeners.append(lambda schema, msg: sent.append(msg) if schema == "obd" else None)
    until(lambda m: False, 2.5)                                     # warming up: 10x (from the first window on)
    t = sent[-1]["channels"]["rpm"]["t"]
    assert len(t) >= 8 and all(abs(b - a - 1.0) < 1e-6 for a, b in zip(t, t[1:]))
    assert all(not messages.errors("obd", m) for m in sent)


def test_the_replay_keeps_its_own_speed():
    from backend.tests.test_carobd_replay import cr, src

    rt = Runtime("healthy", validate=False, clock=lambda: 1000.0)
    rt._tl_mode = "auto"                                            # Auto on, as in the server
    rt.select(cr.NAME, src(10))
    for k in range(30):
        rt._tick_state(k)
    assert rt.scenario.speed == 10 and "time_lapse" not in rt.last_state
    with pytest.raises(TypeError):
        rt.set_time_lapse("manual", 5)


def test_speed_cap_steps_down_when_the_loop_lags_and_back_up_without_flapping(driven):
    rt, until = driven
    until(lambda m: m["time_lapse"]["speed"] == 10, 3)              # warming up at 10x
    rt._state_lag = 0.8                                             # this machine falls behind
    _, seen = until(lambda m: m["time_lapse"]["speed"] == 5, 2)
    assert seen[-1]["time_lapse"]["speed"] == 5
    _, seen = until(lambda m: False, 0.5)                           # no second step within 1 s
    assert {m["time_lapse"]["speed"] for m in seen} == {5}
    rt._state_lag = 0.0                                             # caught up: back to 10x only after 5 s
    rt.set_time_lapse("manual", 10)                                 # 10x wanted all along
    wall, seen = until(lambda m: m["time_lapse"]["speed"] == 10, 8)
    assert wall is not None and 4.5 <= wall <= 6.0
    assert {m["time_lapse"]["speed"] for m in seen[:-1]} == {5}     # held at 5x, then one step up: no flapping


def test_an_injection_during_warm_up_plays_its_onset_at_1x(driven):
    rt, until = driven
    until(lambda m: m["time_lapse"]["reason"] == "warming_up", 2)
    rt.inject("misfire", 3, 1.0)
    _, seen = until(lambda m: False, 2.5)
    assert all(m["time_lapse"]["speed"] == 1 for m in seen[1:])
