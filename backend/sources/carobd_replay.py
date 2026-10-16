"""Real carOBD replay source: a held-out (or uploaded) carOBD log streamed at 1x / 10x / 30x.

REAL data, never "live": every state message says `mode: "replay"`, `source: "real"` (or "injected"
while a lean drift is added) and `source_label` (contract 1.6). The data is display only: the files are
the carOBD HELD-OUT files (already read once for Table A / column C), loaded through
data/carobd.py:load(), and nothing here is fitted or tuned.

Timing: the file's own clock (t_s from ENGINE_RUN_TIME, whole seconds, ~2 rows/s) is the stream clock,
scaled by the speed. Each PID refreshes at only ~0.55 Hz (D47): an obd message carries a channel's
value only when it changed (the first row of each run of repeats), at its row's file time.

Decision chain: Diljit's OBD branch v1 (D55-D58), unchanged: 30 s windows / 15 s hop
(models/obd/features.py), Mahalanobis per regime (warm-up / idle / mid / high) and the trim-drift and
timing-retard rules (models/obd/rules.py), all with the thresholds in models/thresholds/obd_branch.json,
through the normal rule layer.
  * No crank or audio in carOBD: `localisation: "none"`, empty signature, no per-cylinder bars.
  * Witness 2, the OBD classifier, is Diljit's (trained on his branch). If `models.obd_classifier` is
    importable and its `load_witness()` returns a witness, it is used and ALARM follows the normal
    two-witness rule. Otherwise the replay runs single-witness: ADVISORY / UNCLASSIFIED at most, and
    `state.replay` says so.

Injection on real data: `lean_drift` (POST /inject) adds a ramp to STFT/LTFT whose steady total is the
simulator's injector-clog relation T = s / (4 - s) (sim/engine.py). The added trim is evaluated at each
value's own refresh time, so it keeps the logger's ~0.55 Hz staircase. The stream is then labelled
REAL+INJECTED and state.injection echoes the request; POST /clear_faults removes it from that moment on.
"""

from __future__ import annotations

import importlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backend.rule_layer import Evidence
from backend.scenarios import WINDOW_S, Phase, Window
from models.obd import rules as obd_rules
from models.obd.features import HOP_S, LTFT, STFT, WINDOW_S as OBD_WINDOW_S, MapModel, regime, window_features
from models.obd.healthy import HealthyModel

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "data"))
import carobd  # noqa: E402

NAME = "carobd_replay"
SPLIT = REPO / "data" / "splits" / "carobd_split.json"
OBD_BRANCH = REPO / "models" / "thresholds" / "obd_branch.json"   # OBD branch v1 (pre-registered, D56/D58)
UPLOAD_DIR = REPO / "data" / "uploads"
SPEEDS = (1, 10, 30)
DEFAULT_FILE = "heldout:drive2.csv"
ANOMALY_LIMIT = 0.5          # score = 0.5 * d / threshold, the same scale as the simulator's witness
TYPE_LABEL = {"idle": "idle", "drive": "drive", "live": "live", "ufpe": "campus", "long": "long"}

# obd channel name (the simulator's names, so the OBD panel lines them up) <- carOBD column, unit
CHANNELS = {
    "rpm": ("ENGINE_RPM", "rev/min"),
    "load_pct": ("ENGINE_LOAD", "%"),
    "throttle_pct": ("THROTTLE", "%"),
    "map_kpa": ("INTAKE_MANIFOLD_PRESSURE", "kPa"),
    "coolant_c": ("COOLANT_TEMPERATURE", "degC"),
    "stft_pct": (STFT, "%"),
    "ltft_pct": (LTFT, "%"),
    "spark_adv_deg": ("TIMING_ADVANCE", "deg"),
    "intake_air_temp_c": ("INTAKE_AIR_TEMP", "degC"),
    "baro_kpa": ("ABSOLUTE_BAROMETRIC_PRESSURE", "kPa"),
    "battery_v": ("CONTROL_MODULE_VOLTAGE", "V"),
}
# the OBD branch v1 needs these; the rest of CHANNELS is display only and may be missing in an upload
REQUIRED_COLUMNS = (carobd.TIME_COL, "ENGINE_RPM", "ENGINE_LOAD", "VEHICLE_SPEED", "COOLANT_TEMPERATURE",
                    STFT, LTFT, "INTAKE_MANIFOLD_PRESSURE", "TIMING_ADVANCE")

