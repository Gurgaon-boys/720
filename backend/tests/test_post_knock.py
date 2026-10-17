"""Post-knock recovery (pending K1), on the live demo path, the same way as test_post_misfire.py.

The simulator source (SimSource, reference engine, seed 0) is run offline once per scenario; its per-window Evidence
is then stepped through two rule layers, the post-knock recovery gate off (the behaviour before) and on, so both see
identical evidence. An ALARM is acknowledged as soon as it appears, as an operator would.
"""

import pytest

from backend.rule_layer import RECOVERY_MAX_S, RuleLayer
from backend.runtime import Runtime
from backend.tests.test_post_misfire import alarms, evidence
from models.guidance import guidance

pytest.importorskip("scipy")


def run(evs, knock_recovery: bool):
    rl = RuleLayer(knock_recovery=knock_recovery)
    out = []
    for t, batch in evs:
        for ev in batch:
            d = rl.step(ev)
        out.append((t, d, rl.recovering_from))
        if d["state"] == "ALARM" and not d["acked"]:
            rl.ack(d["alarm_id"], "test", t)
    return out


def all_alarms(seq):
    """Every ALARM episode: (alarm_id, first t, fault, location)."""
    seen = {}
    for t, d, _ in seq:
        if d["state"] == "ALARM" and d["alarm_id"] not in seen:
            seen[d["alarm_id"]] = (t, d["fault"], d["location"])
    return seen


def unclassified_s(seq, after):
    """Seconds shown as UNCLASSIFIED after `after` (each decision holds until the next window)."""
    return round(sum(t1 - t0 for (t0, d, _), (t1, _, _) in zip(seq, seq[1:])
                     if t0 >= after and d["state"] == "UNCLASSIFIED"), 1)


# knock cyl 2 p = 1.0 at 25 s (ALARM at ~25.5 s), stopped at 26.2 s as in the finals smoke test, or at 40 s after
# several bursts (the knock period then also reaches the 30 s before the OBD window: spark_retard_deg goes negative)
@pytest.mark.parametrize("CLEAR_T", [26.2, 40.0])
def test_knock_then_clear_is_never_unclassified(CLEAR_T):
    evs = evidence({25.0: ("knock", 2, 1.0), CLEAR_T: None}, 135.0)
    before, after = run(evs, False), run(evs, True)
    # reproduced: without the gate, a cleared knock reads UNCLASSIFIED (anomaly only: spark timing still retarded)
    assert unclassified_s(before, CLEAR_T) >= 10.0
    assert all(d["fault"] is None and d["witnesses"] == [] for t, d, _ in before
               if t >= CLEAR_T and d["state"] == "UNCLASSIFIED")
    # ALARMs unchanged; with the gate no UNCLASSIFIED at all after the clear
    assert all_alarms(after) == all_alarms(before) and {f for _, f, _ in alarms(after)} == {"knock"}
    assert alarms(after, after=CLEAR_T + 2.0) == []
    assert unclassified_s(after, CLEAR_T) == 0.0
    rec = [(t, d) for t, d, r in after if r == "knock"]
    assert rec and min(t for t, _ in rec) < CLEAR_T + 2.0                     # recovery starts at the clear
    assert all((d["state"] == "ADVISORY" and d["fault"] in (None, "knock")) or (d["state"] == "ALARM" and d["fault"] == "knock")
               for _, d in rec)
    assert max(t for t, _ in rec) <= CLEAR_T + RECOVERY_MAX_S                 # at most 90 s after the last knock
    assert after[-1][1]["state"] == "NORMAL" and after[-1][2] is None         # settled by the end
    # the only windows that change are UNCLASSIFIED / NORMAL -> the recovery ADVISORY
    for (t, b, _), (_, a, r) in zip(before, after):
        if b["state"] != a["state"]:
            assert r == "knock" and b["state"] in ("UNCLASSIFIED", "NORMAL") and a["state"] == "ADVISORY", t


def test_sustained_knock_alarms_exactly_as_before():
    # knock left on: the ECU's retard suppresses it between bursts, so ALARMs come and go; every one is unchanged
    evs = evidence({25.0: ("knock", 2, 1.0)}, 90.0)
    before, after = run(evs, False), run(evs, True)
    assert len(all_alarms(before)) >= 3 and all_alarms(after) == all_alarms(before)
    for (t, b, _), (_, a, r) in zip(before, after):
        if b != a:
            assert r == "knock" and b["state"] in ("UNCLASSIFIED", "NORMAL"), t
            assert a["state"] == "ADVISORY" and a["fault"] is None and a["witnesses"] == [], t


@pytest.mark.parametrize("script", [{25.0: ("injector_clog", 1, 0.5)}, {25.0: ("misfire", 3, 1.0), 45.0: None}])
def test_clog_and_misfire_are_untouched_by_the_knock_gate(script):
    evs = evidence(script, 90.0)
    before, after = run(evs, False), run(evs, True)
    assert all_alarms(before) and all_alarms(after) == all_alarms(before)
    assert [d for _, d, _ in before] == [d for _, d, _ in after]


def test_knock_recovery_guidance_line():
    d = {"state": "ADVISORY", "fault": None, "location": None}
    h = {"asset_ne107": "NORMAL", "sensors": {}}
    assert guidance(d, h, None, "recovering_after_knock") == "Recovering after knock: spark timing re-advancing"


def test_runtime_names_the_recovery_it_is_in():
    rt = Runtime("sim", clock=lambda: 1000.0)
    rec = {"state": "ADVISORY", "fault": None}
    for cause in ("misfire", "knock"):
        rt.rules.recovering, rt.rules.recovering_from = True, cause
        assert rt.guidance_context(rec) == f"recovering_after_{cause}"
