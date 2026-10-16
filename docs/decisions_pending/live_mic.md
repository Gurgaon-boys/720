# Pending decision: optional live microphone (branch `feature/live-mic`)

Not numbered yet; `docs/DECISIONS.md` is untouched.

**What.** An optional "Live mic" tab, shown only with `?mic=1`. The browser streams 48 kHz mono float32 in 0.5 s chunks over a new WebSocket `/ws/mic`. That is a separate protocol, so none of the contract's three channels changes. The backend scores each 1 s window (0.5 s hop) with the **existing real-audio path, unchanged**: `models/real_audio/features.py` plus the committed AudioSet P3 healthy model and thresholds (`models/thresholds/audio_healthy.json`), the same path used for P3 and the ESC-50 sweep (D127). It also computes the knock-band score (5–15 kHz envelope kurtosis against its committed threshold). Nothing is fitted. A streaming test checks that the windows equal the offline path's.

**Meters only, no verdict.** This was measured before building. The pre-registered weak knock check of this exact code on the vetted clips (`eval/audio_p3.json`, REAL AudioSet, column C, clips with ≥ 15 kHz bandwidth):

| Clips | Knock-band score over its limit | 95% (Wilson) |
|---|---|---|
| knock_check | 1/19 (5.3 %) | 0.9–24.6 % |
| P3 healthy | 1/32 (3.1 %) | 0.6–15.7 % |

Post-hoc AUROC 0.595 (bootstrap 95 % CI 0.43–0.75). Knock and healthy aren't separated, so per the plan the tab shows only the spectrogram, the knock-band meter, the anomaly meter and the input level. It has no verdict line, and the badge reads "REAL · LIVE MIC · audio only: no cylinder, ADVISORY at most". The tab is isolated: nothing reaches the rule layer, the alarm banner or OPC UA.

**Not re-measured.** The AudioSet clips aren't on this laptop (`data/raw/audioset` is missing), so `python -m eval.live_mic_measure`, which re-runs the path on all 25 knock and 56 P3 clips including the knock clips' anomaly flag rates, wasn't run. Fetching them (`python data/audioset_fetch.py`, Hugging Face mirror) needs an OK. The knock-band numbers would be identical: the code, thresholds and clips are deterministic. For reference, P3 anomaly flags were 2/1,042 windows (0.19 %, Wilson 0.05–0.70 %), and 1/56 P3 clips had any flag (`eval/audio_p3.json`).

**Limits.**
- 48 kHz only. A browser that can't open the microphone at 48 kHz gets an error, not a resample.
- The live knock-band filter is causal (offline uses zero-phase filtering), so its kurtosis can differ slightly from the offline score.
- A laptop microphone in a room isn't the AudioSet recording chain.
