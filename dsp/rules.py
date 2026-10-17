"""Physics rules: the rule layer's physics witness (plan section 8, step 5).

Every rule has two versions (DECISIONS.md D13):
  crank_angle   simulator / injected data, which carry a crank reference
  firing_event  real audio, relative to detected firing pulses (not built yet)
Each rule returns a contract `physics_rules` entry; the `rule` field is the fault name.

Thresholds come from models/thresholds/misfire_half_order.json. From sim v2 they are per rpm band
(normal at idle is not normal at 3000 rpm, plan section 10); the v1 file format (one global
threshold, kept as misfire_half_order_v1.json) is still accepted.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

THRESHOLDS_DIR = Path(__file__).resolve().parents[1] / "models" / "thresholds"
MISFIRE_HALF_ORDER = THRESHOLDS_DIR / "misfire_half_order.json"
MISFIRE_HALF_ORDER_V1 = THRESHOLDS_DIR / "misfire_half_order_v1.json"
KNOCK_BAND_ENERGY = THRESHOLDS_DIR / "knock_band_energy.json"
TRIM_DRIFT = THRESHOLDS_DIR / "trim_drift.json"
PER_LOCATION_THRESHOLD = 0.5


def load_thresholds(path: Path = MISFIRE_HALF_ORDER) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def band_for(thresholds: dict, rpm: float | None, map_kpa: float | None = None) -> dict:
    """The threshold set for an operating point: the rpm x load (MAP) band containing it, else the
    nearest one. An rpm outside every calibrated band (the held-out 3000-3500 rpm) uses the closest
    band: extrapolation, reported separately. A missing MAP uses the low-load band."""
    bands = thresholds.get("bands")
    if not bands:
        return thresholds                                   # v1 format: one global threshold
    rpm = bands[0]["rpm_lo"] if rpm is None else rpm
    m = 0.0 if map_kpa is None else map_kpa

    def dist(b):
        dr = 0.0 if b["rpm_lo"] <= rpm < b["rpm_hi"] else min(abs(rpm - b["rpm_lo"]), abs(rpm - b["rpm_hi"]))
        dm = 0.0 if b.get("map_lo", -1e9) <= m < b.get("map_hi", 1e9) else 1.0
        return (dr, dm)

    return min(bands, key=dist)


def misfire_half_order(features: dict, thresholds: dict) -> dict:
    """Misfire rule, crank-angle version.

    Fires when the window's half-order ratio of crank speed exceeds the threshold calibrated on
    healthy simulator runs for that rpm band. Location = the cylinder with the largest crank roughness.
    """
    if not features.get("crank_valid", True):
        # crank data missing (sensor dropout): the rule can't run, so it can't be a witness
        return {"rule": "misfire", "fired": False, "location": None, "rule_basis": "crank_angle"}
    band = band_for(thresholds, features.get("rpm_mean"), features.get("map_kpa"))
    feature = thresholds.get("feature", "half_order_ratio")     # v1 files predate the key
    fired = bool(features[feature] > band[feature]["threshold"])
    location = int(np.argmax(features["roughness"]) + 1) if fired else None
    return {"rule": "misfire", "fired": fired, "location": location, "rule_basis": "crank_angle"}


def misfire_firing_event(pulse_times: np.ndarray, pulse_strengths: np.ndarray, thresholds: dict) -> dict:
    """Misfire rule, firing-event version for real audio: a missing or weak pulse in the sequence
    of detected firing pulses. Reports no location (real clips have no cam/TDC reference).

    Returns {"rule": "misfire", "fired": ..., "location": None, "rule_basis": "firing_event"}.
    """
    raise NotImplementedError("firing-event misfire rule: needs the firing-pulse detector (dsp, later)")


def per_location(roughness: np.ndarray, thresholds: dict, rpm: float | None = None,
                 map_kpa: float | None = None) -> list[float]:
    """Roughness scaled for the dashboard bars: 0..1, with the band's calibrated limit at 0.5."""
    lim = band_for(thresholds, rpm, map_kpa)["roughness"]["threshold"]
    return [round(float(np.clip(r / (2 * lim), 0.0, 1.0)), 3) for r in roughness]


def knock_band_energy(features: dict, thresholds: dict) -> dict:
    """Knock rule, crank-angle version: a cylinder's knock-band ringing energy right after its TDC
    (features["knock_db"]) exceeds that cylinder's healthy baseline for the rpm band by more than the
    calibrated excess. Location = the cylinder with the largest excess."""
    if not features.get("accel_valid", True):
        return {"rule": "knock", "fired": False, "location": None, "rule_basis": "crank_angle"}
    band = band_for(thresholds, features.get("rpm_mean"))["knock_db"]
    excess = np.asarray(features["knock_db"]) - np.asarray(band["baseline_per_cylinder"])
    fired = bool(excess.max() > band["threshold_excess_db"])
    return {"rule": "knock", "fired": fired, "location": int(np.argmax(excess) + 1) if fired else None,
            "rule_basis": "crank_angle"}