# lean_drift shape: the total added trim ramps to T(s) = s / (4 - s) over RAMP_S of file time; the ECU
# first answers with STFT, and LTFT learns it with time constant TAU_LTFT_S (STFT gives it back).
# Demo-shape assumptions, not measured: only the steady total comes from the simulator's physics.
LEAN_RAMP_S = 60.0
LEAN_TAU_LTFT_S = 120.0
INJECTABLE = ("lean_drift",)
MIN_DURATION_S = 60.0        # two OBD windows
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_UPLOADS = 20             # oldest uploads are deleted beyond this

_RUNNING = Phase()


# ---------------------------------------------------------------- files

def heldout_files() -> list[str]:
    return list(json.loads(SPLIT.read_text())["heldout"])


_DURATIONS: dict[str, float] = {}


def list_files() -> list[dict]:
    """Held-out carOBD files by type (idle, drive, live, campus, long) and the uploads, for the picker."""
    out = []
    for f in heldout_files():
        if f not in _DURATIONS:
            _DURATIONS[f] = carobd.duration_s(carobd.load(carobd.CAROBD_DIR / f))
        out.append({"id": f"heldout:{f}", "name": f, "type": TYPE_LABEL[carobd.file_type(f)],
                    "duration_s": round(_DURATIONS[f], 1), "label": "REAL (replay)"})
    for meta in list_uploads():
        out.append({"id": f"upload:{meta['id']}", "name": meta["name"], "type": "upload",
                    "duration_s": meta["duration_s"], "label": "REAL (uploaded, unverified)"})
    return out


def list_uploads() -> list[dict]:
    if not UPLOAD_DIR.is_dir():
        return []
    metas = []
    for p in sorted(UPLOAD_DIR.glob("*.json")):
        try:
            metas.append(json.loads(p.read_text()))
        except ValueError:
            continue
    return sorted(metas, key=lambda m: m.get("uploaded_at", 0))


UPLOAD_ID = re.compile(r"^[0-9a-f]{12}$")


def resolve(file_ref: str) -> tuple[Path, str, str, bool]:
    """'heldout:<name>' or 'upload:<id>' -> (path, display name, type, uploaded). ValueError otherwise.
    Only names on the held-out list and well-formed upload ids are accepted, so no path comes from the client."""
    kind, _, key = file_ref.partition(":")
    if kind == "heldout":
        if key not in heldout_files():
            raise ValueError(f"{key!r} is not a carOBD held-out file")
        return carobd.CAROBD_DIR / key, key, TYPE_LABEL[carobd.file_type(key)], False
    if kind == "upload":
        if not UPLOAD_ID.match(key) or not (UPLOAD_DIR / f"{key}.csv").is_file():
            raise ValueError(f"no upload {key!r}")
        meta = json.loads((UPLOAD_DIR / f"{key}.json").read_text())
        return UPLOAD_DIR / f"{key}.csv", meta["name"], "upload", True
    raise ValueError("file must be 'heldout:<name>' or 'upload:<id>'")


