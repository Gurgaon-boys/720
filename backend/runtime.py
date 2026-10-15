"""The streaming runtime: three loops at their own rates, fanned out to every connected client.

  state   ~10 Hz  one window -> rule layer -> `state` JSON
  obd     ~1 Hz   one sample per OBD channel -> `obd` JSON
  signal  ~20 Hz  mic + accel waveforms and a mic STFT column -> binary frames

ACKs come in on /ws/state, or through the OPC UA Acknowledge method; both go through ack().
Only the rule layer decides what they do; a valid ACK triggers an immediate `state` broadcast,
an invalid one gets an `ack_error` reply on that socket only.

Listeners (the OPC UA bridge) get every `state` and `obd` message the WebSocket clients get, as the
same dict, so the dashboard and OPC UA can never disagree (DECISIONS.md D16).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from backend import messages
from backend.rule_layer import AckResult, RuleLayer, asset_ne107
from backend.scenarios import OBD_S, SCENARIOS, SIGNAL_S, WINDOW_S, obd_sample, signal_frames, window
from backend.signal_frame import encode_frame
from backend.watchdog import Watchdog

log = logging.getLogger("backend")

QUEUE_SIZE = 64


class Hub:
    """Broadcast to every subscriber. A slow client loses its oldest messages; nobody else waits."""

    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(QUEUE_SIZE)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def publish(self, msg) -> None:
        for q in self.subscribers:
            put_latest(q, msg)


def put_latest(q: asyncio.Queue, msg) -> None:
    if q.full():
        q.get_nowait()
    q.put_nowait(msg)


class Runtime:
    def __init__(self, scenario: str = "healthy", validate: bool = False, clock=time.time) -> None:
        self.validate = validate
        self.clock = clock
        self.state_hub, self.obd_hub, self.signal_hub = Hub(), Hub(), Hub()
        self._tasks: list[asyncio.Task] = []
        self._listeners: list = []
        self.maintenance_mode = False  # explicit maintenance/test mode (asset health CHECK_FUNCTION)
        self.select(scenario)

    def set_maintenance(self, on: bool) -> None:
        """Explicit maintenance/test mode. Only changes the published asset health (CHECK_FUNCTION when
        nothing is wrong); it never touches the rule layer, and a fault's health always wins."""
        self.maintenance_mode = bool(on)
        log.info("maintenance mode %s", "on" if on else "off")

    def add_listener(self, fn) -> None:
        """fn(kind, msg) is called with every outgoing 'state' and 'obd' message dict."""
        self._listeners.append(fn)

    # ------------------------------------------------------------ control

    def select(self, name: str) -> None:
        """Switch scenario. Restarts stream time and the rule layer, so no alarm leaks across."""
        if name not in SCENARIOS:
            raise KeyError(name)
        self.scenario = SCENARIOS[name]
        # alarm ids keep counting across switches, so an id is never reused while the server runs
        old = getattr(self, "rules", None)
        self.rules = RuleLayer(first_alarm_id=old.next_alarm_id if old else 1)
        self.watchdog = Watchdog(self.scenario.sensors, self.clock())
        self.last_window = None
        self.last_state = None
        self._epoch = time.monotonic()
        self._generation = getattr(self, "_generation", 0) + 1

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._loop(p, f)) for p, f in
                       ((WINDOW_S, self._tick_state), (OBD_S, self._tick_obd), (SIGNAL_S, self._tick_signal))]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _loop(self, period: float, tick) -> None:
        """Call tick(i) at period-spaced deadlines measured from the scenario start (no drift)."""
        gen, i = None, 0
        while True:
            if gen != self._generation:  # scenario switched: start counting again
                gen, i = self._generation, 0
            delay = self._epoch + i * period - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            if gen != self._generation:
                continue
            try:
                tick(i)
            except Exception:
                log.exception("tick failed")
            i += 1

    # ------------------------------------------------------------ ticks

    def _offline(self, t: float) -> bool:
        return self.scenario.phase_at(t).offline

    def _tick_state(self, k: int) -> None:
        w = window(self.scenario, k)
        if w.phase.offline:
            return  # nothing goes out, so the UI's 3 s watchdog trips
        start = time.perf_counter()
        decision = self.rules.step(w.evidence)
        self.last_window = w
        self._publish_state(decision, start)

    def _publish_state(self, decision: dict, start: float) -> None:
        w = self.last_window
        now = self.clock()
        health = {"asset_ne107": asset_ne107(decision, self.maintenance_mode), "sensors": self.watchdog.status(now)}
        latency = round((time.perf_counter() - start) * 1000, 2)
        msg = messages.state_message(sent_at=now, decision=decision, health=health, latency_ms=latency, **w.fields)
        self.last_state = msg
        self._send(self.state_hub, "state", msg)

    def _tick_obd(self, i: int) -> None:
        t = i * OBD_S
        if self._offline(t) or "obd" not in self.scenario.live_sensors(t):
            return
        now = self.clock()
        self.watchdog.feed("obd", now)
        channels = {name: {"t": [t], "v": [v], "unit": unit, "source": "simulator"}
                    for name, (v, unit) in obd_sample(self.scenario, i).items()}
        self._send(self.obd_hub, "obd", messages.obd_message(sent_at=now, channels=channels))

    def _tick_signal(self, j: int) -> None:
        t0 = j * SIGNAL_S
        if self._offline(t0):
            return
        now = self.clock()
        for fields, payload in signal_frames(self.scenario, j, self.scenario.live_sensors(t0)):
            if self.watchdog.watches(fields["channel"]):
                self.watchdog.feed(fields["channel"], now)
            header = messages.signal_header(sent_at=now, **fields)
            if self.validate and (errs := messages.errors("signal_header", header)):
                log.error("signal header violates contract: %s", errs)
            self.signal_hub.publish(encode_frame(header, payload))

    def _send(self, hub: Hub, schema: str, msg: dict) -> None:
        if self.validate and (errs := messages.errors(schema, msg)):
            log.error("%s message violates contract 1.3: %s", schema, errs)
        hub.publish(json.dumps(msg, separators=(",", ":")))
        for fn in self._listeners:
            try:
                fn(schema, msg)
            except Exception:
                log.exception("listener failed")

    # ------------------------------------------------------------ ack

    def handle_ack(self, text: str) -> dict | None:
        """Apply an `ack` from the UI. Returns an ack_error to send back, or None when accepted."""
        now = self.clock()
        alarm_id = None
        try:
            msg = json.loads(text)
        except (ValueError, TypeError):
            return self._reject(None, "invalid_message", "not valid JSON", now)
        if isinstance(msg, dict) and isinstance(msg.get("alarm_id"), str):
            alarm_id = msg["alarm_id"]
        if errs := messages.errors("ack", msg):
            return self._reject(alarm_id, "invalid_message", "; ".join(errs), now)

        res = self.ack(msg["alarm_id"], msg["operator"], via="websocket")
        return None if res.ok else messages.ack_error(sent_at=now, alarm_id=alarm_id, reason=res.reason, detail=res.detail)

    def ack(self, alarm_id: str, operator: str, via: str) -> AckResult:
        """The one ACK path (WebSocket and OPC UA). On success, broadcasts the new state at once."""
        res = self.rules.ack(alarm_id, operator, self.clock())
        if not res.ok:
            log.warning("ACK rejected via %s (%s): %s", via, res.reason, res.detail)
            return res
        log.info("ACK %s by %s via %s", alarm_id, operator, via)
        if self.last_window is not None:
            self._publish_state(self.rules.decision(), time.perf_counter())
        return res

    def _reject(self, alarm_id, reason, detail, now) -> dict:
        log.warning("ACK rejected (%s): %s", reason, detail)
        return messages.ack_error(sent_at=now, alarm_id=alarm_id, reason=reason, detail=detail)
