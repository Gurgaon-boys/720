# 720 demo: timed click-through, fallback, recording, slide handoff

Stage version of `docs/RUN_DEMO.md` (that file has the full setup, API and Sabotage Lab steps). Times are
targets; the "Expect" column is what was observed on 2026-10-17 against the real backend (branch
`feature/livana-operating-charts` at the merge with main incl. PR #22).

## Before going on stage (T−10 min)

1. Backend window: `$env:S720_SCENARIO = "sim"; python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`
2. Dashboard window: `cd frontend; $env:VITE_API_URL = "http://127.0.0.1:8000"; npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`
3. Open `http://127.0.0.1:5173/?demo=1`, click **Skip to diagnostics**, turn **Alarm tone on**.
4. `Invoke-RestMethod http://127.0.0.1:8000/health` shows `scenario: sim`; the model row shows `SVM` with a p.
   If it is empty, `models/checkpoints/fast_v1.pkl` is missing (RUN_DEMO section 2): no ALARM is possible.
5. Wait for the header guidance "Warming up (20 s)" to disappear: the engine is NORMAL, grey.
6. Fonts load from Google Fonts. Without Wi-Fi the page falls back to system fonts (layout still works).

## Live script (about 3 min)

| Time | Click | Expect on screen | Say |
|---|---|---|---|
| 0:00 | Nothing | **SIMULATED** badge, NORMAL, grey, "No active alarms" | "Healthy simulated engine. Grey means nothing needs you." |
| 0:15 | Injection console: **Misfire**, cylinder **3**, **1.00**, **Inject** | Console shows "INJECTING Misfire · cyl 3" only once the backend echoes it; ADVISORY, then **ALARM Misfire · Cyl 3** within ~1 s; physics ✓ classifier ✓; "What to do: Reduce load; inspect coil and plug on cyl 3"; tone beeps | "Two independent witnesses agree, so the rule layer raises an alarm and tells the operator what to do." |
| 0:45 | **3D engine** tab (10 s), back to **2D dashboard** | Cylinder 3 marked | "Same decision, shown on the engine." |
| 1:00 | **Acknowledge** | Button waits, then "Acknowledged by operator · condition still active"; tone stops; event list shows *Acknowledged* | "The UI never clears an alarm; ACK goes to the rule layer and comes back as a broadcast." |
| 1:15 | **Stop injection** | ALARM resets (only if acknowledged; an unacknowledged alarm stays latched); **Watch · "Recovering after misfire: fuel trims re-adapting"** ("No fault witness", not an alarm) for 20–90 s, then NORMAL. The SVM row may say *Injector clog* meanwhile; the rule layer does not raise it | "After the fault it says it is recovering instead of inventing a new fault." |
| 1:30 | **Valve tick**, any cylinder, 1.00, **Inject** | **Abnormal, unclassified: inspect** with a physics hint, never a named fault; then **Stop injection** | "Valve tick was never trained. 720 says it doesn't know." |
| 2:00 | **Knock**, cylinder 2, 1.00, **Inject** (last on purpose) | **ALARM Knock · Cyl 2**; ACK and stop here | (after Stop injection it shows **Watch · "Recovering after knock: spark timing re-advancing"** for 60–90 s; don't wait for it) |
| 2:30 | Optional: source picker → **Real car replay** (only if the pre-check below passed) | **REAL · REPLAY** badge; OBD panels lead; lean drift 0.50 gives **Watch · Injector clog**, never ALARM | "Real ECU data. One witness, so a watch, not an alarm." |

**Real car replay pre-check** (do it before going on stage): the picker must list files. On the laptop used for the
checks above it failed with `Replay file list unavailable: Failed to fetch`, because `GET /replay/files` returned 500
(`ModuleNotFoundError: No module named 'pandas'`) and `data/raw/carobd/` is absent. Fix on the demo laptop with
`python -m pip install -r requirements.txt` plus the carOBD raw data, or skip the 2:30 step.

## If the live backend fails

| Symptom | Do |
|---|---|
| "Diagnostics offline" banner, or no state at all | Restart window 1. If it doesn't come back within 30 s, go to mock mode. |
| No ALARM after Inject (model row empty) | The classifier checkpoint is missing; say "the second witness is missing, so the rule layer stops at a watch", then use mock mode for the alarm. |
| Anything else on stage | Play the backup recording. |

**Mock mode** (no backend, always works): stop window 2, then `cd frontend; Remove-Item Env:VITE_API_URL -ErrorAction SilentlyContinue; npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`,
open `http://127.0.0.1:5173/` (without `?demo=1`, so the scenario row shows). The footer says "Mock data · values are
made up". Use the scenario pills: **Simulated · healthy → Misfire → Acknowledge → Valve tick → Real · healthy (P3)**.
Mock ALARMs carry no guidance ("No operator guidance sent by the rule layer"). Say plainly that it is recorded mock
data. Checked 2026-10-17: misfire reaches ALARM and the ACK round trip works.

## Backup recording (not made yet)

No recording has been made or checked. To make one on the demo laptop:

1. Do the T−10 checklist, set the browser zoom to 100 %, close other tabs and notifications (Focus assist on).
2. **Win + Shift + R** (Snipping Tool, Windows 11) → select the browser window → **Start**. For the alarm tone in the
   recording, use the Xbox Game Bar instead: **Win + Alt + R** starts/stops recording the active window with system audio
   (files go to `Videos\Captures`).
3. Run the live script once, 0:00 → 2:30, at speaking pace.
4. Stop, then **play the saved file end to end** and check: badge readable, ALARM + guidance visible, ACK confirmation
   visible, the tone audible (Game Bar only), nothing private on screen. Only then call it the backup.
5. Copy it to a USB stick as well as the laptop.

## Slide handoff (three slides)

Every number below is read from the committed file named; don't round them further or relabel them.

### Slide 1: SIMULATED operating sweet spot

- **Title:** "Operating sweet spot (simulated): where healthy engines raise no false alerts"
- **Source:** `eval/results/operating_map.json` (built by `python -m eval.operating_map` from `eval/results/soak_l3_long.json`).
  Column A, SIMULATED (sim 2.1), 400 healthy runs × 180 s = 20 engine-hours.
- **Chart:** 5 × 4 heatmap, RPM bins 700 / 1100 / 1500 / 2000 / 2500 / 2950 × load bins 3 / 13.5 / 24 / 34.5 / 45 N·m;
  cell value = healthy Unclassified episodes per engine-hour (`after` config), grey = 0.
- **Numbers:** committed setting (`after`): **19 episodes, 15 of 20 cells at zero**; all 19 are at ≥ 24 N·m load
  (700–1100 rpm: 5 and 6; 1100–1500: 2; 1500–2000: 1; 2500–2950: 5, top load band). Before the fix (`before`):
  826 episodes, 3 of 20 cells at zero.
- **Must say on the slide:** "SIMULATED engines, all healthy. Every episode is a false alert. Not fuel efficiency; no
  real-car data."

### Slide 2: REAL carOBD, Unclassified episodes before and after

- **Source:** `eval/results/column_c_detail.json`, `overall_before_l3` vs `overall` → `unclassified`.
  Column C, REAL: 39 held-out carOBD files, 12.545 h, 2,627 windows.
- **Chart:** two bars (before / after) with 95 % upper-bound whiskers.
- **Numbers:** **10 → 4 Unclassified episodes in 12.5 h** = **0.80 → 0.32 episodes/h** (exact Poisson 95 % upper bound
  ≤ 1.35 → ≤ 0.73/h). The change is the 2-window persistence rule (D100).
- **Must say:** "Unclassified episodes, not alarms. ALARM cannot occur on carOBD (no classifier witness:
  `alarms.structurally_limited`)." Don't put an alarm-management line (EEMUA 191 / ISA-18.2) on this chart, and don't
  call the 50-engine figure an operator alarm rate.

### Slide 3: INJECTED into real audio (Column B), shown as weak

- **Source:** `eval/table_b.json` (pre-registered, D97/D98). Simulated knock / valve tick added onto 30 real P2
  AudioSet clips; 5 folds by clip.
- **Chart:** grouped bars with Wilson 95 % intervals and a 50 % reference line.
- **Numbers (0 dB, pooled severities, seed 0):** knock detected **50.5 %** (n 1,685, 48.1–52.9 %) vs knock false
  positive on healthy windows **49.2 %** (n 567, 45.1–53.3 %), i.e. **chance**; valve tick sent to unknown **2.2 %**
  (n 1,701), called knock **50.0 %**.
- **Must say:** "INJECTED INTO REAL AUDIO. At realistic levels the audio classifier is at chance: a measured
  sim-to-real gap. That is why the deterministic rule layer, not the classifier, decides." Post-hoc louder levels
  (`eval/table_b_levels_posthoc.json`) are labelled post-hoc if shown at all.

### Open wording items for teammates (not changed here)

- `eval/eval.json` `basis` string puts the REAL 15.9/h next to the alarm guidance (Diljit).
- `models/guidance.json` writes "cyl 3" in lowercase while the UI uses "Cyl 3" (Dipen).
