"""Message builders and the binary signal frame against contract 1.3."""

import json
import struct
from pathlib import Path

import numpy as np
import pytest

from backend import messages
from backend.signal_frame import decode_frame, encode_frame

EXAMPLES = Path(__file__).resolve().parents[2] / "contract" / "examples"


def load(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("example", ["state.json", "state_real_p3.json"])
def test_state_builder_reproduces_contract_examples(example):
    ex = load(example)
    fields = {k: v for k, v in ex.items() if k not in ("schema_version", "units")}
    built = messages.state_message(**fields)
    assert built == ex
    assert messages.errors("state", built) == []


def test_obd_builder_reproduces_example():
    ex = load("obd.json")
    built = messages.obd_message(sent_at=ex["sent_at"], channels=ex["channels"])
    assert built == ex and messages.errors("obd", built) == []


def test_signal_header_builder_reproduces_example():
    ex = load("signal_header.json")
    built = messages.signal_header(**{k: v for k, v in ex.items() if k not in ("schema_version", "dtype")})
    assert built == ex and messages.errors("signal_header", built) == []


def test_spectrogram_header_needs_its_extra_fields():
    h = messages.signal_header(sent_at=1.0, channel="mic_stft", kind="spectrogram", t0=0.0, sample_rate_hz=20,
                               n_samples=101, n_bins=101, n_frames=1, f_max_hz=2000.0)
    assert messages.errors("signal_header", h) == []
    del h["f_max_hz"]
    assert messages.errors("signal_header", h)


def test_validator_catches_contract_violations():
    bad = load("state_real_p3.json")
    bad["localisation"] = "cylinder"  # real clips must be "none"
    assert messages.errors("state", bad)
    alarm = load("state.json")
    alarm["decision"]["consecutive_windows"] = 1  # ALARM needs 2
    assert messages.errors("state", alarm)


@pytest.mark.parametrize("n", [0, 1, 7, 200])
def test_signal_frame_layout(n):
    payload = np.linspace(-1, 1, n, dtype=np.float32)
    header = messages.signal_header(sent_at=1760551234.53, channel="mic", kind="waveform", t0=12.0, sample_rate_hz=4000, n_samples=n)
    buf = encode_frame(header, payload)

    (hlen,) = struct.unpack_from("<I", buf, 0)  # uint32 little-endian header length
    assert (4 + hlen) % 4 == 0  # payload starts on a 4-byte boundary
    assert len(buf) == 4 + hlen + 4 * n
    raw_header = buf[4 : 4 + hlen].decode("utf-8")
    assert raw_header == raw_header.rstrip() + " " * (len(raw_header) - len(raw_header.rstrip()))  # only trailing spaces
    assert json.loads(raw_header) == header
    assert messages.errors("signal_header", json.loads(raw_header)) == []
    data = np.frombuffer(buf, dtype="<f4", count=n, offset=4 + hlen)  # the zero-copy read from signal_frame.md
    np.testing.assert_array_equal(data, payload)

    h2, d2 = decode_frame(buf)
    assert h2 == header
    np.testing.assert_array_equal(d2, payload)


def test_signal_frame_rejects_wrong_count():
    header = messages.signal_header(sent_at=1.0, channel="mic", kind="waveform", t0=0.0, sample_rate_hz=4000, n_samples=3)
    with pytest.raises(ValueError):
        encode_frame(header, np.zeros(4))
