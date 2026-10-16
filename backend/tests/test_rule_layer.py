"""Rule layer acceptance tests: one window at a time, no server."""

from backend.rule_layer import Evidence, RuleLayer, asset_ne107, assess, classifier_label


def models(label, p=0.88, baseline=None):
    out = [{"model": m, "label": label, "p": p} for m in ("SVM", "ANN", "CNN")]
    if baseline:
        out.insert(0, {"model": "Baseline", "label": baseline, "p": 0.7})
    return out


def rule(name, fired=True, location=3):
    return {"rule": name, "fired": fired, "location": location if fired else None, "rule_basis": "crank_angle"}


HEALTHY = Evidence(models("healthy", 0.93), [rule("misfire", False)], 0.2, 0.6)
MISFIRE = Evidence(models("misfire"), [rule("misfire")], 0.82, 0.6, severity=0.12)
KNOCK = Evidence(models("knock"), [rule("knock", location=2)], 0.8, 0.6, severity=0.3)
CLASSIFIER_ONLY = Evidence(models("knock"), [rule("knock", False)], 0.5, 0.6)
PHYSICS_ONLY = Evidence(models("healthy", 0.9), [rule("misfire")], 0.4, 0.6)
CONFLICT = Evidence(models("misfire"), [rule("knock")], 0.8, 0.6)
VALVE = Evidence(
    [{"model": "SVM", "label": "knock", "p": 0.41}, {"model": "ANN", "label": "healthy", "p": 0.48}, {"model": "CNN", "label": "misfire", "p": 0.39}],
    [rule("valve_tick", location=2)],
    0.91,
    0.6,
)


def run(layer, *windows):
    return [layer.step(w) for w in windows]


def test_healthy_is_normal():
    layer = RuleLayer()
    for d in run(layer, *[HEALTHY] * 20):
        assert d["state"] == "NORMAL" and d["alarm_id"] is None and d["witnesses"] == []
        assert asset_ne107(d) == "NORMAL"


def test_one_witness_is_advisory():
    for ev, witness, fault in ((CLASSIFIER_ONLY, "classifier", "knock"), (PHYSICS_ONLY, "physics", "misfire")):
        layer = RuleLayer()
        for d in run(layer, *[ev] * 6):
            assert d["state"] == "ADVISORY" and d["witnesses"] == [witness] and d["fault"] == fault
            assert not d["latched"] and asset_ne107(d) == "NORMAL"


def test_alarm_after_exactly_two_matching_windows():
    layer = RuleLayer()
    first, second = run(layer, MISFIRE, MISFIRE)
    assert first["state"] == "ADVISORY" and first["consecutive_windows"] == 1 and first["alarm_id"] is None
    assert second["state"] == "ALARM" and second["consecutive_windows"] == 2
    assert second["alarm_id"] == "a_0001" and second["fault"] == "misfire" and second["location"] == 3
    assert second["witnesses"] == ["physics", "classifier"] and second["latched"] and not second["acked"]
    assert asset_ne107(second) == "OFF_SPEC"


def test_interrupted_streak_does_not_alarm():
    layer = RuleLayer()
    for d in run(layer, MISFIRE, HEALTHY, MISFIRE, CLASSIFIER_ONLY, MISFIRE, KNOCK):
        assert d["state"] != "ALARM"


def test_conflicting_labels_never_alarm():
    layer = RuleLayer()
    for d in run(layer, *[CONFLICT] * 10):
        assert d["state"] == "ADVISORY" and d["fault"] is None and not d["latched"]


def test_alarm_stays_latched_when_fault_clears_before_ack():
    layer = RuleLayer()
    run(layer, MISFIRE, MISFIRE)
    for d in run(layer, *[HEALTHY] * 30):
        assert d["state"] == "ALARM" and d["alarm_id"] == "a_0001" and d["latched"] and not d["acked"]
        assert d["witnesses"] == ["physics", "classifier"] and d["consecutive_windows"] == 2


