# data/splits/

**Owner:** Diljit

Timestamped split lists, committed at H0 before any training. Never edit them after training starts; add a new dated file and log the change in `docs/DECISIONS.md`.

- **AudioSet pools, split by YouTube video ID** (clips from one video never cross pools):
  - `P1`: simulator background texture only
  - `P2`: injected training (additive knock / valve tick)
  - `P3`: held-out real test (false alarms, knock check, demo replay). Never touched by training or the simulator.
- **carOBD, split by file:** calibration vs held-out, with at least one held-out file of each type (idle, drive, campus, long trip)

Suggested names: `audioset_pools_<YYYYMMDD-HHMM>.csv` (columns `video_id,pool,engine_tag`) and `carobd_split_<YYYYMMDD-HHMM>.csv` (columns `file,type,role`).
