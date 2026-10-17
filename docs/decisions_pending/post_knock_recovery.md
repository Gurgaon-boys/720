# Pending decisions: post-knock recovery (branch `fix/post-knock-recovery`)

These are not numbered yet. They get the next free D-numbers when this branch merges; `docs/DECISIONS.md` is untouched here. They extend the post-misfire recovery design (D144, D146) to knock.

## K1. Spark-timing enable condition after a confirmed knock (recovery window)

**Problem (finals smoke test, main c0a2407, tag finals-rc).** The live sequence was: simulator, knock on cyl 2 at p = 1.0, ALARM at 1.08 s, ACK, `POST /clear_faults`, NORMAL. Then, 4.6 s later, **UNCLASSIFIED ("Unknown pattern: inspect, do not clear", asset OFF_SPEC) for about 30 s** with no injection active, before NORMAL on its own.

**Reproduced offline** (`backend/tests/test_post_knock.py`; SimSource seed 0, the Evidence stepped through RuleLayer, ACK on ALARM):

| Knock stopped at | UNCLASSIFIED after the clear, gate off | With the gate |
|---|---|---|
| 26.2 s (1.2 s after injection, as in the smoke test) | **18.3 s** (27.0–31.0 s and 42.0–55.8 s) | 0 s; recovery ADVISORY until 88.8 s (62.6 s after the clear) |
| 40.0 s (after several knock bursts) | **44.7 s** (40.2–92.7 s) | 0 s; recovery ADVISORY until 97.8 s (57.8 s after the clear) |

**Cause: the anomaly witness, alone.** In every UNCLASSIFIED window the classifier says *healthy* and no physics rule fires. Only the healthy model's score is over its limit (0.51–0.67 against 0.5). Breaking the Mahalanobis distance down by feature shows two phases:

1. **0.8–5 s after the clear: the accelerometer sectors (12, 13).** Knock control pulls spark on the knocking cylinder (sim: 3° per detected knock, up to 12°) and gives it back at only 0.5°/s (`sim/params.py`, `knock_recover_deg_s`, "recovers slowly"). So cyl 2 burns late for up to 24 s, and the block vibration pattern moves with it.
2. **About 15–30 s after the clear (longer after a longer knock): `spark_retard_deg`, 70–90 % of the distance.** This classifier feature is the median spark advance over the 30 s *before* the OBD window minus its median *inside* the window (`dsp/obd_window.py`, `RETARD_LOOKBACK_S` = 60 s). It reads +1.5° to +2.5° while the window holds the knock period. After a longer knock it also reads −1.75° to −2° once the knock period has moved into the 30 s before the window. The healthy model never saw ECU knock recovery: on the healthy calibration engines `|spark_retard_deg|` never exceeds 0.5° (see below).

The witnesses do exactly what they were built to do. The engine really is running off its healthy spark map, and the 60 s OBD history really does hold the knock. What is wrong is the *reading*: right after a confirmed, cleared knock this is a known situation, not an "unknown pattern". It is the same kind of after-fault artefact as the post-misfire trims (D144).

**Decision.** This is a deterministic enable condition in the rule layer, in the same form as D144. No retraining, and no threshold changes.

- **Armed** by a *confirmed* knock: a two-witness knock `match`. Each new match re-arms it.
- **Ends** when spark timing has settled, or 90 s after the last confirmed knock window (`RECOVERY_MAX_S`, shared with D144), whichever comes first. Settled means both of:
  - `|spark_retard_deg| ≤ 0.5°` (`dsp.rules.SPARK_SETTLED_DEG`, from `Evidence.spark`).
  - At least **60 s** have passed since the last confirmed knock window (`KNOCK_RECOVERY_MIN_S` = `RETARD_LOOKBACK_S`). Until then the feature's 60 s of OBD history still holds the knock: it swings positive, through 0, then negative, and reads 0 in passing.