def test_ack_marks_acknowledged_without_clearing_active_fault():
    layer = RuleLayer()
    run(layer, MISFIRE, MISFIRE)
    res = layer.ack("a_0001", "judge", 1760551240.1)
    assert res.ok
    d = layer.decision()
    assert d["state"] == "ALARM" and d["acked"] and d["acked_by"] == "judge" and d["acked_at"] == 1760551240.1
    for d in run(layer, *[MISFIRE] * 10):
        assert d["state"] == "ALARM" and d["acked"] and d["alarm_id"] == "a_0001"
    assert d["consecutive_windows"] == 12


def test_reset_only_after_ack_plus_two_windows_without_its_fault_matching():
    layer = RuleLayer()
    run(layer, MISFIRE, MISFIRE)
    layer.ack("a_0001", "judge", 1.0)
    one, two = run(layer, HEALTHY, HEALTHY)
    assert one["state"] == "ALARM"
    assert two["state"] == "NORMAL" and two["alarm_id"] is None and not two["latched"]

    # a window where its fault matches again restarts the count
    layer = RuleLayer()
    run(layer, MISFIRE, MISFIRE)
    layer.ack("a_0001", "judge", 1.0)
    states = [d["state"] for d in run(layer, HEALTHY, MISFIRE, HEALTHY, HEALTHY)]
    assert states == ["ALARM", "ALARM", "ALARM", "NORMAL"]


def test_acked_alarm_resets_while_an_anomaly_remains_and_shows_it():
    """D124: after ACK, an alarm whose own fault no longer matches resets even if the engine isn't
    healthy yet (e.g. the ECU still recovering). D134: while the misfire rule fired in the last 3 windows the
    remaining anomaly corroborates a misfire ADVISORY; after that hold it shows as UNCLASSIFIED."""
    anomalous = Evidence(models("healthy", 0.45), [rule("misfire", False)], 0.9, 0.6)
    layer = RuleLayer()
    run(layer, MISFIRE, MISFIRE)
    layer.ack("a_0001", "judge", 1.0)
    ds = run(layer, anomalous, anomalous, anomalous, anomalous)
    assert [d["state"] for d in ds] == ["ALARM", "ADVISORY", "UNCLASSIFIED", "UNCLASSIFIED"]
    assert ds[1]["fault"] == "misfire" and ds[1]["witnesses"] == ["physics"]

    old = RuleLayer(corroborate_windows=0)               # before D134
    run(old, MISFIRE, MISFIRE)
    old.ack("a_0001", "judge", 1.0)
    assert [d["state"] for d in run(old, anomalous, anomalous, anomalous)] == ["ALARM", "UNCLASSIFIED", "UNCLASSIFIED"]


def test_one_witness_of_its_fault_does_not_hold_an_acked_alarm():
    """Only a two-witness match holds the latch; one witness left over shows as ADVISORY."""
    layer = RuleLayer()
    run(layer, MISFIRE, MISFIRE)
    layer.ack("a_0001", "judge", 1.0)
    one, two = run(layer, PHYSICS_ONLY, PHYSICS_ONLY)
    assert one["state"] == "ALARM" and two["state"] == "ADVISORY" and two["fault"] == "misfire"


def test_unacked_alarm_never_resets_and_a_new_fault_shows_once_the_acked_one_resets():
    layer = RuleLayer()
    run(layer, MISFIRE, MISFIRE)
    assert all(d["state"] == "ALARM" and d["fault"] == "misfire" for d in run(layer, *[KNOCK] * 6))   # not acked: stays
    layer.ack("a_0001", "judge", 1.0)
    d = run(layer, KNOCK, KNOCK)                 # misfire no longer matching: resets, knock raises its own alarm
    assert d[-1]["state"] == "ALARM" and d[-1]["fault"] == "knock" and d[-1]["alarm_id"] == "a_0002"


def test_ack_after_fault_cleared_resets_on_next_evaluation_not_on_ack():
    layer = RuleLayer()
    run(layer, MISFIRE, MISFIRE, *[HEALTHY] * 5)
    layer.ack("a_0001", "judge", 1.0)
    assert layer.decision()["state"] == "ALARM"  # the ACK itself never clears
    assert layer.step(HEALTHY)["state"] == "NORMAL"


