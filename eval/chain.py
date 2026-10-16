"""Record the full live chain once per run; replay the rule layer offline under any configuration.

record() runs sim 2.1 -> StreamFeatures -> 3 physics rules + fast classifier witness -> and stores, per
4-cycle window, everything the rule layer reads: the physics rules' outputs, the classifier's label / p /
cylinder (after the witness's abstain logic) and the healthy model's RAW Mahalanobis distance. The anomaly
score is 0.5 x distance / band threshold, so replay() can apply any per-band threshold and any UNCLASSIFIED
persistence to exactly the same windows (a before/after on identical runs). Replay builds the same
Evidence the live source builds and steps the real backend.rule_layer.RuleLayer.

levels() counts, per level, WINDOWS and EPISODES (contiguous runs of windows in that state), over the
counted windows (after warm-up). Levels:
  anomaly_flags            anomaly score over its limit (0.5)
  classifier_false_faults  the classifier witness names a fault (majority, p >= P_MIN)
  physics_windows          any physics rule fired
  advisory / unclassified / alarm   the rule layer's decision state
  advisory_persist2        single-witness ADVISORY only when the same witness named the same fault the window before
  alarms_raised            ALARM latch episodes (auto-ACKed, as an operator would, so later ones count)
  alarms_raised_startup    ALARMs raised during the uncounted warm-up

Recordings are cached per run (npz), so a restart skips finished runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from backend.rule_layer import Evidence, RuleLayer, assess, classifier_label
from dsp.rules import PER_LOCATION_THRESHOLD, all_rules, load_all
from eval.common import eval_engine
from models.classifier import BANDS, band_of
from models.make_dataset import CLASSES
from models.witness import ClassifierWitness
from models.features import StreamFeatures
from sim import EngineSim
from sim.params import Regimes

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "generated" / "chain"
RULES = ("misfire", "knock", "injector_clog")
BASIS = {"misfire": "crank_angle", "knock": "crank_angle", "injector_clog": "obd"}
LEVELS = ("anomaly_flags", "classifier_false_faults", "physics_windows", "advisory", "unclassified", "alarm",
          "advisory_persist2")


def _truth(win, fault: str | None) -> bool:
    """Is the injected fault physically present in this window?"""
    if fault is None:
        return False
    labs = win.labels
    if fault == "misfire":
        return any(any(lab["misfired"]) for lab in labs)
    if fault == "knock":
        return any(any(lab["knocked"]) for lab in labs)
    if fault == "valve_tick":
        return any(max(lab["valve_lash"]) > 0 for lab in labs)
    return any(max(lab["clog"]) > 0 for lab in labs)


def record(rng_name: str, i: int, *, fault: str | None = None, severity: float = 0.0, run_s: float = 180.0,
           warmup_s: float = 30.0, tag: str = "soak") -> Path:
    """Simulate one run on engine i of an evaluation seed range and store its per-window evidence.
    Healthy soak runs use the same operating-point draw as eval/soak_l2.py (seed tag 0x50AC)."""
    name = f"{fault or 'healthy'}_s{severity:.2f}_{i:03d}" if fault else f"run_{i:03d}"
    path = CACHE / tag / rng_name / f"{name}.npz"
    if path.exists():
        return path
    params, seed = eval_engine(rng_name, i)
    rng = np.random.default_rng([seed, 0x50AC if fault is None else 0xFA17])
    rpm, load = float(rng.uniform(700, 2950)), float(rng.uniform(3, 45))
    cyl = int(rng.integers(1, 5))
    sim = EngineSim(params, seed=seed, regimes=Regimes.eval(), purpose="eval")
    sim.set_operating_point(rpm_target=rpm, load_nm=load)
    sf, th, wit = StreamFeatures(), load_all(), ClassifierWitness(thresholds=None)
    cols = {k: [] for k in ("t_start", "t_end", "rpm", "valid", "d", "thr_ckpt", "label", "p", "loc", "rf", "rl",
                            "obd_n", "truth")}
    injected = fault is None
    for n in range(int(round((warmup_s + run_s) / 0.5))):
        if not injected and n * 0.5 >= warmup_s:
            sim.inject(fault, cyl, severity)
            injected = True
        for w in sf.push(sim.step(0.5)):
            rules = all_rules(w.features, w.obd, th)
            valid = bool(w.features["crank_valid"] and w.features["accel_valid"])
            o = wit(w.vector, w.features["rpm_mean"], valid, w.obd["n"])
            m = o.models[0] if o.models else {"label": None, "p": 0.0}
            cols["t_start"].append(w.win.t_start)
            cols["t_end"].append(w.win.t_end)
            cols["rpm"].append(w.features["rpm_mean"])
            cols["valid"].append(valid)
            cols["d"].append(np.nan if o.distance is None else o.distance)
            cols["thr_ckpt"].append(wit.model.maha[band_of(float(w.features["rpm_mean"]))]["threshold"] if wit.model else np.nan)
            cols["label"].append(-1 if m["label"] is None else CLASSES.index(m["label"]))
            cols["p"].append(m["p"])
            cols["loc"].append(o.location or 0)
            cols["rf"].append([r["fired"] for r in rules])
            cols["rl"].append([r["location"] or 0 for r in rules])
            cols["obd_n"].append(w.obd["n"])
            cols["truth"].append(_truth(w.win, fault) if w.win.t_start >= warmup_s else False)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"range": rng_name, "i": i, "seed": seed, "rpm": round(rpm, 1), "load_nm": round(load, 2),
            "fault": fault, "cylinder": cyl if fault else 0, "severity": severity, "warmup_s": warmup_s,
            "run_s": run_s, "inject_t": warmup_s if fault else None}
    np.savez_compressed(path, meta=json.dumps(meta), **{k: np.asarray(v) for k, v in cols.items()})
    return path


def load_rec(path: Path) -> dict:
    z = np.load(path)
    rec = {k: z[k] for k in z.files if k != "meta"}
    rec["meta"] = json.loads(str(z["meta"]))
    return rec


def scores(rec: dict, thresholds: dict | None) -> np.ndarray:
    """Anomaly score per window (NaN where the score is null: sensor dropout or no model)."""
    thr = rec["thr_ckpt"] if thresholds is None else np.array([thresholds[band_of(float(r))] for r in rec["rpm"]])
    s = PER_LOCATION_THRESHOLD * rec["d"] / thr
    return np.where(rec["valid"], s, np.nan)


def replay(rec: dict, thresholds: dict | None = None, unclassified_windows: int = 2,
           corroborate_windows: int | None = None) -> dict:
    """Step the real rule layer over a recording. thresholds: {band: distance threshold}; None = the
    checkpoint's pooled-window thresholds (the configuration before L3)."""
    sc = scores(rec, thresholds)
    layer = (RuleLayer(unclassified_windows=unclassified_windows) if corroborate_windows is None else
             RuleLayer(unclassified_windows=unclassified_windows, corroborate_windows=corroborate_windows))
    n = len(sc)
    out = {k: np.zeros(n, dtype=bool) for k in ("counted", "anomaly_flags", "classifier_false_faults", "physics_windows",
                                                 "advisory", "unclassified", "alarm", "alarm_raised", "advisory_persist2",
                                                 "alarm_raised_startup", "match")}
    out["fault"] = np.array([None] * n, dtype=object)
    prev_single = None
    warm = rec["meta"]["warmup_s"]
    for k in range(n):
        lab = int(rec["label"][k])
        models = [] if lab < 0 else [{"model": "SVM", "label": CLASSES[lab], "p": float(rec["p"][k])}]
        rules = [{"rule": r, "fired": bool(rec["rf"][k][j]), "location": int(rec["rl"][k][j]) or None,
                  "rule_basis": BASIS[r]} for j, r in enumerate(RULES)]
        s = None if np.isnan(sc[k]) else round(float(sc[k]), 4)
        ev = Evidence(models, rules, s, PER_LOCATION_THRESHOLD, classifier_location=int(rec["loc"][k]) or None)
        had = layer.alarm is not None
        d = layer.step(ev)
        st = d["state"]
        out["fault"][k] = d["fault"]
        if rec["t_start"][k] < warm:
            out["alarm_raised_startup"][k] = st == "ALARM" and not had
        else:
            a = assess(ev)
            out["counted"][k] = True
            out["anomaly_flags"][k] = s is not None and s > PER_LOCATION_THRESHOLD
            cls = classifier_label(models)
            out["classifier_false_faults"][k] = cls not in (None, "healthy")
            out["physics_windows"][k] = any(r["fired"] for r in rules)
            out["advisory"][k] = st == "ADVISORY"
            out["unclassified"][k] = st == "UNCLASSIFIED"
            out["alarm"][k] = st == "ALARM"
            out["alarm_raised"][k] = st == "ALARM" and not had
            out["match"][k] = a.kind == "match"
            single = (tuple(a.witnesses), a.fault) if a.kind == "advisory" else None
            out["advisory_persist2"][k] = st == "ADVISORY" and (single is None or single == prev_single)
            prev_single = single
        if layer.alarm is not None and not layer.alarm.acked:
            layer.ack(layer.alarm.alarm_id, "replay", float(rec["t_end"][k]))
    return out


