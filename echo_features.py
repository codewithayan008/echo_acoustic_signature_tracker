from __future__ import annotations

import numpy as np
from scipy.signal import periodogram

FEATURE_LABELS = [
    "rms_energy",
    "zero_crossing_rate",
    "spectral_centroid_hz",
    "spectral_bandwidth_hz",
    "spectral_rolloff_hz",
    "dominant_frequency_hz",
    "crest_factor",
    "duration_seconds",
]


def extract_features(audio: np.ndarray, sample_rate: int):
    """Return a compact, explainable feature vector for an assessment recording."""
    signal = np.asarray(audio, dtype=np.float32)
    signal = signal - np.mean(signal)
    window = np.hanning(len(signal))
    spectrum = np.abs(np.fft.rfft(signal * window))
    frequencies = np.fft.rfftfreq(len(signal), 1 / sample_rate)
    power = spectrum**2
    total_power = float(np.sum(power)) or 1.0

    centroid = float(np.sum(frequencies * power) / total_power)
    bandwidth = float(np.sqrt(np.sum(((frequencies - centroid) ** 2) * power) / total_power))
    cumulative = np.cumsum(power)
    rolloff_index = int(np.searchsorted(cumulative, cumulative[-1] * 0.85))
    rolloff = float(frequencies[min(rolloff_index, len(frequencies) - 1)])
    dominant = float(frequencies[int(np.argmax(power))])
    rms = float(np.sqrt(np.mean(signal**2)))
    crossings = np.count_nonzero(np.diff(np.signbit(signal))) / max(len(signal) - 1, 1)
    crest = float(np.max(np.abs(signal)) / max(rms, 1e-6))

    values = np.array(
        [rms, crossings, centroid, bandwidth, rolloff, dominant, crest, len(signal) / sample_rate],
        dtype=float,
    )
    return values, FEATURE_LABELS
