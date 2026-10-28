import numpy as np
from scipy import signal
from ..domain.errors import ValidationError, ResourceLimit, BackendUnavailable


def number(p, key, default, low=-np.inf, high=np.inf):
    value = p.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not np.isfinite(value)
        or not low <= value <= high
    ):
        raise ValidationError(f"{key} must be a finite number in [{low}, {high}].")
    return float(value)


def integer(p, key, default, low=1, high=65536):
    value = p.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"{key} must be an integer in [{low}, {high}].")
    return value


def optional(module):
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise BackendUnavailable(f"Install cassound[features] for {module}.") from exc


def window(p, length):
    name = p.get("window", "hann")
    if name not in {"hann", "hamming", "boxcar", "blackman", "flattop", "bartlett"}:
        raise ValidationError("Choose hann, hamming, boxcar, blackman, flattop or bartlett window.")
    return signal.get_window(name, length, fftbins=True)


def frame_config(p, n, fs):
    length = integer(p, "frame_length", p.get("nperseg", min(2048, n)), 8, 65536)
    if length > n:
        raise ValidationError("Selection is shorter than the analysis window.")
    hop = integer(p, "hop_length", length - p.get("noverlap", length // 2), 1, length)
    frames = 1 + (n - length) // hop
    if frames > 1000000:
        raise ResourceLimit("Too many feature frames. Increase hop length or select a shorter region.")
    return length, hop, frames


def frame_batches(read, n, channels, p, fs):
    length, hop, count = frame_config(p, n, fs)
    batch = max(1, min(256, 16 * 1024**2 // max(1, length * channels * 32)))
    for first in range(0, count, batch):
        size = min(batch, count - first)
        block = read(first * hop, (first + size - 1) * hop + length)
        frames = np.lib.stride_tricks.sliding_window_view(block, length, axis=0)[::hop].transpose(0, 2, 1)
        yield first, frames


def dense(read, n, channels, maximum=8000000):
    if n * channels > maximum:
        raise ResourceLimit(
            "This dense transform exceeds its working-size budget. Select a shorter region or fewer channels."
        )
    return read(0, n)


def db(value, power=False):
    return (10 if power else 20) * np.log10(np.maximum(np.asarray(value), 1e-30))


def output(arrays=None, summary=None, units=None, presentation=None):
    return {
        "arrays": arrays or {},
        "summary": summary or {},
        "units": units or {},
        "presentation": presentation or {},
    }
