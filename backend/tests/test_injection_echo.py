"""Schema 1.4+ state.injection: the broadcast echoes an accepted POST /inject and is null otherwise.

Runs a real uvicorn server (like test_ws.py): Starlette's in-process TestClient intermittently raises
CancelledError when its WebSocket session closes, which says nothing about the backend.
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


@pytest.fixture
def server():
    started = []

    def start(scenario):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        srv = uvicorn.Server(uvicorn.Config(create_app(scenario, validate=True, opcua=False), host="127.0.0.1",
                                            port=port, log_level="warning", ws="websockets"))
        th = threading.Thread(target=srv.run, daemon=True)
        th.start()
        for _ in range(400):  # the simulator source takes a moment to build
            if srv.started:
                break
            time.sleep(0.05)
        started.append((srv, th))
        return f"127.0.0.1:{port}"

    yield start
    for srv, th in started:
        srv.should_exit = True
        th.join(10)


def next_state(ws, pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        m = json.loads(ws.recv(timeout=2))
        if "type" not in m:
            assert messages.errors("state", m) == [], m.get("injection")
            if pred(m):
                return m
    raise AssertionError("no matching state message")


def test_injection_echo_on_the_simulator(server):
    host = server("sim")
    with connect(f"ws://{host}/ws/state") as ws:
        m = next_state(ws, lambda m: True)
        assert m["schema_version"] == "1.6" and m["injection"] is None

        before = time.time()
        r = httpx.post(f"http://{host}/inject", json={"fault": "misfire", "cylinder": 3, "severity": 1.0})
        assert r.status_code == 200
        inj = next_state(ws, lambda m: m["injection"] is not None)["injection"]
        assert {k: inj[k] for k in ("active", "fault", "cylinder", "severity")} == \
            {"active": True, "fault": "misfire", "cylinder": 3, "severity": 1.0}
        assert before - 1 <= inj["started_at"] <= time.time() + 1

        # a rejected request (invalid cylinder; sim v2 accepts every fault type) is not echoed; the accepted one stays
        r = httpx.post(f"http://{host}/inject", json={"fault": "injector_clog", "cylinder": 9, "severity": 1.0})
        assert r.status_code == 422
        assert next_state(ws, lambda m: True)["injection"]["fault"] == "misfire"

        assert httpx.post(f"http://{host}/clear_faults").status_code == 200
        assert next_state(ws, lambda m: m["injection"] is None)["injection"] is None

        httpx.post(f"http://{host}/inject", json={"fault": "misfire", "cylinder": 2, "severity": 0.5})
        next_state(ws, lambda m: m["injection"] is not None and m["injection"]["cylinder"] == 2)
        assert httpx.post(f"http://{host}/scenario/healthy").status_code == 200  # a new source: nothing injected
        assert next_state(ws, lambda m: m["run_id"] == "fake_healthy")["injection"] is None


def test_inject_refused_without_a_simulator(server):
    host = server("healthy")
    with connect(f"ws://{host}/ws/state") as ws:
        r = httpx.post(f"http://{host}/inject", json={"fault": "misfire", "cylinder": 3, "severity": 1.0})
        assert r.status_code == 409
        m = next_state(ws, lambda m: True)
        assert m["schema_version"] == "1.6" and m["injection"] is None
