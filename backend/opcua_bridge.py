"""OPC UA bridge (plan §11.2): publishes the rule layer's broadcast on an asyncua server.

It reads exactly what the WebSocket clients get (Runtime listener) and never decides anything,
so OPC UA and the dashboard can't disagree (DECISIONS.md D16). ACKs through the Acknowledge method
go into the same Runtime.ack() as dashboard ACKs.

Address space (namespace urn:720:hackhive), under Objects/EngineAsset:
  RunId, Source, Mode, StreamTimeS, SchemaVersion, DiagnosticTimestamp, MaintenanceMode
  OperatingPoint/  Rpm, LoadPct (AnalogItems with EURange + EngineeringUnits), Regime
  Obd/             one AnalogItem per OBD channel, each with Source and Calibration properties
  Diagnostics/     AnomalyScore, AnomalyThreshold, CylinderAnomaly[], LatencyMs, DiagnosticsOnline,
                   ClassifierFault, ClassifierP, ClassifierCylinder, Models[], PhysicsRulesFired[]
  Decision/        State, Fault, Cylinder, Severity, AlarmId, Latched, Acked, AckedBy, AckedAt,
                   Witnesses[], ConsecutiveWindows, RootCause, Guidance
  Injection/       Active, Fault, Cylinder, Severity, StartedAt (echo of state.injection)
  AssetHealth      NE 107 enum (asset layer) + AssetHealthText
  SensorHealth/    one NE 107 enum per sensor (sensor layer, kept separate); a sensor the current
                   recording doesn't have reads Bad_NotConnected. DiagnosticStream (NE 107: FAILURE
                   after 3 s with no state) and LastStateAgeS give the stream's freshness
  Acknowledge(AlarmId, Operator) -> (Accepted, Reason)
  Inject(Fault, Cylinder, Severity) -> (Accepted, Reason)   live simulator only, same path as POST /inject
  DisableSensor(Channel) / EnableSensor(Channel) -> (Accepted, Reason)   live simulator only, same path as
                   POST /sensor/{channel}/off|on (D137); the sensor's state is confirmed by SensorHealth/*

NE 107 values match the OPC UA DI DeviceHealthEnumeration: NORMAL 0, FAILURE 1, CHECK_FUNCTION 2,
OFF_SPEC 3, MAINTENANCE_REQUIRED 4. It's a custom enum with those values; the DI nodeset isn't loaded.

asyncua has no server-side Alarms & Conditions, so faults are EngineFaultEventType events with a
severity, fired on transitions only. Each event is emitted from both EngineAsset and the Server
object (SourceNode = EngineAsset either way), so a client subscribed to either one sees it.

Null contract values: Cylinder 0 = not localised, Severity NaN = none, empty strings for no alarm,
AckedAt = OPC UA null DateTime (1601-01-01).

CHECK_FUNCTION comes from the backend's explicit maintenance/test mode (Runtime.set_maintenance) via
the same health mapping the dashboard sees; it never overrides a fault's health.

"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from asyncua import Server, ua
from asyncua.common.instantiate_util import instantiate
from asyncua.common.methods import uamethod
from asyncua.common.structures104 import new_enum

from backend.messages import SCHEMA_VERSION
from backend.runtime import SensorControlError

log = logging.getLogger("backend.opcua")
logging.getLogger("asyncua").setLevel(logging.WARNING)
# instantiating AnalogItemType logs a warning per type attribute it can't copy; harmless
logging.getLogger("asyncua.common.copy_node_util").setLevel(logging.ERROR)

NS_URI = "urn:720:hackhive"
DEFAULT_ENDPOINT = "opc.tcp://127.0.0.1:4840/720/"
DEFAULT_PORT, ENDPOINT_PATH = 4840, "/720/"


def endpoint_from_env(env=None) -> str:
    """S720_OPCUA_ENDPOINT wins; else opc.tcp://<S720_OPCUA_HOST>:4840/720/ (host default 127.0.0.1, local only;
    0.0.0.0 listens on every interface so a LAN client such as UaExpert on another laptop can connect)."""
    env = os.environ if env is None else env
    if env.get("S720_OPCUA_ENDPOINT"):
        return env["S720_OPCUA_ENDPOINT"]
    return f"opc.tcp://{env.get('S720_OPCUA_HOST', '127.0.0.1')}:{DEFAULT_PORT}{ENDPOINT_PATH}"


def reachable_endpoints(endpoint: str) -> list[str]:
    """The URLs a client can actually dial: for 0.0.0.0, this machine's IPv4 addresses (LAN first) plus 127.0.0.1."""
    u = urlsplit(endpoint)
    if u.hostname not in ("0.0.0.0", ""):
        return [endpoint]
    ips = set()
    try:
        ips |= {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        pass
    try:                                         # the address of the default route (no packet is sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))
            ips.add(sk.getsockname()[0])
    except OSError:
        pass
    lan = sorted(ip for ip in ips if not ip.startswith("127."))
    return [f"opc.tcp://{ip}:{u.port}{u.path}" for ip in lan + ["127.0.0.1"]]
OFFLINE_S = 3.0  # same threshold as the UI watchdog
NE107 = ["NORMAL", "FAILURE", "CHECK_FUNCTION", "OFF_SPEC", "MAINTENANCE_REQUIRED"]  # DI values 0..4
NULL_DATETIME = datetime(1601, 1, 1, tzinfo=timezone.utc)

# OPC UA event severity (1-1000)
SEV_ALARM_ACUTE = 800  # misfire / knock alarm (OFF_SPEC)
SEV_UNCLASSIFIED = 700
SEV_ALARM_MAINT = 600  # injector clog alarm (MAINTENANCE_REQUIRED)
SEV_SENSOR = 500  # sensor FAILURE, diagnostics offline
SEV_ADVISORY = 300
SEV_ACK = 200
SEV_INFO = 100  # cleared, back to normal, recovered, run changed

# OBD channel -> (EURange low, high, UNECE code, display unit)
OBD_SPEC = {
    "rpm": (0, 8000, "RPM", "rev/min"),
    "load_pct": (0, 100, "P1", "%"),
    "throttle_pct": (0, 100, "P1", "%"),
    "map_kpa": (0, 255, "KPA", "kPa"),
    "coolant_c": (-40, 215, "CEL", "°C"),
    "stft_pct": (-100, 99.2, "P1", "%"),
    "ltft_pct": (-100, 99.2, "P1", "%"),
    "spark_adv_deg": (-64, 63.5, "DD", "°"),
    "o2_v": (0, 1.275, "VLT", "V"),
    "lambda": (0, 2, "C62", "1"),
    "intake_air_temp_c": (-40, 215, "CEL", "°C"),
    "battery_v": (0, 20, "VLT", "V"),
    "baro_kpa": (0, 255, "KPA", "kPa"),
}
SENSORS = ("mic", "accel", "obd", "crank", "cam")  # pre-created; any other sensor a source reports is added on the fly

EVENT_FIELDS = [
    ("AlarmId", ua.VariantType.String),
    ("Fault", ua.VariantType.String),
    ("Cylinder", ua.VariantType.Int32),
    ("DecisionState", ua.VariantType.String),
    ("AssetHealth", ua.VariantType.String),
    ("Sensor", ua.VariantType.String),
    ("Acked", ua.VariantType.Boolean),
    ("DataSource", ua.VariantType.String),  # REAL / SIMULATED / INJECTED
]


def eu_information(code: str | None, display: str) -> ua.EUInformation:
    """EngineeringUnits per OPC UA Part 8: UnitId is the UNECE common code packed into an Int32."""
    unit_id = -1
    if code:
        unit_id = 0
        for ch in code.upper():
            unit_id = (unit_id << 8) | ord(ch)
    return ua.EUInformation(NamespaceUri="http://www.opcfoundation.org/UA/units/un/cefact", UnitId=unit_id,
                            DisplayName=ua.LocalizedText(display), Description=ua.LocalizedText(display))


def _dt(unix_s: float | None) -> datetime:
    return NULL_DATETIME if unix_s is None else datetime.fromtimestamp(unix_s, tz=timezone.utc)


def _arg(name: str, type_id: int) -> ua.Argument:
    return ua.Argument(Name=name, DataType=ua.NodeId(type_id), ValueRank=-1, Description=ua.LocalizedText(name))


class OpcuaBridge:
    def __init__(self, runtime, endpoint: str = DEFAULT_ENDPOINT) -> None:
        self.runtime = runtime
        self.endpoint = endpoint
        self.server: Server | None = None
        self.running = False
        self._queue: asyncio.Queue = asyncio.Queue(200)
        self._vars: dict[str, list] = {}  # key -> [node, VariantType, last value]
        self._prev: dict | None = None
        self._last_state_mono: float | None = None
        self._online = True
        self._tasks: list[asyncio.Task] = []
        # for the Sabotage Lab's Industrial Link panel (D138): facts about what this server actually did
        self.last_publish: float | None = None     # backend clock of the last state written to the address space
        self.last_event: dict | None = None        # the last OPC UA event emitted
        self.events_emitted = 0
        runtime.add_listener(self._on_message)

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        s = Server()
        await s.init()
        s.set_endpoint(self.endpoint)
        s.set_server_name("720 condition monitor")
        await s.set_application_uri(NS_URI)
        # demo on a local laptop; production would use Basic256Sha256 Sign&Encrypt + user auth
        s.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self.server = s
        self.idx = await s.register_namespace(NS_URI)
        await self._build()
        await s.start()
        self.running = True
        self._tasks = [asyncio.create_task(self._consume()), asyncio.create_task(self._watchdog())]
        log.info("OPC UA server on %s", self.endpoint)
        for url in reachable_endpoints(self.endpoint):
            print(f"OPC UA reachable at {url}", flush=True)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.server is not None and self.running:
            await self.server.stop()
        self.running = False

    # ------------------------------------------------------------ address space

    def _nid(self, path: str) -> ua.NodeId:
        return ua.NodeId(path, self.idx)

    def _qn(self, name: str) -> ua.QualifiedName:
        return ua.QualifiedName(name, self.idx)

    async def _var(self, parent, path: str, key: str, value, vtype: ua.VariantType, datatype=None):
        name = path.rsplit(".", 1)[-1]
        node = await parent.add_variable(self._nid(path), self._qn(name), ua.Variant(value, vtype), datatype=datatype)
        self._vars[key] = [node, vtype, value, True]
        return node

    async def _analog(self, parent, path: str, key: str, lo: float, hi: float, code: str | None, unit: str, value=0.0):
        name = path.rsplit(".", 1)[-1]
        nodes = await instantiate(parent, self.server.get_node(ua.ObjectIds.AnalogItemType), nodeid=self._nid(path),
                                  bname=self._qn(name), dname=ua.LocalizedText(name), instantiate_optional=False)
        node = nodes[0]
        await node.write_attribute(ua.AttributeIds.DataType,
                                   ua.DataValue(ua.Variant(ua.NodeId(ua.ObjectIds.Double), ua.VariantType.NodeId)))
        await node.write_value(ua.Variant(float(value), ua.VariantType.Double))
        await (await node.get_child(["EURange"])).write_value(ua.Range(Low=float(lo), High=float(hi)))
        await node.add_property(self._nid(f"{path}.EngineeringUnits"), ua.QualifiedName("EngineeringUnits", 0),
                                eu_information(code, unit), datatype=ua.ObjectIds.EUInformation)
        self._vars[key] = [node, ua.VariantType.Double, float(value), True]
        return node

    async def _build(self) -> None:
        s, V = self.server, ua.VariantType
        self.ne107 = await new_enum(s, self.idx, "NE107DeviceHealth", NE107)
        # new_enum only writes the DataTypeDefinition; EnumValues lets older clients show the names too
        await self.ne107.add_property(self._nid("NE107DeviceHealth.EnumValues"), ua.QualifiedName("EnumValues", 0),
                                      [ua.EnumValueType(Value=i, DisplayName=ua.LocalizedText(n), Description=ua.LocalizedText(n))
                                       for i, n in enumerate(NE107)], datatype=ua.ObjectIds.EnumValueType)
        await s.load_data_type_definitions()

        eng = await s.nodes.objects.add_object(self._nid("EngineAsset"), self._qn("EngineAsset"))
        self.engine = eng
        await eng.set_event_notifier([ua.EventNotifier.SubscribeToEvents])
        await s.nodes.server.add_reference(eng.nodeid, ua.ObjectIds.HasNotifier)

        await self._var(eng, "EngineAsset.RunId", "run_id", "", V.String)
        await self._var(eng, "EngineAsset.Source", "source", "", V.String)
        await self._var(eng, "EngineAsset.Mode", "mode", "", V.String)
        await self._var(eng, "EngineAsset.StreamTimeS", "t", 0.0, V.Double)
        await self._var(eng, "EngineAsset.SchemaVersion", "schema_version", SCHEMA_VERSION, V.String)
        await self._var(eng, "EngineAsset.DiagnosticTimestamp", "sent_at", NULL_DATETIME, V.DateTime)
        await self._var(eng, "EngineAsset.MaintenanceMode", "maintenance", False, V.Boolean)

        op = await eng.add_object(self._nid("EngineAsset.OperatingPoint"), self._qn("OperatingPoint"))
        await self._analog(op, "EngineAsset.OperatingPoint.Rpm", "rpm", 0, 8000, "RPM", "rev/min", math.nan)
        await self._analog(op, "EngineAsset.OperatingPoint.LoadPct", "load_pct", 0, 100, "P1", "%", math.nan)
        await self._var(op, "EngineAsset.OperatingPoint.Regime", "regime", "", V.String)

        self.obd = await eng.add_object(self._nid("EngineAsset.Obd"), self._qn("Obd"))
        for name, (lo, hi, code, unit) in OBD_SPEC.items():
            await self._obd_channel(name, lo, hi, code, unit)

        dg = await eng.add_object(self._nid("EngineAsset.Diagnostics"), self._qn("Diagnostics"))
        await self._analog(dg, "EngineAsset.Diagnostics.AnomalyScore", "anomaly_score", 0, 2, "C62", "1")
        await self._analog(dg, "EngineAsset.Diagnostics.AnomalyThreshold", "anomaly_threshold", 0, 2, "C62", "1")
        await self._var(dg, "EngineAsset.Diagnostics.CylinderAnomaly", "per_location", [], V.Double)
        await self._analog(dg, "EngineAsset.Diagnostics.LatencyMs", "latency_ms", 0, 1000, "C26", "ms")
        await self._var(dg, "EngineAsset.Diagnostics.DiagnosticsOnline", "online", False, V.Boolean)
        # the two witnesses, as the rule layer saw them this window
        await self._var(dg, "EngineAsset.Diagnostics.ClassifierFault", "clf_fault", "", V.String)
        await self._analog(dg, "EngineAsset.Diagnostics.ClassifierP", "clf_p", 0, 1, "C62", "1", math.nan)
        await self._var(dg, "EngineAsset.Diagnostics.ClassifierCylinder", "clf_cyl", 0, V.Int32)
        await self._var(dg, "EngineAsset.Diagnostics.Models", "models", [], V.String)
        await self._var(dg, "EngineAsset.Diagnostics.PhysicsRulesFired", "rules_fired", [], V.String)

        de = await eng.add_object(self._nid("EngineAsset.Decision"), self._qn("Decision"))
        await self._var(de, "EngineAsset.Decision.State", "state", "NORMAL", V.String)
        await self._var(de, "EngineAsset.Decision.Fault", "fault", "", V.String)
        await self._var(de, "EngineAsset.Decision.Cylinder", "location", 0, V.Int32)
        await self._analog(de, "EngineAsset.Decision.Severity", "severity", 0, 1, "C62", "0-1", math.nan)
        await self._var(de, "EngineAsset.Decision.AlarmId", "alarm_id", "", V.String)
        await self._var(de, "EngineAsset.Decision.Latched", "latched", False, V.Boolean)
        await self._var(de, "EngineAsset.Decision.Acked", "acked", False, V.Boolean)
        await self._var(de, "EngineAsset.Decision.AckedBy", "acked_by", "", V.String)
        await self._var(de, "EngineAsset.Decision.AckedAt", "acked_at", NULL_DATETIME, V.DateTime)
        await self._var(de, "EngineAsset.Decision.Witnesses", "witnesses", [], V.String)
        await self._var(de, "EngineAsset.Decision.ConsecutiveWindows", "consecutive_windows", 0, V.UInt32)
        await self._var(de, "EngineAsset.Decision.RootCause", "root_cause", "", V.String)
        await self._var(de, "EngineAsset.Decision.Guidance", "guidance", "", V.String)

        ij = await eng.add_object(self._nid("EngineAsset.Injection"), self._qn("Injection"))
        await self._var(ij, "EngineAsset.Injection.Active", "inj_active", False, V.Boolean)
        await self._var(ij, "EngineAsset.Injection.Fault", "inj_fault", "", V.String)
        await self._var(ij, "EngineAsset.Injection.Cylinder", "inj_cyl", 0, V.Int32)
        await self._analog(ij, "EngineAsset.Injection.Severity", "inj_sev", 0, 1, "C62", "0-1", math.nan)
        await self._var(ij, "EngineAsset.Injection.StartedAt", "inj_started", NULL_DATETIME, V.DateTime)

        await self._var(eng, "EngineAsset.AssetHealth", "asset_ne107", 0, V.Int32, datatype=self.ne107.nodeid)
        await self._var(eng, "EngineAsset.AssetHealthText", "asset_ne107_text", "NORMAL", V.String)
        self.sensors = await eng.add_object(self._nid("EngineAsset.SensorHealth"), self._qn("SensorHealth"))
        for sensor in SENSORS:
            await self._sensor(sensor)
        await self._var(self.sensors, "EngineAsset.SensorHealth.DiagnosticStream", "stream", 0, V.Int32,
                        datatype=self.ne107.nodeid)
        await self._var(self.sensors, "EngineAsset.SensorHealth.LastStateAgeS", "state_age_s", math.nan, V.Double)

        # uamethod wraps plain functions (parent, *args), so the methods are closures over self
        @uamethod
        async def acknowledge(parent, alarm_id, operator):
            return self._acknowledge(alarm_id, operator)

        @uamethod
        async def inject(parent, fault, cylinder, severity):
            return self._inject(fault, cylinder, severity)

        await eng.add_method(self._nid("EngineAsset.Acknowledge"), self._qn("Acknowledge"), acknowledge,
                             [_arg("AlarmId", ua.ObjectIds.String), _arg("Operator", ua.ObjectIds.String)],
                             [_arg("Accepted", ua.ObjectIds.Boolean), _arg("Reason", ua.ObjectIds.String)])
        await eng.add_method(self._nid("EngineAsset.Inject"), self._qn("Inject"), inject,
                             [_arg("Fault", ua.ObjectIds.String), _arg("Cylinder", ua.ObjectIds.Int32),
                              _arg("Severity", ua.ObjectIds.Double)],
                             [_arg("Accepted", ua.ObjectIds.Boolean), _arg("Reason", ua.ObjectIds.String)])

        @uamethod
        async def disable_sensor(parent, channel):
            return self._sensor_command(self.runtime.disable_sensor, channel, "DisableSensor")

        @uamethod
        async def enable_sensor(parent, channel):
            return self._sensor_command(self.runtime.enable_sensor, channel, "EnableSensor")

        for name, fn in (("DisableSensor", disable_sensor), ("EnableSensor", enable_sensor)):
            await eng.add_method(self._nid(f"EngineAsset.{name}"), self._qn(name), fn,
                                 [_arg("Channel", ua.ObjectIds.String)],
                                 [_arg("Accepted", ua.ObjectIds.Boolean), _arg("Reason", ua.ObjectIds.String)])

        etype = await s.create_custom_event_type(self.idx, "EngineFaultEventType", ua.ObjectIds.BaseEventType, EVENT_FIELDS)
        self.event_type = etype
        self._gens = [await s.get_event_generator(etype, eng), await s.get_event_generator(etype, ua.ObjectIds.Server)]

    async def _obd_channel(self, name, lo, hi, code, unit):
        node = await self._analog(self.obd, f"EngineAsset.Obd.{name}", f"obd.{name}", lo, hi, code, unit, math.nan)
        src = await node.add_property(self._nid(f"EngineAsset.Obd.{name}.Source"), self._qn("Source"), "")
        self._vars[f"obd.{name}.source"] = [src, ua.VariantType.String, "", True]
        cal = await node.add_property(self._nid(f"EngineAsset.Obd.{name}.Calibration"), self._qn("Calibration"), "")
        self._vars[f"obd.{name}.calibration"] = [cal, ua.VariantType.String, "", True]

    async def _sensor(self, sensor):
        await self._var(self.sensors, f"EngineAsset.SensorHealth.{sensor}", f"sensor.{sensor}", 0, ua.VariantType.Int32,
                        datatype=self.ne107.nodeid)

    # ------------------------------------------------------------ updates

    def _on_message(self, kind: str, msg: dict) -> None:
        if kind not in ("state", "obd"):
            return
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait((kind, msg))

    async def _consume(self) -> None:
        while True:
            kind, msg = await self._queue.get()
            try:
                if kind == "state":
                    await self._apply_state(msg)
                else:
                    await self._apply_obd(msg)
            except Exception:
                log.exception("OPC UA update failed")

    async def _set(self, key: str, value) -> None:
        entry = self._vars[key]
        same = entry[2] == value or (isinstance(value, float) and isinstance(entry[2], float)
                                     and math.isnan(value) and math.isnan(entry[2]))
        if same and entry[3]:
            return
        await entry[0].write_value(ua.Variant(value, entry[1]))
        entry[2], entry[3] = value, True

    async def _not_connected(self, key: str) -> None:
        """Keep the node but mark it Bad_NotConnected (e.g. no accelerometer on a real clip)."""
        entry = self._vars[key]
        if not entry[3]:
            return
        await entry[0].write_value(ua.DataValue(ua.Variant(entry[2], entry[1]),
                                                StatusCode=ua.StatusCode(ua.StatusCodes.BadNotConnected)))
        entry[3] = False

    async def _apply_state(self, m: dict) -> None:
        self.last_publish = self.runtime.clock()
        self._last_state_mono = time.monotonic()
        d, h, op = m["decision"], m["health"], m["operating_point"]
        if self._prev is not None and self._prev["run_id"] != m["run_id"]:
            # a new run: OBD values from the old one stay visible but not as current data, until
            # this run sends its own (a real AudioSet clip has no OBD at all)
            for key in [k for k in self._vars if k.startswith("obd.") and not k.endswith((".source", ".calibration"))]:
                await self._not_connected(key)
        num = lambda x: math.nan if x is None else float(x)  # noqa: E731

        await self._set("run_id", m["run_id"])
        await self._set("source", m["source"].upper())
        await self._set("mode", m["mode"])
        await self._set("t", float(m["t"]))
        await self._set("rpm", num(op["rpm"]))
        await self._set("load_pct", num(op["load_pct"]))
        await self._set("regime", op["regime"])
        score = m["anomaly"]["score"]
        await self._set("anomaly_score", math.nan if score is None else float(score))  # 1.5 null -> NaN (D40)
        await self._set("anomaly_threshold", float(m["anomaly"]["threshold"]))
        await self._set("per_location", [float(x) for x in m["anomaly"]["per_location"]])
        await self._set("latency_ms", float(m["latency_ms"]))
        await self._set("sent_at", _dt(m["sent_at"]))

        await self._set("state", d["state"])
        await self._set("fault", d["fault"] or "")
        await self._set("location", d["location"] or 0)
        await self._set("severity", num(d["severity"]))
        await self._set("alarm_id", d["alarm_id"] or "")
        await self._set("latched", d["latched"])
        await self._set("acked", d["acked"])
        await self._set("acked_by", d["acked_by"] or "")
        await self._set("acked_at", _dt(d["acked_at"]))
        await self._set("witnesses", list(d["witnesses"]))
        await self._set("consecutive_windows", int(d["consecutive_windows"]))
        root = "unclassified" if d["state"] == "UNCLASSIFIED" else (d["fault"] or "") if d["state"] == "ALARM" else ""
        await self._set("root_cause", root)
        await self._set("guidance", d.get("guidance") or "")

        models = m.get("models") or []
        top = max(models, key=lambda e: e["p"]) if models else None
        await self._set("clf_fault", top["label"] if top else "")
        await self._set("clf_p", float(top["p"]) if top else math.nan)
        await self._set("clf_cyl", int((m.get("features_summary") or {}).get("classifier_cylinder") or 0))
        await self._set("models", [f"{e['model']} {e['label']} {e['p']:.2f}" for e in models])
        await self._set("rules_fired", [f"{r['rule']}" + (f" cyl {r['location']}" if r["location"] else "") + f" ({r['rule_basis']})"
                                        for r in m.get("physics_rules") or [] if r["fired"]])

        inj = m.get("injection")
        await self._set("inj_active", bool(inj))
        await self._set("inj_fault", inj["fault"] if inj else "")
        await self._set("inj_cyl", int(inj["cylinder"] or 0) if inj else 0)   # 1.6: null = bank-wide (lean_drift)
        await self._set("inj_sev", float(inj["severity"]) if inj else math.nan)
        await self._set("inj_started", _dt(inj["started_at"]) if inj else NULL_DATETIME)

        await self._set("asset_ne107", NE107.index(h["asset_ne107"]))
        await self._set("asset_ne107_text", h["asset_ne107"])
        for sensor, status in h["sensors"].items():
            if f"sensor.{sensor}" not in self._vars:
                await self._sensor(sensor)
            await self._set(f"sensor.{sensor}", NE107.index(status))
        for key in [k for k in self._vars if k.startswith("sensor.")]:
            if key[len("sensor."):] not in h["sensors"]:
                await self._not_connected(key)
        await self._set("maintenance", bool(self.runtime.maintenance_mode))
        await self._set("stream", NE107.index("NORMAL"))
        await self._set("state_age_s", 0.0)

        if not self._online:
            self._online = True
            await self._event("Diagnostics back online", SEV_INFO, m)
        await self._set("online", True)
        await self._transitions(m)

    async def _apply_obd(self, m: dict) -> None:
        for name, ch in m["channels"].items():
            if f"obd.{name}" not in self._vars:
                await self._obd_channel(name, -1e6, 1e6, None, ch["unit"])
            if ch["v"]:
                await self._set(f"obd.{name}", float(ch["v"][-1]))
            await self._set(f"obd.{name}.source", ch["source"])
            await self._set(f"obd.{name}.calibration", ch.get("calibration", ""))

    async def _transitions(self, m: dict) -> None:
        """Fire events on changes only: alarm raised / acked / cleared, advisory, unclassified, sensors."""
        d, h = m["decision"], m["health"]
        prev = self._prev or {"run_id": m["run_id"], "decision": {"state": "NORMAL", "alarm_id": None, "acked": False},
                              "health": {"sensors": {}}}
        pd = prev["decision"]
        self._prev = m

        if prev["run_id"] != m["run_id"]:
            note = f" (alarm {pd['alarm_id']} discarded)" if pd["alarm_id"] and not d["alarm_id"] else ""
            await self._event(f"Run changed to {m['run_id']}{note}", SEV_INFO, m)
        elif pd["alarm_id"] and pd["alarm_id"] != d["alarm_id"]:
            await self._event(f"{pd['alarm_id']} cleared", SEV_INFO, m, alarm_id=pd["alarm_id"])

        if d["alarm_id"] and d["alarm_id"] != pd["alarm_id"]:
            cyl = f" cyl {d['location']}" if d["location"] else ""
            sev = SEV_ALARM_MAINT if h["asset_ne107"] == "MAINTENANCE_REQUIRED" else SEV_ALARM_ACUTE
            await self._event(f"ALARM {d['fault']}{cyl} ({d['alarm_id']})", sev, m)
        if d["alarm_id"] and d["alarm_id"] == pd["alarm_id"] and d["acked"] and not pd["acked"]:
            await self._event(f"{d['alarm_id']} acknowledged by {d['acked_by']}", SEV_ACK, m)

        if not d["alarm_id"] and d["state"] != pd["state"]:
            if d["state"] == "UNCLASSIFIED":
                hint = f" · physics hint {d['fault']}" + (f" cyl {d['location']}" if d["location"] else "") if d["fault"] else ""
                await self._event(f"Abnormal, unclassified: inspect{hint}", SEV_UNCLASSIFIED, m)
            elif d["state"] == "ADVISORY":
                what = d["fault"] or "witnesses disagree"
                await self._event(f"Advisory: {what}", SEV_ADVISORY, m)
            elif d["state"] == "NORMAL" and pd["state"] in ("ADVISORY", "UNCLASSIFIED"):
                await self._event("Back to normal", SEV_INFO, m)

        for sensor, status in h["sensors"].items():
            before = prev["health"]["sensors"].get(sensor, "NORMAL")
            if status != before:
                sev = SEV_SENSOR if status == "FAILURE" else SEV_INFO
                await self._event(f"Sensor {sensor} {status}", sev, m, sensor=sensor)

    async def _event(self, message: str, severity: int, m: dict | None, alarm_id: str | None = None, sensor: str = "",
                     stale: bool = False) -> None:
        d = (m or {}).get("decision", {})
        fields = {
            "AlarmId": alarm_id or d.get("alarm_id") or "",
            "Fault": d.get("fault") or "",
            "Cylinder": d.get("location") or 0,
            "DecisionState": d.get("state", ""),
            "AssetHealth": (m or {}).get("health", {}).get("asset_ne107", ""),
            "Sensor": sensor,
            "Acked": bool(d.get("acked", False)),
            "DataSource": (m or {}).get("source", "").upper(),
        }
        # the diagnostic time for state-driven events; server time for watchdog events
        when = _dt(m["sent_at"]) if m and not stale else None
        log.info("OPC UA event [%d] %s", severity, message)
        self.events_emitted += 1
        self.last_event = {"t": round(self.runtime.clock(), 3), "message": message, "severity": severity}
        self.runtime.events.record("opcua_event", "confirmed", f"OPC UA event emitted: {message}", via="opcua",
                                   severity=severity, message=message)
        for gen in self._gens:
            ev = gen.event
            ev.Message = ua.LocalizedText(message)
            ev.Severity = severity
            ev.SourceNode = self.engine.nodeid
            ev.SourceName = "EngineAsset"
            for k, v in fields.items():
                setattr(ev, k, v)
            await gen.trigger(time_attr=when)

    async def _watchdog(self) -> None:
        """Stream freshness. No state for 3 s: DiagnosticsOnline false, DiagnosticStream FAILURE and one
        event. Mirrors the UI watchdog."""
        while True:
            await asyncio.sleep(0.25)
            last = self._last_state_mono
            if last is None:
                continue
            age = time.monotonic() - last
            if age >= 0.5:  # fresh states write 0.0 themselves; don't fight them at 10 Hz
                await self._set("state_age_s", round(age, 1))
            if self._online and age > OFFLINE_S:
                self._online = False
                await self._set("online", False)
                await self._set("stream", NE107.index("FAILURE"))
                await self._event(f"Diagnostics offline: no state for {OFFLINE_S:g} s", SEV_SENSOR, self._prev,
                                  sensor="DiagnosticStream", stale=True)

    # ------------------------------------------------------------ methods

    def _acknowledge(self, alarm_id, operator):
        if not isinstance(alarm_id, str) or not alarm_id:
            return self._reply(False, "invalid_message: AlarmId is required")
        res = self.runtime.ack(alarm_id, operator or "opcua", via="opcua")
        return self._reply(res.ok, "" if res.ok else f"{res.reason}: {res.detail}")

    def _inject(self, fault, cylinder, severity):
        """Same path as POST /inject: the live simulator only, the simulator's own validation, echoed in
        state.injection (D46), so the dashboard and OPC UA both confirm it from the broadcast."""
        if not hasattr(self.runtime.scenario, "inject"):
            reason = f"source {self.runtime.scenario.name!r} has no simulator; switch to sim first"
        else:
            try:
                # the REAL carOBD replay's lean_drift is bank-wide: cylinder null, as on POST /inject
                cyl = None if self.runtime.scenario.name == "carobd_replay" else int(cylinder)
                self.runtime.inject(str(fault), cyl, float(severity), via="opcua")
                log.info("OPC UA Inject(%s, %s, %s) accepted", fault, cylinder, severity)
                return self._reply(True, "")
            except (NotImplementedError, ValueError, TypeError) as e:
                reason = str(e)
        log.info("OPC UA Inject(%s, %s, %s) refused: %s", fault, cylinder, severity, reason)
        return self._reply(False, reason)

    def _sensor_command(self, fn, channel, name):
        """Same Runtime call as the HTTP sensor routes (D137). Accepted only means the command was taken;
        the sensor's actual state comes from SensorHealth/<channel> (the watchdog, 3 s)."""
        try:
            fn(str(channel), via="opcua")
        except (SensorControlError, ValueError) as e:
            log.info("OPC UA %s(%s) refused: %s", name, channel, e)
            return self._reply(False, str(e))
        log.info("OPC UA %s(%s) accepted", name, channel)
        return self._reply(True, "")

    @staticmethod
    def _reply(ok: bool, reason: str):
        return ua.Variant(ok, ua.VariantType.Boolean), ua.Variant(reason, ua.VariantType.String)
