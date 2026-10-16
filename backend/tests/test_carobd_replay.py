"""REAL carOBD replay (contract 1.6): timing, injection labelling, the witness plug, upload validation and
path safety, and contract validation of every message. Needs the carOBD files (data/raw/carobd)."""

import json
import math
import socket
import threading
import time

import httpx
import numpy as np
import pytest
import uvicorn
from websockets.sync.client import connect

from backend import messages
from backend.main import create_app
from backend.rule_layer import RuleLayer
from backend.runtime import Runtime
from backend.sources import carobd_replay as cr
from models.witness import WitnessOut

pytestmark = pytest.mark.skipif(not (cr.carobd.CAROBD_DIR / "drive2.csv").exists(),
                                reason="carOBD not downloaded (python data/carobd_inventory.py)")
FILE = "heldout:drive2.csv"


def src(speed=10, **kw):
    return cr.CarObdReplay(FILE, speed, classifier=kw.get("classifier"))


def run_rules(s, k_end, inject_at=None, clear_at=None, severity=0.5):
    """Step the rule layer like the runtime does; returns [(k, file t, label, decision)] for fresh windows."""
    rl, out = RuleLayer(), []
    for k in range(k_end):
        if k == inject_at:
            s.inject("lean_drift", None, severity)
        if k == clear_at:
            s.clear_faults()
        w = s.window(k)
        if w.fresh:
            for ev in w.backlog:
                rl.step(ev)
            out.append((k, w.t, w.fields["source_label"], rl.step(w.evidence)))
    return out


# ---------------------------------------------------------------- timing

