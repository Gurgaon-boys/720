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

UNCLASSIFIED persistence: like ALARM, UNCLASSIFIED needs UNCLASSIFIED_WINDOWS (2) consecutive
`unclassified` windows. The first one is assessed as if there were no anomaly witness (ADVISORY when a
physics rule fired, else NORMAL), so one stray anomalous window raises nothing.

Corroboration (D134): an anomaly flag does not override a physics rule that recently named a trained
fault. If a physics rule for misfire, knock or injector_clog fired in any of the last CORROBORATE_WINDOWS
(3) windows, this one included, an `unclassified` window becomes an `advisory` for that fault (the anomaly
corroborates it; the classifier still hasn't named it, so it is one witness, not ALARM). UNCLASSIFIED
needs a hold with no named fault. The valve-tick rule is not a named fault here (valve tick is never
trained), so it stays a hint on UNCLASSIFIED. Applies to every source.

Post-misfire recovery (D144, D146): a misfire makes the O2 sensor read
lean, so the ECU trims rich; after the misfire stops, the trims take tens of seconds to re-adapt and the trim-drift
rule's 30 s window still holds the misfire period, which looks like an injector clog. Like OBD-II, which suspends
fuel-trim monitoring around a misfire, a confirmed misfire (a two-witness `match`) arms a recovery window that lasts
until the trims have settled (Evidence.trims, dsp.rules.trim_recovery; not before RECOVERY_MIN_S, while the 30 s OBD
window still holds the misfire) or RECOVERY_MAX_S after the last misfire match. In it the injector_clog rule is ignored for the first RECOVERY_BLIND_S, then while the ECU is removing fuel
(STFT <= 0: re-adapting back; a clog makes it add fuel), and an anomaly, a lone clog witness or nothing at all shows as a recovery ADVISORY (fault null,
no witness), never UNCLASSIFIED. Other faults (knock, a new misfire) go through as usual. The window is armed only
by a confirmed misfire, not by the misfire rule alone: in the simulator a clog starves its cylinder and fires the
misfire rule in ~95 % of windows, so gating on the rule alone would switch clog detection off.

Post-knock recovery (pending K1, docs/decisions_pending/post_knock_recovery.md): knock control pulls spark on the
knocking cylinder and gives it back slowly (sim: up to 12 deg, 0.5 deg/s), so after the knock stops the engine runs
retarded for up to ~24 s, and the classifier's OBD feature spark_retard_deg (30 s window minus the 30 s before it)
holds the knock period for up to 60 s: positive while it is in the window, negative while it is in the 30 s before.
The healthy model never saw that, so a cleared knock read UNCLASSIFIED for ~30 s with no fault present. Like the
misfire window, a confirmed knock (a two-witness `match`) arms a recovery window that lasts until spark timing has
settled (Evidence.spark, dsp.rules.spark_recovery; not before KNOCK_RECOVERY_MIN_S, while the feature's 60 s of OBD
history still holds the knock) or RECOVERY_MAX_S after the last knock
match. In it an anomaly or nothing at all shows as a recovery ADVISORY (fault null, no witness), never UNCLASSIFIED.
No physics rule is gated: knock, misfire and clog go through as usual, so ALARMs are unchanged.

Post-clog recovery (pending T2, docs/decisions_pending/time_lapse.md): after an injector clog is fixed, the ECU
spends its learned extra fuel back out (STFT/LTFT fall from about +s/(4-s) to 0) while the 30 s OBD window still
holds the clog; the healthy model never saw that, so a cleared clog read UNCLASSIFIED for ~12 s of stream time.
Built like the knock window: a confirmed injector_clog (a two-witness `match`) arms it; it ends once the trims have
settled (not before RECOVERY_MIN_S) or RECOVERY_MAX_S after the last clog match. In it only an anomaly or nothing
at all becomes the recovery ADVISORY; the trim rule is not gated, so a clog that is still there keeps its ALARM.

Bank-wide faults (D145): when the physics rule names no cylinder (trims are bank-wide) and the classifier names different
cylinders in the windows of the streak, the ALARM's location is null ("bank"), not whichever cylinder came last.

Latch (decided by Nishant, 2026-10-16):
  - ALARM after 2 consecutive `match` windows for the same fault. The first one is ADVISORY.
  - The alarm stays latched until it is acknowledged and its own fault has stopped matching,
    even if the fault clears before anyone presses ACK.
  - ACK only marks the alarm acknowledged. It never clears it.
  - Reset happens on a window evaluation, once the alarm is acknowledged and the last
    2 consecutive windows were not a `match` for the alarm's fault (amended 2026-10-16, D124: it was
    "2 healthy windows", which kept an acknowledged alarm up while the ECU recovered and the healthy
    model still flagged, and hid any new fault behind it). Whatever remains (an anomaly, one witness)
    then shows as itself: UNCLASSIFIED or ADVISORY.
  - Each new episode gets a new alarm_id. One alarm is active at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

TRAINED_FAULTS = ("misfire", "knock", "injector_clog")

