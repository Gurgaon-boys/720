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
| 2 | Injection console: **Lean drift, 0.50**, Inject | REAL + INJECTED badge; total trim climbs over the 7.4 % limit; **Watch · Injector clog** (trim rule, physics witness) about 7–8 s later at 10×; the healthy model flagging the rising trims corroborates it (D131), so it stays a Watch. Never ALARM: there is no OBD classifier yet (the replay note says so) |
| 3 | **Stop injection** | REAL · REPLAY again at once; NORMAL about 3 s later at 10× (2 windows) |
| 4 | **Upload log**, pick a carOBD CSV (≤ 5 MB) | REAL · UPLOAD badge; it streams like a held-out file. A file without the carOBD columns is refused with the reason |

Only the carOBD **held-out** files are offered (display only; nothing is fitted on them). Uploads are stored under
`data/uploads/` (gitignored). API: `GET /replay/files`, `POST /replay {"file", "speed"}`, `POST /replay/speed`,
`POST /upload_obd?filename=…` (raw CSV body), `POST /inject {"fault": "lean_drift", "severity"}`.
