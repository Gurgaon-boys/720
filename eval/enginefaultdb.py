"""EngineFaultDB: does a classifier learn the fault, or the recording session?

REAL lab engine (C14NE), 4 sessions, faults induced on a test bench. Each class is one contiguous
block of rows = one session, so "class" and "session" are the same variable. Splits come only from
data/splits/enginefaultdb_split.json (committed before this script was first run).

  a      random-row split, all 14 variables          (what most published numbers use)
  b      temporal split, all 14 variables
  c_op   temporal split, ONLY operating point:  RPM, TPS, MAP, Speed, Power, Force
  c_comb temporal split, ONLY combustion:       Lambda, AFR, O2, CO, HC, CO2
  (+ a_op / a_comb: the same two subsets on the random split; *_3cls: classes 0, 1, 2 only, dropping
   class 3, which is a noised copy of class 2; lean_vs_lowv: classes 2 vs 3 only)

Models: random forest (seeds 0, 1, 2) and standardised multinomial logistic regression (one fit,
deterministic). Accuracy has a Wilson 95% CI over test ROWS, which is too narrow because neighbouring
rows are near-duplicates; accuracy and macro-F1 also get a block-bootstrap 95% CI that resamples whole
operating-point segments (data/enginefaultdb.py:segments), the honest unit. Wilson does not apply to
macro-F1 (not a proportion), so macro-F1 carries only the block bootstrap.

Physics check: per class vs class 0 (normal), direction of Lambda, AFR, CO, HC, O2, raw and
RPM-matched (250 rpm bins), against the expected direction for the fault.

Writes eval/results/enginefaultdb.json and docs/figures/enginefaultdb_confound.png.
Usage:  python -m eval.enginefaultdb
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from data import enginefaultdb as efdb
from data import enginefaultdb_split as efsplit
from data._common import git_commit, utc_now_iso, write_json
from eval.stats import wilson

ROOT = Path(__file__).resolve().parents[1]
SPLIT = ROOT / "data" / "splits" / "enginefaultdb_split.json"
OUT = ROOT / "eval" / "results" / "enginefaultdb.json"
FIG = ROOT / "docs" / "figures" / "enginefaultdb_confound.png"
SEEDS = (0, 1, 2)
N_BOOT = 1000
DATA_KIND = "REAL lab engine (C14NE), 4 sessions, faults induced on a test bench"

EXPERIMENTS = {
    "a": ("random", efdb.VARIABLES),
    "b": ("temporal", efdb.VARIABLES),
    "c_op": ("temporal", efdb.OPERATING),
    "c_comb": ("temporal", efdb.COMBUSTION),
    "a_op": ("random", efdb.OPERATING),
    "a_comb": ("random", efdb.COMBUSTION),
}
FIG_ORDER = [("a", "a  random rows\nall variables"), ("a_op", "a  random rows\noperating point only"),
             ("b", "b  temporal\nall variables"), ("c_op", "c  temporal\noperating point only"),
             ("c_comb", "c  temporal\ncombustion only")]

# expected direction vs normal: +1 up, -1 down, 0 no expectation
EXPECT = {
    1: {"Lambda": -1, "AFR": -1, "CO": +1, "HC": +1, "O2": -1},   # rich
    2: {"Lambda": +1, "AFR": +1, "CO": -1, "HC": 0, "O2": +1},    # lean
    3: {"Lambda": 0, "AFR": 0, "CO": 0, "HC": +1, "O2": 0},       # low voltage: HC up if misfiring
}
PHYS_VARS = ["Lambda", "AFR", "CO", "HC", "O2"]
NEGLIGIBLE_D = 0.1  # |difference| / normal-class sd below this counts as no change


def models(seed: int):
    return {"rf": RandomForestClassifier(n_estimators=200, min_samples_leaf=1, n_jobs=-1, random_state=seed),
            "lr": make_pipeline(StandardScaler(), LogisticRegression(max_iter=5000))}


def _cm_metrics(cm: np.ndarray) -> tuple[float, float]:
    tp = np.diag(cm).astype(float)
    acc = tp.sum() / cm.sum()
    denom = cm.sum(0) + cm.sum(1)  # 2TP + FP + FN per class
    f1 = np.where(denom > 0, 2 * tp / np.maximum(denom, 1), 0.0)
    present = cm.sum(1) > 0  # macro over classes present in the (resampled) truth, as sklearn does with labels
    return float(acc), float(f1[present | (cm.sum(0) > 0)].mean())


def block_bootstrap(y, preds: list[np.ndarray], seg, labels, rng) -> dict:
    """95% CI of mean accuracy and macro-F1 over models, resampling whole segments within each class."""
    segs = np.unique(seg)
    pos = {s: i for i, s in enumerate(segs)}
    si = np.array([pos[s] for s in seg])
    L = len(labels)
    yi = np.searchsorted(labels, y)
    # per-segment confusion matrices, one stack per model
    cms = []
    for p in preds:
        c = np.zeros((len(segs), L, L), int)
        np.add.at(c, (si, yi, np.searchsorted(labels, p)), 1)
        cms.append(c)
    seg_class = np.array([y[seg == s][0] for s in segs])
    by_class = [np.flatnonzero(seg_class == k) for k in labels if np.any(seg_class == k)]
    accs, f1s = [], []
    for _ in range(N_BOOT):
        pick = np.concatenate([rng.choice(ix, size=len(ix), replace=True) for ix in by_class])
        m = [_cm_metrics(c[pick].sum(0)) for c in cms]
        accs.append(np.mean([a for a, _ in m]))
        f1s.append(np.mean([f for _, f in m]))
    q = lambda v: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]
    return {"accuracy": q(accs), "macro_f1": q(f1s), "n_boot": N_BOOT,
            "segments_in_test": {str(k): int(np.sum(seg_class == k)) for k in labels}}


def summarise(y, preds: list[np.ndarray], seg, labels, rng) -> dict:
    n = len(y)
    accs = [accuracy_score(y, p) for p in preds]
    f1s = [f1_score(y, p, average="macro") for p in preds]
    acc = float(np.mean(accs))
    k = int(round(acc * n))
    cm = sum(confusion_matrix(y, p, labels=labels) for p in preds)
    recall = {}
    for i, lab in enumerate(labels):
        kk, nn = int(cm[i, i]), int(cm[i].sum())
        recall[str(lab)] = {"rate": round(kk / nn, 4), "wilson95": [round(v, 4) for v in wilson(kk, nn)],
                            "note": "pooled over seeds; n = rows x seeds"} if nn else None
    return {
        "n_test_rows": n, "n_models": len(preds),
        "accuracy": round(acc, 4), "accuracy_per_seed": [round(a, 4) for a in accs],
        "accuracy_wilson95_rows": [round(v, 4) for v in wilson(k, n)],
        "macro_f1": round(float(np.mean(f1s)), 4), "macro_f1_per_seed": [round(f, 4) for f in f1s],
        "block_bootstrap95": block_bootstrap(y, preds, seg, np.asarray(labels), rng),
        "recall_per_class": recall,
        "confusion_matrix": {"labels": [int(l) for l in labels], "rows_true_cols_pred": cm.tolist(),
                             "summed_over_models": len(preds)},
    }


def run(df, split_name, cols, masks, seg, labels=(0, 1, 2, 3)) -> dict:
    tr, te = masks[split_name]
    keep = np.isin(df[efdb.LABEL].to_numpy(), labels)
    tr, te = tr & keep, te & keep
    X = df[cols].to_numpy(float)
    y = df[efdb.LABEL].to_numpy()
    out = {"split": split_name, "variables": list(cols), "classes": list(labels),
           "n_train_rows": int(tr.sum()), "majority_baseline": round(float(np.bincount(y[te]).max() / te.sum()), 4)}
    rng = np.random.default_rng(720)
    rf_preds = [models(s)["rf"].fit(X[tr], y[tr]).predict(X[te]) for s in SEEDS]
    out["rf"] = summarise(y[te], rf_preds, seg[te], list(labels), rng)
    lr_pred = models(0)["lr"].fit(X[tr], y[tr]).predict(X[te])
    out["lr"] = summarise(y[te], [lr_pred], seg[te], list(labels), rng)
    return out


def physics(df) -> dict:
    y = df[efdb.LABEL].to_numpy()
    rpm = df["RPM"].to_numpy(float)
    bins = np.floor(rpm / 250).astype(int)
    norm = y == 0
    out = {}
    for k, exp in EXPECT.items():
        fk = y == k
        res = {}
        for v in PHYS_VARS:
            x = df[v].to_numpy(float)
            sd = x[norm].std()
            raw = x[fk].mean() - x[norm].mean()
            diffs, w = [], []
            for b in np.unique(bins[fk]):
                a, c = fk & (bins == b), norm & (bins == b)
                if a.sum() >= 50 and c.sum() >= 50:
                    diffs.append(x[a].mean() - x[c].mean())
                    w.append(min(a.sum(), c.sum()))
            matched = float(np.average(diffs, weights=w)) if diffs else float("nan")
            d = matched / sd
            sign = 0 if abs(d) < NEGLIGIBLE_D else int(np.sign(d))
            e = exp[v]
            verdict = ("no_expectation" if e == 0 else "negligible_change" if sign == 0
                       else "consistent" if sign == e else "inconsistent")
            res[v] = {"normal_mean": round(float(x[norm].mean()), 4), "class_mean": round(float(x[fk].mean()), 4),
                      "raw_diff": round(float(raw), 4), "rpm_matched_diff": round(matched, 4),
                      "rpm_matched_diff_over_normal_sd": round(d, 3),
                      "raw_and_matched_agree_in_sign": bool(np.sign(raw) == np.sign(matched)),
                      "expected": {1: "up", -1: "down", 0: "none"}[e], "verdict": verdict}
        out[str(k)] = {"label": efdb.LABELS[k], "rpm_bins_used": len(w), "variables": res}
    lam = df["Lambda"].to_numpy(float)
    out["lambda_gt_1_frac"] = {str(k): round(float(np.mean(lam[y == k] > 1)), 4) for k in efdb.LABELS}
    # class 3 vs its source (class 2): is there any low-voltage signature at all?
    f2, f3 = y == 2, y == 3
    out["3_vs_2"] = {v: {"diff": round(float(df[v][f3].mean() - df[v][f2].mean()), 4),
                         "diff_over_class2_sd": round(float((df[v][f3].mean() - df[v][f2].mean()) / df[v][f2].std()), 4)}
                     for v in PHYS_VARS}
    out["_rule"] = (f"verdict on the RPM-matched difference (250 rpm bins, >= 50 rows each side, weighted by the "
                    f"smaller count); |diff| / normal sd < {NEGLIGIBLE_D} counts as negligible")
    return out


def figure(res: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, ink2, grid, rf_c, lr_c = "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6", "#eb6834"
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.6), sharey=True)
    xs = np.arange(len(FIG_ORDER))
    rows = [("", "All 4 classes"), ("_3cls", "Classes 0, 1, 2 only (class 3, a noised copy of class 2, dropped)")]
    for r, (suf, row_title) in enumerate(rows):
        for c, metric in enumerate(("accuracy", "macro_f1")):
            ax = axes[r, c]
            ex = [res[k + suf] for k, _ in FIG_ORDER]
            rf = [e["rf"][metric] for e in ex]
            lr = [e["lr"][metric] for e in ex]
            bb = [e["rf"]["block_bootstrap95"][metric] for e in ex]
            ax.bar(xs, rf, width=0.55, color=rf_c, label="random forest, mean of 3 seeds", zorder=2)
            ax.errorbar(xs, rf, yerr=np.array([[v - lo, hi - v] for v, (lo, hi) in zip(rf, bb)]).T, fmt="none",
                        ecolor=ink, elinewidth=1.4, capsize=5, zorder=3, label="95% CI, segment block bootstrap")
            if metric == "accuracy":
                wl = [e["rf"]["accuracy_wilson95_rows"] for e in ex]
                ax.errorbar(xs + 0.14, rf, yerr=np.array([[v - lo, hi - v] for v, (lo, hi) in zip(rf, wl)]).T,
                            fmt="none", ecolor=ink2, elinewidth=1, capsize=3, zorder=3,
                            label="95% Wilson CI over rows (too narrow: rows are not independent)")
            ax.scatter(xs, lr, s=60, marker="D", color=lr_c, edgecolor="white", linewidth=1.5, zorder=4,
                       label="logistic regression")
            for x, v in zip(xs, rf):
                ax.text(x - 0.3, v + 0.012, f"{v:.2f}", ha="right", va="bottom", fontsize=9, color=ink)
            maj = ex[0]["majority_baseline"]
            ax.axhline(maj, color=ink2, lw=1, ls=(0, (4, 3)), zorder=1)
            ax.text(-0.45, maj - 0.012, f"majority class {maj:.2f}", ha="left", va="top", fontsize=8, color=ink2, zorder=5,
                    bbox={"facecolor": "#fcfcfb", "edgecolor": "none", "pad": 1.5})
            ax.set_xticks(xs, [lab for _, lab in FIG_ORDER] if r == 1 else [""] * len(xs), fontsize=8.5, color=ink)
            ax.set_title(f"{'Accuracy' if metric == 'accuracy' else 'Macro-F1'}  |  {row_title}", fontsize=10,
                         color=ink, loc="left")
            ax.set_ylim(0, 1.1)
            ax.grid(axis="y", color=grid, lw=0.8, zorder=0)
            for sp in ("top", "right"):
                ax.spines[sp].set_visible(False)
            for sp in ("left", "bottom"):
                ax.spines[sp].set_color(grid)
            ax.tick_params(colors=ink2)
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper left", bbox_to_anchor=(0.01, 0.955), ncol=4, fontsize=8, frameon=False)
    fig.suptitle(f"EngineFaultDB: fault or session?   {DATA_KIND}", fontsize=12, color=ink, x=0.01, ha="left")
    fig.text(0.01, 0.005, "Each class is one recording session. Test rows: random split 14,000 (4 classes) / 10,500 "
             "(3 classes); temporal split last 25% of each class block. Source: eval/results/enginefaultdb.json",
             fontsize=7.5, color=ink2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.93))
    FIG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG, dpi=130, facecolor="#fcfcfb")
    plt.close(fig)


def main() -> None:
    df = efdb.load()
    split = json.loads(SPLIT.read_text())
    y = df[efdb.LABEL].to_numpy()
    masks = efsplit.masks(split, y)
    seg = efdb.segments(df)
    res = {}
    for name, (sp, cols) in EXPERIMENTS.items():
        res[name] = run(df, sp, cols, masks, seg)
        print(f"{name:7} {sp:8} RF acc {res[name]['rf']['accuracy']:.3f} F1 {res[name]['rf']['macro_f1']:.3f}  "
              f"LR acc {res[name]['lr']['accuracy']:.3f} F1 {res[name]['lr']['macro_f1']:.3f}")
    for name in ("a", "b", "c_op", "c_comb", "a_op"):
        sp, cols = EXPERIMENTS[name]
        res[f"{name}_3cls"] = run(df, sp, cols, masks, seg, labels=(0, 1, 2))
        print(f"{name}_3cls RF acc {res[name + '_3cls']['rf']['accuracy']:.3f}  LR acc {res[name + '_3cls']['lr']['accuracy']:.3f}")
    res["lean_vs_lowv"] = run(df, "temporal", efdb.VARIABLES, masks, seg, labels=(2, 3))
    res["lean_vs_lowv_random"] = run(df, "random", efdb.VARIABLES, masks, seg, labels=(2, 3))
    for name in ("lean_vs_lowv", "lean_vs_lowv_random"):
        print(f"{name}: RF acc {res[name]['rf']['accuracy']:.3f}  LR acc {res[name]['lr']['accuracy']:.3f}")
    phys = physics(df)
    op, comb = res["c_op"]["rf"]["accuracy"], res["c_comb"]["rf"]["accuracy"]
    out = {
        "created_at": utc_now_iso(), "git_commit": git_commit(), "produced_by": "eval/enginefaultdb.py",
        "data_kind": DATA_KIND, "source": efdb.SOURCE, "citation": efdb.CITATION,
        "labels": {str(k): v for k, v in efdb.LABELS.items()}, "labels_confirmed_against_paper": efdb.LABELS_CONFIRMED,
        "split_file": "data/splits/enginefaultdb_split.json",
        "split_created_at": split["created_at"], "split_git_commit": split["git_commit"],
        "ci_note": ("accuracy_wilson95_rows treats rows as independent and is too narrow (neighbouring rows are "
                    "near-duplicates); block_bootstrap95 resamples whole operating-point segments and is the one to quote"),
        "experiments": res,
        "physics_check": phys,
        "headline": {
            "random_all_rf_accuracy": res["a"]["rf"]["accuracy"],
            "random_operating_only_rf_accuracy": res["a_op"]["rf"]["accuracy"],
            "temporal_operating_only_rf_accuracy": op, "temporal_combustion_only_rf_accuracy": comb,
            "temporal_operating_only_scores_high": bool(op >= 0.6),
            "accuracy_ceiling_from_class3_copy": round(float(np.mean(np.isin(y, (0, 1))) + 0.5 * np.mean(np.isin(y, (2, 3)))), 4),
            "statement": (
                "This dataset cannot separate fault from session: each class is one recording session, so every "
                "split, temporal or random, tests on the training session. On the random-row split the operating "
                f"point alone (no exhaust chemistry) scores {res['a_op']['rf']['accuracy']:.3f}, the same as all "
                f"variables ({res['a']['rf']['accuracy']:.3f}), so a random-split accuracy measures session "
                "recognition. On the temporal split, operating-point-only "
                + ("also scores high." if op >= 0.6 else
                   f"falls to {op:.3f} and combustion-only to {comb:.3f}: neither generalises to the later "
                   "operating points of the same session.")
                + " Class 3 (low voltage) is a noised copy of class 2 (lean), so 2 vs 3 is a coin flip."),
            "class3_is_copy_of_class2": True,
        },
    }
    write_json(OUT, out)
    figure(res)
    print(f"Wrote {OUT.relative_to(ROOT)} and {FIG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
