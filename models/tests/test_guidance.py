"""Operator guidance: every (fault, NE 107) pairing the rule layer can produce has one fixed line."""

import pytest

from backend.rule_layer import ALARM_NE107, Evidence, RuleLayer, asset_ne107
from models.guidance import guidance


def dec(state, fault=None, location=None):
    return {"state": state, "fault": fault, "location": location}


def h(asset="NORMAL", **sensors):
    return {"asset_ne107": asset, "sensors": {"mic": "NORMAL", **sensors}}


def test_alarm_lines():
    assert guidance(dec("ALARM", "misfire", 3), h("OFF_SPEC")) == "Reduce load; inspect coil and plug on cyl 3"
    assert guidance(dec("ALARM", "knock", 2), h("OFF_SPEC")) == "Reduce load; check fuel quality and timing"
    assert guidance(dec("ALARM", "injector_clog", 1), h("MAINTENANCE_REQUIRED")) == "Schedule injector inspection; safe to run"


def test_every_alarm_fault_of_the_rule_layer_has_a_line():
    for fault, ne in ALARM_NE107.items():
        d = dec("ALARM", fault, 1)
        assert asset_ne107({**d, "alarm_id": "a"}) == ne
        assert guidance(d, h(ne))


def test_unclassified_sensor_failure_advisory_normal():
    assert guidance(dec("UNCLASSIFIED"), h("OFF_SPEC")) == "Unknown pattern: inspect, do not clear"
    assert guidance(dec("NORMAL"), h(accel="FAILURE")) == "Check sensor wiring (accel); engine state unknown"
    assert guidance(dec("ADVISORY", "knock", 2), h()) == "One witness only (knock): keep running, watch for ALARM"
    assert guidance(dec("NORMAL"), h()) is None


def test_precedence_alarm_then_unclassified_then_sensor_failure():
    assert guidance(dec("ALARM", "misfire", 4), h("OFF_SPEC", crank="FAILURE")).startswith("Reduce load")
    assert guidance(dec("UNCLASSIFIED"), h("OFF_SPEC", obd="FAILURE")).startswith("Unknown pattern")
    assert guidance(dec("ADVISORY", "misfire", 1), h(obd="FAILURE", accel="FAILURE")) == \
        "Check sensor wiring (accel, obd); engine state unknown"


def test_deterministic_and_unknown_alarm_pairing_raises():
    d, hh = dec("ALARM", "knock", 1), h("OFF_SPEC")
    assert len({guidance(d, hh) for _ in range(5)}) == 1
    with pytest.raises(KeyError):
        guidance(dec("ALARM", "knock", 1), h("MAINTENANCE_REQUIRED"))


def test_live_decision_from_the_rule_layer():
    m = Evidence([{"model": "SVM", "label": "misfire", "p": 0.9}],
                 [{"rule": "misfire", "fired": True, "location": 2, "rule_basis": "crank_angle"}], 0.2, 0.5)
    layer = RuleLayer()
    layer.step(m)
    d = layer.step(m)
    assert guidance(d, {"asset_ne107": asset_ne107(d), "sensors": {}}) == "Reduce load; inspect coil and plug on cyl 2"


SINGLE = "Single witness on real OBD data: ALARM needs a second witness this source doesn't have."


def test_single_witness_real_obd_advisory_never_promises_an_alarm():
    d = dec("ADVISORY", "injector_clog")
    assert "watch for ALARM" in guidance(d, h())
    line = guidance(d, h(), "single_witness_real_obd")
    assert line == SINGLE and "watch for ALARM" not in line
    # other states keep their lines; NORMAL stays None
    assert guidance(dec("UNCLASSIFIED"), h("OFF_SPEC"), "single_witness_real_obd") == guidance(dec("UNCLASSIFIED"), h("OFF_SPEC"))
    assert guidance(dec("NORMAL"), h(), "single_witness_real_obd") is None
    with pytest.raises(KeyError):
        guidance(d, h(), "no_such_capability")
