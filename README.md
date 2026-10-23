# 720

**HackHive 2026 · Honeywell PS2: engine signature extraction and fault recognition**

720 is a condition monitor for combustion engines. It names the fault, says when it doesn't know, and leaves the alarm decision to a deterministic rule layer and a human operator.

It folds every sensor onto the engine's 720° four-stroke cycle, the way production engine controllers analyse misfire and knock. It is trained and stress-tested on a physics-informed engine simulator, then judged on real recordings with honest splits, false alarms per hour and a tested "unknown" output. The car engine is the test bed; the target is reciprocating combustion assets such as gas-engine compressors, gensets and engine fleets.

> **Real recordings are the evidence · the simulator is the test bench · the deterministic layer decides.**

## What it does

- **Physics-informed engine simulator:** 1-3-4-2 firing order, Wiebe combustion, cycle-to-cycle variation, 60-2 crank wheel with tooth errors, an idle-speed controller, and injectable misfire, knock, injector clog and valve tick at any severity
- **Signal processing on the 720° cycle:** order tracking, cycle averaging and per-cylinder features from crank, audio and OBD signals
- **Two-witness alarm rule:** an ALARM needs both a physics rule and the classifier to agree; one witness gives an ADVISORY, and an anomaly with no named fault becomes UNCLASSIFIED rather than a guess
- **Industrial integration:** FastAPI backend with a 3-channel WebSocket, and an OPC UA server so plant software sees the same state as the dashboard
- **Operator dashboard:** React, following ISA-101 high-performance HMI guidelines, with a 3D engine view, an evidence panel showing why each decision was made, an incident timeline and PDF incident reports
- **Industrial Resilience Lab:** remove or break a sensor live and see exactly what detection capability is lost
- **Live microphone mode:** real engine audio streamed through the same detection path

## Results

Every number below traces to a file in [`eval/`](eval/) produced by code in this repo. Simulated, injected and fully real results are reported separately and never mixed.

| Test | Data | Result |
|---|---|---|
| Fault classifier (healthy, misfire, knock, injector clog) | Simulated, validation split, 16,318 windows | Macro-F1 **0.942**; faulty cylinder located correctly 95% of the time |
| Time from fault onset to ALARM | Simulated, unseen robustness engines | **592 ms** |
| False flags on healthy driving | **Real** carOBD, 39 held-out files, 12.5 h | **1.1%** of 30 s windows; 0.32 advisories per hour |
| Classifier speed | CPU | 0.84 ms per window |

**Known limitations, stated plainly:** knock detection on real audio does not yet transfer from the simulator (1 of 19 real knock clips), and a fault class the model never saw is sent to "unknown" only 2% of the time. Both are measured, reported and listed as next steps rather than hidden.

## Team

| Area | Owner |
|---|---|
| Engine simulator, signal processing, model design | **Dipen Singh** |
| Data vetting, evaluation, model runs | Diljit Singh |
| Data contract, backend, OPC UA, Resilience Lab | Nishant |
| Dashboard and demo | Livana |

Every design decision is logged with its reason in [`docs/DECISIONS.md`](docs/DECISIONS.md). The full plan is in [`docs/720_Final_Plan.md`](docs/720_Final_Plan.md).

## Run it

Needs Python 3.12+ and Node 20+. Full instructions, including the trained model file: [`docs/RUN_DEMO.md`](docs/RUN_DEMO.md).

```bash
git clone https://github.com/Gurgaon-boys/720.git && cd 720
python -m pip install -r requirements.txt
cd frontend && npm ci && cd ..

# backend (window 1)
S720_SCENARIO=sim python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# dashboard (window 2)
cd frontend && VITE_API_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5173
```

Open <http://127.0.0.1:5173/?demo=1>. Tests: `python -m pytest -q` (222 Python tests) and `npm test` in `frontend/`.

**Stack:** Python, NumPy, SciPy, scikit-learn, PyTorch, librosa, FastAPI, WebSockets, OPC UA (asyncua), React, Vite, Three.js
