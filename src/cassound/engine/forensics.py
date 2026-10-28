"""Forensic measurements and candidates; no authenticity verdicts."""

import numpy as np
from scipy import signal
from .common import dense, output, number, integer
from ..domain.errors import ValidationError


def discontinuities(read, n, c, fs, p):
    x = dense(read, n, c)
    threshold = number(p, "threshold", 0.25, 1e-9, 2)
    jumps = np.max(abs(np.diff(x, axis=0)), axis=1)
    indices = np.flatnonzero(jumps >= threshold) + 1
    return output(
        {"frame": indices, "time": indices / fs, "jump_fs": jumps[indices - 1]},
        {"method": "Sample jump candidates; natural transients can trigger.", "threshold_fs": threshold},
        {"frame": "frame", "time": "s", "jump_fs": "FS"},
    )


def enf(read, n, c, fs, p):
    nominal = number(p, "nominal_hz", 50, 40, 70)
    band = number(p, "bandwidth_hz", 1, 0.1, 5)
    window = integer(p, "window_seconds", 5, 2, 60)
    if fs <= 2 * (nominal + band) or n < 4 * window * fs:
        raise ValidationError("ENF needs at least four windows and adequate sample rate.")
    x = dense(read, n, c).mean(axis=1)
    x = signal.sosfiltfilt(
        signal.butter(4, [nominal - band, nominal + band], fs=fs, btype="bandpass", output="sos"), x
    )
    phase = np.unwrap(np.angle(signal.hilbert(x)))
    width = round(window * fs)
    starts = np.arange(width, n - 2 * width + 1, width)
    t = np.arange(width) / fs
    values = np.array([np.polyfit(t, phase[s : s + width], 1)[0] / (2 * np.pi) for s in starts])
    return output(
        {"time": (starts + width / 2) / fs, "frequency": values},
        {
            "method": "Bandpass Hilbert phase slope; supplied-reference matching required for attribution.",
            "nominal_hz": nominal,
        },
        {"time": "s", "frequency": "Hz"},
    )


def compare(reference, candidate, fs, max_delay_seconds=1):
    a, b = np.asarray(reference, float), np.asarray(candidate, float)
    if (
        a.ndim != 1
        or b.ndim != 1
        or min(len(a), len(b)) < 16
        or max(len(a), len(b)) > 8_000_000
        or fs <= 0
        or not 0 <= max_delay_seconds <= 10
        or not np.all(np.isfinite(np.r_[a, b]))
    ):
        raise ValidationError("Bounded finite mono audio required.")
    corr = signal.correlate(b - b.mean(), a - a.mean(), method="fft")
    lags = signal.correlation_lags(len(b), len(a))
    selected = abs(lags) <= round(max_delay_seconds * fs)
    delay = int(lags[selected][np.argmax(abs(corr[selected]))])
    sa = max(0, -delay)
    sb = max(0, delay)
    length = min(len(a) - sa, len(b) - sb)
    aa = a[sa : sa + length]
    bb = b[sb : sb + length]
    energy = float(aa @ aa)
    if energy < 1e-20:
        raise ValidationError("Insufficient reference energy.")
    gain = float(aa @ bb / energy)
    residual = bb - gain * aa
    return {
        "delay_frames": delay,
        "gain": gain,
        "aligned_frames": length,
        "residual": residual,
        "residual_rms": float(np.sqrt(np.mean(residual**2))),
        "interpretation": "Cross-correlation alignment and least-squares gain; no authenticity conclusion.",
    }


