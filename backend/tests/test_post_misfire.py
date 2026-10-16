"""Post-misfire recovery (D144-D147), on the live demo path.

The simulator source (SimSource, reference engine, seed 0) is run offline once per scenario; its per-window
Evidence is then stepped through two rule layers, the post-misfire recovery gate off (the behaviour before) and on,
so both see identical evidence. An ALARM is acknowledged as soon as it appears, as an operator would.
"""

import pytest

from backend.rule_layer import Evidence, RuleLayer
from backend.runtime import Runtime
from models.guidance import guidance

pytest.importorskip("scipy")


def evidence(script: dict, total_s: float) -> list[tuple[float, list]]:
    """[(t, [evidence of every window finished at this tick, oldest first])] from a SimSource run."""
    from backend.sources.sim_source import SimSource

    s = SimSource()
    out, k = [], 0
    while k * 0.1 < total_s:
        t = round(k * 0.1, 1)
        if t in script:
            s.clear_faults() if script[t] is None else s.inject(*script[t])
        w = s.window(k)
        k += 1
        if w is not None and w.fresh:
            out.append((w.t, [*w.backlog, w.evidence]))
    return out


def run(evs, recovery: bool):
    rl = RuleLayer(recovery=recovery)
    out = []
    for t, batch in evs:
        for ev in batch:
            d = rl.step(ev)
        out.append((t, d, rl.recovering))
        if d["state"] == "ALARM" and not d["acked"]:
            rl.ack(d["alarm_id"], "test", t)
    return out


def alarms(seq, after=0.0):
    seen = {}
    for t, d, _ in seq:
        if d["state"] == "ALARM" and d["alarm_id"] not in seen and t >= after:
            seen[d["alarm_id"]] = (t, d["fault"], d["location"])
    return list(seen.values())


@pytest.fixture(scope="module")
def misfire_then_clear():
    # misfire cyl 3 p = 1.0 at 25 s, cleared at 45 s, then 120 s more
    return evidence({25.0: ("misfire", 3, 1.0), 45.0: None}, 165.0)


def test_misfire_then_clear_gives_no_alarm_in_the_next_120_s(misfire_then_clear):
    before, after = run(misfire_then_clear, False), run(misfire_then_clear, True)
    # reproduced: without the gate, a two-witness injector_clog ALARM follows the clear
    assert any(f == "injector_clog" for t, f, _ in alarms(before, after=45.0))
    # with the gate: only misfire ALARMs (windows still holding misfire cycles finish just after the clear),
    # and no ALARM at all from 2 s after the clear
    assert {f for _, f, _ in alarms(after)} == {"misfire"}
    assert alarms(after, after=47.0) == []
    rec = [(t, d) for t, d, r in after if r]
    assert rec and min(t for t, _ in rec) < 50.0                            # recovery starts at the clear
    # never UNCLASSIFIED: the recovery ADVISORY, or the misfire ALARM while its latch is still resetting
    assert all((d["state"] == "ADVISORY" and d["fault"] is None) or (d["state"] == "ALARM" and d["fault"] == "misfire")
               for _, d in rec)
    assert sum(d["state"] == "ADVISORY" for _, d in rec) > 0.95 * len(rec)
    assert max(t for t, _ in rec) <= 45.0 + 90.0 + 1.0                       # at most 90 s after the last misfire
    assert after[-1][1]["state"] == "NORMAL"                                 # settled by the end


def test_recovery_guidance_line():
    d = {"state": "ADVISORY", "fault": None, "location": None}
    h = {"asset_ne107": "NORMAL", "sensors": {}}
    assert guidance(d, h, None, "recovering_after_misfire") == "Recovering after misfire: fuel trims re-adapting"
    assert guidance({**d, "state": "NORMAL"}, h, None, "warming_up").startswith("Warming up (20 s)")
    assert guidance({"state": "UNCLASSIFIED", "fault": None, "location": None}, {**h, "asset_ne107": "OFF_SPEC"},
                    None, "warming_up") == "Unknown pattern: inspect, do not clear"


@pytest.mark.parametrize("fault, cyl, sev", [("injector_clog", 1, 0.5), ("knock", 2, 1.0)])
def test_clog_and_knock_on_a_settled_engine_alarm_exactly_as_before(fault, cyl, sev):
    evs = evidence({25.0: (fault, cyl, sev)}, 90.0)
    before, after = run(evs, False), run(evs, True)
    a_b, a_a = alarms(before), alarms(after)
    assert a_b and a_b[0][1] == fault
    assert a_a[0] == a_b[0]                                     # same fault, same time, same cylinder
    assert [d["state"] for _, d, _ in before] == [d["state"] for _, d, _ in after]


# ---------------------------------------------------------------- bank-wide ALARM location

def models(label, p=0.88):
    return [{"model": "SVM", "label": label, "p": p}]


TRIM = [{"rule": "injector_clog", "fired": True, "location": None, "rule_basis": "obd"}]


def test_bank_wide_rule_with_hopping_classifier_cylinder_alarms_with_no_cylinder():
    rl = RuleLayer()
    rl.step(Evidence(models("injector_clog"), TRIM, 0.3, 0.5, classifier_location=4))
    d = rl.step(Evidence(models("injector_clog"), TRIM, 0.3, 0.5, classifier_location=2))
    assert d["state"] == "ALARM" and d["location"] is None
    d = rl.step(Evidence(models("injector_clog"), TRIM, 0.3, 0.5, classifier_location=2))
    assert d["location"] is None                                   # stays "bank" once it hopped


def test_bank_wide_rule_with_a_steady_classifier_cylinder_keeps_it():
    rl = RuleLayer()
    for _ in range(3):
        d = rl.step(Evidence(models("injector_clog"), TRIM, 0.3, 0.5, classifier_location=1))
    assert d["state"] == "ALARM" and d["location"] == 1


def test_a_cylinder_rule_keeps_its_own_cylinder():
    mis = [{"rule": "misfire", "fired": True, "location": 3, "rule_basis": "crank_angle"}]
    rl = RuleLayer()
    rl.step(Evidence(models("misfire"), mis, 0.9, 0.5, classifier_location=3))
    d = rl.step(Evidence(models("misfire"), mis, 0.9, 0.5, classifier_location=None))
    assert d["state"] == "ALARM" and d["location"] == 3


# ---------------------------------------------------------------- reset engine, warming up

def test_reset_engine_gives_a_fresh_run_and_warming_up_guidance():
    rt = Runtime("sim", clock=lambda: 1000.0)
    rt.inject("misfire", 3, 1.0)
    for k in range(5):
        rt._tick_state(k)
    rt.reset_engine()
    assert rt.injection is None and rt.resets == 1
    for k in range(20):
        rt._tick_state(k)
    m = rt.last_state
    assert m["run_id"].endswith("_r1") and m["injection"] is None
    assert m["decision"]["guidance"].startswith("Warming up (20 s)")
    rt.select("healthy")
    with pytest.raises(TypeError):
        rt.reset_engine()
