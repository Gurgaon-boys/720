"""Binary `signal` frames, exactly as contract/signal_frame.md describes them:

    uint32 LE header_len | UTF-8 JSON header (space-padded) | float32 LE payload

The header is padded with trailing spaces so the payload starts on a 4-byte boundary.
"""

from __future__ import annotations

import json
import struct

import numpy as np


def encode_frame(header: dict, payload) -> bytes:
    data = np.ascontiguousarray(payload, dtype="<f4").ravel()
    if header["n_samples"] != data.size:
        raise ValueError(f"header n_samples={header['n_samples']} but payload has {data.size} values")
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    raw += b" " * (-(4 + len(raw)) % 4)
    return struct.pack("<I", len(raw)) + raw + data.tobytes()


def decode_frame(buf: bytes) -> tuple[dict, np.ndarray]:
    (hlen,) = struct.unpack_from("<I", buf, 0)
    header = json.loads(buf[4 : 4 + hlen])
    n = header["n_samples"]
    if len(buf) != 4 + hlen + 4 * n:
        raise ValueError(f"frame is {len(buf)} bytes, expected {4 + hlen + 4 * n}")
    return header, np.frombuffer(buf, dtype="<f4", count=n, offset=4 + hlen)
