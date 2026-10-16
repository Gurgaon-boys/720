"""720 Reliability Agent (D138): a read-only investigator over the backend's own state.

Six typed tools read what the runtime already holds (the last state broadcast, the sensor controls and the
incident event log). `investigate()` calls them in order, times each call, runs two deterministic checks
(evidence availability, the explanation) and answers six fixed operator questions. Every answer lists the
facts it used. Nothing here can change an alarm, acknowledge, inject or switch a sensor, and nothing derives a
fault label or a confidence: those come only from the rule layer's broadcast.

No language model is involved; the explanation text is generated deterministically from the facts. A model
could later rephrase `answers` but must not add facts (see D138).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

# what each sensor feeds (backend/sources/sim_source.py, dsp/features.py, models/features.py)
SENSOR_FEEDS = {
    "accel": ["knock rule (accelerometer knock-band energy)", "ML classifier (vibration features)"],
    "crank": ["misfire rule (crank half-order energy)", "per-cylinder localisation", "ML classifier (crank features)"],
    "mic": ["acoustic signature display (not a rule or classifier input)"],
    "cam": ["720° cycle phase (held from the last cam pulse while lost)"],
    "obd": ["injector-clog rule (fuel-trim drift)", "ML classifier (OBD trim features)"],
}
CLASSIFIER_INPUTS = ("crank", "accel")
RULE_SENSOR = {"misfire": "crank", "knock": "accel", "injector_clog": "obd"}
QUESTIONS = {
    "why_alarm": "Why was this alarm raised?",
    "sensor_failed": "Which sensor failed?",
    "evidence_lost": "Which diagnostic evidence became unavailable?",
    "can_confirm": "Can the current fault still be confirmed?",
    "before_alarm": "What happened before the alarm?",
    "inspect_first": "What should the operator inspect first?",
}


# ------------------------------------------------------------ tools (read-only)

def get_engine_state(rt) -> dict:
    s = rt.last_state or {}
    return {"source": rt.scenario.name, "run_id": s.get("run_id"), "mode": s.get("mode"),
            "source_label": s.get("source_label") or s.get("source"), "stream_t": s.get("t"),
            "injection": s.get("injection"), "sent_at": s.get("sent_at")}


def get_operating_point(rt) -> dict:
    s = rt.last_state or {}
    return dict(s.get("operating_point") or {})


def get_sensor_health(rt) -> dict:
    s = rt.last_state or {}
    return {"health": dict((s.get("health") or {}).get("sensors") or {}),
            "asset_ne107": (s.get("health") or {}).get("asset_ne107"),
            "disabled_by_operator": rt.disabled_sensors() or [],
            "controllable": rt.disabled_sensors() is not None}


def get_diagnostic_evidence(rt) -> dict:
    s = rt.last_state or {}
    models = s.get("models") or []
    svm = next((m for m in models if m.get("model") == "SVM"), models[0] if models else None)
    return {"models": models, "classifier": svm, "classifier_available": bool(models),
            "classifier_cylinder": (s.get("features_summary") or {}).get("classifier_cylinder"),
            "physics_rules": s.get("physics_rules") or [],
            "physics_fired": [r for r in (s.get("physics_rules") or []) if r.get("fired")],
            "anomaly_score": (s.get("anomaly") or {}).get("score"),
            "anomaly_threshold": (s.get("anomaly") or {}).get("threshold"),
            "localisation": s.get("localisation")}


def get_alarm_state(rt) -> dict:
    d = (rt.last_state or {}).get("decision") or {}
    keys = ("state", "fault", "location", "alarm_id", "latched", "acked", "acked_by", "acked_at", "witnesses",
            "consecutive_windows", "guidance", "root_cause")
    return {k: d.get(k) for k in keys}


def get_recent_events(rt, n: int = 60) -> list[dict]:
    return [e for e in rt.events.since(0) if e["kind"] != "opcua_event"][-n:]


TOOLS = {
    "get_engine_state": get_engine_state, "get_operating_point": get_operating_point,
    "get_sensor_health": get_sensor_health, "get_diagnostic_evidence": get_diagnostic_evidence,
    "get_alarm_state": get_alarm_state, "get_recent_events": get_recent_events,
}


# ------------------------------------------------------------ deterministic checks

def availability(sensors: dict, evidence: dict) -> dict:
    """Evidence AVAILABILITY (not a decision): which witnesses can still speak."""
    offline = sorted({s for s, v in sensors["health"].items() if v == "FAILURE"} | set(sensors["disabled_by_operator"]))
    missing = [c for c in CLASSIFIER_INPUTS if c in offline]
    if all(c in offline for c in CLASSIFIER_INPUTS):
        level = "INSUFFICIENT"
    elif evidence["classifier_available"] and not missing:
        level = "FULL"
    else:
        level = "PARTIAL"
    lost = {s: SENSOR_FEEDS.get(s, []) for s in offline}
    rules = {r["rule"]: (RULE_SENSOR.get(r["rule"]) not in offline) for r in evidence["physics_rules"]}
    return {"offline": offline, "classifier_inputs_missing": missing, "level": level, "lost": lost,
            "rule_inputs_available": rules, "new_alarm_possible": level == "FULL"}


def _upper(s: list[str]) -> str:
    return ", ".join(x.upper() for x in s)


def _fmt_t(ev: dict | None) -> str:
    if not ev:
        return "time unavailable"
    t = time.strftime("%H:%M:%S", time.localtime(ev["t"]))
    return f"{t}" + (f" (stream t = {ev['stream_t']:.1f} s)" if ev.get("stream_t") is not None else "")


@dataclass
class Step:
    tool: str
    label: str
    ms: float
    summary: str
    result: object = field(repr=False)


def _run(steps: list, tool: str, label: str, fn, summarise) -> object:
    t0 = time.perf_counter()
    out = fn()
    steps.append(Step(tool, label, round((time.perf_counter() - t0) * 1000, 3), summarise(out), out))
    return out


def investigate(rt, trigger: str | None = None) -> dict:
    """One investigation over the current backend state. Returns steps (each a completed call), findings,
    answers (with the facts each one used) and a short headline."""
    started = rt.clock()
    if rt.last_state is None:
        return {"status": "no_data", "started_at": started, "steps": [], "answers": [],
                "headline": "No state broadcast yet: nothing to investigate."}
    steps: list[Step] = []
    sensors = _run(steps, "get_sensor_health", "Checking sensor health", lambda: get_sensor_health(rt),
                   lambda r: "FAILURE: " + _upper([s for s, v in r["health"].items() if v == "FAILURE"])
                   if "FAILURE" in r["health"].values() else "all sensors NORMAL")
    evidence = _run(steps, "get_diagnostic_evidence", "Reading diagnostic evidence", lambda: get_diagnostic_evidence(rt),
                    lambda r: (f"classifier {r['classifier']['model']}: {r['classifier']['label']} p {r['classifier']['p']}"
                               if r["classifier"] else "classifier: no output")
                    + "; physics fired: " + (", ".join(f"{x['rule']}" + (f" cyl {x['location']}" if x.get("location") else "")
                                                       for x in r["physics_fired"]) or "none"))
    alarm = _run(steps, "get_alarm_state", "Reviewing alarm state", lambda: get_alarm_state(rt),
                 lambda r: f"{r['state']}" + (f" {r['fault']}" if r["fault"] else "") + (f" cyl {r['location']}" if r["location"] else "")
                 + (f", {r['alarm_id']} {'acknowledged' if r['acked'] else 'unacknowledged'}" if r["alarm_id"] else ""))
    events = _run(steps, "get_recent_events", "Reviewing the incident record", lambda: get_recent_events(rt),
                  lambda r: f"{len(r)} recorded events")
    engine = _run(steps, "get_engine_state", "Reading engine and source", lambda: get_engine_state(rt),
                  lambda r: f"source {r['source']}, run {r['run_id']}")
    op = _run(steps, "get_operating_point", "Reading operating point", lambda: get_operating_point(rt),
              lambda r: f"{r.get('rpm', 'n/a')} rpm, load {r.get('load_pct', 'n/a')} %")
    avail = _run(steps, "check_evidence_availability", "Evaluating evidence availability",
                 lambda: availability(sensors, evidence), lambda r: f"{r['level']} evidence; new confirmed alarm possible: "
                 + ("yes" if r["new_alarm_possible"] else "no"))
    answers = _run(steps, "explain", "Producing operator explanation",
                   lambda: _answers(sensors, evidence, alarm, events, avail, engine), lambda r: f"{len(r)} answers")
    headline = _headline(alarm, avail)
    return {"status": "ok", "trigger": trigger, "started_at": started, "finished_at": rt.clock(),
            "steps": [{k: v for k, v in asdict(s).items() if k != "result"} for s in steps],
            "findings": {"engine": engine, "operating_point": op, "sensors": sensors, "evidence": evidence,
                         "alarm": alarm, "availability": avail},
            "answers": answers, "headline": headline,
            "authority": "The rule layer is the only diagnostic authority; this investigation only reads its broadcast."}


def _headline(alarm: dict, avail: dict) -> str:
    state = alarm["state"] + (f" {alarm['fault']}" if alarm["fault"] else "") + (f" cyl {alarm['location']}" if alarm["location"] else "")
    if avail["offline"] and avail["level"] != "FULL":
        return (f"{_upper(avail['offline'])} offline: {avail['level'].lower()} evidence. Rule layer reports {state}; "
                "no new alarm can be confirmed until the evidence is back.")
    if avail["offline"]:
        return f"{_upper(avail['offline'])} offline, but both diagnostic witnesses keep their inputs. Rule layer reports {state}."
    return f"All sensors live, {avail['level'].lower()} evidence. Rule layer reports {state}."


def _answers(sensors, evidence, alarm, events, avail, engine) -> list[dict]:
    out = []

    def add(key, text, facts):
        out.append({"key": key, "question": QUESTIONS[key], "answer": text, "facts": facts})

    # 1. why the alarm
    alarm_ev = None
    if alarm["alarm_id"]:
        alarm_ev = next((e for e in events if e["kind"] == "decision" and e["data"].get("alarm_id") == alarm["alarm_id"]
                         and e["data"].get("state") == "ALARM"), None)
    if alarm["state"] == "ALARM":
        src = alarm_ev["data"] if alarm_ev else {"witnesses": alarm["witnesses"], "models": evidence["models"],
                                                 "rules": [], "consecutive_windows": alarm["consecutive_windows"]}
        svm = next((m for m in src.get("models", []) if m.get("model") == "SVM"), None)
        parts = []
        if "physics" in (src.get("witnesses") or []):
            parts.append("the physics witness" + (f" ({', '.join(src['rules'])})" if src.get("rules") else ""))
        if "classifier" in (src.get("witnesses") or []):
            parts.append("the ML classifier" + (f" ({svm['model']}: {svm['label']}, p {svm['p']})" if svm else ""))
        where = f", cylinder {alarm['location']}" if alarm["location"] else ""
        text = (f"{alarm['alarm_id']} ({alarm['fault']}{where}) was raised by the rule layer"
                + (f" at {_fmt_t(alarm_ev)}" if alarm_ev else "") + " because " + " and ".join(parts)
                + " named the same fault in consecutive windows (two-witness rule).")
        if not alarm_ev:
            text += " (The raising event is older than the in-memory record; witnesses shown are the alarm's own.)"
        add("why_alarm", text, ["decision.alarm_id", "decision.witnesses", "events.decision"])
    else:
        w = " + ".join(alarm["witnesses"] or []) or "none"
        add("why_alarm", f"No alarm is active. The rule layer reports {alarm['state']}"
            + (f" {alarm['fault']}" if alarm["fault"] else "") + (f" cyl {alarm['location']}" if alarm["location"] else "")
            + f" with witnesses: {w}.", ["decision.state", "decision.witnesses"])

    # 2. which sensor failed
    failed = [s for s, v in sensors["health"].items() if v == "FAILURE"]
    commanded = sensors["disabled_by_operator"]
    if failed or commanded:
        lines = []
        for s in sorted(set(failed) | set(commanded)):
            cmd = next((e for e in reversed(events) if e["kind"] == "sensor_off" and e["phase"] == "accepted"
                        and e["data"].get("sensor") == s), None)
            conf = next((e for e in reversed(events) if e["kind"] == "sensor_health" and e["data"].get("sensor") == s
                         and e["data"].get("status") == "FAILURE"), None)
            status = sensors["health"].get(s, "n/a")
            line = f"{s.upper()}: {status}"
            if s in commanded:
                line += f", switched off by an operator command (via {cmd['via']}, {_fmt_t(cmd)})" if cmd else ", switched off by an operator command"
            if conf:
                line += f"; watchdog confirmed the failure at {_fmt_t(conf)}"
            elif status != "FAILURE":
                line += "; watchdog has not confirmed the failure yet (3 s without data)"
            lines.append(line)
        add("sensor_failed", ". ".join(lines) + ".", ["health.sensors", "GET /sensors", "events.sensor_off", "events.sensor_health"])
    else:
        add("sensor_failed", "No sensor has failed: every monitored sensor reports NORMAL.", ["health.sensors"])

    # 3. evidence lost
    if avail["lost"]:
        text = "; ".join(f"{s.upper()} feeds {', '.join(f)}" for s, f in avail["lost"].items())
        if avail["classifier_inputs_missing"]:
            text += (f". The ML classifier witness is unavailable ({_upper(avail['classifier_inputs_missing'])} offline):"
                     + (" its output has stopped." if not evidence["classifier_available"] else " its last output is still in this window."))
        add("evidence_lost", text + ".", ["health.sensors", "models[]", "sensor dependency table"])
    else:
        add("evidence_lost", "None: both witnesses have all their inputs.", ["health.sensors", "models[]"])

    # 4. can the current fault still be confirmed
    fault = alarm["fault"]
    if fault:
        rule_ok = avail["rule_inputs_available"].get(fault, False)
        rule_fired = any(r["rule"] == fault for r in evidence["physics_fired"])
        if avail["new_alarm_possible"]:
            text = f"Yes. Both witnesses have their inputs, so the rule layer can confirm {fault} if they agree for 2 windows."
        else:
            text = (f"No, not as a new confirmed alarm: the rule layer needs two matching witnesses and the ML classifier "
                    f"{'has no valid input' if avail['classifier_inputs_missing'] else 'has no output'}. "
                    f"The physics {fault} rule {'still fires' if rule_fired else ('is available' if rule_ok else 'has lost its input')}"
                    + (f" (one witness: ADVISORY at most)." if rule_ok else "."))
        if alarm["state"] == "ALARM" and not alarm["acked"]:
            text += f" {alarm['alarm_id']} stays latched until it is acknowledged: losing a witness never clears an alarm."
        add("can_confirm", text, ["decision.fault", "physics_rules", "models[]", "evidence availability"])
    else:
        add("can_confirm", "No fault is named right now. " + ("A new alarm can be confirmed if one appears."
                                                              if avail["new_alarm_possible"] else
                                                              "Evidence is degraded: a new fault could only reach ADVISORY."),
            ["decision.fault", "evidence availability"])

    # 5. before the alarm
    idx = next((i for i, e in enumerate(events) if e is alarm_ev), None) if alarm_ev else None
    window = events[max(0, idx - 6):idx] if idx is not None else events[-6:]
    label = "Before the alarm" if idx is not None else "Most recent events"
    add("before_alarm", f"{label}: " + ("; ".join(f"{_fmt_t(e).split(' ')[0]} {e['text']}" for e in window) or "nothing recorded") + ".",
        ["events"])

    # 6. inspect first
    if avail["offline"] and avail["level"] != "FULL":
        text = (f"Restore or inspect {_upper(avail['offline'])} first: until it is back, 720 cannot confirm new faults."
                + (f" Then: {alarm['guidance']}" if alarm.get("guidance") else ""))
    else:
        text = alarm.get("guidance") or "Nothing to inspect: no fault named."
    add("inspect_first", text, ["decision.guidance", "evidence availability"])
    return out