def test_new_episode_gets_new_alarm_id():
    layer = RuleLayer()
    run(layer, MISFIRE, MISFIRE)
    layer.ack("a_0001", "judge", 1.0)
    run(layer, HEALTHY, HEALTHY)
    d = run(layer, KNOCK, KNOCK)[-1]
    assert d["state"] == "ALARM" and d["alarm_id"] == "a_0002" and d["fault"] == "knock" and d["location"] == 2


def test_invalid_acks_rejected_without_state_change():
    layer = RuleLayer()
    assert layer.ack("a_0001", "judge", 1.0).reason == "unknown_alarm_id"  # nothing latched yet
    run(layer, MISFIRE, MISFIRE)
    before = layer.decision()

    res = layer.ack("a_9999", "judge", 1.0)
    assert not res.ok and res.reason == "unknown_alarm_id"
    assert layer.decision() == before

    assert layer.ack("a_0001", "judge", 2.0).ok
    acked = layer.decision()
    res = layer.ack("a_0001", "someone_else", 3.0)
    assert not res.ok and res.reason == "duplicate_ack"
    assert layer.decision() == acked and acked["acked_by"] == "judge" and acked["acked_at"] == 2.0

    run(layer, HEALTHY, HEALTHY)
    res = layer.ack("a_0001", "judge", 4.0)
    assert not res.ok and res.reason == "duplicate_ack"
    assert layer.decision()["state"] == "NORMAL"


def test_unclassified_with_physics_hint():
    layer = RuleLayer()
    ds = run(layer, *[VALVE] * 8)
    # the first anomalous window is not yet persistent: the physics hint alone is an ADVISORY
    assert ds[0]["state"] == "ADVISORY" and ds[0]["fault"] == "valve_tick" and asset_ne107(ds[0]) == "NORMAL"
    for d in ds[1:]:
        assert d["state"] == "UNCLASSIFIED" and d["fault"] == "valve_tick" and d["location"] == 2
        assert d["witnesses"] == ["physics"] and not d["latched"]
        assert asset_ne107(d) == "OFF_SPEC"


def test_unclassified_without_hint():
    ev = Evidence(models("healthy", 0.45), [rule("misfire", False)], 0.9, 0.6)
    first, second = run(RuleLayer(), ev, ev)
    assert first["state"] == "NORMAL"
    assert second["state"] == "UNCLASSIFIED" and second["fault"] is None and second["witnesses"] == []


def test_unclassified_needs_two_consecutive_windows():
    anom = Evidence(models("healthy", 0.45), [rule("misfire", False)], 0.9, 0.6)
    seq = [d["state"] for d in run(RuleLayer(), anom, HEALTHY, anom, HEALTHY, anom, anom, anom, HEALTHY)]
    assert seq == ["NORMAL"] * 5 + ["UNCLASSIFIED", "UNCLASSIFIED", "NORMAL"]


def test_injector_clog_alarm_is_maintenance_required():
    clog = Evidence(models("injector_clog"), [rule("injector_clog", location=1)], 0.7, 0.6, severity=0.3)
    d = run(RuleLayer(), clog, clog)[-1]
    assert d["state"] == "ALARM" and asset_ne107(d) == "MAINTENANCE_REQUIRED"


def test_classifier_majority():
    assert classifier_label(models("misfire", baseline="healthy")) == "misfire"  # Baseline doesn't vote
    assert classifier_label(models("misfire", p=0.5)) is None  # majority not confident
    assert classifier_label(VALVE.models) is None  # no majority
    assert classifier_label([{"model": "Baseline", "label": "knock", "p": 0.8}]) == "knock"
    assert assess(HEALTHY).kind == "healthy"