RAISE_WINDOWS = 2  # consecutive two-witness windows before ALARM
CLEAR_WINDOWS = 2  # consecutive windows without a match for the alarm's own fault (after ACK) before reset
UNCLASSIFIED_WINDOWS = 2  # consecutive anomalous, unnamed windows before UNCLASSIFIED
CORROBORATE_WINDOWS = 3   # a trained-fault physics rule in this many recent windows turns an anomaly into its ADVISORY
RECOVERY_MAX_S = 90.0     # longest post-misfire recovery window after the last confirmed misfire window
RECOVERY_MIN_S = 30.0     # "settled" counts only once the 30 s OBD window (dsp.obd_window.WINDOW_S) no longer holds
                          # the last confirmed misfire window: the classifier and healthy model read 30 s OBD features
KNOCK_RECOVERY_MIN_S = 60.0  # spark_retard_deg compares the 30 s window with the 30 s before it
                             # (dsp.obd_window.RETARD_LOOKBACK_S): until both no longer hold the last confirmed knock
                             # window it swings positive, through 0, then negative, and reads 0 in passing
RECOVERY_BLIND_S = 10.0   # trim rule off regardless for this long after the last confirmed misfire window: the 5 s
                          # STFT mean (dsp.obd_window.RECENT_S) still holds misfire-time samples plus O2 transport lag
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
    t: float | None = None                  # window end, s (stream time); needed for the recovery window's time cap
    trims: dict | None = None               # dsp.rules.trim_recovery: {"removing_fuel", "settled"}, None = unknown
    spark: dict | None = None               # dsp.rules.spark_recovery: {"settled"}, None = unknown


