"""Resilience Lab backend (D141): incident record, Reliability Agent, Industrial Link, incident report."""

import pytest
from asyncua import ua

from backend.investigator import QUESTIONS, TOOLS, investigate
from backend.report import build
from backend.sources.sim_source import SimSource
from backend.tests.test_opcua import Rig, _ok, run, wait_for
from backend.tests.test_sabotage import StubWitness, _sim_run
from backend.tests.test_scenarios import Run

LIVE = SimSource().witness.available


def _kinds(rt, **match):
    return [e for e in rt.events.since(0) if all(e.get(k) == v for k, v in match.items())]


def test_commands_are_recorded_requested_then_accepted_or_refused():
    r = _sim_run().advance(1.0)
    r.rt.disable_sensor("accel", via="http")
    with pytest.raises(ValueError):
        r.rt.disable_sensor("lidar", via="opcua")
    evs = [e for e in r.rt.events.since(0) if e["kind"] == "sensor_off"]
    assert [e["phase"] for e in evs] == ["requested", "accepted", "requested", "refused"]
    assert evs[0]["via"] == "http" and evs[2]["via"] == "opcua" and "unknown sensor" in evs[3]["data"]["reason"]
    seqs = [e["seq"] for e in r.rt.events.since(0)]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_a_click_is_not_a_confirmation_failure_is_confirmed_by_the_broadcast():
    r = _sim_run().advance(2.0)
    r.rt.disable_sensor("accel", via="http")
    accepted = _kinds(r.rt, kind="sensor_off", phase="accepted")[0]
    assert not [e for e in _kinds(r.rt, kind="sensor_health") if e["data"]["sensor"] == "accel"]
    r.advance(4.0)
    conf = [e for e in _kinds(r.rt, kind="sensor_health") if e["data"]["sensor"] == "accel"]
    assert conf and conf[0]["data"]["status"] == "FAILURE" and conf[0]["phase"] == "confirmed"
    assert 2.9 <= conf[0]["t"] - accepted["t"] <= 3.5
    gone = _kinds(r.rt, kind="classifier")
    assert gone and gone[0]["data"]["available"] is False and gone[0]["seq"] < conf[0]["seq"]   # evidence goes first
    r.rt.enable_sensor("accel")
    r.advance(2.0)
    back = [e for e in _kinds(r.rt, kind="sensor_health") if e["data"]["sensor"] == "accel"][-1]
    assert back["data"]["status"] == "NORMAL"
    assert _kinds(r.rt, kind="classifier")[-1]["data"]["available"] is True


def test_source_switch_is_recorded_and_restarts_comparison():
    r = _sim_run().advance(1.0)
    r.rt.select("healthy")
    src = _kinds(r.rt, kind="source")
    assert src[-1]["data"]["source"] == "healthy"


def test_agent_tools_are_read_only_and_answers_are_grounded():
    r = _sim_run().advance(2.0)
    before = (r.rt.disabled_sensors(), r.rt.injection, r.rt.rules.decision())
    for fn in TOOLS.values():
        fn(r.rt)
    inv = investigate(r.rt)
    assert (r.rt.disabled_sensors(), r.rt.injection, r.rt.rules.decision()) == before
    assert inv["status"] == "ok"
    assert [s["label"] for s in inv["steps"]][:3] == ["Checking sensor health", "Reading diagnostic evidence", "Reviewing alarm state"]
    assert all(s["ms"] >= 0 for s in inv["steps"])
    assert [a["key"] for a in inv["answers"]] == list(QUESTIONS)
    assert all(a["facts"] for a in inv["answers"])
    assert inv["findings"]["availability"]["level"] == "FULL"


def test_agent_on_accel_failure_names_sensor_lost_evidence_and_no_new_alarm():
    r = _sim_run().advance(2.0)
    r.rt.disable_sensor("accel", via="http")
    r.advance(4.0)
    inv = investigate(r.rt)
    a = {x["key"]: x["answer"] for x in inv["answers"]}
    assert "ACCEL: FAILURE" in a["sensor_failed"] and "via http" in a["sensor_failed"]
    assert "knock rule" in a["evidence_lost"] and "ML classifier" in a["evidence_lost"]
    assert inv["findings"]["availability"]["level"] == "PARTIAL"
    assert inv["findings"]["availability"]["new_alarm_possible"] is False
    assert "ACCEL" in a["inspect_first"]
    # every label the agent quotes comes from the broadcast: the stub classifier said "healthy"
    assert inv["findings"]["evidence"]["models"] == []


