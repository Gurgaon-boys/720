# Run the 720 demo (Windows, PowerShell)

Everything runs on this laptop: `http://127.0.0.1` only (never `localhost`), no network needed after install.

## 1. Fresh clone and install (once)

Needs Python 3.12+ (tested with 3.14) and Node 20+ (tested with 24).

```powershell
git clone https://github.com/Gurgaon-boys/720.git
cd 720
python -m pip install -r requirements.txt
cd frontend; npm ci; cd ..
```

## 2. The trained model files

The live demo needs the fast classifier + healthy model checkpoint **`models/checkpoints/fast_v1.pkl`**. It is
**git-ignored** (`*.pkl`), so a fresh clone does not have it. Everything else it needs is committed:
`models/fast_v1.json` (its metadata), `models/thresholds/*.json` (physics-rule and healthy-model thresholds),
`models/guidance.json`, `data/splits/sim_v2_dataset.json` (the dataset manifest).

Without the checkpoint the backend still starts, but the classifier witness is off: no ALARM can be raised
(it needs two witnesses), the anomaly score is null and nothing becomes UNCLASSIFIED.

**Option A: copy it** from the laptop that has it (same scikit-learn version: the file was written with
scikit-learn 1.9.0 and NumPy 2.4.6) into `models\checkpoints\fast_v1.pkl`. `requirements.txt` pins
scikit-learn 1.9.0 for this reason: with 1.9.1 the backend logs `InconsistentVersionWarning` ("might lead to invalid
results") when it loads the file. Tested: the file loads without warnings on scikit-learn 1.9.0 with NumPy 2.5.4.

**Option B: rebuild it** from the committed manifest (simulates the whole sim 2.1 dataset, then trains; long, run
it the day before, not on stage):

```powershell
$env:S720_WORKERS = "10"            # parallel simulator runs; ~0.5 GB RAM each
python -m models.make_dataset       # -> data\generated\sim_v2\*.npz (resumable)
python -m models.classifier         # -> models\checkpoints\fast_v1.pkl and models\fast_v1.json
```

The training is seeded, so `models/fast_v1.json` should come back unchanged (validation macro-F1 0.9421); a
`git diff models/fast_v1.json` that changes anything but `created_at` / `fit_s` means something differs.

**Check:** `python -m pytest -q -rs` must show **no** skip with reason `run python -m models.classifier`.

## 3. Start (the 4 commands)

Open two PowerShell windows in the repo folder.

**Window 1, backend** (live simulator, OPC UA on `opc.tcp://127.0.0.1:4840/720/`):

```powershell
$env:S720_SCENARIO = "sim"; python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

**Window 2, dashboard:**

```powershell
cd frontend; $env:VITE_API_URL = "http://127.0.0.1:8000"; npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

**Open:** <http://127.0.0.1:5173/?demo=1>

`?demo=1` hides the fake-scenario row and starts on the live simulator. The injection console, the 2D / 3D tabs
and the evidence panel stay. (`$env:VITE_DEMO = "1"` before `npm run dev` does the same without the URL flag.)

**Check the backend:**

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

It should show `scenario: sim` and `opcua: opc.tcp://127.0.0.1:4840/720/`. On the dashboard, the model row
shows `SVM` with a probability; if it is empty, the checkpoint is missing (section 2).

**Stop everything** (both servers, by port):

```powershell
foreach ($p in 8000,5173) { Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force } }
```

Or press Ctrl+C in each window.

## 4. UaExpert (OPC UA client)

1. *Server → Add*, *Custom Discovery*, URL `opc.tcp://127.0.0.1:4840/720/`, security **None**, anonymous.
2. Connect, browse `Objects / EngineAsset`; drag `Decision` (State, Fault, Cylinder, Acked, AlarmId) and
   `AssetHealth` into the Data Access view.
3. Add an *Event View* on the `Server` object to see ALARM / ACK / cleared / unclassified events.
4. An ACK from the dashboard shows `Decision.Acked = true` here; `Acknowledge` (right-click, Call) on
   `EngineAsset` with the AlarmId acknowledges from OPC UA instead, and the dashboard follows.

UaExpert on another laptop: start the backend with `$env:S720_OPCUA_HOST = "0.0.0.0"` and see the firewall note
in `backend/README.md`. The server has no security: trusted networks only.

## 5. Demo order

Use the injection console. Each step starts from a healthy engine.

| # | Do | Expect |
|---|---|---|
| 1 | Nothing (healthy) | NORMAL, grey; SVM says healthy; no alarm |
| 2 | Inject **misfire, cylinder 3, severity 1.0** | ADVISORY, then **ALARM misfire cyl 3** in about 1 s; guidance "Reduce load; inspect coil and plug on cyl 3"; 3D tab: cylinder 3 glows |
| 3 | **ACK** on the dashboard | Acked by the operator; UaExpert shows `Acked = true` with the same AlarmId; the alarm stays latched while the fault is active |
| 4 | **Clear** faults | The acknowledged alarm resets once 2 windows no longer match misfire (about 1 s). If the ECU trims are still settling, the dashboard may show UNCLASSIFIED or ADVISORY for a while before NORMAL |
| 5 | Inject **valve tick** (any cylinder, 1.0) | **UNCLASSIFIED** ("abnormal, unclassified: inspect") in about 1 s, never a named fault: valve tick was never trained. Clear |
| 6 | Inject **injector clog, cylinder 1, severity 0.5** | ADVISORY first, **ALARM injector_clog** after about 22 s (the 30 s fuel-trim window), asset health **MAINTENANCE_REQUIRED**, guidance "Schedule injector inspection; safe to run". ACK, clear |
| 7 | Inject **knock, cylinder 2, severity 1.0** — **last** | **ALARM knock cyl 2** in about 1 s. ACK and stop here |

**Knock goes last on purpose.** After knock is cleared, the ECU's spark retard recovers slowly, and the healthy
model (never trained on post-knock recovery) shows UNCLASSIFIED for up to ~75 s. This is a known limitation
(DECISIONS: alarm budget and demo notes); don't wait for it to recover on stage.

With the hard simulator regimes on (`$env:S720_SIM_REGIMES = "eval"`, not the default), an injection can start
up to 0.5 s late (D107).

## 6. Real car replay (REAL data, honestly labelled)

The source picker under the header (kept in demo mode) switches between **Simulator**, **Real car replay** and
**Upload log**. The big badge says what is streaming: `SIMULATED`, `REAL · REPLAY`, `REAL + INJECTED` or
`REAL · UPLOAD` (uploaded, unverified). Replayed data is never called live (`mode: replay`).

| # | Do | Expect |
|---|---|---|
| 1 | **Real car replay**, file **drive2.csv**, speed **10×** | REAL · REPLAY badge; the OBD panel and the OBD-branch trend lead (11 carOBD channels at their own ~0.5 Hz refresh); cylinder, fingerprint and audio panels hidden ("no crank signal in this data"); NORMAL |
| 2 | Injection console: **Lean drift, 0.50**, Inject | REAL + INJECTED badge; total trim climbs over the 7.4 % limit; **Watch · Injector clog** (trim rule, physics witness) about 7–8 s later at 10×; the healthy model flagging the rising trims corroborates it (D134), so it stays a Watch. Never ALARM: there is no OBD classifier yet (the replay note says so) |
| 3 | **Stop injection** | REAL · REPLAY again at once; NORMAL about 3 s later at 10× (2 windows) |
| 4 | **Upload log**, pick a carOBD CSV (≤ 5 MB) | REAL · UPLOAD badge; it streams like a held-out file. A file without the carOBD columns is refused with the reason |

Only the carOBD **held-out** files are offered (display only; nothing is fitted on them). Uploads are stored under
`data/uploads/` (gitignored). API: `GET /replay/files`, `POST /replay {"file", "speed"}`, `POST /replay/speed`,
`POST /upload_obd?filename=…` (raw CSV body), `POST /inject {"fault": "lean_drift", "severity"}`.

## 7. Sabotage Lab (take a sensor away, live)

Tab **SABOTAGE LAB** (`http://127.0.0.1:5173/?demo=1#sabotage`), labelled **SIMULATED TEST ENVIRONMENT**. A judge
switches simulator sensors off and on and watches what 720 can still prove. It uses the same backend runtime,
rule layer and OPC UA server as every other tab; it adds no decision logic (D140).

**Start the backend at least 30 s before this demo.** The classifier abstains until it has 20 s of OBD history,
and the first ~2 s after start can show a one-window knock Watch or UNCLASSIFIED (start-up transient).

| # | Click | Backend (rule layer, watchdog) | Lab shows |
|---|---|---|---|
| 1 | Open **SABOTAGE LAB** | NORMAL, every sensor NORMAL | Four sensor nodes wired to the engine with flowing pulses, **FULL EVIDENCE**, Witness 2 "Healthy, SVM p ≈ 0.9" |
| 2 | Fault injection: **Misfire, cylinder 3, 1.00**, **Inject** | ADVISORY in 0.45 s, **ALARM misfire cyl 3** (physics + classifier) in 1.30 s | Cylinder 3 red, camera on it; Witness 1 "FIRED · cyl 3", Witness 2 "Misfire · cyl 3, counted by the rule layer" |
| 3 | **ACCEL → Disable** | Command accepted in ~0.1 s; the classifier's output is gone in **0.40 s** (no valid accelerometer window); the watchdog reports **accel FAILURE at 3.1 s**. The ALARM **stays latched** (unacknowledged alarms never clear) | SENDING → AWAITING WATCHDOG (amber) → **SENSOR OFFLINE**: the ACCEL wire breaks and its pulses stop; ACCEL strip frozen and labelled STALE; **PARTIAL EVIDENCE**; Witness 2 "Classifier witness unavailable: accelerometer offline. New confirmed alarms require two matching witnesses."; "Previously confirmed alarm remains latched. Current diagnostic evidence is degraded."; knock rule "NO INPUT · ACCEL offline" |
| 4 | **Acknowledge alarm** | The acknowledged alarm resets once its two-witness match is gone (D124): **ADVISORY misfire cyl 3, physics only**, in 0.48 s; guidance "Check sensor wiring (accel); engine state unknown" | Key line: *"You took away a sensor, so 720 stopped claiming what it could no longer prove."* |
| 5 | **ACCEL → Enable** | Sensor NORMAL again in **0.3–0.4 s**; classifier output back in **0.86 s**; a **new ALARM** (new id) in 1.16 s once both witnesses match for 2 windows | RECOVERING until valid data arrives, then the wire rejoins and pulses resume; Witness 2 returns only when the backend sends model output again; FULL EVIDENCE |
| 6 | UaExpert (optional) | `EngineAsset/SensorHealth/accel` = 1 (FAILURE) while it is off, 0 after; `Diagnostics/Models` empty while off | Same state as the lab; `DisableSensor` / `EnableSensor` (right-click, Call, Channel = `accel`) do the same as the buttons |

All times above were measured on this laptop in the browser (click → first broadcast showing the change); the lab's
timeline records them as `+x s` on every run. The watchdog's 3 s is by design (D15): a sensor is called failed only
after 3 s without data, so the break animation waits for it.

**Which sensor does what** (from the code, shown in the lab): **ACCEL** = knock rule + classifier input; **CRANK** =
misfire rule + cylinder localisation + classifier input (crank off also blanks the per-cylinder glow); **MIC** =
acoustic signature display only (evidence stays FULL); **CAM** = 720° phase, held from the last cam pulse while
lost (evidence stays FULL). Crank and accelerometer both off = **INSUFFICIENT EVIDENCE**.

**Rules the lab keeps:** it never forces a state, never fakes a classifier output, and never shows a sensor live before
the watchdog says so. Clearing a fault never re-enables a sensor. Switching tabs changes nothing in the backend
(the lab keeps its record). Selecting a source again (e.g. *Simulator* in the picker) starts a new run with every
sensor on. **Restore all** re-enables every sensor.

**If the classifier is unavailable** (no `fast_v1.pkl`, section 2): Witness 2 reads NOT LOADED, evidence is PARTIAL
from the start and the rule layer stops at ADVISORY, so step 2 gives ADVISORY, not ALARM. The sensor topology,
watchdog, physics witness and recovery still work; skip step 4 and say plainly that the second witness is missing.

API: `GET /sensors`, `POST /sensor/{mic|accel|crank|cam}/off|on`, `POST /sensors/restore` (409 off the simulator,
422 for any other channel). A reply only means the command was accepted; the sensor's state comes from
`state.health.sensors`.

## 8. Industrial Resilience Lab (the SABOTAGE LAB tab, 60–90 s judge script)

The tab is now the **INDUSTRIAL RESILIENCE LAB** (D141), laid out to present top to bottom: the title with **Export PDF
report** on the right, a **1-2-3 guide** (Inject a fault → Take a sensor away → Bring it back; the current step lights
up from backend state), then the controls on the left (one big **Inject misfire · cylinder 3** button and four sensor
tiles) next to the **rule-layer decision bar** (state, witnesses, evidence, guidance, **Acknowledge**) above the large
**3D stage** (the engine with its four sensors cabled to an EDGE unit and an OPC UA mast; **Reset view** if a scroll
zoomed it), the **What just happened** panel, and one tabbed **Details** area (Reliability Agent · Incident timeline ·
OPC UA link · Live signals). Start the backend 30 s
before (classifier warm-up). Measured on this laptop, browser click → first broadcast showing the change:

| # | Judge does | What they see | Measured |
|---|---|---|---|
| 1 | Nothing | All four cables carry white pulses into 720 EDGE; PHYSICS quiet, ML "healthy p ≈ 0.9", decision NORMAL; mast rings = OPC UA publishing | — |
| 2 | **Inject** misfire, cyl 3, 1.00 | Cylinder 3 glows; PHYSICS fired, ML misfire; red decision light; "ALARM confirmed · misfire · cylinder 3"; the agent investigates the new ALARM by itself | ADVISORY 0.55–0.69 s, **ALARM 0.93–0.99 s** |
| 3 | **ACCEL → Disable** (or UaExpert `EngineAsset.DisableSensor("accel")`) | ACCEL cable amber "AWAITING WATCHDOG", then **fractures red with a spark**; ML lamp off; **What just happened**: YOU REMOVED ACCEL → 720 LOST knock rule, ML classifier witness → STILL WORKING misfire rule, injector-clog rule, localisation → RESULT "No new alarm can be confirmed", "a_000N stays latched", "Rule layer now: ALARM"; key line; agent explains which sensor, which evidence, whether the fault can still be confirmed; timeline shows requested → accepted → confirmed | accepted ~0.1 s; **classifier gone 0.17–0.40 s**; **watchdog FAILURE 3.06–3.25 s** |
| 4 | **Acknowledge alarm** | Decision drops to **ADVISORY, physics only** (the latch resets once its two-witness match is gone, D124) | 0.23 s |
| 5 | **ACCEL → Enable** | Cable rejoins, pulses resume; ML lamp back only when the model answers again; a new ALARM is confirmed | **sensor LIVE 0.23–0.29 s; classifier back 0.94–1.03 s; new ALARM 1.20–1.36 s** |
| 6 | **Export PDF report** (header button, or the Incident timeline tab) | Downloads `720_incident_<run>_<time>.pdf` (D143); "View report" opens the same content as a printable page: asset, source, episodes, alarm ids, sensor availability, physics and ML evidence, ACK, timeline with phases, agent findings, limitations | — |

Closing line: *"Most systems tell you when an engine fails. 720 also tells you when its own diagnosis can no longer
be trusted."*

**From UaExpert** (bidirectional): `EngineAsset → DisableSensor("accel")` returned `(True, "")`; 4 s later
`SensorHealth/accel = 1` (FAILURE); the lab's 3D cable broke, the timeline showed "Disable ACCEL · via opcua", and
the Industrial link showed it as the last command plus the emitted OPC UA event "Sensor accel FAILURE".

**Fallbacks.** No classifier checkpoint: ML lamp "no input" from the start, decision tops out at ADVISORY; do steps
1, 3, 5 and say the second witness is missing. WebGL lost: the 3D stage offers "Restart 3D view"; everything else
(outcome, agent, timeline, report) works without it. Agent: it is deterministic and runs on the backend; there is no
language model to fail.

API (D141, D143): `GET /events?after=N`, `GET /investigate`, `GET /industrial`, `GET /report`, `GET /report.pdf`.