@dataclass
class Assessment:
    kind: str  # match | conflict | unclassified | advisory | healthy
    fault: str | None = None
    location: int | None = None
    witnesses: list[str] = field(default_factory=list)
    bank: bool = False  # match on a physics rule that names no cylinder (bank-wide, e.g. the trim rule)


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
            bank = same[0]["location"] is None
            loc = cl if bank else same[0]["location"]
            return Assessment("match", cls, loc, ["physics", "classifier"], bank=bank)
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
    def __init__(self, first_alarm_id: int = 1, unclassified_windows: int = UNCLASSIFIED_WINDOWS,
                 corroborate_windows: int = CORROBORATE_WINDOWS, recovery: bool = True,
                 knock_recovery: bool = True, clog_recovery: bool = True) -> None:
        self.unclassified_windows = unclassified_windows
        self.corroborate_windows = corroborate_windows    # 0 = no corroboration (the behaviour before D134)
        self.recovery = recovery                          # False = no post-misfire recovery gate (behaviour before it)
        self.knock_recovery = knock_recovery              # False = no post-knock recovery gate (behaviour before it)
        self.clog_recovery = clog_recovery                # False = no post-clog recovery window (behaviour before it)
        self._named: list[dict | None] = []                # newest last: a fired trained-fault physics rule per window
        self.alarm: ActiveAlarm | None = None
        self._streak_fault: str | None = None
        self._streak_n = 0
        self._quiet_n = 0  # consecutive windows without a match for the latched alarm's fault
        self._next_id = first_alarm_id
        self._cleared_ids: set[str] = set()
        self._last = Assessment("healthy")
        self._last_severity: float | None = None
        self._unclassified_n = 0
        self._misfire_t: float | None = None   # stream time of the last confirmed misfire window (arms recovery)
        self._knock_t: float | None = None     # stream time of the last confirmed knock window (arms recovery)
        self._clog_t: float | None = None      # stream time of the last confirmed injector_clog window (arms recovery)
        self.recovering = False                # this window is in a recovery window (post-misfire or post-knock)
        self.recovering_from: str | None = None  # "misfire" | "knock" | "injector_clog" (misfire first: it gates trims)
        self._streak_cyls: list = []           # classifier cylinders of the current match streak (bank-wide check)

    def _recovery(self, ev: Evidence) -> Evidence:
        """Decide whether this window is in a recovery window (post-misfire first, then post-knock, post-clog)."""
        ev = self._misfire_recovery(ev)
        knock = self._knock_recovering(ev)
        clog = self._clog_recovering(ev)
        self.recovering_from = ("misfire" if self.recovering else "knock" if knock
                                else "injector_clog" if clog else None)
        self.recovering = self.recovering_from is not None
        return ev

    def _clog_recovering(self, ev: Evidence) -> bool:
        """Post-clog recovery window: until the trims have settled (not before RECOVERY_MIN_S, while the 30 s OBD
        window still holds the last confirmed clog window) or RECOVERY_MAX_S after it."""
        if self._clog_t is None:
            return False
        expired = ev.t is not None and ev.t - self._clog_t > RECOVERY_MAX_S
        settled = (ev.trims is not None and ev.trims["settled"]
                   and (ev.t is None or ev.t - self._clog_t >= RECOVERY_MIN_S))
        if expired or settled:
            self._clog_t = None
            return False
        return True

    def _knock_recovering(self, ev: Evidence) -> bool:
        """Post-knock recovery window: until spark timing has settled (not before KNOCK_RECOVERY_MIN_S, while the
        60 s of OBD history behind spark_retard_deg still holds the last confirmed knock window) or RECOVERY_MAX_S
        after it."""
        if self._knock_t is None:
            return False
        expired = ev.t is not None and ev.t - self._knock_t > RECOVERY_MAX_S
        settled = (ev.spark is not None and ev.spark["settled"]
                   and (ev.t is None or ev.t - self._knock_t >= KNOCK_RECOVERY_MIN_S))
        if expired or settled:
            self._knock_t = None
            return False
        return True

    def _misfire_recovery(self, ev: Evidence) -> Evidence:
        """Post-misfire recovery window: decide whether this window is in it and, if the ECU is removing fuel,
        take the injector_clog rule out of the evidence (OBD-II style enable condition)."""
        if self._misfire_t is None:
            self.recovering = False
            return ev
        expired = ev.t is not None and ev.t - self._misfire_t > RECOVERY_MAX_S
        settled = (ev.trims is not None and ev.trims["settled"]
                   and (ev.t is None or ev.t - self._misfire_t >= RECOVERY_MIN_S))
        if expired or settled:
            self._misfire_t, self.recovering = None, False
            return ev
        self.recovering = True
        blind = ev.t is not None and ev.t - self._misfire_t <= RECOVERY_BLIND_S
        if blind or (ev.trims is not None and ev.trims["removing_fuel"]):
            rules = [{**r, "fired": False} if r["rule"] == "injector_clog" else r for r in ev.physics_rules]
            ev = replace(ev, physics_rules=rules)
        return ev

    def step(self, ev: Evidence) -> dict:
        """Evaluate one window and return the contract's `decision` object."""
        ev = self._recovery(ev)
        raw = a = assess(ev)
        self._unclassified_n = self._unclassified_n + 1 if raw.kind == "unclassified" else 0
        if raw.kind == "unclassified" and self._unclassified_n < self.unclassified_windows:
            a = assess(replace(ev, anomaly_score=None))      # not yet persistent: no anomaly witness
        if self.corroborate_windows:
            fired = [r for r in ev.physics_rules if r["fired"] and r["rule"] in TRAINED_FAULTS]
            self._named = (self._named + [fired[0] if fired else None])[-self.corroborate_windows:]
            named = next((r for r in reversed(self._named) if r is not None), None)
            if raw.kind == "unclassified" and named is not None:   # the anomaly corroborates the named fault
                a = Assessment("advisory", named["rule"], named["location"], ["physics"])
        if a.kind == "match" and a.fault == "misfire" and ev.t is not None and self.recovery:
            self._misfire_t, self.recovering, self.recovering_from = ev.t, False, None  # (re)arms the misfire window
        elif a.kind == "match" and a.fault == "knock" and ev.t is not None and self.knock_recovery:
            self._knock_t, self.recovering, self.recovering_from = ev.t, False, None    # (re)arms the knock window
        elif a.kind == "match" and a.fault == "injector_clog" and ev.t is not None and self.clog_recovery:
            self._clog_t, self.recovering, self.recovering_from = ev.t, False, None     # (re)arms the clog window
        elif self.recovering and (a.kind in ("unclassified", "healthy", "conflict")
                                  or (self.recovering_from == "misfire" and a.kind == "advisory"
                                      and a.fault == "injector_clog")):
            a = Assessment("advisory")                             # recovery ADVISORY: fault null, no witness

        if a.kind == "match":
            same = a.fault == self._streak_fault
            self._streak_n = self._streak_n + 1 if same else 1
            self._streak_cyls = (self._streak_cyls if same else []) + [a.location]
            self._streak_fault = a.fault
            if a.bank and len(set(self._streak_cyls[-RAISE_WINDOWS:])) > 1:
                a = replace(a, location=None)                      # bank-wide rule, classifier cylinder hopping
        else:
            self._streak_fault, self._streak_n, self._streak_cyls = None, 0, []
        self._last, self._last_severity = a, ev.severity
        own = self.alarm is not None and a.kind == "match" and a.fault == self.alarm.fault
        self._quiet_n = 0 if own else self._quiet_n + 1

        if self.alarm is None and self._streak_n >= RAISE_WINDOWS:
            self.alarm = ActiveAlarm(f"a_{self._next_id:04d}", a.fault, a.location, ev.severity)
            self._next_id += 1
            self._quiet_n = 0
        elif self.alarm is not None and a.kind == "match" and a.fault == self.alarm.fault:
            # a bank-wide alarm becomes (and stays) "bank" once its classifier cylinder hops
            loc = a.location if not a.bank or a.location == self.alarm.location else None
            self.alarm.location, self.alarm.severity = loc, ev.severity

        if self.alarm is not None and self.alarm.acked and self._quiet_n >= CLEAR_WINDOWS:
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
