"""FastAPI app: the three WebSocket channels from contract/ plus a few plain HTTP helpers.

    python -m uvicorn backend.main:app --port 8000

Environment:
    S720_SCENARIO         fake scenario to start with (default: misfire); see GET /scenarios
    S720_VALIDATE         1 = check every outgoing message against contract/schemas and log violations
    S720_OPCUA            0 = don't start the OPC UA server (default: on)
    S720_OPCUA_ENDPOINT   default opc.tcp://127.0.0.1:4840/720/
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from backend.messages import SCHEMA_VERSION
from backend.opcua_bridge import DEFAULT_ENDPOINT, OpcuaBridge
from backend.runtime import Hub, Runtime, put_latest
from backend.scenarios import SCENARIOS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backend")


def create_app(scenario: str | None = None, validate: bool | None = None, opcua: str | bool | None = None) -> FastAPI:
    """opcua: None = from the environment, False = off, a string = the endpoint to serve on."""
    scenario = scenario or os.environ.get("S720_SCENARIO", "misfire")
    if validate is None:
        validate = os.environ.get("S720_VALIDATE", "0") == "1"
    if opcua is None:
        opcua = os.environ.get("S720_OPCUA_ENDPOINT", DEFAULT_ENDPOINT) if os.environ.get("S720_OPCUA", "1") != "0" else False
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
                       allow_methods=["*"], allow_headers=["*"])

    @app.get("/health")
    def health():
        return {"status": "ok", "schema_version": SCHEMA_VERSION, "scenario": runtime.scenario.name,
                "maintenance": runtime.maintenance_mode,
                "opcua": bridge.endpoint if bridge is not None and bridge.running else None}

    @app.get("/scenarios")
    def scenarios():
        return {"current": runtime.scenario.name,
                "scenarios": [{"name": s.name, "description": s.description, "source": s.source,
                               "loop_s": s.period} for s in SCENARIOS.values()]}

    @app.post("/scenario/{name}")
    def select(name: str):
        """Switch fake scenario (L0 only). Resets stream time and the rule layer."""
        if name not in SCENARIOS:
            raise HTTPException(404, f"unknown scenario {name!r}; see GET /scenarios")
        runtime.select(name)
        return {"current": name}

    @app.post("/maintenance/{mode}")
    def maintenance(mode: str):
        """Explicit maintenance/test mode: asset health shows CHECK_FUNCTION while nothing is wrong.
        A latched alarm or an unclassified anomaly keeps its own health. Doesn't touch the rule layer."""
        if mode not in ("on", "off"):
            raise HTTPException(400, "use /maintenance/on or /maintenance/off")
        runtime.set_maintenance(mode == "on")
        return {"maintenance": runtime.maintenance_mode}

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
