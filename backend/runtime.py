"""The streaming runtime: three loops at their own rates, fanned out to every connected client.

  state   ~10 Hz  one window -> rule layer -> `state` JSON
  obd     ~1 Hz   one sample per OBD channel -> `obd` JSON
  signal  ~20 Hz  mic + accel waveforms and a mic STFT column -> binary frames

ACKs come in on /ws/state. Only the rule layer decides what they do; a valid ACK triggers an
immediate `state` broadcast, an invalid one gets an `ack_error` reply on that socket only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from backend import messages
from backend.rule_layer import RuleLayer, asset_ne107
from backend.scenarios import OBD_S, SIGNAL_S, WINDOW_S
from backend.sources import make_source
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
        self.select(scenario)

    # ------------------------------------------------------------ control

    def select(self, name: str, source=None) -> None:
        """Switch source (a fake scenario, or "sim"). Restarts stream time and the rule layer, so no
        alarm leaks across. Raises KeyError for an unknown name.

        Call it on the event loop (or before start): the swap and the clock reset must not interleave
        with a tick, or a stateful source (the simulator) could be stepped with the old stream's tick
        index. `source` lets the caller build a slow source elsewhere first (make_source(name))."""
        self.scenario = source if source is not None else make_source(name)
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
        start = time.perf_counter()
        w = self.scenario.window(k)
        if w is None or w.phase.offline:
            return  # no window yet, or offline: nothing goes out (offline trips the UI's 3 s watchdog)
        if w.fresh:
            for ev in w.backlog:
                self.rules.step(ev)
            decision = self.rules.step(w.evidence)
        else:
            decision = self.rules.decision()  # same window as last tick: one rule-layer step per window
        self.last_window = w
        self._publish_state(decision, start)

    def _publish_state(self, decision: dict, start: float) -> None:
        w = self.last_window
        now = self.clock()
        health = {"asset_ne107": asset_ne107(decision), "sensors": self.watchdog.status(now)}
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
                    for name, (v, unit) in self.scenario.obd_sample(i).items()}
        self._send(self.obd_hub, "obd", messages.obd_message(sent_at=now, channels=channels))

    def _tick_signal(self, j: int) -> None:
        t0 = j * SIGNAL_S
        if self._offline(t0):
            return
        now = self.clock()
        for fields, payload in self.scenario.signal_frames(j, self.scenario.live_sensors(t0)):
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

        res = self.rules.ack(msg["alarm_id"], msg["operator"], now)
        if not res.ok:
            return self._reject(alarm_id, res.reason, res.detail, now)
        log.info("ACK %s by %s", alarm_id, msg["operator"])
        if self.last_window is not None:
            self._publish_state(self.rules.decision(), time.perf_counter())
        return None

    def _reject(self, alarm_id, reason, detail, now) -> dict:
        log.warning("ACK rejected (%s): %s", reason, detail)
        return messages.ack_error(sent_at=now, alarm_id=alarm_id, reason=reason, detail=detail)
