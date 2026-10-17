"""Live microphone (optional, isolated; the dashboard shows it only with ?mic=1). REAL · LIVE MIC, audio only.

The browser streams 48 kHz mono float32 in 0.5 s chunks over /ws/mic. This module runs the existing real-audio
path, unchanged (models/real_audio/features.py: 1 s windows, 0.5 s hop, 0-4 kHz features; the committed healthy
model and thresholds in models/thresholds/audio_healthy.json, as for AudioSet P3 and the ESC-50 sweep), and
the knock-band score (5-15 kHz envelope kurtosis vs its committed threshold). Nothing is fitted here.

It gives meters, never a verdict: on the vetted clips the knock-band score did not separate knock from healthy
(eval/audio_p3.json: 1/19 knock clips vs 1/32 healthy P3 over threshold, AUROC 0.60), so the live tab shows the
numbers only. No crank, so no cylinder; no second witness, so nothing here can reach the rule layer or ALARM.
It never touches the main stream, the rule layer or OPC UA.

Protocol (not part of the contract's three channels):
  client -> {"type": "mic_hello", "sample_rate": 48000, "channels": 1}      first, as text
  client -> binary: little-endian float32 samples, 1 .. CHUNK_SAMPLES per message (0.5 s at 48 kHz)
  server -> {"type": "mic_ready", ...} after a valid hello
  server -> {"type": "mic_window", t, rms_dbfs, silent, anomaly: {...}, knock_band: {...}} per 0.5 s hop
  server -> {"type": "mic_error", "reason", "detail"}; on a protocol error the socket is closed (1003 / 1009)
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
from scipy.signal import hilbert, sosfilt, sosfilt_zi
from scipy.stats import kurtosis

from models.obd.healthy import HealthyModel
from models.real_audio.features import FEATURES, HOP_S, KNOCK_BAND, SILENCE_RMS, SR, WIN_S, _KNOCK_SOS, window_features
from models.real_audio.run_p3 import THRESHOLDS

CHUNK_SAMPLES = int(0.5 * SR)              # 24 000 samples = 96 000 bytes
MAX_CHUNK_BYTES = CHUNK_SAMPLES * 4
MAX_ABS = 8.0                              # float audio is nominally +-1; anything far beyond is not audio
LABEL = "REAL · LIVE MIC · audio only: no cylinder, ADVISORY at most"


class MicError(ValueError):
    def __init__(self, reason: str, detail: str, close_code: int = 1003):
        super().__init__(detail)
        self.reason, self.detail, self.close_code = reason, detail, close_code


@lru_cache(maxsize=1)
def _model() -> tuple[HealthyModel, float]:
    th = json.loads(THRESHOLDS.read_text())
    return HealthyModel.from_json(th["anomaly"]), float(th["knock_score_threshold"])


def parse_hello(text: str) -> dict:
    try:
        m = json.loads(text)
    except ValueError as e:
        raise MicError("invalid_hello", "first message must be JSON mic_hello") from e
    if not isinstance(m, dict) or m.get("type") != "mic_hello":
        raise MicError("invalid_hello", "first message must be {\"type\": \"mic_hello\", ...}")
    if m.get("sample_rate") != SR:
        raise MicError("unsupported_rate", f"sample_rate must be {SR} (the healthy model's rate), got {m.get('sample_rate')!r}")
    if m.get("channels", 1) != 1:
        raise MicError("unsupported_channels", "mono only (channels = 1)")
    return m


def parse_chunk(data: bytes) -> np.ndarray:
    if len(data) > MAX_CHUNK_BYTES:
        raise MicError("chunk_too_large", f"{len(data)} bytes; at most {MAX_CHUNK_BYTES} (0.5 s of float32 at {SR} Hz)", 1009)
    if len(data) == 0 or len(data) % 4:
        raise MicError("invalid_chunk", f"{len(data)} bytes is not a whole number of float32 samples")
    x = np.frombuffer(data, dtype="<f4").astype(float)
    if not np.all(np.isfinite(x)):
        raise MicError("invalid_chunk", "NaN or infinite samples")
    if np.max(np.abs(x)) > MAX_ABS:
        raise MicError("invalid_chunk", f"samples beyond +-{MAX_ABS}: not float audio in [-1, 1]")
    return x


class MicAnalyzer:
    """Buffers samples and emits one result per 0.5 s hop over the last 1 s, as the offline path does."""

    def __init__(self):
        self.model, self.knock_thr = _model()
        self.win, self.hop = int(WIN_S * SR), int(HOP_S * SR)
        self.buf = np.zeros(0)
        self.hbuf = np.zeros(0)                    # knock band (causal filter; offline uses zero-phase filtfilt)
        self._zi = sosfilt_zi(_KNOCK_SOS) * 0.0
        self.samples = 0                           # total samples received
        self._next = self.win                      # sample count at which the next window completes

    def push(self, x: np.ndarray) -> list[dict]:
        h, self._zi = sosfilt(_KNOCK_SOS, x, zi=self._zi)
        self.buf, self.hbuf = np.concatenate([self.buf, x]), np.concatenate([self.hbuf, h])   # always aligned
        self.samples += len(x)
        out = []
        while self.samples >= self._next:
            end = len(self.buf) - (self.samples - self._next)        # this window's end inside the buffers
            out.append(self._score(self.buf[end - self.win:end], self.hbuf[end - self.win:end], self._next / SR))
            self._next += self.hop
        keep = self.win + CHUNK_SAMPLES
        self.buf, self.hbuf = self.buf[-keep:], self.hbuf[-keep:]
        return out

    def _score(self, w: np.ndarray, hw: np.ndarray, t: float) -> dict:
        rms = float(np.sqrt(np.mean(w ** 2)))
        silent = rms <= SILENCE_RMS
        res = {"type": "mic_window", "t": round(t, 3), "rms_dbfs": round(20 * np.log10(rms + 1e-12), 1), "silent": silent,
               "anomaly": None, "knock_band": None, "label": LABEL}
        if silent:                                      # digital silence: not engine audio, as offline
            return res
        f = dict(zip(FEATURES, window_features(w)))
        f["regime"] = "all"
        d, thr = self.model.distance(f), self.model.threshold(f)
        k = float(kurtosis(np.abs(hilbert(hw))))
        e = float(10 * np.log10(np.mean(hw ** 2) / (np.mean(w ** 2) + 1e-20) + 1e-12))
        res["anomaly"] = {"distance": round(d, 3), "threshold": round(thr, 3), "ratio": round(d / thr, 3),
                          "over": bool(d > thr)}
        res["knock_band"] = {"band_hz": list(KNOCK_BAND), "envelope_kurtosis": round(k, 2),
                             "threshold": round(self.knock_thr, 2), "over": bool(k > self.knock_thr),
                             "energy_db_rel": round(e, 1)}
        return res
