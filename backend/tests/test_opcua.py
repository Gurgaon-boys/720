"""OPC UA bridge, end to end with a real asyncua client (real time; ~40 s in total).

Covers the address space (AnalogItems, NE 107 enum), the decision mirror, Acknowledge (valid,
unknown, duplicate), Inject (refused off the simulator; live injection echoed), events on both EngineAsset and Server,
sensor FAILURE, diagnostics offline, and that the dashboard and OPC UA see the same alarm state.
"""

import asyncio
import json
import math
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from asyncua import Client, ua
from websockets.sync.client import connect

from backend.main import create_app
from backend.opcua_bridge import NE107, OpcuaBridge, endpoint_from_env, reachable_endpoints
from backend.runtime import Runtime


def free_port():
    return free_ports(1)[0]


def free_ports(n):
    """n distinct free ports: all sockets stay bound until every port is picked, so two calls can't
    hand out the same port. The OS may still give a port away before the server binds it, so servers
    started on one retry on OSError (Rig, _start_bridge)."""
    socks = [socket.socket() for _ in range(n)]
    try:
        for s in socks:
            s.bind(("127.0.0.1", 0))
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


async def _start_bridge(runtime, attempts=5):
    for k in range(attempts):
        endpoint = f"opc.tcp://127.0.0.1:{free_port()}/720/"
        bridge = OpcuaBridge(runtime, endpoint)
        try:
            await bridge.start()
            return bridge, endpoint
        except OSError:
            await bridge.stop()
            if k == attempts - 1:
                raise


class Events:
    def __init__(self):
        self.items = []

    def event_notification(self, ev):
        self.items.append({"msg": ev.Message.Text, "sev": ev.Severity, "alarm_id": ev.AlarmId,
                           "fault": ev.Fault, "sensor": ev.Sensor, "source": ev.SourceName,
                           "data_source": ev.DataSource, "time": ev.Time})

    def find(self, text):
        return [e for e in self.items if text in e["msg"]]