def test_check_function_only_in_maintenance_and_never_over_a_fault():
    layer = RuleLayer()
    normal = layer.step(HEALTHY)
    assert asset_ne107(normal) == "NORMAL" and asset_ne107(normal, maintenance=True) == "CHECK_FUNCTION"
    advisory = RuleLayer().step(CLASSIFIER_ONLY)
    assert asset_ne107(advisory, maintenance=True) == "CHECK_FUNCTION"
    alarm = run(RuleLayer(), MISFIRE, MISFIRE)[-1]
    assert asset_ne107(alarm, maintenance=True) == "OFF_SPEC"
    clog = Evidence(models("injector_clog"), [rule("injector_clog", location=1)], 0.7, 0.6)
    assert asset_ne107(run(RuleLayer(), clog, clog)[-1], maintenance=True) == "MAINTENANCE_REQUIRED"
    assert asset_ne107(run(RuleLayer(), VALVE, VALVE)[-1], maintenance=True) == "OFF_SPEC"


# ---------------------------------------------------------------- L2: the classifier's cylinder

def test_match_needs_the_same_cylinder_when_both_name_one():
    agree = Evidence(models("misfire"), [rule("misfire", location=3)], 0.8, 0.5, classifier_location=3)
    disagree = Evidence(models("misfire"), [rule("misfire", location=3)], 0.8, 0.5, classifier_location=1)
    assert assess(agree).kind == "match" and assess(agree).location == 3
    assert assess(disagree).kind != "match"
    layer = RuleLayer()
    assert [d["state"] for d in run(layer, disagree, disagree, disagree)] == ["ADVISORY"] * 3
    assert [d["state"] for d in run(layer, agree, agree)] == ["ADVISORY", "ALARM"]


def test_bank_wide_rule_takes_the_classifier_cylinder():
    trim = {"rule": "injector_clog", "fired": True, "location": None, "rule_basis": "obd"}
    ev = Evidence(models("injector_clog"), [trim], 0.8, 0.5, classifier_location=2)
    a = assess(ev)
    assert a.kind == "match" and a.fault == "injector_clog" and a.location == 2
    layer = RuleLayer()
    d = run(layer, ev, ev)[-1]
    assert d["state"] == "ALARM" and d["location"] == 2


# ---------------------------------------------------------------- D134: anomaly corroborates a named fault

TRIM = Evidence([], [rule("injector_clog", location=None)], 0.9, 0.5)          # trim rule + anomaly, no classifier
TRIM_QUIET = Evidence([], [rule("injector_clog", False, location=None)], 0.9, 0.5)
ANOMALY_ONLY = Evidence([], [rule("injector_clog", False, location=None)], 0.9, 0.5)


def test_anomaly_with_a_named_physics_fault_is_its_advisory_not_unclassified():
    ds = run(RuleLayer(), TRIM, TRIM, TRIM, TRIM)
    assert all(d["state"] == "ADVISORY" and d["fault"] == "injector_clog" and d["witnesses"] == ["physics"] for d in ds)
    # before D134 the same evidence went UNCLASSIFIED from the 2nd window
    assert [d["state"] for d in run(RuleLayer(corroborate_windows=0), TRIM, TRIM, TRIM)] ==         ["ADVISORY", "UNCLASSIFIED", "UNCLASSIFIED"]


def test_the_hold_lasts_3_windows_then_an_unnamed_anomaly_is_unclassified():
    # the rule fired once; windows 2 and 3 are inside the hold, window 4 is not
    states = [d["state"] for d in run(RuleLayer(), TRIM, TRIM_QUIET, TRIM_QUIET, TRIM_QUIET, TRIM_QUIET)]
    assert states == ["ADVISORY", "ADVISORY", "ADVISORY", "UNCLASSIFIED", "UNCLASSIFIED"]


def test_no_named_fault_in_the_hold_still_raises_unclassified():
    assert [d["state"] for d in run(RuleLayer(), ANOMALY_ONLY, ANOMALY_ONLY)] == ["NORMAL", "UNCLASSIFIED"]


def test_valve_tick_rule_is_a_hint_not_a_named_fault():
    """Valve tick is never trained: its rule stays a hint on UNCLASSIFIED (the open-set demo)."""
    d = run(RuleLayer(), VALVE, VALVE)[-1]
    assert d["state"] == "UNCLASSIFIED" and d["fault"] == "valve_tick"


def test_classifier_match_and_alarm_are_unchanged_by_corroboration():
    assert [d["state"] for d in run(RuleLayer(), MISFIRE, MISFIRE)] == ["ADVISORY", "ALARM"]
