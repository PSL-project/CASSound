"""Window-normalized numerical measurements, with explicit one-sided conventions."""

import numpy as np
from scipy import signal, fft
from .common import number, integer, window, frame_config, frame_batches, dense, db, output
from ..domain.errors import ValidationError, ResourceLimit


def metrics(read, n, c, fs, p):
    total = np.zeros(c)
    squares = np.zeros(c)
    peak = np.zeros(c)
    minimum = np.full(c, np.inf)
    maximum = -minimum
    crossings = np.zeros(c, dtype="int64")
    last = None
    for start in range(0, n, 65536):
        x = read(start, min(n, start + 65536))
        total += x.sum(axis=0)
        squares += np.einsum("ij,ij->j", x, x)
        peak = np.maximum(peak, np.max(abs(x), axis=0))
        minimum = np.minimum(minimum, x.min(axis=0))
        maximum = np.maximum(maximum, x.max(axis=0))
        if last is not None:
            crossings += np.signbit(last) != np.signbit(x[0])
        crossings += np.sum(np.diff(np.signbit(x), axis=0), axis=0)
        last = x[-1]
    rms = np.sqrt(squares / n)
    return output(
        {
            "channel": np.arange(c),
            "peak": peak,
            "rms": rms,
            "dc": total / n,
            "mean_square": squares / n,
            "signal_energy": squares / fs,
            "crest_factor": np.divide(peak, rms, out=np.full(c, np.nan), where=rms > 0),
            "minimum": minimum,
            "maximum": maximum,
            "zero_crossings": crossings,
        },
        {
            "frames": n,
            "duration_seconds": n / fs,
            "energy_definition": "sum(x^2)/fs; not acoustic joules",
            "silence_crest_factor": "undefined (NaN)",
        },
        {
            "peak": "FS",
            "rms": "FS",
            "dc": "FS",
            "mean_square": "FS²",
            "signal_energy": "FS² s",
            "crest_factor": "ratio",
            "zero_crossings": "count",
        },
    )


def histogram(read, n, c, fs, p):
    bins = integer(p, "bins", 128, 2, 4096)
    lo = number(p, "minimum", -1)
    hi = number(p, "maximum", 1)
    if lo >= hi:
        raise ValidationError("Histogram minimum must be below maximum.")
    edges = np.linspace(lo, hi, bins + 1)
    counts = np.zeros((bins, c), dtype="int64")
    under = np.zeros(c, dtype="int64")
    over = under.copy()
    for start in range(0, n, 65536):
        x = read(start, min(n, start + 65536))
        for ch in range(c):
            counts[:, ch] += np.histogram(x[:, ch], edges)[0]
            under[ch] += np.sum(x[:, ch] < lo)
            over[ch] += np.sum(x[:, ch] > hi)
    return output(
        {"edges": edges, "counts": counts, "underflow": under, "overflow": over},
        units={"edges": "FS", "counts": "count"},
        presentation={"x": "edges", "y": "counts", "kind": "histogram"},
    )


def spectrum(read, n, c, fs, p):
    x = dense(read, n, c)
    win = window(p, n)
    nfft = integer(p, "nfft", n, n, 8388608)
    if nfft * c > 16000000:
        raise ResourceLimit("FFT output budget exceeded.")
    if p.get("detrend", False):
        x = x - x.mean(axis=0)
    z = fft.rfft(x * win[:, None], n=nfft, axis=0)
    amplitude = abs(z) / win.sum()
    amplitude[1:] *= 2
    if nfft % 2 == 0:
        amplitude[-1] /= 2
    return output(
        {
            "frequency": fft.rfftfreq(nfft, 1 / fs),
            "amplitude": amplitude,
            "phase": np.angle(z),
            "phase_valid": amplitude > amplitude.max(axis=0) * 1e-8,
        },
        {
            "window": p.get("window", "hann"),
            "bin_spacing_hz": fs / nfft,
            "observation_seconds": n / fs,
            "zero_padding_improves_resolution": False,
        },
        {"frequency": "Hz", "amplitude": "FS peak", "phase": "rad"},
        {"kind": "line", "x": "frequency", "y": "amplitude"},
    )


