"""OPC UA bridge, end to end with a real asyncua client (real time; ~40 s in total).

Covers the address space (AnalogItems, NE 107 enum), the decision mirror, Acknowledge (valid,
unknown, duplicate), Inject (not supported until L1), events on both EngineAsset and Server,
sensor FAILURE, diagnostics offline, and that the dashboard and OPC UA see the same alarm state.
"""

import asyncio
import json
import math
import socket
import threading
import time

import httpx
import uvicorn
from asyncua import Client, ua
from websockets.sync.client import connect

from backend.main import create_app
from backend.opcua_bridge import NE107, NOT_SUPPORTED, OpcuaBridge
from backend.runtime import Runtime


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Events:
    def __init__(self):
        self.items = []

    def event_notification(self, ev):
        self.items.append({"msg": ev.Message.Text, "sev": ev.Severity, "alarm_id": ev.AlarmId,
                           "fault": ev.Fault, "sensor": ev.Sensor, "source": ev.SourceName})

    def find(self, text):
        return [e for e in self.items if text in e["msg"]]


async def wait_for(fn, timeout=12.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await fn():
            return True
        await asyncio.sleep(step)
    raise AssertionError("condition not met in time")


class Rig:
    """Runtime + bridge on a free port, plus a connected client with event subscriptions."""

    def __init__(self, scenario):
        self.endpoint = f"opc.tcp://127.0.0.1:{free_port()}/720/"
        self.runtime = Runtime(scenario)
        self.bridge = OpcuaBridge(self.runtime, self.endpoint)

    async def __aenter__(self):
        await self.runtime.start()
        await self.bridge.start()
        self.client = Client(self.endpoint)
        await self.client.connect()
        self.idx = await self.client.get_namespace_index("urn:720:hackhive")
        etype = self.client.get_node(self.bridge.event_type.nodeid)
        self.server_events, self.engine_events = Events(), Events()
        self.subs = [await self.client.create_subscription(50, self.server_events),
                     await self.client.create_subscription(50, self.engine_events)]
        await self.subs[0].subscribe_events(self.client.nodes.server, etype)
        await self.subs[1].subscribe_events(self.node("EngineAsset"), etype)
        await asyncio.sleep(0.3)
        return self

    async def __aexit__(self, *exc):
        await self.client.disconnect()
        await self.bridge.stop()
        await self.runtime.stop()

    def node(self, path):
        return self.client.get_node(ua.NodeId(path, self.idx))

    async def read(self, path):
        return await self.node(path).read_value()

    async def call(self, method, *args):
        return await self.node("EngineAsset").call_method(ua.NodeId(f"EngineAsset.{method}", self.idx), *args)


def run(coro):
    asyncio.run(coro)


def test_address_space():
    async def go():
        async with Rig("healthy") as r:
            names = {(await c.read_browse_name()).Name for c in await r.node("EngineAsset").get_children()}
            assert {"RunId", "Source", "OperatingPoint", "Obd", "Diagnostics", "Decision", "AssetHealth",
                    "SensorHealth", "Acknowledge", "Inject"} <= names

            rpm = r.node("EngineAsset.OperatingPoint.Rpm")
            assert (await rpm.read_type_definition()).Identifier == ua.ObjectIds.AnalogItemType
            eu_range = await (await rpm.get_child(["EURange"])).read_value()
            units = await (await rpm.get_child(["0:EngineeringUnits"])).read_value()
            assert (eu_range.Low, eu_range.High) == (0, 8000) and units.DisplayName.Text == "rev/min"
            await wait_for(lambda: _ok(r.read("EngineAsset.OperatingPoint.Rpm"), lambda v: 2300 < v < 2500))

            health = r.node("EngineAsset.AssetHealth")
            enum = r.client.get_node(await health.read_data_type())
            fields = await (await enum.get_child(["0:EnumValues"])).read_value()
            assert [(f.Value, f.DisplayName.Text) for f in fields] == list(enumerate(NE107))  # DI values
            definition = await enum.read_data_type_definition()
            assert [(f.Value, f.Name) for f in definition.Fields] == list(enumerate(NE107))
            assert NE107 == ["NORMAL", "FAILURE", "CHECK_FUNCTION", "OFF_SPEC", "MAINTENANCE_REQUIRED"]
            assert await health.read_value() == 0

            assert await r.read("EngineAsset.Source") == "SIMULATED"
            assert await r.read("EngineAsset.Diagnostics.DiagnosticsOnline") is True
            await wait_for(lambda: _ok(r.read("EngineAsset.Obd.coolant_c"), lambda v: not math.isnan(v)), timeout=3)
            assert await (await r.node("EngineAsset.Obd.coolant_c").get_child([f"{r.idx}:Source"])).read_value() == "simulator"
    run(go())


async def _ok(coro, pred):
    return pred(await coro)


def test_misfire_alarm_ack_and_clear():
    async def go():
        async with Rig("misfire") as r:
            await wait_for(lambda: _ok(r.read("EngineAsset.Decision.State"), lambda v: v == "ALARM"))
            assert await r.read("EngineAsset.Decision.AlarmId") == "a_0001"
            assert await r.read("EngineAsset.Decision.Cylinder") == 3
            assert await r.read("EngineAsset.Decision.RootCause") == "misfire"
            assert await r.read("EngineAsset.AssetHealth") == NE107.index("OFF_SPEC")
            assert await r.read("EngineAsset.Decision.Witnesses") == ["physics", "classifier"]

            # unknown id: rejected, nothing changes
            ok, reason = await r.call("Acknowledge", "a_9999", "judge")
            assert ok is False and reason.startswith("unknown_alarm_id")
            assert await r.read("EngineAsset.Decision.Acked") is False

            ok, reason = await r.call("Acknowledge", "a_0001", "judge")
            assert ok is True and reason == ""
            await wait_for(lambda: _ok(r.read("EngineAsset.Decision.Acked"), lambda v: v is True), timeout=2)
            assert await r.read("EngineAsset.Decision.State") == "ALARM"  # ACK never clears an active fault
            assert await r.read("EngineAsset.Decision.AckedBy") == "judge"

            ok, reason = await r.call("Acknowledge", "a_0001", "someone_else")
            assert ok is False and reason.startswith("duplicate_ack")
            ok, reason = await r.call("Acknowledge", "", "judge")
            assert ok is False and reason.startswith("invalid_message")

            # fault ends at 8 s; reset after 2 healthy windows
            await wait_for(lambda: _ok(r.read("EngineAsset.Decision.State"), lambda v: v == "NORMAL"))
            assert await r.read("EngineAsset.Decision.AlarmId") == ""
            assert await r.read("EngineAsset.AssetHealth") == 0
            await asyncio.sleep(0.3)

            for events in (r.server_events, r.engine_events):
                raised = events.find("ALARM misfire cyl 3 (a_0001)")
                assert len(raised) == 1 and raised[0]["sev"] == 800 and raised[0]["source"] == "EngineAsset"
                assert [e["sev"] for e in events.find("a_0001 acknowledged by judge")] == [200]
                assert [e["sev"] for e in events.find("a_0001 cleared")] == [100]
                assert events.find("Advisory: misfire")  # the first matching window
    run(go())


def test_inject_not_supported_until_l1():
    async def go():
        async with Rig("healthy") as r:
            ok, reason = await r.call("Inject", "misfire", ua.Variant(3, ua.VariantType.Int32), 0.5)
            assert ok is False and reason == NOT_SUPPORTED == "not supported until L1"
            assert await r.read("EngineAsset.Decision.State") == "NORMAL"
    run(go())


def test_unclassified_injector_clog_and_sensor_failure():
    async def go():
        async with Rig("unclassified") as r:
            await wait_for(lambda: _ok(r.read("EngineAsset.Decision.State"), lambda v: v == "UNCLASSIFIED"))
            assert await r.read("EngineAsset.AssetHealth") == NE107.index("OFF_SPEC")
            assert await r.read("EngineAsset.Decision.RootCause") == "unclassified"
            await asyncio.sleep(0.2)
            u = r.server_events.find("Abnormal, unclassified: inspect · physics hint valve_tick cyl 2")
            assert u and u[0]["sev"] == 700

            r.runtime.select("injector_clog")
            await wait_for(lambda: _ok(r.read("EngineAsset.Decision.State"), lambda v: v == "ALARM"))
            assert await r.read("EngineAsset.AssetHealth") == NE107.index("MAINTENANCE_REQUIRED")
            await asyncio.sleep(0.2)
            assert [e["sev"] for e in r.server_events.find("ALARM injector_clog cyl 1")] == [600]

            r.runtime.select("mic_dropout")
            await wait_for(lambda: _ok(r.read("EngineAsset.SensorHealth.mic"), lambda v: v == NE107.index("FAILURE")))
            assert await r.read("EngineAsset.SensorHealth.accel") == 0
            assert await r.read("EngineAsset.AssetHealth") == 0  # sensor health stays separate
            await asyncio.sleep(0.2)
            fail = r.server_events.find("Sensor mic FAILURE")
            assert fail and fail[0]["sev"] == 500 and fail[0]["sensor"] == "mic"
            assert r.server_events.find("Run changed to fake_mic_dropout")
    run(go())


def test_diagnostics_offline():
    async def go():
        async with Rig("offline") as r:  # healthy 4 s, silent 5 s
            await wait_for(lambda: _ok(r.read("EngineAsset.Diagnostics.DiagnosticsOnline"), lambda v: v is False))
            await asyncio.sleep(0.2)
            off = r.server_events.find("Diagnostics offline")
            assert off and off[0]["sev"] == 500
            await wait_for(lambda: _ok(r.read("EngineAsset.Diagnostics.DiagnosticsOnline"), lambda v: v is True))
            await asyncio.sleep(0.2)
            assert r.server_events.find("Diagnostics back online")
    run(go())


def test_dashboard_and_opcua_agree():
    """ACK from the dashboard shows up on OPC UA, and ACK from OPC UA shows up on the dashboard."""
    port, ua_port = free_port(), free_port()
    endpoint = f"opc.tcp://127.0.0.1:{ua_port}/720/"
    srv = uvicorn.Server(uvicorn.Config(create_app("misfire", opcua=endpoint), host="127.0.0.1", port=port,
                                        log_level="warning", ws="websockets"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    try:
        for _ in range(100):
            if srv.started:
                break
            time.sleep(0.05)

        async def go():
            async with Client(endpoint) as c:
                idx = await c.get_namespace_index("urn:720:hackhive")
                node = lambda p: c.get_node(ua.NodeId(p, idx))  # noqa: E731
                await wait_for(lambda: _ok(node("EngineAsset.Decision.State").read_value(), lambda v: v == "ALARM"))
                alarm_id = await node("EngineAsset.Decision.AlarmId").read_value()

                with connect(f"ws://127.0.0.1:{port}/ws/state") as ws:  # dashboard ACK
                    ws.send(json.dumps({"schema_version": "1.3", "type": "ack", "alarm_id": alarm_id,
                                        "operator": "dashboard", "sent_at": time.time()}))
                    await wait_for(lambda: _ok(node("EngineAsset.Decision.AckedBy").read_value(), lambda v: v == "dashboard"), timeout=3)
                return alarm_id

        asyncio.run(go())

        # a fresh alarm, acknowledged over OPC UA, must reach the dashboard broadcast
        httpx.post(f"http://127.0.0.1:{port}/scenario/knock")

        async def go2():
            async with Client(endpoint) as c:
                idx = await c.get_namespace_index("urn:720:hackhive")
                node = lambda p: c.get_node(ua.NodeId(p, idx))  # noqa: E731
                # wait for the knock run's own latched alarm (Fault is already "knock" on the first,
                # ADVISORY, window, and the previous run's alarm may still be showing)
                async def knock_alarm():
                    return (await node("EngineAsset.Decision.State").read_value() == "ALARM"
                            and await node("EngineAsset.Decision.Fault").read_value() == "knock")
                await wait_for(knock_alarm)
                alarm_id = await node("EngineAsset.Decision.AlarmId").read_value()
                with connect(f"ws://127.0.0.1:{port}/ws/state") as ws:
                    ok, _ = await node("EngineAsset").call_method(ua.NodeId("EngineAsset.Acknowledge", idx), alarm_id, "uaexpert")
                    assert ok
                    end = time.monotonic() + 3
                    while time.monotonic() < end:
                        m = json.loads(ws.recv(timeout=1))
                        if m.get("decision", {}).get("acked_by") == "uaexpert":
                            assert m["decision"]["alarm_id"] == alarm_id and m["decision"]["state"] == "ALARM"
                            return
                    raise AssertionError("dashboard never saw the OPC UA ack")

        asyncio.run(go2())
        assert httpx.get(f"http://127.0.0.1:{port}/health").json()["opcua"] == endpoint
    finally:
        srv.should_exit = True
        th.join(5)
