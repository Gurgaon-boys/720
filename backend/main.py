"""FastAPI app: the three WebSocket channels from contract/ plus a few plain HTTP helpers.

    python -m uvicorn backend.main:app --port 8000

Environment:
    S720_SCENARIO         fake scenario to start with (default: misfire); see GET /scenarios
    S720_VALIDATE         1 = check every outgoing message against contract/schemas and log violations
    S720_OPCUA            0 = don't start the OPC UA server (default: on)
    S720_OPCUA_HOST       interface for OPC UA (default 127.0.0.1; 0.0.0.0 = every interface, for a LAN client)
    S720_OPCUA_ENDPOINT   full endpoint, overrides the host (default opc.tcp://<host>:4840/720/)
    S720_SCENARIO   source to start with (default: misfire): a fake scenario, or "sim" for the live
                    simulator (L1); see GET /scenarios
    S720_SIM_SEED, S720_SIM_ENGINE   simulator run seed / randomised engine seed (sim only)
    S720_VALIDATE   1 = check every outgoing message against contract/schemas and log violations
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend import investigator
from backend import report as incident_report
from backend import report_pdf as incident_pdf
from backend.messages import SCHEMA_VERSION
from backend.opcua_bridge import OpcuaBridge, endpoint_from_env
from backend.runtime import Hub, Runtime, SensorControlError, put_latest
from backend.sources import REPLAY, describe, make_source, source_names
from backend.sources.sim_source import SABOTAGE_CHANNELS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backend")


def create_app(scenario: str | None = None, validate: bool | None = None, opcua: str | bool | None = None) -> FastAPI:
    """opcua: None = from the environment, False = off, a string = the endpoint to serve on."""
    scenario = scenario or os.environ.get("S720_SCENARIO", "misfire")
    if validate is None:
        validate = os.environ.get("S720_VALIDATE", "0") == "1"
    if opcua is None:
        opcua = endpoint_from_env() if os.environ.get("S720_OPCUA", "1") != "0" else False
    runtime = Runtime(scenario, validate=validate)
    bridge = OpcuaBridge(runtime, opcua) if opcua else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        if bridge is not None:
            try:
                await bridge.start()
            except Exception:
                # the dashboard keeps working without OPC UA (e.g. port 4840 already taken)
                log.exception("OPC UA server failed to start on %s; continuing without it", bridge.endpoint)
        yield
        if bridge is not None:
            await bridge.stop()
        await runtime.stop()

    app = FastAPI(title="720 backend", version="L0", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.opcua = bridge
    # the Vite dev server on any local port (5173 by default) may call the HTTP helpers
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["Content-Disposition"])  # PDF report file name (D143)

    @app.get("/health")
    def health():
        return {"status": "ok", "schema_version": SCHEMA_VERSION, "scenario": runtime.scenario.name,
                "maintenance": runtime.maintenance_mode,
                "opcua": bridge.endpoint if bridge is not None and bridge.running else None}

    @app.get("/scenarios")
    def scenarios():
        return {"current": runtime.scenario.name, "scenarios": describe()}

    @app.post("/scenario/{name}")
    async def select(name: str):
        """Switch source: a fake scenario or "sim". Resets stream time and the rule layer.

        The source is built in a worker thread (the simulator takes a moment), then swapped in on the
        event loop so no tick runs halfway through the switch."""
        if name not in source_names():
            raise HTTPException(404, f"unknown scenario {name!r}; see GET /scenarios")
        source = await run_in_threadpool(make_source, name)
        runtime.select(name, source)
        return {"current": name}

    @app.post("/maintenance/{mode}")
    def maintenance(mode: str):
        """Explicit maintenance/test mode: asset health shows CHECK_FUNCTION while nothing is wrong.
        A latched alarm or an unclassified anomaly keeps its own health. Doesn't touch the rule layer."""
        if mode not in ("on", "off"):
            raise HTTPException(400, "use /maintenance/on or /maintenance/off")
        runtime.set_maintenance(mode == "on")
        return {"maintenance": runtime.maintenance_mode}
    # ------------------------------------------------------------ REAL carOBD replay

    @app.get("/replay/files")
    async def replay_files():
        """Held-out carOBD files by type (idle, drive, live, campus, long) and uploaded logs."""
        from backend.sources import carobd_replay as cr

        files = await run_in_threadpool(cr.list_files)
        cur = runtime.scenario
        return {"files": files, "speeds": list(cr.SPEEDS),
                "current": {"file": cur.file_ref, "speed": cur.speed} if cur.name == REPLAY else None}

    @app.post("/replay")
    async def replay(body: ReplayRequest):
        """Switch to the REAL carOBD replay of one file at 1x / 10x / 30x. Resets stream time and the rule layer."""
        from backend.sources import carobd_replay as cr

        try:
            source = await run_in_threadpool(cr.CarObdReplay, body.file, body.speed)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        runtime.select(REPLAY, source)
        return {"current": REPLAY, "file": body.file, "speed": source.speed}

    @app.post("/replay/speed")
    async def replay_speed(body: SpeedRequest):
        """Change the replay speed without restarting the file."""
        src = runtime.scenario
        if src.name != REPLAY:
            raise HTTPException(409, "not replaying; POST /replay first")
        try:
            src.set_speed(body.speed)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"speed": src.speed}

    @app.post("/upload_obd")
    async def upload_obd(request: Request, filename: str = "upload.csv"):
        """A carOBD-format CSV as the raw request body (Content-Type text/csv), at most 5 MB. Validated
        with the same loader, stored under data/uploads/ with a random id, and listed as a replay file
        labelled REAL (uploaded, unverified). Select it with POST /replay {"file": "upload:<id>"}."""
        from backend.sources import carobd_replay as cr

        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cr.MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file is larger than {cr.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        data = bytearray()
        async for part in request.stream():
            data += part
            if len(data) > cr.MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"file is larger than {cr.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        try:
            meta = await run_in_threadpool(cr.save_upload, filename, bytes(data), runtime.clock())
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        log.info("UPLOAD %s -> %s (%d rows, %.0f s)", meta["name"], meta["id"], meta["rows"], meta["duration_s"])
        return {"file": f"upload:{meta['id']}", **meta}

    @app.post("/inject")
    async def inject(body: Injection):
        """Inject a fault: into the live simulator (source "sim"), or `lean_drift` onto the REAL carOBD
        replay (labelled REAL+INJECTED). Applies from the next engine cycle / logged row."""
        src = runtime.scenario
        if not hasattr(src, "inject"):
            raise HTTPException(409, f"source {src.name!r} takes no injection; POST /scenario/sim or /replay first")
        cylinder = None if src.name == REPLAY else body.cylinder
        if cylinder is None and src.name != REPLAY:
            raise HTTPException(422, "cylinder is required for the simulator")
        try:
            runtime.inject(body.fault, cylinder, body.severity, via="http")  # echoed in state.injection
        except NotImplementedError as e:
            raise HTTPException(501, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        logging.getLogger("backend").info("INJECT %s cyl %s p=%.2f at t=%.2f s", body.fault, cylinder,
                                          body.severity, src.stream_t)
        return {"injected": body.model_dump(), "t": round(src.stream_t, 3)}

    @app.post("/clear_faults")
    async def clear_faults():
        src = runtime.scenario
        if not hasattr(src, "clear_faults"):
            raise HTTPException(409, f"source {src.name!r} takes no injection")
        runtime.clear_faults(via="http")  # state.injection -> null
        return {"cleared": True, "t": round(src.stream_t, 3)}

    # ------------------------------------------------------------ Sabotage Lab sensor controls (D140)

    def _sensor_reply(disabled: list[str] | None) -> dict:
        """What was commanded. Sensor health itself is only ever confirmed by state.health.sensors."""
        health = (runtime.last_state or {}).get("health", {}).get("sensors")
        return {"source": runtime.scenario.name, "controllable": disabled is not None,
                "channels": list(SABOTAGE_CHANNELS), "disabled": disabled or [], "health": health,
                "t": round(getattr(runtime.scenario, "stream_t", 0.0), 3)}

    def _sensor_command(fn, *args) -> dict:
        try:
            return _sensor_reply(fn(*args, via="http"))
        except SensorControlError as e:
            raise HTTPException(409, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/sensors")
    def sensors():
        return _sensor_reply(runtime.disabled_sensors())

    @app.post("/sensor/{channel}/off")
    async def sensor_off(channel: str):
        """Switch a simulator sensor off (mic, accel, crank, cam). 409 off the simulator."""
        return _sensor_command(runtime.disable_sensor, channel)

    @app.post("/sensor/{channel}/on")
    async def sensor_on(channel: str):
        return _sensor_command(runtime.enable_sensor, channel)

    @app.post("/sensors/restore")
    async def sensors_restore():
        return _sensor_command(runtime.restore_sensors)

    # ------------------------------------------------------------ Resilience Lab (D141): record, agent, link, report

    @app.get("/events")
    def events(after: int = 0):
        """Incident record since sequence number `after` (requested / accepted / refused / confirmed)."""
        return {"events": runtime.events.since(after), "now": runtime.clock()}

    @app.get("/investigate")
    def investigate_now(trigger: str | None = None):
        """Reliability Agent: read-only tools over the backend state, deterministic answers."""
        return investigator.investigate(runtime, trigger)

    @app.get("/industrial")
    def industrial():
        """Facts about the OPC UA server as it ran (nothing estimated). No client count: asyncua's
        session list is not exposed here, so none is reported."""
        last_cmd = runtime.events.last(pred=lambda e: e["via"] == "opcua" and e["phase"] in ("accepted", "refused"))
        running = bool(bridge is not None and bridge.running)
        return {"running": running, "endpoint": bridge.endpoint if bridge is not None else None,
                "namespace": "urn:720:hackhive", "security": "None (local demo)",
                "last_publish": bridge.last_publish if bridge is not None else None,
                "last_event": bridge.last_event if bridge is not None else None,
                "events_emitted": bridge.events_emitted if bridge is not None else 0,
                "last_command": last_cmd, "sensor_health": (runtime.last_state or {}).get("health", {}).get("sensors"),
                "now": runtime.clock()}

    @app.get("/report", response_class=HTMLResponse)
    def report():
        """Printable incident report from recorded data only."""
        return incident_report.build(runtime)

    @app.get("/report.pdf")
    def report_pdf():
        """The same incident report as a downloadable PDF (D143). 503 without reportlab: use GET /report."""
        pdf = incident_pdf.build_pdf(runtime)
        if pdf is None:
            raise HTTPException(503, "PDF export needs reportlab (pip install -r requirements.txt); use GET /report and print")
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{incident_pdf.filename(runtime)}"'})

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket):
        await ws.accept()
        q = runtime.state_hub.subscribe()

        async def receive_acks():
            while True:
                text = await ws.receive_text()
                if (err := runtime.handle_ack(text)) is not None:
                    put_latest(q, err)  # reply on this socket only

        await _serve(ws, runtime.state_hub, q, receive_acks)

    @app.websocket("/ws/obd")
    async def ws_obd(ws: WebSocket):
        await ws.accept()
        await _serve(ws, runtime.obd_hub, runtime.obd_hub.subscribe())

    @app.websocket("/ws/signal")
    async def ws_signal(ws: WebSocket):
        await ws.accept()
        await _serve(ws, runtime.signal_hub, runtime.signal_hub.subscribe())

    return app


class Injection(BaseModel):
    fault: str
    cylinder: int | None = None   # required by the simulator; ignored by the carOBD replay (bank-wide)
    severity: float


class ReplayRequest(BaseModel):
    file: str                     # "heldout:<name>" or "upload:<id>" (GET /replay/files)
    speed: float = 10


class SpeedRequest(BaseModel):
    speed: float


async def _serve(ws: WebSocket, hub: Hub, q: asyncio.Queue, receiver=None) -> None:
    """Send everything queued for this client; when there's no upstream channel, still read so a
    disconnect is noticed. Ends when either side stops."""

    async def send():
        while True:
            msg = await q.get()
            if isinstance(msg, bytes):
                await ws.send_bytes(msg)
            elif isinstance(msg, dict):
                await ws.send_json(msg)
            else:
                await ws.send_text(msg)

    async def drain():
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass

    tasks = [asyncio.create_task(send()), asyncio.create_task((receiver or drain)())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        hub.unsubscribe(q)


app = create_app()
