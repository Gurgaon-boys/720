"""End-to-end OPC UA check: starts the real FastAPI backend (with its OPC UA server), drives the
L0 fake scenarios over HTTP, and verifies everything through an independent asyncua client.

    python -m backend.opcua_check                       # ports 8000 / 4840
    python -m backend.opcua_check --http-port 8010 --opcua-port 4850

Prints one PASS/FAIL line per check and exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import socket
import sys
import threading
import time

import httpx
import uvicorn
from asyncua import Client, ua
from websockets.sync.client import connect

from backend.main import create_app
from backend.opcua_bridge import NE107

logging.basicConfig(level=logging.WARNING)
logging.getLogger("asyncua").setLevel(logging.ERROR)
logging.getLogger("backend").setLevel(logging.WARNING)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}", flush=True)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class Ua:
    def __init__(self, client: Client, idx: int):
        self.c, self.idx = client, idx

    def node(self, path):
        return self.c.get_node(ua.NodeId(path, self.idx))

    async def read(self, path):
        return await self.node(f"EngineAsset.{path}").read_value()

    async def status(self, path):
        dv = await self.node(f"EngineAsset.{path}").read_data_value(raise_on_bad_status=False)
        return dv.StatusCode

    async def wait(self, path, pred, timeout=12.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            v = await self.read(path)
            if pred(v):
                return v
            await asyncio.sleep(0.05)
        return await self.read(path)


async def verify(http: str, endpoint: str) -> None:
    def scenario(name):
        httpx.post(f"{http}/scenario/{name}").raise_for_status()

    async with Client(endpoint) as c:
        u = Ua(c, await c.get_namespace_index("urn:720:hackhive"))
        health = lambda v: NE107[v]  # noqa: E731

        # 1. healthy -> NORMAL
        scenario("healthy")
        await u.wait("RunId", lambda v: v == "fake_healthy")
        await asyncio.sleep(0.5)
        st, ah = await u.read("Decision.State"), await u.read("AssetHealth")
        check("1 healthy publishes NORMAL", st == "NORMAL" and health(ah) == "NORMAL", f"State={st}, AssetHealth={health(ah)}")

        # 2. misfire -> ALARM + OFF_SPEC
        scenario("misfire")
        st = await u.wait("Decision.State", lambda v: v == "ALARM")
        ah, fault, cyl, aid = (await u.read("AssetHealth"), await u.read("Decision.Fault"),
                               await u.read("Decision.Cylinder"), await u.read("Decision.AlarmId"))
        check("2 misfire publishes ALARM + OFF_SPEC", st == "ALARM" and health(ah) == "OFF_SPEC" and fault == "misfire" and cyl == 3,
              f"State={st}, AssetHealth={health(ah)}, Fault={fault}, Cylinder={cyl}, AlarmId={aid}")

        # 5. ACK through OPC UA changes the one authoritative state (rule layer -> dashboard broadcast too)
        with connect(http.replace("http", "ws") + "/ws/state") as ws:
            ok_bad, reason_bad = await u.node("EngineAsset").call_method(ua.NodeId("EngineAsset.Acknowledge", u.idx), "a_9999", "uaexpert")
            acked_after_bad = await u.read("Decision.Acked")
            ok, reason = await u.node("EngineAsset").call_method(ua.NodeId("EngineAsset.Acknowledge", u.idx), aid, "uaexpert")
            seen = None
            end = time.monotonic() + 3
            while time.monotonic() < end and seen is None:
                m = json.loads(ws.recv(timeout=1))
                if m.get("decision", {}).get("acked_by") == "uaexpert":
                    seen = m["decision"]
            ua_acked, ua_by, ua_state = await u.read("Decision.Acked"), await u.read("Decision.AckedBy"), await u.read("Decision.State")
        check("5a unknown ACK rejected, nothing changes", not ok_bad and reason_bad.startswith("unknown_alarm_id") and acked_after_bad is False,
              reason_bad)
        check("5b OPC UA ACK reaches the dashboard broadcast", ok and seen is not None and seen["alarm_id"] == aid and seen["state"] == "ALARM",
              f"WebSocket decision: state={seen and seen['state']}, acked={seen and seen['acked']}, acked_by={seen and seen['acked_by']}")
        check("5c OPC UA shows the same ACK, alarm still latched", ua_acked is True and ua_by == "uaexpert" and ua_state == "ALARM",
              f"Acked={ua_acked}, AckedBy={ua_by}, State={ua_state}")
        ok_dup, reason_dup = await u.node("EngineAsset").call_method(ua.NodeId("EngineAsset.Acknowledge", u.idx), aid, "someone_else")
        check("5d duplicate ACK rejected", not ok_dup and reason_dup.startswith("duplicate_ack"), reason_dup)

        # 7a. misfire -> healthy: nothing left over from the alarm
        scenario("healthy")
        await u.wait("RunId", lambda v: v == "fake_healthy")
        await asyncio.sleep(0.5)
        left = {k: await u.read(k) for k in ("Decision.State", "Decision.Fault", "Decision.Cylinder", "Decision.AlarmId",
                                              "Decision.Acked", "Decision.RootCause", "AssetHealth")}
        sev = await u.read("Decision.Severity")
        per_cyl = await u.read("Diagnostics.CylinderAnomaly")
        clean = (left["Decision.State"] == "NORMAL" and left["Decision.Fault"] == "" and left["Decision.Cylinder"] == 0
                 and left["Decision.AlarmId"] == "" and left["Decision.Acked"] is False and left["Decision.RootCause"] == ""
                 and left["AssetHealth"] == 0 and math.isnan(sev) and max(per_cyl) < 0.5)
        check("7a misfire -> healthy leaves no alarm values", clean, f"{left}, Severity={sev}, CylinderAnomaly max={max(per_cyl):.2f}")

        # 3. injector clog -> MAINTENANCE_REQUIRED
        scenario("injector_clog")
        st = await u.wait("Decision.State", lambda v: v == "ALARM")
        ah = await u.read("AssetHealth")
        check("3 injector clog publishes MAINTENANCE_REQUIRED", st == "ALARM" and health(ah) == "MAINTENANCE_REQUIRED",
              f"State={st}, AssetHealth={health(ah)}, Fault={await u.read('Decision.Fault')}")

        # 4. unclassified -> OFF_SPEC + unclassified diagnostic state
        scenario("unclassified")
        st = await u.wait("Decision.State", lambda v: v == "UNCLASSIFIED")
        ah, root, hint = await u.read("AssetHealth"), await u.read("Decision.RootCause"), await u.read("Decision.Fault")
        check("4 unclassified publishes OFF_SPEC + UNCLASSIFIED", st == "UNCLASSIFIED" and health(ah) == "OFF_SPEC" and root == "unclassified",
              f"State={st}, AssetHealth={health(ah)}, RootCause={root}, physics hint={hint}, AlarmId='{await u.read('Decision.AlarmId')}'")

        # 6. mic dropout -> mic FAILURE, engine not failed
        scenario("mic_dropout")
        mic = await u.wait("SensorHealth.mic", lambda v: v == NE107.index("FAILURE"))
        accel, ah, st = await u.read("SensorHealth.accel"), await u.read("AssetHealth"), await u.read("Decision.State")
        check("6 mic dropout: mic FAILURE, engine still NORMAL", health(mic) == "FAILURE" and health(accel) == "NORMAL"
              and health(ah) == "NORMAL" and st == "NORMAL",
              f"mic={health(mic)}, accel={health(accel)}, AssetHealth={health(ah)}, State={st}")

        # 7b. -> real clip: sensors and OBD it doesn't have must not keep old values as good
        scenario("real_p3")
        await u.wait("Source", lambda v: v == "REAL")
        await asyncio.sleep(1.5)
        mic_s = await u.status("SensorHealth.mic")
        accel_s, obd_s = await u.status("SensorHealth.accel"), await u.status("SensorHealth.obd")
        rpm = await u.read("OperatingPoint.Rpm")
        obd_rpm_s = await u.status("Obd.rpm")
        check("7b real clip: absent sensors are Bad_NotConnected, RPM unknown",
              mic_s.is_good() and accel_s.value == ua.StatusCodes.BadNotConnected and obd_s.value == ua.StatusCodes.BadNotConnected
              and math.isnan(rpm), f"mic={mic_s.name}, accel={accel_s.name}, obd={obd_s.name}, Rpm={rpm}")
        check("7c real clip: OBD values from the previous run aren't shown as current",
              not obd_rpm_s.is_good(), f"Obd.rpm status={obd_rpm_s.name}")

        # 7d. back to a simulated run: everything good again
        scenario("healthy")
        await u.wait("Source", lambda v: v == "SIMULATED")
        await asyncio.sleep(1.5)
        accel_s, obd_rpm_s, rpm = await u.status("SensorHealth.accel"), await u.status("Obd.rpm"), await u.read("OperatingPoint.Rpm")
        check("7d back to simulated: sensors and OBD good again", accel_s.is_good() and obd_rpm_s.is_good() and 2000 < rpm < 3000,
              f"accel={accel_s.name}, Obd.rpm={obd_rpm_s.name}, Rpm={rpm:.0f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http-port", type=int, default=8000)
    ap.add_argument("--opcua-port", type=int, default=4840)
    a = ap.parse_args()
    http, endpoint = f"http://127.0.0.1:{a.http_port}", f"opc.tcp://127.0.0.1:{a.opcua_port}/720/"

    for port in (a.http_port, a.opcua_port):
        if not port_free(port):
            print(f"port {port} is in use; stop the other backend or pass --http-port / --opcua-port")
            return 2

    server = uvicorn.Server(uvicorn.Config(create_app("healthy", validate=True, opcua=endpoint),
                                           host="127.0.0.1", port=a.http_port, log_level="warning", ws="websockets"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    h = httpx.get(f"{http}/health").json()
    check("0 backend up with OPC UA", h["opcua"] == endpoint, f"/health opcua={h['opcua']}")

    try:
        asyncio.run(verify(http, endpoint))
    except Exception as e:  # noqa: BLE001
        check("verification ran to the end", False, repr(e))

    # 8. clean shutdown: lifespan stops the bridge; both ports are released and clients are refused
    server.should_exit = True
    thread.join(10)

    async def refused():
        try:
            async with Client(endpoint, timeout=2):
                return False
        except Exception:  # noqa: BLE001
            return True

    gone = asyncio.run(refused())
    check("8 clean shutdown: thread exited, ports released, OPC UA refuses clients",
          not thread.is_alive() and port_free(a.opcua_port) and port_free(a.http_port) and gone,
          f"thread alive={thread.is_alive()}, {a.opcua_port} free={port_free(a.opcua_port)}, {a.http_port} free={port_free(a.http_port)}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
