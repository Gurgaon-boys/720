# data/

**Owner:** Diljit

Download and vetting scripts and split lists (plan section 4). **No raw audio or CSV is committed.** `data/raw/` and `data/audio/` are git-ignored.

- **AudioSet** ("Idling", "Engine", "Engine knocking"): download broadly, expect dead links, and keep only clips that two people independently agree are a passenger car, plausibly 4-cylinder, with no speech or music
- **carOBD:** healthy OBD-II logs (~2 rows/s, each PID refreshing at ~0.5 Hz), split **by file** into calibration and held-out sets
- **ESC-50:** ambient noise for the robustness sweep, split by source clip
- **IEEE DataPort Fiesta set:** only if access appears; split by contiguous time blocks

The split lists live in `splits/` and are written at H0, **with a timestamp, before any training**.

## Scripts (run from the repo root, `.venv/bin/python …`)

| Step | Command | Output |
|---|---|---|
| carOBD | `git clone --depth 1 https://github.com/eron93br/carOBD data/raw/carobd` | raw CSVs |
| | `python data/carobd_inventory.py` | `eval/carobd_inventory.json` (prints total hours) |
| | `python data/carobd_split.py` | `data/splits/carobd_split.json` |
| AudioSet | `python data/audioset_fetch.py` (resumable; `--cap-idling/--cap-engine/--cap-knock`) | `data/raw/audioset/<vid>_<start>.wav`, `eval/audioset_manifest.csv` |
| Vetting | `python data/vet.py --rater <name>` (each rater on their own machine/port) | `data/vetting/<name>.csv` |
| | `python data/vet_merge.py` | `data/vetting/vetted_clips.csv`, `eval/vetting_agreement.json` |
| Pools | `python data/make_pools.py` (after vetting) | `data/splits/audioset_pools.json` |
| ESC-50 | `python data/esc50_split.py` (clones if missing) | `data/splits/esc50_split.json` |
| EngineFaultDB | `git clone https://github.com/Leo-Thomas/EngineFaultDB data/raw/enginefaultdb` (GPL-3, never commit the CSV) | raw CSV |
| | `python data/enginefaultdb.py` | `eval/enginefaultdb_inventory.json` |
| | `python data/enginefaultdb_split.py` | `data/splits/enginefaultdb_split.json` |
| Tests | `python -m pytest data/tests -q` | |

**Loading carOBD:** always use `data/carobd.py:load()`. Every data row ends with a trailing comma, and a plain `pd.read_csv` silently shifts all columns by one.

**AudioSet source:** the Hugging Face mirror `agkphysics/AudioSet` (March 2023 snapshot). `hf_parquet.py` range-reads only the parquet pages that hold our clips. Clips the mirror lacks are logged as `missing_on_hf`; `--youtube-fallback` tries yt-dlp for them.

**Vetting rules:** don't look at the other rater's CSV until both are done. Labels are hidden in the tool on purpose. Use "unsure" freely: only clips both raters mark yes / yes / no (plus detonation = yes for knock clips) survive.

**Generated (Dipen):** `data/generated/` (git-ignored) holds the simulator dataset (`python -m models.make_dataset`) and soak caches. Its manifest, split by engine seed, is committed as `data/splits/sim_v2_dataset.json`. carOBD calibration code (`sim/calibrate_ecu.py`, `dsp/calibrate_trim.py`) reads only the `calibration` list (tested in `models/tests`).

**Loading EngineFaultDB:** use `data/enginefaultdb.py:load()`. Each class is one session (one contiguous block), and class 3 is a noised copy of class 2; see `docs/ENGINEFAULTDB.md`.
