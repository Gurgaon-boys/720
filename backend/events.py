"""Incident event log (D141): a bounded in-memory record of what was requested, what the backend
accepted, and what the broadcast then confirmed. Backend clock only; nothing here decides anything.

    requested  a command arrived (HTTP, OPC UA or WebSocket)
    accepted   the backend applied it (or `refused`, with the reason)
    confirmed  the state broadcast shows a change (decision, sensor health, a witness appearing or going)

Kept in memory only (the last MAX_EVENTS), served by GET /events and used by the Reliability Agent and the
incident report. Not part of the WebSocket contract.
"""

from __future__ import annotations

import threading
from collections import deque

MAX_EVENTS = 500
PHASES = ("requested", "accepted", "refused", "confirmed")


class EventLog:
    def __init__(self, clock, maxlen: int = MAX_EVENTS) -> None:
        self.clock = clock
        self._events: deque = deque(maxlen=maxlen)
        self._seq = 0
        self._lock = threading.Lock()   # OPC UA methods and HTTP routes may record concurrently
        self._prev: dict | None = None  # last state digest, for confirmed transitions

    def record(self, kind: str, phase: str, text: str, via: str | None = None, stream_t: float | None = None,
               **data) -> dict:
        if phase not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}")
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "t": round(self.clock(), 3), "kind": kind, "phase": phase, "text": text,
                  "via": via, "stream_t": None if stream_t is None else round(stream_t, 3), "data": data}
            self._events.append(ev)
            return ev

    def since(self, after: int = 0) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e["seq"] > after]

    def last(self, kind: str | None = None, pred=None) -> dict | None:
        with self._lock:
            for e in reversed(self._events):
                if (kind is None or e["kind"] == kind) and (pred is None or pred(e)):
                    return e
        return None

    def reset_digest(self) -> None:
        self._prev = None

    # ------------------------------------------------------------ confirmed transitions

    def observe(self, msg: dict) -> None:
        """Compare one outgoing state message with the previous one and record what changed."""
        d, h = msg["decision"], msg["health"]["sensors"]
        models = msg.get("models") or []
        svm = next((m for m in models if m.get("model") == "SVM"), models[0] if models else None)
        fired = sorted(f"{r['rule']}" + (f" cyl {r['location']}" if r.get("location") else "")
                       for r in msg.get("physics_rules", []) if r.get("fired"))
        digest = {"state": d["state"], "fault": d.get("fault"), "location": d.get("location"),
                  "alarm_id": d.get("alarm_id"), "acked": bool(d.get("acked")), "sensors": dict(h),
                  "classifier": bool(models), "fired": fired}
        prev, self._prev = self._prev, digest
        st = msg.get("t")
        if prev is None:
            return
        for s, status in h.items():
            if prev["sensors"].get(s) != status:
                self.record("sensor_health", "confirmed",
                            f"{s.upper()} {'FAILURE: no data for 3 s (backend watchdog)' if status == 'FAILURE' else 'NORMAL: data flowing again'}",
                            stream_t=st, sensor=s, status=status)
        if prev["classifier"] != digest["classifier"]:
            if digest["classifier"]:
                self.record("classifier", "confirmed", f"ML classifier witness back: {svm['model']} says {svm['label']} (p {svm['p']})",
                            stream_t=st, available=True, label=svm["label"], p=svm["p"], model=svm["model"])
            else:
                self.record("classifier", "confirmed", "ML classifier witness gone: no valid crank/accelerometer window",
                            stream_t=st, available=False)
        new_rules = [r for r in fired if r not in prev["fired"]]
        if new_rules:
            self.record("physics", "confirmed", f"Physics witness fired: {', '.join(new_rules)}", stream_t=st, rules=new_rules)
        if (prev["state"], prev["fault"], prev["location"], prev["alarm_id"]) != (d["state"], d.get("fault"), d.get("location"), d.get("alarm_id")):
            what = d["state"] + (f" {d['fault']}" if d.get("fault") else "") + (f" cyl {d['location']}" if d.get("location") else "")
            if d["state"] == "ALARM" and d.get("alarm_id") != prev["alarm_id"]:
                what += f" ({d['alarm_id']} raised)"
            self.record("decision", "confirmed", f"Rule layer: {what}", stream_t=st, state=d["state"], fault=d.get("fault"),
                        location=d.get("location"), alarm_id=d.get("alarm_id"), witnesses=list(d.get("witnesses") or []),
                        models=[dict(m) for m in models], rules=fired, consecutive_windows=d.get("consecutive_windows"))
        if digest["acked"] and not prev["acked"] and d.get("alarm_id") == prev["alarm_id"]:
            self.record("ack", "confirmed", f"Broadcast shows {d['alarm_id']} acknowledged by {d.get('acked_by')}",
                        stream_t=st, alarm_id=d.get("alarm_id"), operator=d.get("acked_by"))
