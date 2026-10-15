# 720

**HackHive 2026 · Honeywell PS2: engine signature extraction and fault recognition**

720 is a condition monitor for combustion engines that names the fault, says when it doesn't know, and leaves the alarm decision to a deterministic rule layer and a human. It folds every sensor onto the engine's 720° four-stroke cycle, the way production engine controllers analyse misfire and knock. It is trained and stress-tested on a physics-informed engine simulator, and judged on real recordings with honest splits, false alarms per hour, and a properly tested "unknown" output. The car engine is our test bed for reciprocating combustion assets: gas-engine compressors, gensets and engine fleets.

> **Real recordings are the evidence · the simulator is the test bench · the deterministic layer decides.**

Full plan, the source of truth: [`docs/720_Final_Plan.md`](docs/720_Final_Plan.md) · Conventions: [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) · Decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md)

## Folder map

| Folder | Owner | Purpose |
|---|---|---|
| [`contract/`](contract/) | Nishant | Message schemas + examples, v1.3 (locked; bump `schema_version` on change) |
| [`sim/`](sim/) | Dipen | Physics-informed engine simulator |
| [`dsp/`](dsp/) | Dipen | Order tracking, 720° averaging, features, physics rules (2 versions each) |
| [`models/`](models/) | Dipen (design) / Diljit (runs) | Baseline, SVM, ANN, CNN, OBD branch |
| [`data/`](data/) | Diljit | Download/vetting scripts and timestamped split lists in `data/splits/`. No raw data committed |
| [`backend/`](backend/) | Nishant | FastAPI, 3-channel WebSocket, rule layer, OPC UA |
| [`frontend/`](frontend/) | Livana | React dashboard (ISA-101) |
| [`eval/`](eval/) | Diljit | Results JSON + charts |
| [`docs/`](docs/) | all | Plan, conventions, decisions log |

## Setup

```bash
python -m venv .venv
pip install -r requirements.txt
python contract/validate.py
```

## Step 1 complete: next steps (H0–6, plan section 14)

**Everyone, H0–1:** agree the data contract (done in draft: `contract/`, v1.3), the 48 kHz rate and the channel list. Diljit commits the AudioSet pool split, the carOBD file split and the held-out list to `data/splits/` **with a timestamp, before any training**.

| Who | First task (H1–6) |
|---|---|
| **Dipen** | Crank + combustion model in `sim/`: firing order 1-3-4-2, Wiebe burn, cycle-to-cycle variation, per-tooth spacing errors on the 60-2 wheel, idle-speed controller, misfire |
| **Diljit** | Download AudioSet broadly (budget for dead links); two-person vetting (passenger car, plausibly 4-cylinder, no speech or music); pools by video ID; knock tagging; sum the carOBD hours; fetch ESC-50 |
| **Nishant** | Own the contract doc; WebSocket stub with fake data (H0–1); then FastAPI replay mode, windowing, 3-channel WebSocket with server-side downsampling |
| **Livana** | Move `engine-fingerprint-ui` into `frontend/`; ISA-101 grey layout against the stub; WebSocket client; UI watchdog on `sent_at`; waveforms, STFT, OBD panel with source badges; deck skeleton |