async def wait_for(fn, timeout=12.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await fn():
            return True
        await asyncio.sleep(step)
    raise AssertionError("condition not met in time")


async def event(events, text, timeout=5.0):
    """The events whose message contains text, once at least one has arrived (subscriptions publish
    every 50 ms, later under load: never assume a fixed delay)."""
    async def arrived():
        return bool(events.find(text))
    await wait_for(arrived, timeout)
    return events.find(text)


class Rig:
    """Runtime + bridge on a free port, plus a connected client with event subscriptions."""

    def __init__(self, scenario):
        self.runtime = Runtime(scenario)

    async def __aenter__(self):
        await self.runtime.start()
        self.bridge, self.endpoint = await _start_bridge(self.runtime)
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
            assert {"RunId", "Source", "DiagnosticTimestamp", "MaintenanceMode", "OperatingPoint", "Obd",
                    "Diagnostics", "Decision", "Injection", "AssetHealth", "SensorHealth", "Acknowledge", "Inject"} <= names
            sensors = {(await c.read_browse_name()).Name for c in await r.node("EngineAsset.SensorHealth").get_children()}
            assert sensors == {"mic", "accel", "obd", "crank", "cam", "DiagnosticStream", "LastStateAgeS"}
            stamp = await r.read("EngineAsset.DiagnosticTimestamp")
            assert abs(stamp.timestamp() - time.time()) < 5

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
        async with Rig("healthy") as r:
            # switch only once the client is connected: the switch restarts stream time, so the
            # misfire scenario's 2 s healthy lead-in is guaranteed whatever the start-up took
            await wait_for(lambda: _ok(r.read("EngineAsset.RunId"), lambda v: v == "fake_healthy"), timeout=3)
            r.runtime.select("misfire")
            await wait_for(lambda: _ok(r.read("EngineAsset.RunId"), lambda v: v == "fake_misfire"), timeout=3)
            assert await r.read("EngineAsset.Decision.State") == "NORMAL"
            assert await r.read("EngineAsset.AssetHealth") == 0
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

            for events in (r.server_events, r.engine_events):
                await event(events, "a_0001 cleared")
                raised = events.find("ALARM misfire cyl 3 (a_0001)")
                assert len(raised) == 1 and raised[0]["sev"] == 800 and raised[0]["source"] == "EngineAsset"
                assert raised[0]["fault"] == "misfire" and raised[0]["data_source"] == "SIMULATED"
                assert abs(raised[0]["time"].timestamp() - time.time()) < 15
                assert [e["sev"] for e in events.find("a_0001 acknowledged by judge")] == [200]
                assert [e["sev"] for e in events.find("a_0001 cleared")] == [100]
                assert events.find("Advisory: misfire")  # the first matching window
    run(go())


def test_inject_refused_without_simulator():
    async def go():
        async with Rig("healthy") as r:
            ok, reason = await r.call("Inject", "misfire", ua.Variant(3, ua.VariantType.Int32), 0.5)
            assert ok is False and "has no simulator" in reason
            assert await r.read("EngineAsset.Decision.State") == "NORMAL"
            assert await r.read("EngineAsset.Injection.Active") is False
    run(go())


def test_inject_live_simulator_and_new_nodes():
    async def go():
        async with Rig("sim") as r:
            ok, reason = await r.call("Inject", "bogus", ua.Variant(3, ua.VariantType.Int32), 0.5)
            assert ok is False and reason                      # the simulator's own validation
            ok, reason = await r.call("Inject", "misfire", ua.Variant(3, ua.VariantType.Int32), 1.0)
            assert ok is True and reason == ""
            await wait_for(lambda: _ok(r.read("EngineAsset.Injection.Active"), lambda v: v is True))
            assert await r.read("EngineAsset.Injection.Fault") == "misfire"
            assert await r.read("EngineAsset.Injection.Cylinder") == 3
            assert await r.read("EngineAsset.Injection.Severity") == 1.0
            # the misfire rule names cylinder 3 once the faulty windows arrive
            await wait_for(lambda: _ok(r.read("EngineAsset.Diagnostics.PhysicsRulesFired"),
                                       lambda v: any(x.startswith("misfire cyl 3") for x in (v or []))), timeout=20)
            for ch in ("lambda", "intake_air_temp_c", "battery_v", "baro_kpa"):
                await wait_for(lambda ch=ch: _ok(r.read(f"EngineAsset.Obd.{ch}"), lambda v: v == v), timeout=5)
            for sensor in ("crank", "cam"):
                assert await r.read(f"EngineAsset.SensorHealth.{sensor}") == NE107.index("NORMAL")
            assert isinstance(await r.read("EngineAsset.Decision.Guidance"), str)
            r.runtime.clear_faults()
            await wait_for(lambda: _ok(r.read("EngineAsset.Injection.Active"), lambda v: v is False))
    run(go())


def test_unclassified_injector_clog_and_sensor_failure():
    async def go():
        async with Rig("unclassified") as r:
            await wait_for(lambda: _ok(r.read("EngineAsset.Decision.State"), lambda v: v == "UNCLASSIFIED"))
            assert await r.read("EngineAsset.AssetHealth") == NE107.index("OFF_SPEC")
            assert await r.read("EngineAsset.Decision.RootCause") == "unclassified"
            u = await event(r.server_events, "Abnormal, unclassified: inspect · physics hint valve_tick cyl 2")
            assert u and u[0]["sev"] == 700

            r.runtime.select("injector_clog")
            await wait_for(lambda: _ok(r.read("EngineAsset.Decision.State"), lambda v: v == "ALARM"))
            assert await r.read("EngineAsset.AssetHealth") == NE107.index("MAINTENANCE_REQUIRED")
            assert [e["sev"] for e in await event(r.server_events, "ALARM injector_clog cyl 1")] == [600]

            r.runtime.select("mic_dropout")
            await wait_for(lambda: _ok(r.read("EngineAsset.SensorHealth.mic"), lambda v: v == NE107.index("FAILURE")))
            assert await r.read("EngineAsset.SensorHealth.accel") == 0
            assert await r.read("EngineAsset.AssetHealth") == 0  # sensor health stays separate
            fail = await event(r.server_events, "Sensor mic FAILURE")
            assert fail and fail[0]["sev"] == 500 and fail[0]["sensor"] == "mic"
            assert await event(r.server_events, "Run changed to fake_mic_dropout")
    run(go())


def test_diagnostics_offline():
    async def go():
        async with Rig("offline") as r:  # healthy 4 s, silent 5 s
            assert await r.read("EngineAsset.SensorHealth.DiagnosticStream") == 0
            await wait_for(lambda: _ok(r.read("EngineAsset.Diagnostics.DiagnosticsOnline"), lambda v: v is False))
            assert await r.read("EngineAsset.SensorHealth.DiagnosticStream") == NE107.index("FAILURE")
            assert await r.read("EngineAsset.SensorHealth.LastStateAgeS") >= 3.0   # offline is declared at 3 s
            assert await r.read("EngineAsset.AssetHealth") == 0  # stream health isn't engine health
            off = await event(r.server_events, "Diagnostics offline")
            assert off and off[0]["sev"] == 500 and off[0]["sensor"] == "DiagnosticStream"
            await wait_for(lambda: _ok(r.read("EngineAsset.Diagnostics.DiagnosticsOnline"), lambda v: v is True))
            assert await r.read("EngineAsset.SensorHealth.DiagnosticStream") == 0
            assert await event(r.server_events, "Diagnostics back online")
    run(go())


def test_dashboard_and_opcua_agree():
    """ACK from the dashboard shows up on OPC UA, and ACK from OPC UA shows up on the dashboard."""
    port, ua_port = free_ports(2)
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
                    ws.send(json.dumps({"schema_version": "1.5", "type": "ack", "alarm_id": alarm_id,
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


def test_check_function_only_in_maintenance_mode():
    async def go():
        async with Rig("healthy") as r:
            await wait_for(lambda: _ok(r.read("EngineAsset.RunId"), lambda v: v == "fake_healthy"), timeout=3)
            assert await r.read("EngineAsset.AssetHealth") == 0
            r.runtime.set_maintenance(True)
            await wait_for(lambda: _ok(r.read("EngineAsset.AssetHealth"), lambda v: v == NE107.index("CHECK_FUNCTION")), timeout=2)
            assert await r.read("EngineAsset.MaintenanceMode") is True
            assert r.runtime.last_state["health"]["asset_ne107"] == "CHECK_FUNCTION"  # the dashboard sees the same

            r.runtime.select("misfire")  # a fault's health wins over maintenance mode
            await wait_for(lambda: _ok(r.read("EngineAsset.Decision.State"), lambda v: v == "ALARM"))
            assert await r.read("EngineAsset.AssetHealth") == NE107.index("OFF_SPEC")
            assert r.runtime.last_state["health"]["asset_ne107"] == "OFF_SPEC"

            r.runtime.set_maintenance(False)
            r.runtime.select("healthy")
            await wait_for(lambda: _ok(r.read("EngineAsset.AssetHealth"), lambda v: v == 0), timeout=2)
            assert await r.read("EngineAsset.MaintenanceMode") is False
    run(go())


def test_real_clip_sensors_and_nulls():
    async def go():
        async with Rig("real_p3") as r:
            await wait_for(lambda: _ok(r.read("EngineAsset.Source"), lambda v: v == "REAL"), timeout=3)
            await wait_for(lambda: _ok(r.read("EngineAsset.OperatingPoint.Rpm"), math.isnan), timeout=3)
            assert await r.read("EngineAsset.Decision.Cylinder") == 0
            mic = await r.node("EngineAsset.SensorHealth.mic").read_data_value()
            assert mic.StatusCode.is_good() and mic.Value.Value == 0
            for absent in ("accel", "obd"):  # not on an AudioSet clip: Bad_NotConnected, not a fake NORMAL
                dv = await r.node(f"EngineAsset.SensorHealth.{absent}").read_data_value(raise_on_bad_status=False)
                assert dv.StatusCode.value == ua.StatusCodes.BadNotConnected
            r.runtime.select("healthy")  # back on a recording that has them
            await wait_for(lambda: _ok(r.read("EngineAsset.Source"), lambda v: v == "SIMULATED"), timeout=3)
            good = lambda path: _ok(r.node(path).read_data_value(raise_on_bad_status=False), lambda dv: dv.StatusCode.is_good())  # noqa: E731
            await wait_for(lambda: good("EngineAsset.SensorHealth.accel"), timeout=4)
            await wait_for(lambda: good("EngineAsset.Obd.rpm"), timeout=4)            # OBD is 1 Hz

            r.runtime.select("real_p3")  # simulated -> real: the old run's OBD values must not stay current
            await wait_for(lambda: _ok(r.read("EngineAsset.Source"), lambda v: v == "REAL"), timeout=3)
            await wait_for(lambda: _ok(r.node("EngineAsset.Obd.rpm").read_data_value(raise_on_bad_status=False),
                                       lambda dv: dv.StatusCode.value == ua.StatusCodes.BadNotConnected), timeout=4)
    run(go())


def test_start_stop_restart_and_client_reconnect():
    async def go():
        runtime = Runtime("healthy")
        await runtime.start()
        try:
            bridge, endpoint = await _start_bridge(runtime)
            assert bridge.running
            client = Client(endpoint)
            await client.connect()
            idx = await client.get_namespace_index("urn:720:hackhive")
            state = client.get_node(ua.NodeId("EngineAsset.Decision.State", idx))
            await wait_for(lambda: _ok(state.read_value(), lambda v: v == "NORMAL"), timeout=3)

            await client.disconnect()  # client reconnects to a running server
            await client.connect()
            assert await state.read_value() == "NORMAL"

            await bridge.stop()  # clean shutdown with a client still attached
            assert not bridge.running
            with pytest.raises(Exception):
                await asyncio.wait_for(state.read_value(), 3)
            try:
                await client.disconnect()
            except Exception:
                pass

            bridge2 = OpcuaBridge(runtime, endpoint)  # the same port is free again
            await bridge2.start()
            async with Client(endpoint) as c2:
                idx2 = await c2.get_namespace_index("urn:720:hackhive")
                await wait_for(lambda: _ok(c2.get_node(ua.NodeId("EngineAsset.RunId", idx2)).read_value(),
                                           lambda v: v == "fake_healthy"), timeout=3)
            await bridge2.stop()
        finally:
            await runtime.stop()
    run(go())


def test_opcua_host_from_environment_and_reachable_endpoints():
    assert endpoint_from_env({}) == "opc.tcp://127.0.0.1:4840/720/"
    assert endpoint_from_env({"S720_OPCUA_HOST": "0.0.0.0"}) == "opc.tcp://0.0.0.0:4840/720/"
    assert endpoint_from_env({"S720_OPCUA_HOST": "0.0.0.0", "S720_OPCUA_ENDPOINT": "opc.tcp://10.0.0.5:4841/x/"}) ==         "opc.tcp://10.0.0.5:4841/x/"
    assert reachable_endpoints("opc.tcp://127.0.0.1:4840/720/") == ["opc.tcp://127.0.0.1:4840/720/"]
    lan = reachable_endpoints("opc.tcp://0.0.0.0:4840/720/")
    assert lan[-1] == "opc.tcp://127.0.0.1:4840/720/" and all("0.0.0.0" not in u for u in lan)
    assert all(u.startswith("opc.tcp://") and u.endswith(":4840/720/") for u in lan)
