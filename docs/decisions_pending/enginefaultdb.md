# Pending decisions: EngineFaultDB (feature/enginefaultdb)

For whoever owns `docs/DECISIONS.md` to number and merge. Nothing here has been written to `DECISIONS.md`.

**E1. EngineFaultDB is used only as a confound demonstration and a chemistry sanity check, never as a 720 result column.**
Each class is one recording session (one contiguous block of rows), so no split can separate fault from session. Its numbers stay out of columns A/B/C and out of any claim that 720 detects rich, lean or low-voltage faults. See `docs/ENGINEFAULTDB.md`.

**E2. Class 3 ("low voltage") is treated as a duplicate of class 2 ("lean").**
Rows 0–13,999 of class 3 equal rows 1,000–14,999 of class 2, with MAP and TPS bit-identical and small zero-mean noise on the rest (`eval/enginefaultdb_inventory.json`, `class3_copies_class2`). No low-voltage claim is drawn from this dataset. Results are reported with all 4 classes (as published) and with classes 0, 1, 2 only. *Open question:* report the copy upstream (GitHub issue on Leo-Thomas/EngineFaultDB). That is outward-facing, so it waits for the team's OK.

**E3. Label names are unconfirmed.**
The README gives counts but not names. We use 0 normal / 1 rich / 2 lean / 3 low voltage, flagged `labels_confirmed_against_paper: false` in every output. The rich/lean chemistry is consistent; nothing in the data confirms or refutes low voltage. Revisit if someone gets the full paper.

**E4. Split, written before training (`data/splits/enginefaultdb_split.json`, commit 3738689).**
The temporal split takes, within each class block, the first 70% for train, a 5% gap, and the last 25% for test. A random 25% per class (seed 720, hash-checked) is kept only to show the leak.

**E5. CIs on autocorrelated rows.**
Wilson intervals over rows are about ±0.008 and overstate precision, because neighbouring rows are near-duplicates. The quoted CI is a block bootstrap over operating-point segments (|ΔRPM| > 500 starts a segment). Wilson stays in the JSON because the convention asks for it. Macro-F1 has no Wilson interval: it is not a proportion.

**E6. GPL-3 data handling.**
The CSV lives only in `data/raw/enginefaultdb/` (already git-ignored by `data/raw/`). We commit only scripts, the split, derived summary statistics and results. The dataset is cited in every output JSON and in `docs/ENGINEFAULTDB.md`.
