"""Optional live microphone /ws/mic (backend/live_mic.py): hello and chunk validation, size limits, results that match
the offline real-audio path, and isolation from the main stream."""

import json
import socket
import threading
import time

import numpy as np
import pytest
import uvicorn
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from backend import live_mic as lm
from backend.main import create_app
from models.real_audio.features import FEATURES, array_features

HELLO = json.dumps({"type": "mic_hello", "sample_rate": 48000, "channels": 1})


def f32(x) -> bytes:
    return np.asarray(x, dtype="<f4").tobytes()


# ---------------------------------------------------------------- unit: validation

@pytest.mark.parametrize("text, reason", [
    ("not json", "invalid_hello"),
    (json.dumps({"type": "hello"}), "invalid_hello"),
    (json.dumps({"type": "mic_hello", "sample_rate": 44100}), "unsupported_rate"),
    (json.dumps({"type": "mic_hello", "sample_rate": 48000, "channels": 2}), "unsupported_channels"),
])
def test_hello_is_validated(text, reason):
    with pytest.raises(lm.MicError) as e:
        lm.parse_hello(text)
    assert e.value.reason == reason


@pytest.mark.parametrize("data, reason, code", [
    (b"", "invalid_chunk", 1003),
    (b"\x00\x00\x00", "invalid_chunk", 1003),                                    # not whole float32 samples
    (f32([0.1, np.nan]), "invalid_chunk", 1003),
    (f32([0.1, 50.0]), "invalid_chunk", 1003),                                   # not audio in [-1, 1]
    (f32(np.zeros(lm.CHUNK_SAMPLES + 1)), "chunk_too_large", 1009),             # more than 0.5 s
], ids=["empty", "partial_sample", "nan", "not_audio", "too_large"])
def test_chunks_are_validated_and_size_limited(data, reason, code):
    with pytest.raises(lm.MicError) as e:
        lm.parse_chunk(data)
    assert e.value.reason == reason and e.value.close_code == code


def test_a_full_half_second_chunk_is_accepted():
    assert len(lm.parse_chunk(f32(np.zeros(lm.CHUNK_SAMPLES)))) == lm.CHUNK_SAMPLES


def test_streamed_windows_equal_the_offline_path():
    x = 0.05 * np.random.default_rng(0).standard_normal(48000 * 3)
    a = lm.MicAnalyzer()
    out = []
    for i in range(0, len(x), 7_000):                        # odd chunk sizes: the windows must not depend on them
        out += a.push(x[i:i + 7_000])
    model, _ = lm._model()
    off = [model.distance({**dict(zip(FEATURES, r)), "regime": "all"}) for r in array_features(x)]
    assert [o["t"] for o in out] == [1.0, 1.5, 2.0, 2.5, 3.0]
    assert [o["anomaly"]["distance"] for o in out] == [round(d, 3) for d in off]
    assert all(o["label"] == lm.LABEL for o in out)


def test_silence_has_no_score():
    a = lm.MicAnalyzer()
    out = a.push(np.zeros(48000))
    assert out and out[0]["silent"] and out[0]["anomaly"] is None and out[0]["knock_band"] is None


# ---------------------------------------------------------------- endpoint

@pytest.fixture(scope="module")
def server():
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


def refused(ws) -> dict:
    m = json.loads(ws.recv(timeout=5))
    with pytest.raises(ConnectionClosed):
        ws.recv(timeout=5)
    return m


def test_endpoint_streams_meters_for_valid_audio(server):
    x = (0.05 * np.random.default_rng(1).standard_normal(48000 * 2)).astype("<f4")
    with connect(f"ws://{server}/ws/mic") as ws:
        ws.send(HELLO)
        ready = json.loads(ws.recv(timeout=10))
        assert ready["type"] == "mic_ready" and ready["max_chunk_bytes"] == 96_000 and "ADVISORY at most" in ready["label"]
        for i in range(0, len(x), lm.CHUNK_SAMPLES):
            ws.send(x[i:i + lm.CHUNK_SAMPLES].tobytes())
        got = [json.loads(ws.recv(timeout=10)) for _ in range(3)]
    assert [g["t"] for g in got] == [1.0, 1.5, 2.0]
    assert all(g["type"] == "mic_window" and g["anomaly"]["threshold"] > 0 and g["knock_band"]["threshold"] > 0 for g in got)
    assert all("verdict" not in g for g in got)                          # meters only: no verdict (eval/audio_p3.json)


@pytest.mark.parametrize("first, then, reason", [
    (f32(np.zeros(10)), None, "invalid_hello"),                              # audio before hello
    (json.dumps({"type": "mic_hello", "sample_rate": 44100}), None, "unsupported_rate"),
    (HELLO, f32(np.zeros(lm.CHUNK_SAMPLES * 2)), "chunk_too_large"),         # 1 s in one frame
    (HELLO, "text after hello", "invalid_chunk"),
], ids=["audio_before_hello", "wrong_rate", "too_large", "text_after_hello"])
def test_endpoint_refuses_bad_input_and_closes(server, first, then, reason):
    with connect(f"ws://{server}/ws/mic") as ws:
        ws.send(first)
        if then is not None:
            assert json.loads(ws.recv(timeout=10))["type"] == "mic_ready"
            ws.send(then)
        m = refused(ws)
    assert m == {"type": "mic_error", "reason": reason, "detail": m["detail"]}


def test_main_stream_is_untouched_by_a_mic_session(server):
    with connect(f"ws://{server}/ws/state") as st, connect(f"ws://{server}/ws/mic") as mic:
        mic.send(HELLO)
        json.loads(mic.recv(timeout=10))
        mic.send(f32(0.01 * np.ones(lm.CHUNK_SAMPLES)))
        msgs = [json.loads(st.recv(timeout=3)) for _ in range(5)]
    # the main stream keeps its own state messages; nothing of the mic session leaks into them
    assert all("decision" in m and m.get("type") is None and not str(m.get("type", "")).startswith("mic") for m in msgs)
    assert all("anomaly" in m and "knock_band" not in m for m in msgs)