def welch(read, n, c, fs, p):
    length, hop, count = frame_config(p, n, fs)
    win = window(p, length)
    nfft = integer(p, "nfft", length, length, 131072)
    if nfft * c > 16000000:
        raise ResourceLimit("PSD output budget exceeded.")
    result = np.zeros((nfft // 2 + 1, c))
    detrend = p.get("detrend", True)
    for first, frames in frame_batches(read, n, c, p, fs):
        if detrend:
            frames = frames - frames.mean(axis=1, keepdims=True)
        z = fft.rfft(frames * win[None, :, None], n=nfft, axis=1)
        result += (abs(z) ** 2).sum(axis=0)
    scaling = p.get("scaling", "density")
    if scaling not in {"density", "spectrum"}:
        raise ValidationError("scaling must be density or spectrum.")
    result /= count * (fs * np.sum(win**2) if scaling == "density" else win.sum() ** 2)
    result[1:] *= 2
    if nfft % 2 == 0:
        result[-1] /= 2
    return output(
        {"frequency": fft.rfftfreq(nfft, 1 / fs), "psd": result},
        {
            "segments": count,
            "frame_length": length,
            "hop_length": hop,
            "window": p.get("window", "hann"),
            "scaling": scaling,
            "detrend": "constant" if detrend else False,
            "bin_spacing_hz": fs / nfft,
        },
        {"frequency": "Hz", "psd": "FS²/Hz" if scaling == "density" else "FS²"},
        {"kind": "line", "x": "frequency", "y": "psd"},
    )


def spectrogram(read, n, c, fs, p):
    length, hop, count = frame_config(p, n, fs)
    win = window(p, length)
    nfft = integer(p, "nfft", length, length, 65536)
    cells = (nfft // 2 + 1) * count * c
    if cells > 16000000:
        raise ResourceLimit(
            "Spectrogram output budget exceeded. Increase hop length or select a smaller region."
        )
    power = np.empty((count, nfft // 2 + 1, c))
    ridge = np.empty((count, c))
    frequency = fft.rfftfreq(nfft, 1 / fs)
    for first, frames in frame_batches(read, n, c, p, fs):
        z = fft.rfft(frames * win[None, :, None], n=nfft, axis=1)
        v = abs(z) ** 2 / (fs * np.sum(win**2))
        v[:, 1:] *= 2
        if nfft % 2 == 0:
            v[:, -1] /= 2
        power[first : first + len(v)] = v
        ridge[first : first + len(v)] = frequency[np.argmax(v[:, 1:, :], axis=1) + 1]
    ridge[power.max(axis=1) < 1e-25] = np.nan
    return output(
        {
            "time": (np.arange(count) * hop + length / 2) / fs,
            "frequency": frequency,
            "power": power,
            "ridge": ridge,
        },
        {
            "frame_length": length,
            "hop_length": hop,
            "padding": False,
            "window": p.get("window", "hann"),
            "time_resolution_seconds": length / fs,
        },
        {"time": "s relative to selection", "frequency": "Hz", "power": "FS²/Hz", "ridge": "Hz"},
        {"kind": "image", "x": "time", "y": "frequency", "z": "power"},
    )


def peaks(read, n, c, fs, p):
    result = welch(read, n, c, fs, p)
    f = result["arrays"]["frequency"]
    power = result["arrays"]["psd"]
    floor = number(p, "prominence_db", 10, 0, 200)
    rows = []
    for ch in range(c):
        logpower = db(power[:, ch], power=True)
        locations, properties = signal.find_peaks(logpower, prominence=floor)
        for i, idx in enumerate(locations):
            rows.append([ch, f[idx], power[idx, ch], properties["prominences"][i]])
    rows = sorted(rows, key=lambda row: row[2], reverse=True)[: integer(p, "max_peaks", 100, 1, 10000)]
    return output(
        {"peaks": np.asarray(rows, dtype=float).reshape(-1, 4)},
        {
            "columns": ["channel", "frequency_hz", "psd", "prominence_db"],
            "peak_definition": "local PSD peak; not necessarily pitch",
        },
        {"peaks": "mixed columns"},
        {"kind": "table", "y": "peaks"},
    )


def band_power(read, n, c, fs, p):
    result = welch(read, n, c, fs, p)
    f = result["arrays"]["frequency"]
    psd = result["arrays"]["psd"]
    low = number(p, "fmin", 0, 0, fs / 2)
    high = number(p, "fmax", fs / 2, 0, fs / 2)
    if low >= high:
        raise ValidationError("Band bounds must be ordered.")
    if p.get("scaling", "density") != "density":
        raise ValidationError("Band power requires PSD density scaling.")
    mask = (f >= low) & (f <= high)
    if not mask.any():
        raise ValidationError("Band contains no FFT bins; use a longer window.")
    power = np.sum(psd[mask], axis=0) * (f[1] - f[0])
    return output(
        {"power": power},
        {"fmin": low, "fmax": high, "integration": "sum linear PSD bins × df; bin-center bounds"},
        {"power": "FS²"},
    )


def harmonics(read, n, c, fs, p):
    fundamental = number(p, "fundamental", 0, 1, fs / 2)
    result = welch(read, n, c, fs, p)
    f = result["arrays"]["frequency"]
    psd = result["arrays"]["psd"]
    tolerance = number(p, "tolerance_hz", max(fs / frame_config(p, n, fs)[0], 2), 0, fs / 4)
    count = min(integer(p, "harmonics", 10, 1, 128), int(fs / 2 / fundamental))
    values = []
    freq = []
    for order in range(1, count + 1):
        mask = abs(f - order * fundamental) <= tolerance
        if not mask.any():
            raise ValidationError("Harmonic tolerance contains no frequency bin.")
        indices = np.flatnonzero(mask)
        best = indices[np.argmax(psd[mask], axis=0)]
        values.append(psd[best, np.arange(c)])
        freq.append(f[best])
    return output(
        {
            "order": np.arange(1, count + 1),
            "frequency": np.asarray(freq),
            "power_density": np.asarray(values),
        },
        {"assumed_fundamental_hz": fundamental, "tolerance_hz": tolerance},
        {"frequency": "Hz", "power_density": "FS²/Hz"},
        {"kind": "line", "x": "order", "y": "power_density"},
    )


def analytic(read, n, c, fs, p):
    x = dense(read, n, c)
    z = signal.hilbert(x, axis=0)
    phase = np.unwrap(np.angle(z), axis=0)
    envelope = abs(z)
    instantaneous = np.gradient(phase, axis=0) * fs / (2 * np.pi)
    valid = envelope > number(p, "envelope_floor", 1e-6, 0, 1e6)
    instantaneous[~valid] = np.nan
    return output(
        {
            "time": np.arange(n) / fs,
            "envelope": envelope,
            "phase": phase,
            "instantaneous_frequency": instantaneous,
            "valid": valid,
        },
        {
            "interpretation": "Instantaneous frequency is meaningful mainly for monocomponent/narrowband signals; Hilbert edge effects remain."
        },
        {"envelope": "FS", "phase": "rad", "instantaneous_frequency": "Hz"},
        {"kind": "line", "x": "time", "y": "envelope"},
    )


def relationships(read, n, c, fs, p):
    if c != 2:
        raise ValidationError("Select exactly two channels for channel relationships.")
    x = dense(read, n, c)
    mid = (x[:, 0] + x[:, 1]) / 2
    side = (x[:, 0] - x[:, 1]) / 2
    a = x[:, 0] - x[:, 0].mean()
    b = x[:, 1] - x[:, 1].mean()
    corr = signal.correlate(b, a, method="fft")
    lags = signal.correlation_lags(n, n)
    maxlag = number(p, "max_lag_seconds", min(0.5, n / fs), 0, n / fs)
    mask = abs(lags) <= maxlag * fs
    values = corr[mask]
    lag = lags[mask][np.argmax(abs(values))]
    coefficient = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else None
    stride = max(1, n // 10000)
    return output(
        {
            "time": np.arange(n) / fs,
            "mid": mid,
            "side": side,
            "lissajous": x[::stride],
            "lag_seconds": lags[mask] / fs,
            "cross_correlation": values,
        },
        {
            "pearson_correlation": coefficient,
            "second_channel_delay_seconds": float(lag / fs),
            "lag_convention": "positive means channel 2 lags channel 1; DC removed",
        },
        {"mid": "FS", "side": "FS", "lag_seconds": "s", "cross_correlation": "FS² sum"},
        {"kind": "line", "x": "lag_seconds", "y": "cross_correlation"},
    )


def correlation(read, n, c, fs, p):
    x = dense(read, n, c, maximum=2000000)
    if c not in (1, 2):
        raise ValidationError("Select one channel (auto) or two (cross).")
    a = x[:, 0] - x[:, 0].mean()
    b = a if c == 1 else x[:, 1] - x[:, 1].mean()
    values = signal.correlate(b, a, method="fft")
    lags = signal.correlation_lags(n, n)
    maximum = number(p, "max_lag_seconds", min(1, n / fs), 0, n / fs)
    mask = abs(lags) <= maximum * fs
    norm = np.sqrt(np.dot(a, a) * np.dot(b, b))
    return output(
        {"lag": lags[mask] / fs, "correlation": values[mask] / norm if norm else np.full(mask.sum(), np.nan)},
        {
            "normalization": "global energy; biased normalized correlation",
            "lag_convention": "positive means second lags first",
        },
        {"lag": "s", "correlation": "ratio"},
        {"kind": "line", "x": "lag", "y": "correlation"},
    )


def coherence(read, n, c, fs, p):
    if c != 2:
        raise ValidationError("Select exactly two channels.")
    length, hop, count = frame_config(p, n, fs)
    win = window(p, length)
    aa = np.zeros(length // 2 + 1)
    bb = aa.copy()
    ab = np.zeros_like(aa, dtype=complex)
    for first, frames in frame_batches(read, n, c, p, fs):
        frames = frames - frames.mean(axis=1, keepdims=True)
        z = fft.rfft(frames * win[None, :, None], axis=1)
        aa += (abs(z[:, :, 0]) ** 2).sum(axis=0)
        bb += (abs(z[:, :, 1]) ** 2).sum(axis=0)
        ab += (z[:, :, 0].conj() * z[:, :, 1]).sum(axis=0)
    den = aa * bb
    coh = np.divide(abs(ab) ** 2, den, out=np.full_like(aa, np.nan), where=den > 0)
    cross = ab / (count * fs * np.sum(win**2))
    cross[1:] *= 2
    if length % 2 == 0:
        cross[-1] /= 2
    return output(
        {"frequency": fft.rfftfreq(length, 1 / fs), "coherence": np.clip(coh, 0, 1), "cross_spectrum": cross},
        {
            "segments": count,
            "warning": "One segment gives trivial coherence; use multiple windows." if count < 2 else None,
        },
        {"frequency": "Hz", "coherence": "ratio", "cross_spectrum": "FS²/Hz"},
        {"kind": "line", "x": "frequency", "y": "coherence"},
    )


def filter_response(read, n, c, fs, p):
    # Response of a specified filter; input samples are not changed.
    order = integer(p, "order", 4, 1, 20)
    kind = p.get("kind", "butterworth")
    mode = p.get("mode", "lowpass")
    cutoff = p.get("cutoff_hz", min(1000, fs / 4))
    if mode not in {"lowpass", "highpass", "bandpass", "bandstop"}:
        raise ValidationError("Unsupported filter mode.")
    vals = np.atleast_1d(cutoff)
    if (
        len(vals) != (2 if mode in {"bandpass", "bandstop"} else 1)
        or np.any(vals <= 0)
        or np.any(vals >= fs / 2)
        or np.any(np.diff(vals) <= 0)
    ):
        raise ValidationError("Filter cutoff(s) must be ordered strictly inside Nyquist.")
    if kind == "butterworth":
        b, a = signal.butter(order, cutoff, btype=mode, fs=fs)
    elif kind == "fir":
        taps = integer(p, "taps", 101, 3, 8191)
        if taps % 2 == 0:
            raise ValidationError("Use odd FIR taps in this design profile.")
        b = signal.firwin(taps, cutoff, pass_zero=mode, fs=fs)
        a = np.array([1.0])
    else:
        raise ValidationError("Choose butterworth or fir.")
    f, h = signal.freqz(b, a, worN=2048, fs=fs)
    _, delay = signal.group_delay((b, a), w=f, fs=fs)
    zeros, poles, gain = signal.tf2zpk(b, a)
    return output(
        {
            "frequency": f,
            "magnitude": abs(h),
            "phase": np.unwrap(np.angle(h)),
            "group_delay": delay / fs,
            "zeros": zeros,
            "poles": poles,
            "b": b,
            "a": a,
            "impulse": signal.lfilter(b, a, np.r_[1.0, np.zeros(1023)]),
        },
        {
            "kind": kind,
            "mode": mode,
            "causal": True,
            "stable": bool(np.all(abs(poles) < 1)),
            "gain": float(gain),
            "group_delay_warning": "Ill-conditioned near spectral zeros.",
        },
        {"frequency": "Hz", "phase": "rad", "group_delay": "s"},
        {"kind": "line", "x": "frequency", "y": "magnitude"},
    )


def bicoherence(read, n, c, fs, p):
    if c != 1:
        raise ValidationError("Select one channel for bicoherence.")
    length, hop, count = frame_config(p, n, fs)
    if count < 8:
        raise ValidationError("Bicoherence requires at least eight windows.")
    bins = integer(p, "bins", min(128, length // 2), 4, min(256, length // 2))
    i, j = np.meshgrid(np.arange(bins), np.arange(bins), indexing="ij")
    valid = i + j <= length // 2
    numerator = np.zeros((bins, bins), complex)
    den1 = np.zeros((bins, bins))
    den2 = den1.copy()
    win = window(p, length)
    for _, frames in frame_batches(read, n, c, p, fs):
        z = fft.rfft((frames[:, :, 0] - frames[:, :, 0].mean(axis=1, keepdims=True)) * win, axis=1)
        for row in z:
            pair = row[i] * row[j]
            other = row[np.minimum(i + j, len(row) - 1)]
            numerator += pair * other.conj()
            den1 += abs(pair) ** 2
            den2 += abs(other) ** 2
    den = den1 * den2
    coherence = np.divide(abs(numerator) ** 2, den, out=np.full_like(den, np.nan), where=valid & (den > 0))
    coherence[~valid] = np.nan
    return output(
        {
            "frequency": np.arange(bins) * fs / length,
            "bicoherence": np.clip(coherence, 0, 1),
            "bispectrum": np.where(valid, numerator / count, np.nan),
        },
        {
            "segments": count,
            "definition": "squared normalized bicoherence, no significance claim",
            "caution": "Finite-sample and deterministic-tone coupling can yield high values.",
        },
        {"frequency": "Hz", "bicoherence": "ratio", "bispectrum": "unnormalized FFT³"},
        {"kind": "image", "x": "frequency", "y": "frequency", "z": "bicoherence"},
    )
