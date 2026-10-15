# Decisions log

Newest entries go at the bottom. Each entry has a date, the decision, the reason and its source. Never rewrite a past entry; add a new one that supersedes it.

## 2026-10-15 · Locked in plan v1.4 (`720_Final_Plan.md`)

| # | Decision | Why | Plan |
|---|---|---|---|
| D1 | **Audio at 48 kHz** for the simulator and every pipeline (real clips stay at their native rate) | Knock energy sits at ~5–15 kHz, so 16 kHz would miss most of it | §4 |
| D2 | **Cut:** the "earlier than the ECU" race | We would build both the referee and the contestant. Replaced by measured early detection (★7) | §2 |
| D3 | **Cut:** the ELM327 emulator | Simulated data through an emulated protocol proves nothing. OPC UA speaks Honeywell | §2 |
| D4 | **Cut:** the ignition-coil current stream | Not measurable on a real car in a demo. Replaced by MAP | §2 |
| D5 | **Cut:** a separate knock sensor | A knock sensor *is* a block accelerometer, so it is merged into one 48 kHz block accelerometer | §2 |
| D6 | **Deferred:** twin inversion for severity | Only if Dipen is ahead at hour 14 | §2 |
| D7 | **Cut:** Grad-CAM as the explanation | Unreliable on spectra. We explain with physics evidence instead | §2 |
| D8 | **Cut:** 3D engine on the main screen | Reads as decoration under ISA-101. It becomes an optional "explain" view | §2 |
| D9 | **Additive-only injection onto real audio:** column B is knock and valve tick only | A misfire is a *missing* pulse and can't be added. Misfire and injector clog stay simulator-only | §4 |
| D10 | **carOBD is the headline real false-alarm bound**, split by file (calibration vs held-out, ≥1 held-out file of each type). Vetted P3 audio is secondary | P3 may hold only dozens of clips (~22/h bound at 50 clips) | §4 |
| D11 | **AudioSet split by video ID into P1 / P2 / P3**, timestamped at H0. P1 is background texture only, and P3 is never touched by training or the simulator | Prevents the leakage we criticise | §4 |
| D12 | **No 720° fingerprint and no cylinder localisation on real clips.** False alarms there use only features that don't depend on cylinder count or RPM | Real clips have an unknown cylinder count, stroke and RPM, and no cam/TDC reference | §4, §7 |
| D13 | **Every physics rule has a `crank_angle` and a `firing_event` version.** If no rule can run, the system can only raise an advisory | Real audio has no crank angle | §8 |
| D14 | **NE 107 states reused for asset health; sensor health kept separate.** Misfire/knock alarm → OFF_SPEC · injector clog → MAINTENANCE_REQUIRED · unclassified → OFF_SPEC · advisory → NORMAL + event · stale stream → FAILURE (sensor node) · injection console active → CHECK_FUNCTION | NE 107 is a field-device standard, used here as an explicit analogy | §11.2 |
| D15 | **Two-layer watchdog:** the backend marks a stream stalled for 3 s as sensor FAILURE, and the UI shows "diagnostics offline" after 3 s without messages | A crashed backend can't report its own crash | §11.1 |
| D16 | **The rule layer is the single source of truth for alarm and ACK state** | The dashboard and OPC UA can never disagree | §11.1 |
| D17 | **3D engine: protected slot at hours 14–18** only if the 2D dashboard and WebSocket client are done by hour 14. **Hard cut-off at hour 20 → 2D fallback, no debate** | First thing cut if anything slips | §12 |
| D18 | **Three WebSocket channels by rate** (`signal` binary, `state` ~10 Hz, `obd` ~1 Hz) plus `ack` | One big message per update would choke the WebSocket | §12 |
| D19 | **`valve_tick` held out of ML training.** Open-set AUROC is measured on the ML side without the physics rule | Tests "unknown" honestly | §10 |

## 2026-10-15 · Step 1 repo setup (contract v1.3)

