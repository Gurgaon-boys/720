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
from backend.events import EventLog
from backend.watchdog import Watchdog
from models.guidance import guidance

log = logging.getLogger("backend")

QUEUE_SIZE = 64


class SensorControlError(RuntimeError):
    """The current source has no sensor controls (only the live simulator has)."""


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
        self.events = EventLog(clock)  # incident record: requested / accepted / confirmed (D138)
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
        self.events.reset_digest()
        self.events.record("source", "confirmed", f"Source switched to {name!r}: new run, rule layer reset, every sensor on",
                           source=name)

    def _command(self, kind: str, text: str, via: str, fn, **data):
        """Run one operator command with its requested -> accepted / refused record (D138)."""
        st = getattr(self.scenario, "stream_t", None)
        self.events.record(kind, "requested", text, via=via, stream_t=st, **data)
        try:
            out = fn()
        except Exception as e:
            self.events.record(kind, "refused", f"{text}: refused ({e})", via=via, stream_t=st, reason=str(e), **data)
            raise
        self.events.record(kind, "accepted", f"{text}: accepted by the backend", via=via, stream_t=st, **data)
        return out

    def inject(self, fault: str, cylinder: int, severity: float, via: str = "api") -> dict:
        """Inject into the simulator source and record the accepted request for state.injection (1.4).
        The source's own errors (NotImplementedError, ValueError) pass through unrecorded."""
        where = "" if cylinder is None else f" cyl {cylinder}"
        self._command("inject", f"Inject {fault}{where} (severity {float(severity):.2f})", via,
                      lambda: self.scenario.inject(fault, cylinder, severity), fault=fault, cylinder=cylinder,
                      severity=float(severity))
        self.injection = {"active": True, "fault": fault, "cylinder": None if cylinder is None else int(cylinder),
                          "severity": float(severity),
                          "started_at": self.clock()}
        return self.injection

    def clear_faults(self, via: str = "api") -> None:
        self._command("clear", "Clear faults", via, self.scenario.clear_faults)   # disabled sensors stay disabled (D137)
        self.injection = None

    # ------------------------------------------------------------ sensor sabotage (D137)

    def _sabotage_source(self):
        """The current source if it can switch sensors off (the live simulator), else SensorControlError.
        HTTP and OPC UA both come through here, so there is one sensor state: the source's."""
        if not hasattr(self.scenario, "disable_sensor"):
            raise SensorControlError(f"source {self.scenario.name!r} has no simulator; switch to sim first")
        return self.scenario

    def disable_sensor(self, channel: str, via: str = "api") -> list[str]:
        """Raises SensorControlError off the simulator, ValueError for an unknown channel."""
        src = self._command("sensor_off", f"Disable {str(channel).upper()}", via,
                            lambda: (self._sabotage_source().disable_sensor(channel), self.scenario)[1], sensor=channel)
        log.info("SENSOR %s off at t=%.2f s", channel, src.stream_t)
        return src.disabled_sensors()

    def enable_sensor(self, channel: str, via: str = "api") -> list[str]:
        src = self._command("sensor_on", f"Enable {str(channel).upper()}", via,
                            lambda: (self._sabotage_source().enable_sensor(channel), self.scenario)[1], sensor=channel)
        log.info("SENSOR %s on at t=%.2f s", channel, src.stream_t)
        return src.disabled_sensors()

    def restore_sensors(self, via: str = "api") -> list[str]:
        src = self._command("sensor_restore", "Restore all sensors", via,
                            lambda: (self._sabotage_source().restore_sensors(), self.scenario)[1])
        log.info("SENSORS restored at t=%.2f s", src.stream_t)
        return src.disabled_sensors()

    def disabled_sensors(self) -> list[str] | None:
        """None when the source has no sensor controls."""
        fn = getattr(self.scenario, "disabled_sensors", None)
        return fn() if fn else None

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
        cap = getattr(self.scenario, "guidance_capability", None)   # e.g. single-witness REAL replay (D135)
        decision = {**decision, "guidance": guidance(decision, health, cap, self.guidance_context(decision))}
        latency = round((time.perf_counter() - start) * 1000, 2)
        msg = messages.state_message(sent_at=now, decision=decision, health=health, latency_ms=latency,
                                     injection=self.injection, **w.fields)
        self.last_state = msg
        self.events.observe(msg)
        self._send(self.state_hub, "state", msg)

    @property
    def resets(self) -> int:
        return getattr(self, "_resets", 0)

    def guidance_context(self, decision: dict) -> str | None:
        """The engine's situation for the guidance line: the post-misfire recovery ADVISORY, or warming up."""
        if decision["state"] == "ADVISORY" and decision["fault"] is None and self.rules.recovering:
            return "recovering_after_misfire"
        if getattr(self.scenario, "warming_up", False):
            return "warming_up"
        return None

    def reset_engine(self, source=None) -> None:
        """A fresh healthy simulator engine with the same seed (POST /reset_engine). Like a source switch, it
        restarts stream time and the rule layer; alarm ids keep counting. Simulator only."""
        from backend.sources.sim_source import SimSource

        if not isinstance(self.scenario, SimSource):
            raise TypeError(f"source {self.scenario.name!r} is not the simulator")
        self._resets = self.resets + 1
        self.select("sim", source if source is not None else SimSource.from_env(reset_n=self._resets))
        log.info("engine reset (%d): fresh healthy simulator engine, same seed", self._resets)

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
        st = getattr(self.scenario, "stream_t", None)
        self.events.record("ack", "requested", f"ACK {alarm_id} by {operator}", via=via, stream_t=st, alarm_id=alarm_id)
        res = self.rules.ack(alarm_id, operator, self.clock())
        if not res.ok:
            log.warning("ACK rejected via %s (%s): %s", via, res.reason, res.detail)
            self.events.record("ack", "refused", f"ACK {alarm_id}: refused ({res.reason})", via=via, stream_t=st,
                               alarm_id=alarm_id, reason=res.reason)
            return res
        self.events.record("ack", "accepted", f"ACK {alarm_id} accepted by the rule layer", via=via, stream_t=st,
                           alarm_id=alarm_id, operator=operator)
        log.info("ACK %s by %s via %s", alarm_id, operator, via)
        if self.last_window is not None:
            self._publish_state(self.rules.decision(), time.perf_counter())
        return res

    def _reject(self, alarm_id, reason, detail, now) -> dict:
        log.warning("ACK rejected (%s): %s", reason, detail)
        return messages.ack_error(sent_at=now, alarm_id=alarm_id, reason=reason, detail=detail)
