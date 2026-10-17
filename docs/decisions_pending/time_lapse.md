# Pending decisions: simulator time-lapse and post-clog recovery (branch `feature/sim-time-lapse`)

These are not numbered yet. They get the next free D-numbers when this branch merges; `docs/DECISIONS.md` is untouched here. The branch also carries PR #27 (post-knock recovery, K1–K2), which they build on.

## T1. Simulator time-lapse: Auto (10× while nothing new happens), 1×, 5×, 10×

**Problem.** On stage, the live simulator makes the judges wait. Finals smoke test (main c0a2407):

| Phase | At 1× |
|---|---|
| Warm-up after a start or **Reset engine** | 20 s |
| Misfire cleared → NORMAL (D144 recovery window) | 90.9 s live |
| Knock cleared → NORMAL (K1 recovery window) | 60–90 s |
| Injector clog s 0.5 → ALARM (the 30 s fuel-trim window) | 23.4 s |
| Injector clog cleared → NORMAL | ~41 s, ~12 s of it UNCLASSIFIED (T2) |

All of these are **stream-time** effects. The engine physics, the 30 s OBD windows, the recovery windows (D144, K1) and the rule layer's streak counts all run in simulator time. Shortening any window would change what the rule layer decides.

**Decision.** The simulator source maps the runtime's tick (wall) time to stream time at a speed of 1, 5 or 10. It changes only the rate: stream time never jumps. Every window is computed exactly as at 1×, and so is every decision, just sooner in wall time. This is the same idea as the carOBD replay's 10×/30× (D129).

- **Auto (the default) runs at 10× only while there is nothing new to read:**
  - warming up (the first 20 s of a run);
  - after a cleared fault, while the decision is not yet NORMAL ("recovering"), unless an ALARM is unacknowledged;
  - while an injected fault has played 3 s at 1× and is still NORMAL or ADVISORY ("building": a slow fault such as fuel trims).
- **Auto plays at 1× in every other case:** a healthy engine, the first 3 s of an injection, an unacknowledged ALARM, UNCLASSIFIED with a fault injected, a disabled sensor on a NORMAL engine.
- **A fresh injection always plays its first 3 s at 1×**, even during warm-up.
- **Manual 1×, 5× or 10×** holds the operator's choice. It survives Reset engine.
- **Speed cap (a slow laptop degrades, never freezes).** If the state loop falls 0.5 s behind its deadline, the cap steps down one speed (10× → 5× → 1×, at most once a second). It steps back up after 5 s of time-lapse without lag. The runtime loop also always yields to HTTP, WebSocket and ACK handling while it catches up (before, a loop that fell behind ran its ticks back to back).
- **What is not time-lapsed:** the sensor watchdog's 3 s (D15) is wall time. The 3D view's slow motion is display only.

**Contract.** `state.time_lapse = {mode, speed, reason}` is a 1.6 optional, additive field (like `replay`), sent only while the source is the simulator. Simulator OBD messages carry every 1 Hz row of stream time since the last tick, so the charts keep their stream rate (about 10 samples per message at 10×). Their `t` is stream time.

**UI.** The dashboard header shows a Time-lapse control (Auto · 1× · 5× · 10×). It also shows a dashed **"⏩ TIME-LAPSE 10× · recovering after the fault"** badge whenever the clock is not real time; the 3D header shows the same badge. The label is text, never colour alone.

**Measured (offline, `backend/tests/test_time_lapse.py` and the runtime driven tick by tick, all three loops, Auto):**

| Phase | Stream s | Wall s |
|---|---|---|
| Warm-up | 20.8 | **2.8** |
| Misfire ALARM | 0.7 | 0.7 (1×) |
| Misfire cleared → NORMAL | 31.1 | **3.2** |
| Knock cleared → NORMAL (K1) | 61.1 | **6.2** |
| Injector clog ALARM | 24.1 | **5.2** (3 s at 1×, then 10×) |
| Injector clog cleared → NORMAL (T2) | 42.1 | **4.3** |

**Cost and limits.**
- At 10× the simulator computes 1 s of engine per 0.1 s tick. That's about 50 ms on the demo laptop (the simulator alone runs at ~19× real time). Live on that laptop, POSTs during a 10× warm-up answered in 160–200 ms. The speed cap handles a slower machine; `S720_TIME_LAPSE=off` switches the time-lapse off.
- Mic audio and waveforms show the current stream time during a time-lapse, not every sample of it.
- Auto never time-lapses a fault's onset or an unacknowledged ALARM, so nothing the operator must act on goes by at 10×.

## T2. Post-clog recovery window

**Problem.** After an injector clog is cleared, the ECU spends its learned extra fuel back out. STFT and LTFT fall from about +s/(4−s) to 0 while the 30 s OBD window still holds the clog. The healthy model never saw that, so a cleared clog read **UNCLASSIFIED** ("Unknown pattern: inspect, do not clear") for about 12 s of stream time before NORMAL.

**Decision.** This is built exactly like D144 (misfire) and K1 (knock):
- A confirmed injector_clog (a two-witness `match`) arms a recovery window.
- The window ends once the trims have settled (`dsp.rules.trim_recovery`, not before RECOVERY_MIN_S = 30 s), or RECOVERY_MAX_S = 90 s after the last clog match.
- In it, **only an anomaly or nothing at all** becomes the recovery ADVISORY: "Recovering after injector clog: fuel trims re-adapting".
- The trim rule is not gated, so a clog that is still there keeps its ALARM, and an injector_clog ADVISORY stays an injector_clog ADVISORY.
- Priority when windows overlap: misfire, then knock, then clog.

**Evidence.** `eval/post_clog_check.py` → `eval/results/post_clog_check.json`. It replays every recorded L3 run with the window off and on:
- Healthy soaks: 0 windows changed.
- 120 robustness fault runs: 0 windows changed, ALARM episodes 1,748 → 1,748, every detection and latency identical. The recordings never clear a clog, so the window never opens there.

Live (offline runtime, above): the clear now reads recovery for its whole length, with no UNCLASSIFIED.

**Cost.** This is the trade-off D144 and K1 already accept: an unrelated anomaly inside the window (for example a valve tick injected right after a clog) shows as recovery, not UNCLASSIFIED.
