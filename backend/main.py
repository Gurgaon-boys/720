"""FastAPI app: the three WebSocket channels from contract/ plus a few plain HTTP helpers.

    python -m uvicorn backend.main:app --port 8000

Environment:
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

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.messages import SCHEMA_VERSION
from backend.runtime import Hub, Runtime, put_latest
from backend.sources import describe, make_source, source_names

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(scenario: str | None = None, validate: bool | None = None) -> FastAPI:
    scenario = scenario or os.environ.get("S720_SCENARIO", "misfire")
    if validate is None:
        validate = os.environ.get("S720_VALIDATE", "0") == "1"
    runtime = Runtime(scenario, validate=validate)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        yield
        await runtime.stop()

    app = FastAPI(title="720 backend", version="L0", lifespan=lifespan)
    app.state.runtime = runtime
    # the Vite dev server on any local port (5173 by default) may call the HTTP helpers
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"])

    @app.get("/health")
    def health():
        return {"status": "ok", "schema_version": SCHEMA_VERSION, "scenario": runtime.scenario.name}

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

    @app.post("/inject")
    async def inject(body: Injection):
        """Inject a fault into the live simulator (source "sim" only). Applies from the next engine cycle."""
        src = runtime.scenario
        if not hasattr(src, "inject"):
            raise HTTPException(409, f"source {src.name!r} has no simulator; POST /scenario/sim first")
        try:
            src.inject(body.fault, body.cylinder, body.severity)
        except NotImplementedError as e:
            raise HTTPException(501, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        logging.getLogger("backend").info("INJECT %s cyl %d p=%.2f at t=%.2f s", body.fault, body.cylinder,
                                          body.severity, src.stream_t)
        return {"injected": body.model_dump(), "t": round(src.stream_t, 3)}

    @app.post("/clear_faults")
    async def clear_faults():
        src = runtime.scenario
        if not hasattr(src, "clear_faults"):
            raise HTTPException(409, f"source {src.name!r} has no simulator")
        src.clear_faults()
        return {"cleared": True, "t": round(src.stream_t, 3)}

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
    cylinder: int
    severity: float


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
