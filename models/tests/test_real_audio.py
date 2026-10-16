import numpy as np
import soundfile as sf

from models.real_audio import features as F


def test_features_shape_and_knock_score(tmp_path):
    rng = np.random.default_rng(0)
    t = np.arange(10 * F.SR) / F.SR
    steady = 0.1 * np.sin(2 * np.pi * 120 * t) + 0.01 * rng.normal(size=t.size)
    sf.write(tmp_path / "steady.wav", steady.astype("float32"), F.SR)
    x = F.clip_features(tmp_path / "steady.wav")
    assert x.shape == (19, len(F.FEATURES)) and np.isfinite(x).all()
    # add periodic 8 kHz ringing bursts (knock-like) -> more impulsive 5-15 kHz envelope
    knock = steady.copy()
    for t0 in np.arange(0.05, 10, 0.07):
        i = int(t0 * F.SR)
        n = np.arange(int(0.003 * F.SR))
        knock[i:i + n.size] += 0.05 * np.sin(2 * np.pi * 8000 * n / F.SR) * np.exp(-n / (0.0008 * F.SR))
    sf.write(tmp_path / "knock.wav", knock.astype("float32"), F.SR)
    assert F.knock_score(tmp_path / "knock.wav") > F.knock_score(tmp_path / "steady.wav") + 1
