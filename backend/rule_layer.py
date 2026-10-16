"""Deterministic rule layer: the single source of truth for alarm and ACK state (plan §11.1).

Every window is first assessed on its own (assess), then fed through the latch (RuleLayer.step).

Per-window assessment, in order of precedence:
  match         the classifier names a fault and the physics rule for that same fault fired (and,
                when both name a cylinder, the same cylinder; a bank-wide rule names none)
  conflict      the classifier names a fault, but only physics rules for other faults fired
  unclassified  the anomaly score is over threshold and the classifier names no fault;
                a physics rule that fired (e.g. valve_tick) is passed along as a hint
  advisory      one witness only (classifier or physics)
  healthy       none of the above

Latch (decided by Nishant, 2026-10-16):
  - ALARM after 2 consecutive `match` windows for the same fault. The first one is ADVISORY.
  - The alarm stays latched until it is acknowledged and the engine is healthy again,
    even if the fault clears before anyone presses ACK.
  - ACK only marks the alarm acknowledged. It never clears it.
  - Reset happens on a window evaluation, once the alarm is acknowledged and the last
    2 consecutive windows were healthy.
  - Each new episode gets a new alarm_id. One alarm is active at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TRAINED_FAULTS = ("misfire", "knock", "injector_clog")

RAISE_WINDOWS = 2  # consecutive two-witness windows before ALARM
CLEAR_WINDOWS = 2  # consecutive healthy windows (after ACK) before reset
P_MIN = 0.6  # mean probability the classifier majority needs to count as a witness

# NE 107 asset health for a latched alarm, by fault (DECISIONS.md D14)
ALARM_NE107 = {"misfire": "OFF_SPEC", "knock": "OFF_SPEC", "injector_clog": "MAINTENANCE_REQUIRED"}


@dataclass
class Evidence:
    """One window's evidence, using the contract's own shapes for models and physics_rules."""

    models: list[dict]
    physics_rules: list[dict]
    anomaly_score: float | None  # None (schema 1.5): no healthy model, so no anomaly witness
    anomaly_threshold: float
    severity: float | None = None
    classifier_location: int | None = None  # the classifier's cylinder (not in the contract's models[] entry)


@dataclass
class Assessment:
    kind: str  # match | conflict | unclassified | advisory | healthy
    fault: str | None = None
    location: int | None = None
    witnesses: list[str] = field(default_factory=list)


@dataclass
class ActiveAlarm:
    alarm_id: str
    fault: str
    location: int | None
    severity: float | None
    acked: bool = False
    acked_by: str | None = None
    acked_at: float | None = None


@dataclass
class AckResult:
    ok: bool
    reason: str | None = None  # unknown_alarm_id | duplicate_ack
    detail: str = ""


def classifier_label(models: list[dict]) -> str | None:
    """The classifier witness: majority label of the learned models (the trivial Baseline
    doesn't vote unless it's the only model). None when there's no clear, confident majority."""
    voters = [m for m in models if m["model"] != "Baseline"] or list(models)
    if not voters:
        return None
    counts: dict[str, list[float]] = {}
    for m in voters:
        counts.setdefault(m["label"], []).append(m["p"])
    label, ps = max(counts.items(), key=lambda kv: len(kv[1]))
    if len(ps) * 2 <= len(voters) or sum(ps) / len(ps) < P_MIN:
        return None
    return label


def assess(ev: Evidence) -> Assessment:
    cls = classifier_label(ev.models)
    fired = [r for r in ev.physics_rules if r["fired"]]
    cls_fault = cls in TRAINED_FAULTS
    anomaly_high = ev.anomaly_score is not None and ev.anomaly_score > ev.anomaly_threshold

    if cls_fault:
        cl = ev.classifier_location
        same = [r for r in fired if r["rule"] == cls and (cl is None or r["location"] is None or r["location"] == cl)]
        if same:
            loc = same[0]["location"] if same[0]["location"] is not None else cl
            return Assessment("match", cls, loc, ["physics", "classifier"])
        if fired:
            return Assessment("conflict", None, None, ["physics", "classifier"])
    if anomaly_high and not cls_fault:
        hint = fired[0] if fired else None
        return Assessment(
            "unclassified",
            hint["rule"] if hint else None,
            hint["location"] if hint else None,
            ["physics"] if hint else [],
        )
    if cls_fault:
        return Assessment("advisory", cls, None, ["classifier"])
    if fired:
        return Assessment("advisory", fired[0]["rule"], fired[0]["location"], ["physics"])
    return Assessment("healthy")