| # | Decision | Why |
|---|---|---|
| D20 | The `signal` binary frame is a `uint32` header length + a UTF-8 JSON header + a float32 LE payload. The header adds `kind` (waveform/spectrogram), `t0`, and, for spectrograms only, `n_bins`, `n_frames` and `f_max_hz` | Plan §12 doesn't specify a layout. JSON headers are easy to read in both JS and Python, and `t0` aligns frames with `state.t` |
| D21 | The example messages use one concrete value where the plan lists alternatives (e.g. `"source": "simulated"`). The schemas enforce the full enums | An example has to be a valid message |
| D22 | The schemas are strict (`additionalProperties: false`). For `source: "real"` they enforce `localisation: "none"` and an empty `signature`. For `ALARM` they enforce 2 witnesses and ≥2 consecutive windows | Catches frontend/backend drift and contract violations at validation time |
| D23 | `operating_point.rpm` and `load_pct` may be `null` | RPM is unknown on real AudioSet clips, which skip order tracking |

## 2026-10-16 · Step 2 data acquisition (Diljit, `feature/diljit-data`)

| # | Decision / finding | Why | Evidence |
|---|---|---|---|
| D24 | **carOBD is 41.7 h, not ~84 h.** The logger writes ~2.03 rows/s, not the README's 1 Hz; hours come from the `ENGINE_RUN_TIME` column. Each PID refreshes at only **~0.55 Hz** (rows repeat between polls), so a 30 s OBD window holds ~15 fresh samples per variable | Durations feed the rule-of-three bound; rows/3600 would have doubled it | `eval/carobd_inventory.json` |
| D25 | **carOBD rows end with a trailing comma**, so plain `pd.read_csv` shifts every column by one (RPM lands in the run-time column). Everyone loads carOBD through `data/carobd.py:load()` | Silent column shift would corrupt calibration and the OBD branch | `data/carobd.py`, `data/tests` |
| D26 | carOBD split: **by file, stratified by type, 30.2% of rows / 12.5 h held out**, ≥3 files of each of the 5 types (idle, drive, live, ufpe = campus, long). `live` (work → home commute) is a fifth type the plan didn't list | Plan §4 | `data/splits/carobd_split.json` |
| D27 | **AudioSet audio from the Hugging Face mirror `agkphysics/AudioSet`** (FLAC, March 2023 snapshot, 48 kHz or 44.1 kHz), fetched by HTTP range reads of single parquet pages; no YouTube scraping. Mids are looked up by name in `ontology.json`: Idling `/m/07pb8fc`, Engine `/m/02mk9`, Engine knocking `/m/01h82_` | No dead links; ~3 MB per clip instead of whole 2.7 GB shards | `data/audioset_fetch.py`, `eval/audioset_manifest.csv` |
| D28 | **Idling / Engine clips are drawn from `unbalanced_train` only; Engine knocking from all three splits.** The mirror's eval/balanced shards have no page index (one clip costs ~140 MB there). Same annotation process across splits, and the caps (1,500) are far below the ~6k / ~16k unbalanced candidates | Download cost | `data/audioset_fetch.py` docstring |
| D29 | **AudioSet clips are stored as 48 kHz mono float32 WAV**; the ~10% recorded at 44.1 kHz are resampled, and `original_sr` is kept in the manifest (those clips carry nothing above 22.05 kHz). This supersedes "real clips stay at their native rate" (D1/CONVENTIONS) for stored AudioSet files | One rate for every pipeline; band limit still traceable per clip | `eval/audioset_manifest.csv` |
| D30 | Split lists are JSON (`carobd_split.json`, `audioset_pools.json`, `esc50_split.json`) with `created_at` (UTC) and `git_commit` inside, not timestamped CSV filenames | The Step 2 brief asks for JSON with these fields | `data/splits/` |
| D31 | AudioSet pools: `sha256(seed:video_id)` → P1 40% / P2 30% / P3 30%; a video's pool doesn't depend on which other clips survive vetting. Videos that also have a knock clip go to P3. ESC-50 is split 50/50 **by Freesound source clip** within each category; its `engine` category is flagged so it isn't used as "ambient" noise on engine audio | Plan §4 leakage rules | `data/make_pools.py`, `data/esc50_split.py` |