def _display_name(name: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    return (re.sub(r"[^A-Za-z0-9._-]", "_", base)[:64]) or "upload.csv"


def save_upload(name: str, data: bytes, now: float) -> dict:
    """Validate an uploaded carOBD CSV and store it as data/uploads/<id>.csv (+ <id>.json).
    The client's file name is only displayed; the stored name is a random id, so nothing is ever
    written outside UPLOAD_DIR. ValueError (with the reason) when the file is not usable."""
    import uuid

    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"file is {len(data) / 1e6:.1f} MB; the limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data.strip():
        raise ValueError("empty file")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:12]
    part, csv, meta_p = UPLOAD_DIR / f"{uid}.part", UPLOAD_DIR / f"{uid}.csv", UPLOAD_DIR / f"{uid}.json"
    part.write_bytes(data)
    try:
        try:
            df = carobd.load(part)                # the same loader as every carOBD file
        except Exception as e:                    # pandas parse errors, missing time column, ...
            raise ValueError(f"not a carOBD CSV: {type(e).__name__}: {e}") from e
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"missing carOBD columns: {', '.join(missing)}")
        cols = [c for c in REQUIRED_COLUMNS]
        num = df[cols].apply(pd.to_numeric, errors="coerce")
        bad = [c for c in cols if num[c].isna().any()]
        if bad:
            raise ValueError(f"non-numeric or empty values in: {', '.join(bad)}")
        if len(df) < 60:
            raise ValueError(f"{len(df)} rows; need at least 60")
        dur = carobd.duration_s(df)
        if dur < MIN_DURATION_S or df["t_s"].iloc[-1] < MIN_DURATION_S:
            raise ValueError(f"{dur:.0f} s of ENGINE_RUN_TIME; need at least {MIN_DURATION_S:.0f} s")
        part.replace(csv)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    meta = {"id": uid, "name": _display_name(name), "bytes": len(data), "rows": int(len(df)),
            "duration_s": round(dur, 1), "uploaded_at": now, "label": "REAL (uploaded, unverified)"}
    meta_p.write_text(json.dumps(meta))
    for old in list_uploads()[:-MAX_UPLOADS]:
        for ext in (".csv", ".json"):
            (UPLOAD_DIR / f"{old['id']}{ext}").unlink(missing_ok=True)
    return meta


# ---------------------------------------------------------------- witness 2 (Diljit's OBD classifier)

def load_obd_classifier():
    """Witness 2 for the OBD branch: Diljit's OBD classifier, DISABLED by default (D133: it failed
    sim-to-real, D126). `models.obd_classifier.load_witness()` returns a callable only with
    S720_OBD_CLASSIFIER=1 and a checkpoint, else None. The callable takes one
    OBD branch v1 window (the dict from models.obd.features.window_features, with map_resid_* and regime)
    and returns models.witness.WitnessOut: `models` = contract models[] entries with trained labels,
    `location` None (OBD is bank-wide)."""
    try:
        mod = importlib.import_module("models.obd_classifier")
    except ModuleNotFoundError:
        return None
    return mod.load_witness()


# ---------------------------------------------------------------- source

@dataclass
class _Segment:
    severity: float
    on: float                 # total file time (loops included), s
    off: float = math.inf