@pytest.mark.parametrize("speed", [1, 10, 30])
def test_file_time_follows_the_speed_and_windows_hop_15_s(speed):
    s = src(speed)
    n = min(600, int(s.t[-1] / (0.1 * speed)))      # one pass of the file at most
    ts = [s.window(k).t for k in range(n)]
    # 10 Hz state ticks: file time = k * 0.1 s * speed
    assert ts == pytest.approx([k * 0.1 * speed for k in range(n)], abs=1e-6)
    # an OBD branch window completes every 15 s of file time once 30 s are in (engine-off windows skipped)
    span = (n - 1) * 0.1 * speed
    assert int((span - 30) // 15) - 2 <= s.windows_done <= int((span - 30) // 15) + 1


def test_obd_values_go_out_at_their_file_times_and_only_when_refreshed():
    s = src(10)
    sent = {}
    for i in range(0, 60):
        ch = s.obd_channels(i)
        for name, c in (ch or {}).items():
            assert c["source"] == "real_carobd" and messages.errors("obd", messages.obd_message(sent_at=0, channels=ch)) == []
            assert all(i * 10 - 10 - 1e-9 <= t <= i * 10 + 1e-9 for t in c["t"]) or i == 0
            sent.setdefault(name, []).extend(zip(c["t"], c["v"]))
    rpm = sent["rpm"]
    # never two equal values in a row: a value repeated by the logger's round-robin polling is not resent
    assert all(a[1] != b[1] for a, b in zip(rpm, rpm[1:]))
    # ~0.55 Hz refresh per PID: far fewer values than the ~2 rows/s the logger writes
    rows = int(np.searchsorted(s.t, 590, side="right"))
    assert len(rpm) < 0.6 * rows


def test_speed_change_keeps_the_file_position():
    s = src(10)
    s.window(100)                       # 10 s wall -> 100 s of file
    assert s.stream_t == pytest.approx(100)
    s.set_speed(30)
    s.window(110)                       # +1 s wall at 30x
    assert s.stream_t == pytest.approx(130)
    with pytest.raises(ValueError):
        s.set_speed(5)


def test_the_replay_loops_with_a_new_run_id():
    s = src(30)
    first = s.window(0).fields["run_id"]
    k_wrap = math.ceil(s.duration / 3.0) + 1         # 3 s of file per tick at 30x
    w = s.window(k_wrap)
    assert w.fields["run_id"] != first and w.fields["replay"]["loop"] == 1 and w.t < 10


# ---------------------------------------------------------------- injection and labels

def test_lean_drift_steady_total_is_s_over_4_minus_s():
    s = src(10)
    s.inject("lean_drift", None, 0.5)
    tau = np.array([0.0, 30.0, 60.0, 5000.0])
    st, lt = s._added_trim(tau)
    total = st + lt
    assert total[0] == 0 and total[1] == pytest.approx(100 * 0.5 / 3.5 / 2)
    assert total[3] == pytest.approx(100 * 0.5 / 3.5) and lt[3] == pytest.approx(total[3], rel=1e-6)   # LTFT learnt it


def test_lean_drift_is_labelled_real_injected_and_raises_the_trim_advisory_then_clears():
    s = src(10)
    out = run_rules(s, 1000, inject_at=300, clear_at=700)
    before = [d for k, _, lab, d in out if k < 300]
    during = [(lab, d) for k, _, lab, d in out if 300 <= k < 700]
    after = [(lab, d) for k, _, lab, d in out if k >= 760]
    assert all(d["state"] == "NORMAL" for d in before)
    assert all(lab == "real_replay_injected" for lab, _ in during)
    assert any(d["state"] == "ADVISORY" and d["fault"] == "injector_clog" and d["witnesses"] == ["physics"]
               for _, d in during)
    # single witness: never ALARM
    assert all(d["state"] != "ALARM" for _, _, _, d in out)
    assert all(lab == "real_replay" and d["state"] == "NORMAL" for lab, d in after)


def test_replay_rejects_faults_it_cannot_carry():
    s = src(10)
    with pytest.raises(ValueError, match="lean_drift"):
        s.inject("misfire", 3, 1.0)
    with pytest.raises(ValueError):
        s.inject("lean_drift", None, 0)


def test_runtime_echoes_lean_drift_with_no_cylinder_and_every_message_validates():
    rt = Runtime("healthy", validate=False, clock=lambda: 1000.0)
    rt.select(cr.NAME, src(10))
    sent = []
    rt.add_listener(lambda kind, m: sent.append((kind, m)))
    for k in range(0, 400):
        if k == 100:
            rt.inject("lean_drift", None, 0.5)
        if k == 350:
            rt.clear_faults()
        rt._tick_state(k)
        if k % 10 == 0:
            rt._tick_obd(k // 10)
    states = [m for kind, m in sent if kind == "state"]
    obds = [m for kind, m in sent if kind == "obd"]
    assert states and obds
    for kind, m in sent:
        assert messages.errors(kind, m) == [], (kind, messages.errors(kind, m))
    for m in states:
        assert m["mode"] == "replay" and m["localisation"] == "none" and m["replay"]["file"] == "drive2.csv"
        assert m["replay"]["max_decision"] == "ADVISORY" and "never ALARM" in m["replay"]["note"]
        assert m["decision"]["state"] != "ALARM"
    adv = [m for m in states if m["decision"]["state"] == "ADVISORY"]
    assert adv and all(m["decision"]["guidance"] == "Single witness on real OBD data: ALARM needs a second witness "
                       "this source doesn't have." for m in adv)
    inj = [m for m in states if m["injection"] is not None]
    assert inj and all(m["injection"]["fault"] == "lean_drift" and m["injection"]["cylinder"] is None
                       and m["source"] == "injected" and m["source_label"] == "real_replay_injected" for m in inj)
    assert states[-1]["injection"] is None and states[-1]["source"] == "real"
    assert states[-1]["source_label"] == "real_replay"
    assert {m["source_label"] for m in obds} >= {"real_replay", "real_replay_injected"}


# ---------------------------------------------------------------- witness 2 (Diljit's OBD classifier)

def fake_clog_classifier(w):
    label = "injector_clog" if w["total_trim"] > 7.0 else "healthy"
    return WitnessOut([{"model": "OBD-fake", "label": label, "p": 0.9}], None, None, 0.1)


def test_with_an_obd_classifier_alarm_follows_the_two_witness_rule():
    s = src(10, classifier=fake_clog_classifier)
    out = run_rules(s, 700, inject_at=300)
    alarms = [d for _, _, _, d in out if d["state"] == "ALARM"]
    assert alarms and alarms[0]["fault"] == "injector_clog" and alarms[0]["witnesses"] == ["physics", "classifier"]
    assert s.replay_info()["max_decision"] == "ALARM" and s.guidance_capability is None


def test_classifier_is_absent_unless_models_obd_classifier_exists():
    assert cr.load_obd_classifier() is None          # nothing on main yet: single witness, said in state


# ---------------------------------------------------------------- files, upload, path safety

def test_only_held_out_files_and_well_formed_upload_ids_resolve():
    assert cr.resolve(FILE)[2] == "drive"
    for bad in ("heldout:drive1.csv",              # a calibration file
                "heldout:../../README.md", "upload:../../x", "upload:ABCDEF012345", "drive2.csv", "x:y"):
        with pytest.raises(ValueError):
            cr.resolve(bad)


def test_file_list_groups_held_out_files_by_type():
    files = cr.list_files()
    types = {f["type"] for f in files if f["id"].startswith("heldout:")}
    assert types == {"idle", "drive", "live", "campus", "long"}
    assert len([f for f in files if f["id"].startswith("heldout:")]) == 39


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "UPLOAD_DIR", tmp_path / "uploads")
    return tmp_path / "uploads"


def raw(name="drive5.csv"):
    return (cr.carobd.CAROBD_DIR / name).read_bytes()


def test_upload_valid_file_is_stored_under_a_random_id_and_replays_as_real_upload(uploads):
    meta = cr.save_upload("..\\..\\evil/../drive copy.csv", raw(), 1.0)
    files = sorted(p.name for p in uploads.iterdir())
    assert files == [f"{meta['id']}.csv", f"{meta['id']}.json"]
    assert meta["name"] == "drive_copy.csv" and meta["label"] == "REAL (uploaded, unverified)"
    s = cr.CarObdReplay(f"upload:{meta['id']}", 30, classifier=None)
    w = s.window(50)
    assert w.fields["source_label"] == "real_upload" and w.fields["replay"]["uploaded"] is True
    assert s.obd_channels(0)["rpm"]["source"] == "real_upload"


@pytest.mark.parametrize("data, match", [
    (b"", "empty"),
    (b"a,b,c\n1,2,3\n", "ENGINE_RUN_TIME|missing"),
    (raw().replace(b"ENGINE_RPM ()", b"RPM_X ()"), "ENGINE_RPM"),
    (raw().decode("latin-1").replace(",", ",x", 40).encode(), "non-numeric|missing|not a carOBD"),
    (b"\n".join(raw().splitlines()[:30]), "rows|need at least"),
], ids=["empty", "not_carobd", "missing_rpm", "non_numeric", "too_short"])
def test_upload_rejects_bad_files_and_leaves_nothing_behind(uploads, data, match):
    with pytest.raises(ValueError, match=match):
        cr.save_upload("x.csv", data, 1.0)
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_upload_size_limit(uploads):
    with pytest.raises(ValueError, match="limit is 5 MB"):
        cr.save_upload("big.csv", b"0" * (cr.MAX_UPLOAD_BYTES + 1), 1.0)


# ---------------------------------------------------------------- HTTP, end to end

@pytest.fixture
def server(uploads):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_app("healthy", validate=True, opcua=False), host="127.0.0.1",
                                        port=port, log_level="warning", ws="websockets"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"127.0.0.1:{port}"
    srv.should_exit = True
    th.join(10)


def test_http_replay_inject_upload(server):
    api = f"http://{server}"
    assert httpx.post(f"{api}/inject", json={"fault": "lean_drift", "severity": 0.5}).status_code == 409
    files = httpx.get(f"{api}/replay/files").json()
    assert files["speeds"] == [1, 10, 30] and files["current"] is None
    assert httpx.post(f"{api}/replay", json={"file": "heldout:drive1.csv"}).status_code == 422
    assert httpx.post(f"{api}/replay", json={"file": FILE, "speed": 7}).status_code == 422
    assert httpx.post(f"{api}/replay", json={"file": FILE, "speed": 30}).json()["speed"] == 30
    assert httpx.post(f"{api}/inject", json={"fault": "misfire", "cylinder": 3, "severity": 1}).status_code == 422
    assert httpx.post(f"{api}/inject", json={"fault": "lean_drift", "cylinder": 2, "severity": 0.5}).status_code == 200
    with connect(f"ws://{server}/ws/state") as ws:
        for _ in range(50):
            m = json.loads(ws.recv(timeout=2))
            assert messages.errors("state", m) == []
            if m["injection"]:
                break
        assert m["injection"]["fault"] == "lean_drift" and m["injection"]["cylinder"] is None
        assert m["source_label"] == "real_replay_injected" and m["mode"] == "replay"
    assert httpx.post(f"{api}/clear_faults").status_code == 200
    assert httpx.post(f"{api}/replay/speed", json={"speed": 1}).json() == {"speed": 1.0}

    big = httpx.post(f"{api}/upload_obd?filename=big.csv", content=b"0" * (cr.MAX_UPLOAD_BYTES + 10))
    assert big.status_code == 413
    bad = httpx.post(f"{api}/upload_obd?filename=bad.csv", content=b"a,b\n1,2\n")
    assert bad.status_code == 422
    ok = httpx.post(f"{api}/upload_obd?filename=../../drive5.csv", content=raw(),
                    headers={"Content-Type": "text/csv"}).json()
    assert ok["file"].startswith("upload:") and ok["name"] == "drive5.csv"
    listed = httpx.get(f"{api}/replay/files").json()["files"]
    assert any(f["id"] == ok["file"] and f["label"] == "REAL (uploaded, unverified)" for f in listed)
    assert httpx.post(f"{api}/replay", json={"file": ok["file"], "speed": 30}).status_code == 200
    with connect(f"ws://{server}/ws/obd") as ws:
        m = json.loads(ws.recv(timeout=3))
        assert messages.errors("obd", m) == [] and m["source_label"] == "real_upload"
        assert {c["source"] for c in m["channels"].values()} == {"real_upload"}


def test_only_channels_the_injection_modifies_are_labelled_injected():
    s = src(10)
    for i in range(30):
        s.obd_channels(i)
    s.window(300)
    s.inject("lean_drift", None, 0.5)
    seen = {}
    for i in range(31, 60):
        for name, c in (s.obd_channels(i) or {}).items():
            seen.setdefault(name, set()).add(c["source_label"])
            assert messages.errors("obd", messages.obd_message(sent_at=0, channels={name: c})) == []
    assert seen["stft_pct"] == {"real_replay_injected"} and seen["ltft_pct"] == {"real_replay_injected"}
    assert all(v == {"real_replay"} for k, v in seen.items() if k not in ("stft_pct", "ltft_pct"))
    s.window(600)
    s.clear_faults()                                   # at file time 600 s
    s.obd_channels(60)                                 # (590, 600]: rows logged before the clear
    later = {}
    for i in range(61, 75):
        for name, c in (s.obd_channels(i) or {}).items():
            later.setdefault(name, set()).add(c["source_label"])
    assert all(v == {"real_replay"} for v in later.values())        # nothing injected after clear
