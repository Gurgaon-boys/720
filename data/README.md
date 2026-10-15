# data/

**Owner:** Diljit

Download and vetting scripts and split lists (plan section 4). **No raw audio or CSV is committed.** `data/raw/` and `data/audio/` are git-ignored.

- **AudioSet** ("Idling", "Engine", "Engine knocking"): download broadly, expect dead links, and keep only clips that two people independently agree are a passenger car, plausibly 4-cylinder, with no speech or music
- **carOBD:** healthy OBD-II logs at ~1 Hz, split **by file** into calibration and held-out sets
- **ESC-50:** ambient noise for the robustness sweep, split by source clip
- **IEEE DataPort Fiesta set:** only if access appears; split by contiguous time blocks

The split lists live in `splits/` and are written at H0, **with a timestamp, before any training**.
