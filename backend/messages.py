"""Builders for the contract's messages (schema_version 1.4) and validation against contract/schemas.

The schemas are read from contract/ at runtime, never copied, so the backend can't drift from them.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "1.4"
SCHEMAS = Path(__file__).resolve().parent.parent / "contract" / "schemas"

UNITS = {"t": "s", "sent_at": "unix s", "rpm": "rev/min", "load_pct": "%", "latency_ms": "ms", "severity": "0-1", "crank_deg": "deg"}


@lru_cache(maxsize=None)
def validator(name: str) -> Draft202012Validator:
    with open(SCHEMAS / f"{name}.schema.json", encoding="utf-8") as f:
        return Draft202012Validator(json.load(f))


def errors(name: str, message: dict) -> list[str]:
    """Schema errors for a message ('state', 'obd', 'ack' or 'signal_header'); empty when valid."""
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in validator(name).iter_errors(message)]


def state_message(*, sent_at, run_id, source, audioset_pool, engine_tag, mode, t, operating_point, localisation,
                  features_summary, anomaly, models, physics_rules, decision, health, latency_ms, signature,
                  injection=None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "sent_at": sent_at,
        "units": dict(UNITS),
        "run_id": run_id,
        "source": source,
        "audioset_pool": audioset_pool,
        "engine_tag": engine_tag,
        "mode": mode,
        "t": t,
        "operating_point": operating_point,
        "localisation": localisation,
        "features_summary": features_summary,
        "anomaly": anomaly,
        "models": models,
        "physics_rules": physics_rules,
        "decision": decision,
        "health": health,
        "latency_ms": latency_ms,
        "signature": signature,
        "injection": injection,  # 1.4: the accepted POST /inject body, or None
    }


def obd_message(*, sent_at, channels: dict) -> dict:
    return {"schema_version": SCHEMA_VERSION, "sent_at": sent_at, "channels": channels}


def signal_header(*, sent_at, channel, kind, t0, sample_rate_hz, n_samples, n_bins=None, n_frames=None, f_max_hz=None) -> dict:
    h = {
        "schema_version": SCHEMA_VERSION,
        "sent_at": sent_at,
        "channel": channel,
        "kind": kind,
        "t0": t0,
        "sample_rate_hz": sample_rate_hz,
        "n_samples": n_samples,
        "dtype": "float32",
    }
    if kind == "spectrogram":
        h.update(n_bins=n_bins, n_frames=n_frames, f_max_hz=f_max_hz)
    return h


def ack_error(*, sent_at, alarm_id, reason, detail) -> dict:
    """Reply on /ws/state to a rejected ACK. Not a contract 1.3 message: the UI tells it apart
    from `state` by its `type` field (state messages have none)."""
    return {
        "type": "ack_error",
        "schema_version": SCHEMA_VERSION,
        "sent_at": sent_at,
        "alarm_id": alarm_id,
        "reason": reason,
        "detail": detail,
    }
