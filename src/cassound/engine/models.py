"""Fitted statistical audio models, distinct from pretrained neural models."""

import numpy as np
from scipy import signal
from .common import dense, output, integer, number
from ..domain.errors import ValidationError


def separate(read, n, c, fs, p):
    from sklearn.decomposition import NMF

    components = integer(p, "components", 2, 2, 8)
    x = dense(read, n, c).mean(axis=1)
    if len(x) < 1024:
        raise ValidationError("Need >=1024 samples.")
    _, _, z = signal.stft(x, fs, nperseg=512, noverlap=384)
    magnitude = abs(z)
    model = NMF(n_components=components, init="nndsvda", random_state=0, max_iter=300)
    w = model.fit_transform(magnitude)
    h = model.components_
    reconstructed = w @ h
    arrays = {}
    for i in range(components):
        mask = (w[:, i, None] * h[None, i, :]) / np.maximum(reconstructed, 1e-20)
        _, wave = signal.istft(z * mask, fs, nperseg=512, noverlap=384)
        arrays[f"component_{i}"] = wave[: len(x)]
    return output(
        arrays,
        {
            "method": "Per-recording fitted nonnegative spectral factors, not instrument/speaker labels.",
            "iterations": model.n_iter_,
        },
        {k: "FS" for k in arrays},
    )


def dereverberate(read, n, c, fs, p):
    taps = integer(p, "taps", 8, 2, 32)
    delay = integer(p, "delay_frames", 64, 1, 4096)
    ridge = number(p, "ridge", 0.001, 1e-9, 1)
    x = dense(read, n, c)
    start = delay + taps
    if n < start + 128:
        raise ValidationError("Recording too short.")
    arrays = {}
    for channel in range(c):
        y = x[:, channel]
        indices = np.arange(start, n, max(1, (n - start) // 100000))
        predictors = np.column_stack([y[indices - delay - j] for j in range(taps)])
        gram = predictors.T @ predictors
        coef = np.linalg.solve(
            gram + ridge * max(np.trace(gram) / taps, 1e-12) * np.eye(taps), predictors.T @ y[indices]
        )
        residual = y.copy()
        for j in range(taps):
            residual[start:] -= coef[j] * y[start - delay - j : n - delay - j]
        arrays[f"channel_{channel}"] = residual
    return output(
        arrays,
        {
            "method": "Delayed linear prediction residual, not neural dereverberation.",
            "delay_frames": delay,
            "taps": taps,
        },
        {k: "FS" for k in arrays},
    )