def match_enf(times, frequency, reference_times, reference_frequency, max_shift_seconds=60):
    times = np.asarray(times, float)
    frequency = np.asarray(frequency, float)
    rt = np.asarray(reference_times, float)
    rf = np.asarray(reference_frequency, float)
    if (
        times.size < 3
        or rt.size < 3
        or rt.shape != rf.shape
        or not np.all(np.isfinite(np.r_[times, frequency, rt, rf]))
        or np.any(np.diff(rt) <= 0)
        or not 0 <= max_shift_seconds <= 86400
    ):
        raise ValidationError("ENF reference requires finite, increasing times and at least three points.")
    candidates = []
    for shift in range(-max_shift_seconds, max_shift_seconds + 1):
        t = times + shift
        valid = (t >= rt[0]) & (t <= rt[-1])
        if valid.sum() < max(3, len(times) // 2):
            continue
        expected = np.interp(t[valid], rt, rf)
        rmse = float(np.sqrt(np.mean((frequency[valid] - expected) ** 2)))
        candidates.append((rmse, shift, int(valid.sum())))
    if not candidates:
        raise ValidationError("No sufficient overlap with supplied ENF reference.")
    candidates.sort()
    best = candidates[0]
    return {
        "rmse_hz": best[0],
        "shift_seconds": best[1],
        "matched_windows": best[2],
        "alternatives": [
            {"rmse_hz": r, "shift_seconds": s, "matched_windows": n} for r, s, n in candidates[:10]
        ],
        "interpretation": "Fit to supplied reference only; periodic/flat traces can be ambiguous. No independent timestamp/location authentication.",
    }


def recorder_profile(x, fs):
    x = np.asarray(x, float)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if len(x) < fs or len(x) > 8_000_000:
        raise ValidationError("Profile requires 1 s or more bounded audio.")
    f, p = signal.welch(x, fs, nperseg=min(4096, len(x)))
    total = max(float(p.sum()), 1e-30)
    vector = [
        float(np.mean(x)),
        float(np.sqrt(np.mean(x * x))),
        float(np.mean(abs(x) >= 0.999)),
        float(np.sum(f * p) / total / max(fs / 2, 1)),
        float(np.exp(np.mean(np.log(p + 1e-30))) / (np.mean(p) + 1e-30)),
    ]
    return {
        "version": 1,
        "sample_rate": fs,
        "features": vector,
        "feature_names": [
            "dc_fs",
            "rms_fs",
            "clip_fraction",
            "nyquist_normalized_centroid",
            "spectral_flatness",
        ],
    }


def profile_compare(candidate, reference):
    reference = reference.get("profile", reference)
    if reference.get("version") != 1 or reference.get("feature_names") != candidate["feature_names"]:
        raise ValidationError("Unsupported reference feature profile.")
    a = np.asarray(candidate["features"], float)
    b = np.asarray(reference["features"], float)
    if a.shape != (5,) or b.shape != (5,) or not np.all(np.isfinite(np.r_[a, b])):
        raise ValidationError("Invalid recorder profile.")
    distance = float(np.linalg.norm((a - b) / np.maximum(abs(b), 0.01)))
    return {
        "distance": distance,
        "candidate_profile": candidate,
        "reference_profile": reference,
        "identity": "unknown",
        "interpretation": "Recording-content-sensitive feature distance; no device attribution from a single reference.",
    }


def spectral_changes(read, n, c, fs, p):
    length = integer(p, "frame_length", 2048, 128, 65536)
    threshold = number(p, "threshold_db", 12, 0.1, 120)
    x = dense(read, n, c).mean(axis=1)
    if len(x) < 2 * length:
        raise ValidationError("Need at least two complete frames.")
    blocks = x[: len(x) // length * length].reshape(-1, length)
    power = abs(np.fft.rfft(blocks * np.hanning(length), axis=1)) ** 2 + 1e-20
    rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    distance = np.sqrt(np.mean(np.diff(10 * np.log10(power), axis=0) ** 2, axis=1))
    indices = np.flatnonzero(distance >= threshold) + 1
    return output(
        {
            "frame": indices * length,
            "time": indices * length / fs,
            "spectral_change_db": distance[indices - 1],
            "rms_before": rms[indices - 1],
            "rms_after": rms[indices],
        },
        {
            "method": "Adjacent-window log-spectrum change candidates; natural scene transitions also trigger.",
            "frame_length": length,
            "threshold_db": threshold,
        },
        {"frame": "frame", "time": "s", "spectral_change_db": "dB", "rms_before": "FS", "rms_after": "FS"},
    )