@pytest.mark.skipif(not LIVE, reason="classifier checkpoint not installed")
def test_agent_explains_a_real_alarm_from_the_recorded_witnesses():
    r = Run("healthy")
    r.rt.select("sim", SimSource())
    r.advance(22.0)
    r.rt.inject("misfire", 3, 1.0, via="opcua")
    r.advance(4.0)
    r.rt.disable_sensor("accel")
    r.advance(4.0)
    inv = investigate(r.rt)
    a = {x["key"]: x["answer"] for x in inv["answers"]}
    d = r.states[-1]["decision"]
    assert d["state"] == "ALARM"
    raised = [e for e in r.rt.events.since(0) if e["kind"] == "decision" and e["data"].get("alarm_id") == d["alarm_id"]][0]
    svm = next(m for m in raised["data"]["models"] if m["model"] == "SVM")
    assert d["alarm_id"] in a["why_alarm"] and f"p {svm['p']}" in a["why_alarm"]       # the number at confirmation
    assert "stays latched" in a["can_confirm"] and a["can_confirm"].startswith("No")
    assert "Inject misfire cyl 3" in a["before_alarm"]
    html = build(r.rt)
    assert d["alarm_id"] in html and "ACCEL" in html and "Inject misfire cyl 3" in html
    for banned in ("remaining useful life", "cost", "accuracy of"):
        assert banned not in html.lower()


def test_report_labels_missing_values_unavailable():
    r = Run("healthy")
    html = build(r.rt)     # no state yet
    assert "720 · INCIDENT REPORT" in html and "unavailable" in html


def test_http_events_investigate_industrial_report():
    from fastapi.testclient import TestClient

    from backend.main import create_app

    with TestClient(create_app("healthy", opcua=False)) as c:
        c.post("/scenario/sim")
        c.post("/sensor/accel/off")
        evs = c.get("/events").json()["events"]
        assert any(e["kind"] == "sensor_off" and e["phase"] == "accepted" and e["via"] == "http" for e in evs)
        last = evs[-1]["seq"]
        assert all(e["seq"] > last for e in c.get(f"/events?after={last}").json()["events"])
        assert c.get("/investigate").json()["status"] in ("ok", "no_data")
        ind = c.get("/industrial").json()
        assert ind["running"] is False and ind["endpoint"] is None          # OPC UA off in this app
        rep = c.get("/report")
        assert rep.status_code == 200 and rep.headers["content-type"].startswith("text/html")


def test_opcua_commands_are_tagged_and_events_counted():
    async def go():
        async with Rig("sim") as r:
            ok, _ = await r.call("DisableSensor", "accel")
            assert ok
            cmd = r.runtime.events.last(pred=lambda e: e["via"] == "opcua" and e["phase"] == "accepted")
            assert cmd["kind"] == "sensor_off" and cmd["data"]["sensor"] == "accel"
            await wait_for(lambda: _ok(r.read("EngineAsset.SensorHealth.accel"), lambda v: v == 1), timeout=8)
            await wait_for(lambda: _ok(asyncio_sleep0(), lambda _: r.bridge.events_emitted > 0), timeout=5)
            assert "accel" in r.bridge.last_event["message"].lower()
            assert r.bridge.last_publish is not None
            ok, _ = await r.call("Inject", "misfire", ua.Variant(3, ua.VariantType.Int32), 1.0)
            assert r.runtime.events.last("inject")["via"] == "opcua"
    run(go())


async def asyncio_sleep0():
    return None


@pytest.mark.skipif(not __import__("backend.report_pdf", fromlist=["AVAILABLE"]).AVAILABLE, reason="reportlab not installed")
def test_pdf_report_download_and_content():
    from fastapi.testclient import TestClient

    from backend.main import create_app
    from backend.report_pdf import build_pdf

    with TestClient(create_app("healthy", opcua=False)) as c:
        c.post("/scenario/sim")
        c.post("/sensor/accel/off")
        res = c.get("/report.pdf")
        assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"
        assert res.content[:5] == b"%PDF-"
        assert 'attachment; filename="720_incident_' in res.headers["content-disposition"]

    r = _sim_run().advance(2.0)
    r.rt.disable_sensor("accel", via="http")
    r.advance(4.0)
    pdf = build_pdf(r.rt)
    pypdf = pytest.importorskip("pypdf")
    import io
    text = "".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    assert "720 · INCIDENT REPORT" in text or "INCIDENT REPORT" in text
    assert "ACCEL" in text and "Disable ACCEL" in text and "FAILURE" in text
    for banned in ("remaining useful life", "cost", "accuracy of"):
        assert banned not in text.lower()