class CarObdReplay:
    name = NAME
    description = "Real carOBD log replay (held-out or uploaded), 1x / 10x / 30x"
    source = "real"
    sensors = ("obd",)
    real = True
    period = None

    def __init__(self, file_ref: str = DEFAULT_FILE, speed: float = 10, classifier="auto"):
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {SPEEDS}")
        path, self.file_name, self.file_type, self.uploaded = resolve(file_ref)
        self.file_ref = file_ref
        df = carobd.load(path)
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"missing carOBD columns: {', '.join(missing)}")
        self.df = df.reset_index(drop=True)
        self.t = self.df["t_s"].to_numpy(float)
        self.duration = float(self.t[-1]) + 1.0
        if self.t[-1] < MIN_DURATION_S:
            raise ValueError(f"the log is {self.t[-1]:.0f} s long; the OBD branch needs at least {MIN_DURATION_S:.0f} s")
        # each row's run start (first row of its run of repeated values), per channel: the PID refresh
        self._fresh = {}
        self._run_start = {}
        for name, (col, _) in CHANNELS.items():
            if col in self.df.columns:
                v = self.df[col].to_numpy(float)
                fresh = np.r_[True, np.diff(v) != 0]
                self._fresh[name] = fresh
                self._run_start[name] = np.maximum.accumulate(np.where(fresh, np.arange(len(v)), 0))

        branch = json.loads(OBD_BRANCH.read_text())
        self.map_model = MapModel(branch["map_model"]["coef"])
        self.healthy = HealthyModel.from_json(branch["anomaly"])
        self.rule_th = branch["rules"]
        self.classifier = load_obd_classifier() if classifier == "auto" else classifier

        self.speed = float(speed)
        self._wall0, self._ft0 = 0.0, 0.0        # file time = _ft0 + (wall - _wall0) * speed
        self._wall = 0.0                         # last wall time a tick asked for
        self._loop, self._j = 0, 0               # next OBD window: loop number, window index in the file
        self._obd_ft = None                      # total file time already sent on the obd channel
        self._segments: list[_Segment] = []
        self._last_ev: Evidence | None = None
        self._last_fields: dict = {}
        self._last_regime: str | None = None
        self.windows_done = 0

    # ------------------------------------------------------------ clock

    def _ft(self, wall: float) -> float:
        return self._ft0 + (wall - self._wall0) * self.speed

    @property
    def stream_t(self) -> float:
        """File time of the current tick within this pass of the file, s."""
        return self._ft(self._wall) % self.duration

    def set_speed(self, speed: float) -> None:
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {SPEEDS}")
        self._ft0, self._wall0 = self._ft(self._wall), self._wall
        self.speed = float(speed)

    # ------------------------------------------------------------ injection (lean_drift only)

    @property
    def injecting(self) -> bool:
        return bool(self._segments) and self._segments[-1].off == math.inf

    def inject(self, fault: str, cylinder, severity: float) -> None:
        if fault not in INJECTABLE:
            raise ValueError(f"the carOBD replay accepts {', '.join(INJECTABLE)} only (no crank or audio for {fault!r})")
        if not 0 < severity <= 1:
            raise ValueError("severity must be in (0, 1]")
        self.clear_faults()
        self._segments.append(_Segment(float(severity), self._ft(self._wall)))

    def clear_faults(self) -> None:
        if self.injecting:
            self._segments[-1].off = self._ft(self._wall)

    def _added_trim(self, tau: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(STFT, LTFT) added in percent at total file times tau."""
        st, lt = np.zeros(len(tau)), np.zeros(len(tau))
        for s in self._segments:
            on = (tau >= s.on) & (tau < s.off)
            if not on.any():
                continue
            dt = tau[on] - s.on
            total = 100.0 * s.severity / (4.0 - s.severity) * np.minimum(1.0, dt / LEAN_RAMP_S)
            l = total * (1.0 - np.exp(-dt / LEAN_TAU_LTFT_S))
            lt[on] += l
            st[on] += total - l
        return st, lt

    INJECTED_CHANNELS = ("stft_pct", "ltft_pct")   # what lean_drift modifies (carOBD has no lambda)

    def _values(self, name: str, idx: np.ndarray, loop: int, with_flag: bool = False):
        """Channel values at rows idx; with_flag: also whether any of them carries an injected addition."""
        col = CHANNELS[name][0]
        v = self.df[col].to_numpy(float)[idx]
        added = False
        if name in self.INJECTED_CHANNELS and self._segments:
            # the added trim at each value's own refresh time keeps the ~0.55 Hz staircase
            tau = loop * self.duration + self.t[self._run_start[name][idx]]
            st, lt = self._added_trim(tau)
            add = st if name == "stft_pct" else lt
            added = bool(np.any(add != 0))
            v = v + add
        return (v, added) if with_flag else v

    def _frame(self, lo: int, hi: int, loop: int) -> pd.DataFrame:
        win = self.df.iloc[lo:hi].copy()
        if self._segments:
            idx = np.arange(lo, hi)
            win[STFT] = self._values("stft_pct", idx, loop)
            win[LTFT] = self._values("ltft_pct", idx, loop)
        return win

    # ------------------------------------------------------------ labels

    @property
    def guidance_capability(self) -> str | None:
        """Operator guidance keyed by what this source can do (models/guidance.json "capability", D132)."""
        return "single_witness_real_obd" if self.classifier is None else None

    @property
    def source_label(self) -> str:
        base = "real_upload" if self.uploaded else "real_replay"
        return f"{base}_injected" if self.injecting else base

    def replay_info(self) -> dict:
        two = self.classifier is not None
        return {
            "file": self.file_name,
            "file_type": self.file_type,
            "uploaded": self.uploaded,
            "speed": self.speed,
            "position_s": round(self.stream_t, 1),
            "duration_s": round(self.duration, 1),
            "loop": int(self._ft(self._wall) // self.duration),
            "witnesses": ["physics", "anomaly", "classifier"] if two else ["physics", "anomaly"],
            "max_decision": "ALARM" if two else "ADVISORY",
            "note": ("No crank or audio in carOBD: no cylinder localisation, no fingerprint. "
                     + ("OBD classifier loaded: ALARM needs it and an OBD rule for 2 windows."
                        if two else "No OBD classifier loaded: single witness (OBD rules + healthy model), "
                                    "ADVISORY or UNCLASSIFIED at most, never ALARM.")),
        }

    # ------------------------------------------------------------ source interface

    def phase_at(self, t: float) -> Phase:
        return _RUNNING

    def live_sensors(self, t: float) -> set[str]:
        return set(self.sensors)

    def signal_frames(self, j: int, live: set[str]) -> list:
        return []          # no mic, accel or crank in carOBD

    def obd_sample(self, i: int) -> dict:
        return {}          # the runtime uses obd_channels() for this source

    def window(self, k: int) -> Window | None:
        self._wall = k * WINDOW_S
        ft = self._ft(self._wall)
        evs = []
        while True:
            t0 = self._j * HOP_S
            if t0 + OBD_WINDOW_S > self.t[-1] + 1e-9:          # this pass of the file is done
                self._loop, self._j = self._loop + 1, 0
                continue
            if self._loop * self.duration + t0 + OBD_WINDOW_S > ft:
                break
            lo, hi = np.searchsorted(self.t, t0), np.searchsorted(self.t, t0 + OBD_WINDOW_S)
            self._j += 1
            if hi - lo > 1 and (w := window_features(self._frame(lo, hi, self._loop), self.map_model)) is not None:
                evs.append(self._evaluate(w))
        if evs:
            *earlier, (ev, fields) = evs
            self._last_ev, self._last_fields = ev, fields
            self.windows_done += len(evs)
            return Window(round(self.stream_t, 3), _RUNNING, ev, self._fields(), backlog=[e for e, _ in earlier])
        ev = self._last_ev or Evidence(models=[], physics_rules=[], anomaly_score=None, anomaly_threshold=ANOMALY_LIMIT)
        return Window(round(self.stream_t, 3), _RUNNING, ev, self._fields(), fresh=False)

    def _evaluate(self, w: dict) -> tuple[Evidence, dict]:
        d, thr = self.healthy.distance(w), self.healthy.threshold(w)
        score = round(ANOMALY_LIMIT * d / thr, 4)
        pr = [{**r, "rule_basis": "obd"} for r in obd_rules.evaluate(w, self.rule_th)]   # contract 1.5 rule 13
        models, loc = [], None
        if self.classifier is not None:
            out = self.classifier(w)
            models, loc = out.models, out.location
        ev = Evidence(models=models, physics_rules=pr, anomaly_score=score, anomaly_threshold=ANOMALY_LIMIT,
                      classifier_location=loc)
        self._last_regime = w["regime"]
        fields = {"features_summary": {
            "total_trim_pct": round(w["total_trim"], 2), "stft_mean_pct": round(w["stft_mean"], 2),
            "ltft_mean_pct": round(w["ltft_mean"], 2), "trim_rule_max_pct": self.rule_th["total_trim_max"],
            "retard_events": w["retard_events"], "timing_mean_deg": round(w["timing_mean"], 2),
            "mahalanobis": round(d, 3), "mahalanobis_threshold": round(thr, 3),
            "window_t0_s": round(w["t0"], 1)},
            "models": models, "physics_rules": pr, "score": score}
        return ev, fields

    def _fields(self) -> dict:
        i = min(int(np.searchsorted(self.t, self.stream_t, side="right")) - 1, len(self.t) - 1)
        i = max(i, 0)
        row = self.df.iloc[i]
        f = self._last_fields
        reg = self._last_regime or regime(float(row["ENGINE_RPM"]), float(row["COOLANT_TEMPERATURE"]))
        stem = Path(self.file_name).stem if not self.uploaded else f"upload_{self.file_ref.split(':', 1)[1]}"
        return {
            "run_id": f"carobd_{stem}_p{int(self._ft(self._wall) // self.duration) + 1}",
            "source": "injected" if self.injecting else "real",
            "source_label": self.source_label,
            "audioset_pool": None,
            "engine_tag": None,
            "mode": "replay",
            "t": round(self.stream_t, 3),
            "operating_point": {"rpm": round(max(float(row["ENGINE_RPM"]), 0.0), 1),
                                "load_pct": round(min(max(float(row["ENGINE_LOAD"]), 0.0), 100.0), 1),
                                "regime": reg},
            "localisation": "none",
            "features_summary": f.get("features_summary", {}),
            "anomaly": {"score": f.get("score"), "threshold": ANOMALY_LIMIT, "per_location": []},
            "models": f.get("models", []),
            "physics_rules": f.get("physics_rules", []),
            "signature": {"crank_deg": [], "amplitude": [], "baseline_amplitude": []},
            "replay": self.replay_info(),
        }

    def obd_channels(self, i: int) -> dict | None:
        """Channel values that refreshed since the last obd tick, at their file times (this pass of the file).
        None when no row was logged in that span (a logger gap), {} when rows came but nothing changed."""
        ft = self._ft(i * 1.0)
        a = self._obd_ft
        self._obd_ft = ft
        first = a is None                              # first message: every channel's current value
        if first:
            a = ft
        out: dict[str, dict] = {}
        any_rows = False
        for loop in range(int(a // self.duration), int(ft // self.duration) + 1):
            base = loop * self.duration
            lo = int(np.searchsorted(self.t, a - base, side="right"))
            hi = int(np.searchsorted(self.t, ft - base, side="right"))
            if first:
                hi = max(hi, 1)
                lo = hi - 1
            if hi <= lo:
                continue
            any_rows = True
            idx = np.arange(lo, hi)
            for name, fresh in self._fresh.items():
                sel = idx if first else idx[fresh[idx]]
                if not len(sel):
                    continue
                v, added = self._values(name, sel, loop, with_flag=True)
                base = "real_upload" if self.uploaded else "real_replay"
                ch = out.setdefault(name, {"t": [], "v": [], "unit": CHANNELS[name][1],
                                           "source": "real_upload" if self.uploaded else "real_carobd",
                                           "source_label": base})
                if added:                              # 1.6: this channel's values carry the injection
                    ch["source_label"] = f"{base}_injected"
                ch["t"] += [round(float(x), 2) for x in self.t[sel]]
                ch["v"] += [round(float(x), 3) for x in v]
        return out if any_rows else None


def make(file_ref: str | None = None, speed: float = 10) -> CarObdReplay:
    return CarObdReplay(file_ref or DEFAULT_FILE, speed)
