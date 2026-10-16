"""Frontend ↔ contract checks, run through Node so they use the frontend's own code.

1. Binary frames encoded by the backend decode correctly with frontend/src/api/signalFrame.js.
2. Every message the frontend's mock transport emits (state, obd, ack) validates against schema 1.4,
   so mock mode and the real backend feed the dashboard the same shapes.

Skipped when Node isn't installed.
"""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from backend import messages
from backend.scenarios import SCENARIOS, signal_frames
from backend.signal_frame import encode_frame

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE or not (FRONTEND / "src").is_dir(), reason="needs node and frontend/")


def node(script: str, stdin: bytes = b"") -> str:
    res = subprocess.run([NODE, "--input-type=module", "-e", script], input=stdin, cwd=FRONTEND,
                         capture_output=True, timeout=60)
    assert res.returncode == 0, res.stderr.decode()
    return res.stdout.decode()


def test_frontend_decodes_backend_signal_frames():
    frames = []
    for fields, payload in signal_frames(SCENARIOS["knock"], 50, {"mic", "accel"}):
        header = messages.signal_header(sent_at=1760551234.5, **fields)
        frames.append((header, payload, encode_frame(header, payload)))
    blob = b"".join(len(f).to_bytes(4, "little") + f for _, _, f in frames)

    out = json.loads(node("""
        import { decodeFrame, toUiFrame } from './src/api/signalFrame.js';
        const chunks = []; for await (const c of process.stdin) chunks.push(c);
        const all = Buffer.concat(chunks); const res = [];
        for (let o = 0; o < all.length;) {
          const n = all.readUInt32LE(o); o += 4;
          const ab = all.buffer.slice(all.byteOffset + o, all.byteOffset + o + n); o += n;
          res.push(toUiFrame(decodeFrame(ab)));
        }
        console.log(JSON.stringify(res));
    """, blob))

    assert [f["channel"] for f in out] == ["mic", "accel", "stft"]  # mic_stft → SignalStrip's 'stft'
    for (header, payload, _), ui in zip(frames, out):
        assert ui["t0"] == header["t0"] and ui["sent_at"] == header["sent_at"]
        if header["kind"] == "waveform":
            assert ui["sample_rate_hz"] == header["sample_rate_hz"]
            np.testing.assert_allclose(ui["samples"], payload, rtol=0, atol=1e-6)
        else:
            assert len(ui["columns"]) == header["n_frames"] and len(ui["freqs_hz"]) == header["n_bins"]
            assert ui["freqs_hz"][0] == 0 and ui["freqs_hz"][-1] == pytest.approx(header["f_max_hz"])
            assert ui["dt_s"] == pytest.approx(1 / header["sample_rate_hz"])
            np.testing.assert_allclose(ui["columns"][0], payload, atol=1e-4)


def test_frontend_mock_messages_follow_schema_1_4():
    out = json.loads(node("""
        import { SCENARIOS, buildState, buildObd, buildAck, scenarioRaisesAlarm } from './src/data/mockEngine.js';
        const res = [];
        for (const s of SCENARIOS) {
          const alarms = scenarioRaisesAlarm(s.id)
            ? [{ alarm_id: 'a_0001', latched: true, acked: false, acked_by: null, acked_at: null },
               { alarm_id: 'a_0001', latched: true, acked: true, acked_by: 'operator', acked_at: 1760551240.1 }]
            : [null];
          for (const a of alarms) res.push({ id: s.id, state: buildState(s.id, 3.0, a) });
          res.push({ id: s.id, obd: buildObd(s.id, 3.0) });
        }
        res.push({ id: 'ack', ack: buildAck('a_0001', 'operator') });
        console.log(JSON.stringify(res));
    """))
    failures = []
    for item in out:
        for kind in ("state", "obd", "ack"):
            if kind in item and (errs := messages.errors(kind, item[kind])):
                failures.append(f"{item['id']} {kind}: {errs[:3]}")
    assert not failures, "\n".join(failures)
