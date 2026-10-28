"""Seeded audio laboratory sources; generation never starts audio playback."""

import numpy as np
from scipy import signal
from .common import integer, number
from .editing import MAX_SAMPLES
from ..domain.errors import ValidationError, ResourceLimit

PARAMETERS = {
    "kind",
    "sample_rate",
    "duration_seconds",
    "channels",
    "amplitude_db",
    "frequency_hz",
    "end_frequency_hz",
    "phase_degrees",
    "seed",
    "modulation_hz",
    "depth",
    "deviation_hz",
    "tones",
}


def generate(p):
    if set(p) - PARAMETERS:
        raise ValidationError("Unknown generator parameter.")
    fs = integer(p, "sample_rate", 48000, 8000, 384000)
    c = integer(p, "channels", 1, 1, 32)
    duration = number(p, "duration_seconds", 1, 1 / fs, 3600)
    n = round(duration * fs)
    if n * c > MAX_SAMPLES:
        raise ResourceLimit("Generated audio exceeds 8M-sample working budget.")
    kind = p.get("kind", "sine")
    t = np.arange(n) / fs
    f = number(p, "frequency_hz", 440, 0, fs / 2 - 0.001)
    phase = number(p, "phase_degrees", 0, -36000, 36000) * np.pi / 180
    a = 10 ** (number(p, "amplitude_db", -18, -120, 0) / 20)
    rng = np.random.default_rng(integer(p, "seed", 0, 0, 2**32 - 1))
    if kind == "sine":
        x = np.sin(2 * np.pi * f * t + phase)
    elif kind in {"square", "sawtooth"}:
        # Explicit Fourier construction, excludes all harmonics at/above Nyquist.
        if f <= 0:
            raise ValidationError("Periodic source requires positive frequency.")
        x = np.zeros(n)
        for k in range(1, min(4096, int((fs / 2 - 1e-9) / f)) + 1):
            if kind == "square" and k % 2 == 0:
                continue
            x += np.sin(k * (2 * np.pi * f * t + phase)) / k * (1 if kind == "square" else (-1) ** (k + 1))
        x *= 4 / np.pi if kind == "square" else 2 / np.pi
    elif kind in {"white", "pink", "brown"}:
        x = rng.normal(size=n)
        if kind != "white":
            freqs = np.fft.rfftfreq(n, 1 / fs)
            weight = np.zeros_like(freqs)
            weight[1:] = freqs[1:] ** (-0.5 if kind == "pink" else -1)
            x = np.fft.irfft(np.fft.rfft(x) * weight, n=n)
        peak = np.max(np.abs(x))
        x = x / peak if peak else x
    elif kind in {"chirp", "log_sweep"}:
        end = number(p, "end_frequency_hz", min(20000, fs / 2 - 1), 0.001, fs / 2 - 0.001)
        if f <= 0:
            raise ValidationError("Sweep start must be positive.")
        x = signal.chirp(
            t,
            f0=f,
            f1=end,
            t1=n / fs,
            method="linear" if kind == "chirp" else "logarithmic",
            phi=p.get("phase_degrees", 0),
        )
        fade = min(n // 2, max(1, round(fs * 0.005)))
        env = np.ones(n)
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        x *= env
    elif kind in {"am", "fm"}:
        m = number(p, "modulation_hz", 5, 0.001, fs / 4)
        if kind == "am":
            depth = number(p, "depth", 0.5, 0, 1)
            if f + m >= fs / 2 or f - m <= 0:
                raise ValidationError("AM sidebands must be inside (0, Nyquist).")
            x = (1 + depth * np.sin(2 * np.pi * m * t)) / (1 + depth) * np.sin(2 * np.pi * f * t + phase)
        else:
            dev = number(p, "deviation_hz", 50, 0, fs / 4)
            if f - dev - m <= 0 or f + dev + m >= fs / 2:
                raise ValidationError("FM Carson-band estimate exceeds available band.")
            x = np.sin(2 * np.pi * f * t - (dev / m) * np.cos(2 * np.pi * m * t) + phase)
    elif kind == "multitone":
        tones = p.get("tones", [440, 880])
        if not isinstance(tones, list) or not 1 <= len(tones) <= 256:
            raise ValidationError("Use 1–256 tone frequencies.")
        x = np.zeros(n)
        for v in tones:
            x += np.sin(2 * np.pi * number({"f": v}, "f", 440, 0.001, fs / 2 - 0.001) * t + phase)
        x /= len(tones)
    elif kind == "impulse":
        x = np.zeros(n)
        x[0] = 1
    elif kind == "step":
        x = np.ones(n)
    elif kind == "silence":
        x = np.zeros(n)
    else:
        raise ValidationError("Unknown generator kind.")
    return (
        np.repeat((a * x)[:, None], c, axis=1),
        fs,
        {
            "kind": kind,
            "seed": p.get("seed", 0),
            "amplitude_reference": "peak bound except Fourier overshoot",
            "playback": "not started",
        },
    )