def trim_drift(obd: dict, thresholds: dict) -> dict:
    """Injector-clog rule, OBD version: the bank's total fuel trim (1 + STFT)(1 + LTFT) - 1, averaged
    over the last 30 s of OBD, sits above the healthy range measured on carOBD calibration files.

    Trims are bank-wide, so the rule names no cylinder (the classifier's weak-cylinder evidence does).
    It doesn't run while the ECU is pulling spark (knock control), so knock can't read as clog, nor
    without a full 30 s of OBD. obd: {"trim_mean_pct", "n", "spark_retard_deg"} from dsp.obd_window."""
    off = {"rule": "injector_clog", "fired": False, "location": None, "rule_basis": "obd"}
    if obd.get("n", 0) < thresholds["min_samples"] or obd.get("spark_retard_deg", 0.0) > thresholds["retard_exclude_deg"]:
        return off
    return {**off, "fired": bool(obd["trim_mean_pct"] > thresholds["threshold_pct"])}


# Post-misfire recovery gate (backend/rule_layer.py, D144).
# The trims count as settled when STFT and LTFT (5 s means) are within +-TRIM_SETTLED_PCT and the trim rule's own
# 30 s window is back under its threshold. 2.5 % ~ the healthy simulator's 99th percentile of total trim
# (models/thresholds/trim_drift.json sim_check p99 = 2.21 %), rounded up.
TRIM_SETTLED_PCT = 2.5


def trim_recovery(obd: dict, thresholds: dict) -> dict | None:
    """What the trims say now, for the rule layer's post-misfire recovery gate (None without a full OBD window).
    removing_fuel: the ECU is taking fuel out (STFT <= 0 over the last 5 s), i.e. trims re-adapting back after a
    misfire made the O2 sensor read lean. A clog does the opposite: STFT stays positive (adding fuel).
    settled: STFT and LTFT are back in the healthy band and the 30 s trim mean is under the rule threshold."""
    if obd.get("n", 0) < thresholds["min_samples"] or "stft_recent_pct" not in obd:
        return None
    s, l = obd["stft_recent_pct"], obd["ltft_recent_pct"]
    return {"removing_fuel": bool(s <= 0.0),
            "settled": bool(abs(s) <= TRIM_SETTLED_PCT and abs(l) <= TRIM_SETTLED_PCT
                            and obd["trim_mean_pct"] <= thresholds["threshold_pct"])}


# Post-knock recovery gate (backend/rule_layer.py, pending K1). Knock control pulls spark on the knocking cylinder
# (sim: 3 deg per knock, up to 12 deg) and gives it back slowly (0.5 deg/s); the 30 s OBD window's spark_retard_deg
# (median spark advance over the 30 s before minus inside) holds that. Healthy calibration engines (dsp/calibrate_trim.py
# grid, engines 0-11, sim 2.1.1, 3234 windows at 1 s steps): |spark_retard_deg| <= 0.5 deg in every window (p50 0,
# max 0.5 = one OBD quantisation step), so 0.5 deg is the healthy band.
SPARK_SETTLED_DEG = 0.5


def spark_recovery(obd: dict, thresholds: dict) -> dict | None:
    """What spark timing says now, for the rule layer's post-knock recovery gate (None without a full OBD window).
    settled: the 30 s window's spark advance matches the 30 s before it (|spark_retard_deg| in the healthy band)."""
    if obd.get("n", 0) < thresholds["min_samples"] or "spark_retard_deg" not in obd:
        return None
    return {"settled": bool(abs(obd["spark_retard_deg"]) <= SPARK_SETTLED_DEG)}


def load_all() -> dict:
    """Every physics rule's thresholds, by fault name."""
    return {"misfire": load_thresholds(MISFIRE_HALF_ORDER), "knock": load_thresholds(KNOCK_BAND_ENERGY),
            "injector_clog": load_thresholds(TRIM_DRIFT)}


def all_rules(features: dict, obd: dict, th: dict) -> list[dict]:
    """The physics witnesses for one window, in contract order (misfire, knock, injector_clog)."""
    return [misfire_half_order(features, th["misfire"]), knock_band_energy(features, th["knock"]),
            trim_drift(obd, th["injector_clog"])]
