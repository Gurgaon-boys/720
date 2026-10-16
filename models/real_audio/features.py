"""Features for real engine audio that don't depend on cylinder count or RPM (DECISIONS D12).

Windows: 1.0 s, 0.5 s hop (real clips have no crank angle, so no cycle windows).
Anomaly features are computed in 0-4 kHz only: YouTube codecs cut 7% of AudioSet clips below
5 kHz (D59), and bands above a clip's cut-off would measure the codec, not the engine.

Knock score (weak knock check only): impulsiveness of the 5-15 kHz band, i.e. the kurtosis of its
Hilbert envelope per window, median over the clip's windows. Detonation is a train of short
ringing bursts in that band; steady engine noise gives a Gaussian-like envelope. Only meaningful on
clips whose measured bandwidth reaches 15 kHz.
"""
from __future__ import annotations

import numpy as np
import soundfile as sf
from scipy.fft import dct
from scipy.signal import butter, hilbert, sosfiltfilt
from scipy.stats import kurtosis

SR = 48_000
WIN_S, HOP_S = 1.0, 0.5
NFFT = 4096
FMAX = 4000.0
BANDS = ((0, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 3000), (3000, 4000))
N_MEL, N_MFCC = 26, 8
KNOCK_BAND = (5000.0, 15000.0)

FEATURES = ([f"band_{lo}_{hi}" for lo, hi in BANDS]
            + ["centroid_hz", "rolloff85_hz", "flatness", "kurtosis", "crest_db"]
            + [f"mfcc_{i}" for i in range(1, N_MFCC + 1)])


def load(path) -> np.ndarray:
    x, sr = sf.read(path, dtype="float32")
    if sr != SR:
        raise ValueError(f"{path}: expected {SR} Hz, got {sr}")
    return x.astype(float)


def frames(x: np.ndarray, win_s: float = WIN_S, hop_s: float = HOP_S) -> list[np.ndarray]:
    w, h = int(win_s * SR), int(hop_s * SR)
    return [x[i:i + w] for i in range(0, len(x) - w + 1, h)]


def _mel_fb(n_fft: int = NFFT, n_mel: int = N_MEL, fmax: float = FMAX) -> np.ndarray:
    mel = lambda f: 2595 * np.log10(1 + f / 700)
    imel = lambda m: 700 * (10 ** (m / 2595) - 1)
    edges = imel(np.linspace(mel(0), mel(fmax), n_mel + 2))
    f = np.fft.rfftfreq(n_fft, 1 / SR)
    fb = np.zeros((n_mel, len(f)))
    for i in range(n_mel):
        lo, c, hi = edges[i:i + 3]
        fb[i] = np.clip(np.minimum((f - lo) / (c - lo), (hi - f) / (hi - c)), 0, None)
    return fb


_FB = _mel_fb()
_F = np.fft.rfftfreq(NFFT, 1 / SR)
_LOW = _F <= FMAX


def window_features(w: np.ndarray) -> list[float]:
    # averaged power spectrum (Welch-like, 4096-point Hann segments, 50% overlap)
    seg = [w[i:i + NFFT] * np.hanning(NFFT) for i in range(0, len(w) - NFFT + 1, NFFT // 2)]
    p = np.mean([np.abs(np.fft.rfft(s)) ** 2 for s in seg], axis=0) + 1e-20
    pl, fl = p[_LOW], _F[_LOW]
    tot = pl.sum()
    bands = [np.log10(p[(_F >= lo) & (_F < hi)].sum() / tot + 1e-12) for lo, hi in BANDS]
    centroid = float((fl * pl).sum() / tot)
    rolloff = float(fl[np.searchsorted(np.cumsum(pl), 0.85 * tot)])
    flatness = float(np.exp(np.mean(np.log(pl))) / np.mean(pl))
    mfcc = dct(np.log(_FB @ p + 1e-20), type=2, norm="ortho")[1:N_MFCC + 1]
    rms = np.sqrt(np.mean(w ** 2)) + 1e-12
    return [*bands, centroid, rolloff, float(np.log10(flatness + 1e-12)), float(kurtosis(w)),
            float(20 * np.log10(np.max(np.abs(w)) / rms)), *map(float, mfcc)]


SILENCE_RMS = 1e-5  # digital silence (some YouTube clips have muted stretches): not engine audio


def array_features(x: np.ndarray) -> np.ndarray:
    """(n_windows, len(FEATURES)) for an audio array; windows of digital silence are skipped."""
    ws = [w for w in frames(x) if np.sqrt(np.mean(w ** 2)) > SILENCE_RMS]
    return np.array([window_features(w) for w in ws]).reshape(len(ws), len(FEATURES))


def clip_features(path) -> np.ndarray:
    return array_features(load(path))


# ---- high-frequency features for column B (injected knock / valve tick), clips >= 8 kHz bandwidth
HF_BANDS = ((4000, 6000), (6000, 8000), (8000, 10000), (10000, 12000), (12000, 14000))
HF_FEATURES = [f"hf_{lo // 1000}_{hi // 1000}k_db" for lo, hi in HF_BANDS] + ["hf_env_kurtosis", "hf_crest_db"]
FEATURES_B = FEATURES + HF_FEATURES


def hf_window_features(w: np.ndarray, hf: np.ndarray) -> list[float]:
    """w: the window; hf: the same window band-passed 5 kHz .. min(15 kHz, clip bandwidth)."""
    seg = [w[i:i + NFFT] * np.hanning(NFFT) for i in range(0, len(w) - NFFT + 1, NFFT // 2)]
    p = np.mean([np.abs(np.fft.rfft(s)) ** 2 for s in seg], axis=0) + 1e-20
    low = p[_LOW].sum()
    bands = [10 * np.log10(p[(_F >= lo) & (_F < hi)].sum() / low + 1e-12) for lo, hi in HF_BANDS]
    rms = np.sqrt(np.mean(hf ** 2)) + 1e-12
    return [*map(float, bands), float(kurtosis(np.abs(hilbert(hf)))), float(20 * np.log10(np.max(np.abs(hf)) / rms))]


def features_b(x: np.ndarray, bandwidth_hz: float) -> tuple[np.ndarray, np.ndarray]:
    """(features (n, len(FEATURES_B)), window start times (n,)) for an audio array; silence skipped."""
    hi = min(15000.0, 0.95 * bandwidth_hz)
    sos = butter(4, (5000.0, hi), btype="bandpass", fs=SR, output="sos")
    xh = sosfiltfilt(sos, x)
    w_n, h_n = int(WIN_S * SR), int(HOP_S * SR)
    rows, starts = [], []
    for i in range(0, len(x) - w_n + 1, h_n):
        w = x[i:i + w_n]
        if np.sqrt(np.mean(w ** 2)) <= SILENCE_RMS:
            continue
        rows.append(window_features(w) + hf_window_features(w, xh[i:i + w_n]))
        starts.append(i / SR)
    return np.array(rows).reshape(len(rows), len(FEATURES_B)), np.array(starts)


_KNOCK_SOS = butter(4, KNOCK_BAND, btype="bandpass", fs=SR, output="sos")


def knock_score(path) -> float:
    """Median over windows of the 5-15 kHz envelope kurtosis (higher = more impulsive)."""
    y = sosfiltfilt(_KNOCK_SOS, load(path))
    return float(np.median([kurtosis(np.abs(hilbert(w))) for w in frames(y)]))