class RuleLayer:
    def __init__(self, first_alarm_id: int = 1) -> None:
        self.alarm: ActiveAlarm | None = None
        self._streak_fault: str | None = None
        self._streak_n = 0
        self._healthy_n = 0
        self._next_id = first_alarm_id
        self._cleared_ids: set[str] = set()
        self._last = Assessment("healthy")
        self._last_severity: float | None = None

    def step(self, ev: Evidence) -> dict:
        """Evaluate one window and return the contract's `decision` object."""
        a = assess(ev)
        self._last, self._last_severity = a, ev.severity

        if a.kind == "match":
            self._streak_n = self._streak_n + 1 if a.fault == self._streak_fault else 1
            self._streak_fault = a.fault
        else:
            self._streak_fault, self._streak_n = None, 0
        self._healthy_n = self._healthy_n + 1 if a.kind == "healthy" else 0

        if self.alarm is None and self._streak_n >= RAISE_WINDOWS:
            self.alarm = ActiveAlarm(f"a_{self._next_id:04d}", a.fault, a.location, ev.severity)
            self._next_id += 1
        elif self.alarm is not None and a.kind == "match" and a.fault == self.alarm.fault:
            self.alarm.location, self.alarm.severity = a.location, ev.severity

        if self.alarm is not None and self.alarm.acked and self._healthy_n >= CLEAR_WINDOWS:
            self._cleared_ids.add(self.alarm.alarm_id)
            self.alarm = None

        return self.decision()

    @property
    def next_alarm_id(self) -> int:
        return self._next_id

    def ack(self, alarm_id: str, operator: str, now: float) -> AckResult:
        """Mark the active alarm acknowledged. Never clears it and never touches anything else."""
        if self.alarm is not None and alarm_id == self.alarm.alarm_id:
            if self.alarm.acked:
                return AckResult(False, "duplicate_ack", f"{alarm_id} was already acknowledged by {self.alarm.acked_by}")
            self.alarm.acked, self.alarm.acked_by, self.alarm.acked_at = True, operator, now
            return AckResult(True)
        if alarm_id in self._cleared_ids:
            return AckResult(False, "duplicate_ack", f"{alarm_id} was already acknowledged and has cleared")
        active = self.alarm.alarm_id if self.alarm else "none"
        return AckResult(False, "unknown_alarm_id", f"{alarm_id} is not an alarm; active alarm: {active}")

    def decision(self) -> dict:
        """The current `decision` object, from the latch and the last window's assessment."""
        if self.alarm is not None:
            al = self.alarm
            matching = self._streak_fault == al.fault and self._streak_n >= RAISE_WINDOWS
            return {
                "state": "ALARM",
                "alarm_id": al.alarm_id,
                "fault": al.fault,
                "location": al.location,
                "severity": al.severity,
                # the evidence that raised the latch; the count is live while it still matches
                "witnesses": ["physics", "classifier"],
                "consecutive_windows": self._streak_n if matching else RAISE_WINDOWS,
                "latched": True,
                "acked": al.acked,
                "acked_by": al.acked_by,
                "acked_at": al.acked_at,
            }

        a = self._last
        state = {"healthy": "NORMAL", "unclassified": "UNCLASSIFIED"}.get(a.kind, "ADVISORY")
        return {
            "state": state,
            "alarm_id": None,
            "fault": a.fault,
            "location": a.location,
            "severity": self._last_severity if a.kind == "match" else None,
            "witnesses": list(a.witnesses),
            "consecutive_windows": self._streak_n if a.kind == "match" else 0,
            "latched": False,
            "acked": False,
            "acked_by": None,
            "acked_at": None,
        }


def asset_ne107(decision: dict, maintenance: bool = False) -> str:
    """NE 107 asset (engine) health for a decision. Sensor health is reported separately.

    CHECK_FUNCTION only in an explicit maintenance/test mode, and never over a fault's own health:
    a latched alarm or an unclassified anomaly keeps OFF_SPEC / MAINTENANCE_REQUIRED.
    """
    if decision["state"] == "ALARM":
        return ALARM_NE107.get(decision["fault"], "OFF_SPEC")
    if decision["state"] == "UNCLASSIFIED":
        return "OFF_SPEC"
    if maintenance:
        return "CHECK_FUNCTION"
    return "NORMAL"  # NORMAL, and ADVISORY (NORMAL + advisory event)
