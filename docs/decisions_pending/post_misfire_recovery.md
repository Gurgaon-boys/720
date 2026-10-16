# Pending decisions: post-misfire recovery (branch `fix/post-misfire-recovery`)

These are not numbered yet. They get the next free D-numbers when this branch merges; `docs/DECISIONS.md` is untouched here.

## P1. Fuel-trim enable condition after a confirmed misfire (recovery window)

**Problem (Livana, reproduced).** The sequence was: simulator, misfire on cyl 3 at p = 1.0, ALARM, ACK, clear. Then a false two-witness **injector_clog ALARM 1.44 s after the clear** (cyl 4; the classifier's cylinder hopped 3→1→4→1→4), which stayed latched for the whole 120 s. Source: live demo backend on main, misfire held 20 s (`eval/results/post_misfire_repro_before_hold20.json`).

The cause is in the trims. While a cylinder misfires, its unburnt oxygen makes the O2 sensor read lean, so the ECU trims rich: STFT reaches about +24 % and LTFT learns about +8 %. After the clear, STFT turns negative within seconds (the ECU now removes fuel). But the trim-drift rule's 30 s window still holds the misfire period (a 19 % mean against the 9.7 % threshold), and the classifier reads the same 30 s OBD features as "injector clog". The result is two witnesses for a fault that isn't there.

**Decision.** This is a deterministic enable condition, like OBD-II, which suspends fuel-trim monitoring around a misfire. No retraining.

- **Armed** by a *confirmed* misfire: a two-witness misfire `match` in the rule layer.
- **Ends** when the trims have settled, or 90 s after the last confirmed misfire window (`RECOVERY_MAX_S`), whichever comes first. Settled means all of:
  - STFT and LTFT (5 s means) are within ±2.5 %. That is the healthy simulator's 99th percentile of total trim, 2.21 % in `trim_drift.json`, rounded up.
  - The 30 s trim mean is under the rule threshold.
  - At least 30 s have passed since the last confirmed misfire window, so the 30 s OBD window no longer holds misfire samples.
- **Inside the window:**
  - The `injector_clog` rule is ignored for the first 10 s (`RECOVERY_BLIND_S`, twice the 5 s STFT averaging). After that it is ignored only while the ECU is removing fuel (STFT ≤ 0).
  - An anomaly flag, a lone clog witness, a conflict or nothing at all shows as a **recovery ADVISORY** (fault null, no witness) with guidance "Recovering after misfire: fuel trims re-adapting". It never shows as UNCLASSIFIED.
  - Knock and a new misfire go through as usual.

**Deviations from the request, and why.**

- *"Trim_drift can't fire while the misfire rule is active."* In this simulator a clog starves its cylinder. On the 20 recorded robustness clog runs (s 0.5), the misfire rule fires in **95 %** of windows after the clog starts. In an offline run on the demo engine it fires from the clog's first window onward. Gating on the misfire rule alone would therefore switch clog detection off. The window is armed only by a *confirmed* misfire (rule and classifier agree), and the ECU's own direction separates the two cases: a clog makes it add fuel (STFT +5 to +15 %), recovery makes it remove fuel (STFT −3 to −9 %).
- *The 10 s blind period* was added after the first version still raised the false ALARM at 4 s: right after the clear, the 5 s STFT mean still contains misfire-time samples. The constant was chosen on the demo engine (reference parameters, seed 0), never on test or robustness engines.

**Evidence.** All SIM 2.1, column A.

| Run | Before (main) | After (this branch) |
|---|---|---|
| Live demo backend: misfire cyl 3 p = 1.0, ACK, **20 s** hold, clear, watch 120 s | **False injector_clog ALARM at 1.44 s** (cyl 4), latched to the end | **No ALARM.** Recovery ADVISORY from 1.2 s, NORMAL at 76.2 s |
| Same, **2 s** hold | No ALARM, but injector_clog ADVISORY flapping (13 episodes) and 6 UNCLASSIFIED episodes over ~31 s; first NORMAL at 3.9 s, then flapping | **No ALARM.** Recovery ADVISORY from 1.4 s; one UNCLASSIFIED blip (0.4 s) at 30.8 s; NORMAL at 31.2 s |
| Offline demo engine: misfire 25–45 s, clear, 120 s (`backend/tests/test_post_misfire.py`) | injector_clog ALARM after the clear | No ALARM after the clear; recovery 45.4–116.7 s, then NORMAL |
| Offline demo engine: clog cyl 1 s 0.5 / knock cyl 2 p 1.0 on a settled engine | ALARM | Same ALARM, same time, same cylinder; every window's state identical |

The live rows come from `eval/results/post_misfire_repro_{before,after}_hold{2,20}.json`. Each one is a single run on a live, wall-clock-timed stream, so the timings vary by tenths of a second between runs.

**Recorded L3 runs replayed with the gate off and on** (`python -m eval.post_misfire_check` → `eval/results/post_misfire_check.json`; committed thresholds, D134 on):

- **Healthy soaks** (val_thr 8 h, fresh2 2 h, fresh3 2 h, long 20 h): **identical, window for window.** Soaks never have a confirmed misfire, so the gate never arms.
- **Fault runs:** the 120 robustness runs were re-recorded with the trims the gate reads (`eval.record_runs faults_trims`). They are identical to the original recording on every shared column (120/120).
  - **Knock, valve tick (s 0.3 / 0.6 / 1.0):** identical (detection 20, 1, 7 and 20 of 20; same latencies).
  - **Misfire (p 0.5):** detection identical (20/20, median 0.615 s). **A second, false ALARM naming another fault drops from 15 of 20 runs to 0.** These were injector_clog ALARMs raised during the misfire, the same mechanism as Livana's.
  - **Injector clog (s 0.5): the cost.** Detected **17 → 16 of 20**; 4 runs changed latency (median 23.30 → 23.32 s; individual runs +5.6, +7.0 and +9.0 s, and one run, 007, not within the 90 s fault). These are clog runs where the starved cylinder also produced confirmed misfires, which re-arm the 10 s blind period. 9 of 20 clog runs raise a misfire ALARM, before and after this change.

## P2. Bank-wide ALARM location

When the physics rule names no cylinder (the trim rule is bank-wide) and the classifier names different cylinders across the streak's windows, the ALARM's `location` is null ("bank"). It stays null for that alarm once the cylinder has hopped. A rule with its own cylinder (misfire, knock) keeps it, and a steady classifier cylinder is kept too. Before, the location followed whichever cylinder came last (4→2→1→4 in Livana's case).

## P3. Recovery guidance

`models/guidance.json` gains a `context` table. `recovering_after_misfire` gives the ADVISORY line "Recovering after misfire: fuel trims re-adapting". The runtime uses it when the rule layer is in the recovery window and the decision is the recovery ADVISORY. ALARM, UNCLASSIFIED and sensor FAILURE keep their own lines.

## P4. Reset engine and warm-up

- **`POST /reset_engine`** (simulator only, 409 otherwise) starts a fresh healthy engine with the same seed. Stream time and the rule layer restart, as on a source switch; alarm ids keep counting. The run gets its own `run_id` (`sim_0000_r1`, …) so the dashboard clears its buffers.
- **"Reset engine" button** next to Inject / Stop injection in the injection console (simulator only, kept in demo mode; hidden in mock mode).
- **Warm-up:** for the first 20 s of a simulator run, guidance is "Warming up (20 s): diagnostics settling". In the same 20 s the **anomaly witness is off**, as the classifier already is (MIN_OBD_ROWS). Without that, every start (and so every reset) showed UNCLASSIFIED for about 1.2 s from the start transient. The score is still shown. This only affects the live simulator source; recorded evaluations are not affected (their counted windows start after a 30 s warm-up and are replayed from recordings).
