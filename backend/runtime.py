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
from backend.scenarios import OBD_S, SIGNAL_S, WINDOW_S
from backend.sources import make_source
from backend.signal_frame import encode_frame
from backend.watchdog import Watchdog
from models.guidance import guidance

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
        self.injection = None  # a new source starts with nothing injected
        self._epoch = time.monotonic()
        self._generation = getattr(self, "_generation", 0) + 1

    def inject(self, fault: str, cylinder: int, severity: float) -> dict:
        """Inject into the simulator source and record the accepted request for state.injection (1.4).
        The source's own errors (NotImplementedError, ValueError) pass through unrecorded."""
        self.scenario.inject(fault, cylinder, severity)
        self.injection = {"active": True, "fault": fault, "cylinder": None if cylinder is None else int(cylinder),
                          "severity": float(severity),
                          "started_at": self.clock()}
        return self.injection

    def clear_faults(self) -> None:
        self.scenario.clear_faults()
        self.injection = None

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
        # sensors without a stream of their own (the simulator's crank) are fed here while live
        polled = getattr(self.scenario, "polled_sensors", ())
        if polled:
            now, live = self.clock(), self.scenario.live_sensors(k * WINDOW_S)
            for s in polled:
                if s in live:
                    self.watchdog.feed(s, now)
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
        health = {"asset_ne107": asset_ne107(decision, self.maintenance_mode), "sensors": self.watchdog.status(now)}
        decision = {**decision, "guidance": guidance(decision, health)}
        latency = round((time.perf_counter() - start) * 1000, 2)
        msg = messages.state_message(sent_at=now, decision=decision, health=health, latency_ms=latency,
                                     injection=self.injection, **w.fields)
        self.last_state = msg
        self._send(self.state_hub, "state", msg)

    def _tick_obd(self, i: int) -> None:
        t = i * OBD_S
        if self._offline(t) or "obd" not in self.scenario.live_sensors(t):
            return
        now = self.clock()
        label = getattr(self.scenario, "source_label", None)
        if hasattr(self.scenario, "obd_channels"):      # REAL replay: values at their own file times
            channels = self.scenario.obd_channels(i)
            if channels is None:                        # nothing logged in this span (logger gap)
                return
            self.watchdog.feed("obd", now)
            if channels:
                self._send(self.obd_hub, "obd", messages.obd_message(sent_at=now, channels=channels, source_label=label))
            return
        self.watchdog.feed("obd", now)
        cal = getattr(self.scenario, "channel_calibration", {})
        channels = {name: {"t": [t], "v": [v], "unit": unit, "source": "simulator",
                           **({"calibration": cal[name]} if name in cal else {})}
                    for name, (v, unit) in self.scenario.obd_sample(i).items()}
        self._send(self.obd_hub, "obd", messages.obd_message(sent_at=now, channels=channels, source_label=label))

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
            log.error("%s message violates contract %s: %s", schema, messages.SCHEMA_VERSION, errs)
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