def episodes(mask: np.ndarray) -> int:
    """Number of contiguous runs of True."""
    m = np.asarray(mask, dtype=bool)
    return int(m[0] + np.sum(m[1:] & ~m[:-1])) if len(m) else 0


def levels(rep: dict) -> dict:
    """Windows and episodes per level for one replayed run (counted windows only)."""
    c = rep["counted"]
    out = {lv: {"windows": int(rep[lv][c].sum()), "episodes": episodes(rep[lv][c])} for lv in LEVELS}
    out["alarms_raised"] = {"windows": int(rep["alarm_raised"][c].sum()), "episodes": int(rep["alarm_raised"][c].sum())}
    out["alarms_raised_startup"] = {"windows": int(rep["alarm_raised_startup"].sum()),
                                    "episodes": int(rep["alarm_raised_startup"].sum())}
    out["counted_windows"] = int(c.sum())
    return out


def total(per_run: list[dict]) -> dict:
    keys = [k for k in per_run[0] if k != "counted_windows"]
    tot = {k: {u: sum(r[k][u] for r in per_run) for u in ("windows", "episodes")} for k in keys}
    tot["counted_windows"] = sum(r["counted_windows"] for r in per_run)
    return tot


def first_time(rep: dict, rec: dict, mask_key: str, fault: str | None = None) -> float | None:
    """Seconds from injection to the first counted window (by t_end) in that state (and naming that fault)."""
    m = rep[mask_key] & rep["counted"]
    if fault is not None:
        m = m & np.array([f == fault for f in rep["fault"]])
    idx = np.flatnonzero(m)
    return None if not len(idx) else round(float(rec["t_end"][idx[0]] - rec["meta"]["inject_t"]), 3)


def checkpoint_thresholds() -> dict:
    from models.classifier import META
    return {b: v["threshold"] for b, v in json.loads(META.read_text())["healthy_model"]["bands"].items()}


assert [b for b, _, _ in BANDS] == ["idle", "mid", "high"]
