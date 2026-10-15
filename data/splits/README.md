# data/splits/

**Owner:** Diljit

Timestamped split lists, committed at H0 before any training. Never edit them after training starts; add a new dated file and log the change in `docs/DECISIONS.md`.

- **AudioSet pools, split by YouTube video ID** (clips from one video never cross pools):
  - `P1`: simulator background texture only
  - `P2`: injected training (additive knock / valve tick)
  - `P3`: held-out real test (false alarms, knock check, demo replay). Never touched by training or the simulator.
- **carOBD, split by file:** calibration vs held-out, with at least one held-out file of each type (idle, drive, campus, long trip)

Files (JSON, each with `created_at` in UTC, `git_commit` and `seed` inside; see DECISIONS D30):

- `carobd_split.json`: `calibration` / `heldout` file lists, per-type rows and hours (`data/carobd_split.py`)
- `audioset_pools.json`: P1 / P2 / P3 / `knock_check` entries `{clip_id, video_id, start, engine_tag}` with counts (`data/make_pools.py`, after vetting)
- `esc50_split.json`: `noise_train` / `noise_test` file lists, split by source clip (`data/esc50_split.py`)