- **Inside the window:** an anomaly flag, a conflict or nothing at all shows as a **recovery ADVISORY** (fault null, no witness), never as UNCLASSIFIED. **No physics rule is gated**, unlike D144's trim rule, because knock recovery does not mimic another trained fault. A new knock, a misfire or a clog goes through as usual. The latch only counts `match` windows, and the gate changes only non-match windows, so **ALARMs cannot change**.
- **Precedence:** if both windows are open, the misfire one wins (`RuleLayer.recovering_from`), because it is also the one that gates the trim rule.

**Constants.**

- `SPARK_SETTLED_DEG` = 0.5° is the healthy band, measured on the calibration engines only. It used the `dsp/calibrate_trim.py` sim-check grid (engines 0–11, rpm 800 / 1500 / 2500, load 5 / 25 N·m, 120 s each, sim 2.1.1). That gave 3,234 windows at 1 s steps from 75 s on: `|spark_retard_deg|` p50 = 0°, p99 = max = 0.5°, which is one OBD quantisation step.
- `KNOCK_RECOVERY_MIN_S` = 60 s comes from the feature's definition and was not fitted. The first version used D144's 30 s with a one-sided `spark_retard_deg ≤ 0.5°`. That closed the window early (at 67.8 s) when the knock was stopped at 40 s, as the feature crossed zero on its way negative, and left 20.7 s of UNCLASSIFIED afterwards.
- No test, soak or held-out seed, and no carOBD file, was used for any of these.

**Cost.**

- **The recovery ADVISORY lasts longer than the UNCLASSIFIED it replaces:** about 58–63 s after the clear on the demo engine, against 18–45 s. It ends as soon as both conditions hold, and never more than 90 s after the last knock.
- **For 60–90 s after a confirmed knock, an unrelated anomaly (for example valve tick) shows as the recovery ADVISORY, not UNCLASSIFIED.** D144 accepts the same trade-off after a misfire. Trained faults still ALARM.
- **With knock still injected**, the ECU's retard suppresses it between bursts, so ALARMs come and go (≥ 3 episodes in 65 s on the demo engine). The windows between bursts used to read UNCLASSIFIED and now read "Recovering after knock". The ALARMs themselves are unchanged (`test_sustained_knock_alarms_exactly_as_before`).

**Evidence.** All SIM 2.1, column A.

- **Offline, demo engine** (`backend/tests/test_post_knock.py`):
  - The reproduction: ≥ 10 s of UNCLASSIFIED with the gate off, all of it fault null and no witness.
  - The fix: 0 s of UNCLASSIFIED after the clear, the same ALARMs, and the only changed windows are UNCLASSIFIED/NORMAL → recovery ADVISORY.
  - Sustained knock: the same ALARM episodes.
  - Clog and misfire runs: identical decisions, window for window.
- **Recorded L3 runs replayed with the gate off/on** (`python -m eval.post_knock_check` → `eval/results/post_knock_check.json`, misfire gate on in both). The recordings carry no spark advance, so every window runs its full 90 s here, an upper bound.
  - Healthy soaks (val_thr, soak_fresh2, soak_fresh3, soak_long; 640 runs, 32 engine-hours): **identical, window for window.**
  - Fault runs (120): **ALARM episodes 1,748 → 1,748.** Detection and latency are identical for every fault: clog 16/20, knock 20/20, misfire 20/20, valve tick 1, 7 and 20/20.
  - The 11,007 changed windows are all in the 20 (sustained) knock runs: UNCLASSIFIED → recovery ADVISORY 10,412, NORMAL → recovery ADVISORY 595.
- `eval/post_misfire_check.py` now pins the knock gate off, so it measures the misfire gate alone. Re-run, it reproduces its committed `post_misfire_check.json` exactly (apart from `created_at`).

## K2. Recovery guidance after knock

`models/guidance.json` `context` gains `recovering_after_knock`: "Recovering after knock: spark timing re-advancing". Inside the post-knock window the decision is the recovery ADVISORY, and `Runtime.guidance_context` names whichever recovery the rule layer is in (`recovering_after_{misfire|knock}`). ALARM, UNCLASSIFIED and sensor FAILURE keep their own lines (as in D146). `docs/RUN_DEMO.md` and `frontend/docs/DEMO_SCRIPT.md` now describe this instead of the old "UNCLASSIFIED for up to ~75 s" limitation.
