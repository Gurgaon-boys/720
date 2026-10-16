"""End-to-end over real sockets: a uvicorn server in a thread, a websockets client, real time.

These take ~25 s because they check actual rates and the 3 s offline gap.
"""

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect

from backend import messages
from backend.main import create_app
from backend.signal_frame import decode_frame


@pytest.fixture
def server():
    started = []

    def start(scenario):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        srv = uvicorn.Server(uvicorn.Config(create_app(scenario, validate=True, opcua=False), host="127.0.0.1", port=port,
                                            log_level="warning", ws="websockets"))
        th = threading.Thread(target=srv.run, daemon=True)
        th.start()
        for _ in range(100):
            if srv.started:
                break
            time.sleep(0.05)
        started.append((srv, th))
        return f"127.0.0.1:{port}"

    yield start
    for srv, th in started:
        srv.should_exit = True
        th.join(5)


def recv_json(ws, timeout=2.0):
    return json.loads(ws.recv(timeout=timeout))


def test_http_helpers(server):
    host = server("healthy")
    assert httpx.get(f"http://{host}/health").json() == {"status": "ok", "schema_version": "1.6", "scenario": "healthy", "maintenance": False, "opcua": None}
    names = [s["name"] for s in httpx.get(f"http://{host}/scenarios").json()["scenarios"]]
    assert {"healthy", "misfire", "unclassified", "offline", "real_p3"} <= set(names)
    assert httpx.post(f"http://{host}/scenario/nope").status_code == 404
    assert httpx.post(f"http://{host}/scenario/knock").json() == {"current": "knock"}


def test_stream_rates_and_contract(server):
    host = server("misfire")
    with connect(f"ws://{host}/ws/state") as st, connect(f"ws://{host}/ws/obd") as ob, connect(f"ws://{host}/ws/signal") as sg:
        t_end = time.monotonic() + 3.2
        states, obds, frames = [], [], []
        while time.monotonic() < t_end:
            states.append(recv_json(st))
            while True:  # drain the other two without blocking
                try:
                    frames.append(sg.recv(timeout=0))
                except TimeoutError:
                    break
            try:
                obds.append(recv_json(ob, timeout=0))
            except TimeoutError:
                pass
        for _ in range(2):  # obd is 1 Hz; make sure we have three
            if len(obds) < 3:
                obds.append(recv_json(ob, timeout=1.5))

    assert all(messages.errors("state", m) == [] for m in states)
    assert all(messages.errors("obd", m) == [] for m in obds)
    headers = [decode_frame(f)[0] for f in frames]
    assert all(messages.errors("signal_header", h) == [] for h in headers)

    def rate(ts):
        return (len(ts) - 1) / (ts[-1] - ts[0])

    assert 8.5 < rate([m["sent_at"] for m in states]) < 11.5
    assert 0.8 < rate([m["sent_at"] for m in obds]) < 1.2
    mic = [h["sent_at"] for h in headers if h["channel"] == "mic"]
    assert 17 < rate(mic) < 23
    assert {"mic", "accel", "mic_stft"} == {h["channel"] for h in headers}


def test_ack_over_websocket(server):
    host = server("misfire")
    with connect(f"ws://{host}/ws/state") as ws:
        m = recv_json(ws)
        while m["decision"]["state"] != "ALARM":
            m = recv_json(ws)
        alarm_id = m["decision"]["alarm_id"]

        def ack(aid, op="judge"):
            ws.send(json.dumps({"schema_version": "1.6", "type": "ack", "alarm_id": aid, "operator": op, "sent_at": time.time()}))

        def next_error():
            while "type" not in (m := recv_json(ws)):
                last.append(m)
            return m

        last = []
        ack("a_9999")
        err = next_error()
        assert err["type"] == "ack_error" and err["reason"] == "unknown_alarm_id" and err["alarm_id"] == "a_9999"
        ws.send("{not json")
        assert next_error()["reason"] == "invalid_message"
        st = recv_json(ws)
        assert st["decision"]["alarm_id"] == alarm_id and not st["decision"]["acked"]  # unchanged

        ack(alarm_id)
        st = recv_json(ws)
        while not st["decision"]["acked"]:
            st = recv_json(ws)
        assert st["decision"]["state"] == "ALARM" and st["decision"]["acked_by"] == "judge"  # ACK doesn't clear

        ack(alarm_id, "someone_else")
        assert next_error()["reason"] == "duplicate_ack"
        assert recv_json(ws)["decision"]["acked_by"] == "judge"


def test_offline_scenario_stops_all_output(server):
    host = server("offline")  # healthy 4 s, silent 5 s
    with connect(f"ws://{host}/ws/state") as st, connect(f"ws://{host}/ws/signal") as sg:
        last = recv_json(st)
        while True:
            try:
                last = recv_json(st, timeout=1.0)
            except TimeoutError:
                break
        silent_from = time.time()
        with pytest.raises(TimeoutError):
            while True:  # drain buffered frames, then the signal channel must be silent too
                sg.recv(timeout=1.0)
        nxt = recv_json(st, timeout=6.0)  # comes back when the loop restarts
        gap = nxt["sent_at"] - last["sent_at"]
        assert gap > 3.0, gap  # long enough for the UI's 3 s watchdog
        assert nxt["sent_at"] - silent_from > 2.0
