"""Deterministic bounded float64 rendering. Frames are half-open, output is never silently clipped."""

from copy import deepcopy
import math
import numpy as np
from scipy import signal
from .common import integer, number, optional
from ..domain.errors import ValidationError, ResourceLimit

MAX_SAMPLES = 8_000_000


def checked(x):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 2 or not x.size or x.size > MAX_SAMPLES or not np.isfinite(x).all():
        raise ResourceLimit("Render requires finite nonempty audio within the 8M-sample dense budget.")
    return x


def envelope(points, n):
    if not isinstance(points, list) or not 1 <= len(points) <= 4096:
        raise ValidationError("Automation needs 1–4096 [frame, gain_db] points.")
    frames, values = [], []
    for pair in points:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValidationError("Automation point must be [frame, gain_db].")
        f, db = pair
        if type(f) is not int or not 0 <= f < n or (frames and f <= frames[-1]):
            raise ValidationError("Automation frames must be unique, ordered and within the audio.")
        values.append(number({"v": db}, "v", 0, -120, 36))
        frames.append(f)
    return 10 ** (np.interp(np.arange(n), frames, values) / 20)


def design(p, fs):
    kind = p.get("kind", "butterworth")
    mode = p.get("mode", "lowpass")
    if mode not in {"lowpass", "highpass", "bandpass", "bandstop", "notch", "peaking"}:
        raise ValidationError("Unknown filter mode.")
    if mode in {"notch", "peaking"}:
        f = number(p, "frequency_hz", 50, 0.001, fs / 2 - 0.001)
        q = number(p, "q", 30, 0.1, 1000)
        if mode == "notch":
            b, a = signal.iirnotch(f, q, fs)
            return signal.tf2sos(b, a)
        gain = number(p, "gain_db", 0, -36, 36)
        A = 10 ** (gain / 40)
        w = 2 * np.pi * f / fs
        alpha = np.sin(w) / (2 * q)
        return signal.tf2sos(
            [1 + alpha * A, -2 * np.cos(w), 1 - alpha * A], [1 + alpha / A, -2 * np.cos(w), 1 - alpha / A]
        )
    cutoff = np.asarray(p.get("cutoff_hz", min(1000, fs / 4)), dtype=float)
    if (
        cutoff.ndim > 1
        or cutoff.size != (2 if mode.startswith("band") else 1)
        or not np.isfinite(cutoff).all()
        or np.any(cutoff <= 0)
        or np.any(cutoff >= fs / 2)
        or (cutoff.size == 2 and cutoff[0] >= cutoff[1])
    ):
        raise ValidationError("Cutoffs must be ordered inside (0, Nyquist).")
    order = integer(p, "order", 4, 1, 16)
    if kind == "butterworth":
        return signal.butter(
            order, cutoff if cutoff.size == 2 else cutoff.item(), btype=mode, fs=fs, output="sos"
        )
    if kind == "fir":
        taps = integer(p, "taps", 129, 3, 8191)
        if taps % 2 == 0:
            raise ValidationError("Use odd FIR tap count.")
        return signal.firwin(
            taps,
            cutoff if cutoff.size == 2 else cutoff.item(),
            pass_zero=mode in {"lowpass", "bandstop"},
            fs=fs,
        )
    raise ValidationError("Choose butterworth or fir.")


def dynamics(x, fs, p):
    mode = p.get("mode", "compressor")
    if mode not in {"compressor", "limiter", "gate", "expander"}:
        raise ValidationError("Unknown dynamics mode.")
    threshold = number(p, "threshold_db", -18, -120, 0)
    ratio = number(p, "ratio", 4, 1, 100)
    attack = number(p, "attack_ms", 5, 0, 5000)
    release = number(p, "release_ms", 100, 0, 10000)
    makeup = number(p, "makeup_db", 0, -36, 36)
    level = 20 * np.log10(np.maximum(np.max(np.abs(x), axis=1), 1e-30))
    if mode in {"compressor", "limiter"}:
        gain = np.minimum(0, (threshold - level) * (1 - 1 / ratio if mode == "compressor" else 1))
    elif mode == "gate":
        gain = np.where(level < threshold, -120.0, 0.0)
    else:
        gain = np.minimum(0, (level - threshold) * (ratio - 1))
    a = math.exp(-1 / (max(attack, 1e-9) * 0.001 * fs))
    r = math.exp(-1 / (max(release, 1e-9) * 0.001 * fs))
    state = 0.0
    smoothed = np.empty(len(x))
    for i, target in enumerate(gain):
        coef = a if target < state else r
        state = coef * state + (1 - coef) * target
        smoothed[i] = state
    out = x * 10 ** ((smoothed + makeup) / 20)[:, None]
    if mode == "limiter":
        # Explicit final sample-peak ceiling; not a true-peak/lookahead limiter.
        ceiling = 10 ** ((threshold + makeup) / 20)
        out = np.clip(out, -ceiling, ceiling)
    return out


EFFECT_PARAMETERS = {
    "gain": {"db", "automation"},
    "fade": {"in_frames", "out_frames", "curve"},
    "normalize": {"target_db", "mode"},
    "reverse": set(),
    "silence": {"start_frame", "end_frame"},
    "trim": {"start_frame", "end_frame"},
    "resample": {"sample_rate"},
    "stretch": {"rate", "n_fft", "hop_length"},
    "pitch": {"semitones", "n_fft"},
    "filter": {"kind", "mode", "cutoff_hz", "order", "taps", "frequency_hz", "q", "gain_db", "zero_phase"},
    "dynamics": {"mode", "threshold_db", "ratio", "attack_ms", "release_ms", "makeup_db"},
    "convolve": {"impulse", "tail"},
    "spectral_mask": {"frame_length", "hop_length", "regions"},
    "denoise": {"frame_length", "hop_length", "noise_start", "noise_end", "strength", "floor"},
    "dehum": {"frequency_hz", "harmonics", "q"},
    "declick": {"threshold", "width"},
    "route": {"matrix"},
    "vst3": {"plugin", "class_id", "parameters", "state", "tail_seconds", "bypass", "plugin_digest"},
}


def stft_edit(x, fs, p, denoise=False):
    nfft = integer(p, "frame_length", 1024, 32, 16384)
    hop = integer(p, "hop_length", nfft // 4, 1, nfft // 2)
    if not signal.check_NOLA("hann", nfft, nfft - hop):
        raise ValidationError("Mask window/hop fails NOLA.")
    f, t, z = signal.stft(
        x, fs=fs, window="hann", nperseg=nfft, noverlap=nfft - hop, axis=0, boundary="zeros", padded=True
    )
    # scipy shape: frequency, channels, time
    if denoise:
        a = integer(p, "noise_start", 0, 0, len(x) - 1)
        b = integer(p, "noise_end", min(len(x), nfft * 4), a + 1, len(x))
        valid = (t >= a / fs) & (t < b / fs)
        if not valid.any():
            raise ValidationError("Noise profile has no STFT frames.")
        noise = np.mean(np.abs(z[:, :, valid]) ** 2, axis=-1, keepdims=True)
        strength = number(p, "strength", 1, 0, 10)
        floor = number(p, "floor", 0.05, 0, 1)
        gain = np.maximum(floor, 1 - strength * noise / np.maximum(np.abs(z) ** 2, 1e-30)) ** 0.5
        z *= gain
    else:
        regions = p.get("regions", [])
        if not isinstance(regions, list) or len(regions) > 256:
            raise ValidationError("Use at most 256 mask rectangles.")
        for region in regions:
            if set(region) - {"start_seconds", "end_seconds", "fmin", "fmax", "gain_db"}:
                raise ValidationError("Unknown mask field.")
            a = number(region, "start_seconds", 0, 0, len(x) / fs)
            b = number(region, "end_seconds", len(x) / fs, a, len(x) / fs)
            lo = number(region, "fmin", 0, 0, fs / 2)
            hi = number(region, "fmax", fs / 2, lo, fs / 2)
            weight = 10 ** (number(region, "gain_db", -120, -120, 24) / 20)
            z *= np.where(
                (
                    (f[:, None, None] >= lo)
                    & (f[:, None, None] <= hi)
                    & (t[None, None, :] >= a)
                    & (t[None, None, :] < b)
                ),
                weight,
                1,
            )
    _, out = signal.istft(
        z,
        fs=fs,
        window="hann",
        nperseg=nfft,
        noverlap=nfft - hop,
        input_onesided=True,
        boundary=True,
        time_axis=-1,
        freq_axis=0,
    )
    return out.T[: len(x)]


def apply(x, fs, effects, plugin_processor=None):
    x = checked(x).copy()
    notes = []
    if not isinstance(effects, list) or len(effects) > 64:
        raise ValidationError("Use at most 64 effects per chain.")
    for p in effects:
        if not isinstance(p, dict):
            raise ValidationError("Effect must be an object.")
        op = p.get("type")
        if op not in EFFECT_PARAMETERS or set(p) - EFFECT_PARAMETERS[op] - {"type"}:
            raise ValidationError("Unknown effect or parameter: " + str(op))
        if op == "gain":
            x *= (
                envelope(p["automation"], len(x))[:, None]
                if "automation" in p
                else 10 ** (number(p, "db", 0, -120, 36) / 20)
            )
        elif op == "fade":
            a = integer(p, "in_frames", 0, 0, len(x))
            b = integer(p, "out_frames", 0, 0, len(x))
            if p.get("curve", "linear") not in {"linear", "equal_power"}:
                raise ValidationError("Unknown fade curve.")
            env = np.ones(len(x))
            if a:
                env[:a] *= np.linspace(0, 1, a)
            if b:
                env[-b:] *= np.linspace(1, 0, b)
            if p.get("curve") == "equal_power":
                env = np.sqrt(env)
            x *= env[:, None]
        elif op == "normalize":
            target = 10 ** (number(p, "target_db", -1, -120, 0) / 20)
            mode = p.get("mode", "peak")
            if mode not in {"peak", "rms"}:
                raise ValidationError("Normalize mode is peak or rms.")
            measured = np.max(np.abs(x)) if mode == "peak" else np.sqrt(np.mean(x * x))
            if measured > 0:
                x *= target / measured
        elif op == "reverse":
            x = x[::-1].copy()
        elif op in {"silence", "trim"}:
            a = integer(p, "start_frame", 0, 0, len(x) - 1)
            b = integer(p, "end_frame", len(x), a + 1, len(x))
            if op == "silence":
                x[a:b] = 0
            else:
                x = x[a:b].copy()
        elif op == "resample":
            new = integer(p, "sample_rate", fs, 8000, 384000)
            if math.ceil(len(x) * new / fs) * x.shape[1] > MAX_SAMPLES:
                raise ResourceLimit("Resampled output exceeds budget.")
            g = math.gcd(fs, new)
            x = signal.resample_poly(x, new // g, fs // g, axis=0)
            fs = new
        elif op in {"stretch", "pitch"}:
            librosa = optional("librosa")
            nfft = integer(p, "n_fft", 2048, 32, 16384)
            if len(x) < nfft:
                raise ValidationError("Pitch/stretch selection must span an FFT window.")
            if op == "stretch":
                rate = number(p, "rate", 1, 0.125, 8)
                if math.ceil(len(x) / rate) * x.shape[1] > MAX_SAMPLES:
                    raise ResourceLimit("Stretched output exceeds budget.")
                hop = integer(p, "hop_length", nfft // 4, 1, nfft // 2)
                x = librosa.effects.time_stretch(x.T, rate=rate, n_fft=nfft, hop_length=hop).T
            else:
                x = librosa.effects.pitch_shift(
                    x.T, sr=fs, n_steps=number(p, "semitones", 0, -24, 24), n_fft=nfft
                ).T
            notes.append("Phase-vocoder time support is approximate and may smear transients.")
        elif op == "filter":
            coeff = design(p, fs)
            zero = p.get("zero_phase", False)
            if type(zero) is not bool:
                raise ValidationError("zero_phase must be Boolean.")
            if coeff.ndim == 2:
                if np.any(np.abs(signal.sos2zpk(coeff)[1]) >= 1):
                    raise ValidationError("Unstable IIR filter.")
                x = signal.sosfiltfilt(coeff, x, axis=0) if zero else signal.sosfilt(coeff, x, axis=0)
            else:
                x = (
                    signal.filtfilt(coeff, [1.0], x, axis=0)
                    if zero
                    else signal.lfilter(coeff, [1.0], x, axis=0)
                )
            notes.append(
                "Zero-phase noncausal boundaries."
                if zero
                else "Causal filter starts from zero state; group delay retained."
            )
        elif op == "dynamics":
            x = dynamics(x, fs, p)
        elif op == "convolve":
            h = np.asarray(p.get("impulse"), dtype=float)
            if h.ndim != 1 or not 1 <= h.size <= 262144 or not np.isfinite(h).all():
                raise ValidationError("Impulse must be finite mono samples (1–262144).")
            if p.get("tail", "keep") not in {"keep", "trim"}:
                raise ValidationError("Convolution tail is keep or trim.")
            length = len(x)
            outlength = length + len(h) - 1
            if outlength * x.shape[1] > MAX_SAMPLES:
                raise ResourceLimit("Convolution output exceeds budget.")
            x = signal.oaconvolve(x, h[:, None], mode="full", axes=0)
            if p.get("tail") == "trim":
                x = x[:length]
        elif op in {"spectral_mask", "denoise"}:
            if len(x) < integer(p, "frame_length", 1024, 32, 16384):
                raise ValidationError("Selection shorter than STFT window.")
            x = stft_edit(x, fs, p, op == "denoise")
            notes.append(
                "STFT processing has overlapping time support; mask boundaries are not sample-local."
            )
        elif op == "dehum":
            f = number(p, "frequency_hz", 50, 0.1, fs / 2 - 0.01)
            count = integer(p, "harmonics", 6, 1, 64)
            q = number(p, "q", 30, 0.1, 1000)
            for k in range(1, count + 1):
                if k * f >= fs / 2:
                    break
                b, a = signal.iirnotch(k * f, q, fs)
                x = signal.lfilter(b, a, x, axis=0)
            notes.append("Causal notches can remove wanted signal at matching frequencies.")
        elif op == "declick":
            width = integer(p, "width", 5, 3, 101)
            if width % 2 == 0:
                raise ValidationError("Declick width must be odd.")
            threshold = number(p, "threshold", 8, 2, 100)
            from scipy.ndimage import median_filter

            med = median_filter(x, size=(width, 1), mode="nearest")
            residual = x - med
            scale = np.median(np.abs(residual), axis=0) * 1.4826
            mask = np.abs(residual) > threshold * np.maximum(scale, 1e-6)
            x = np.where(mask, med, x)
            notes.append("Median replacement can alter percussion/transients; compare the residual.")
        elif op == "route":
            matrix = np.asarray(p.get("matrix"), dtype=float)
            if (
                matrix.ndim != 2
                or matrix.shape[0] != x.shape[1]
                or not 1 <= matrix.shape[1] <= 32
                or not np.isfinite(matrix).all()
                or np.max(np.abs(matrix)) > 16
            ):
                raise ValidationError(
                    "Routing matrix is input_channels × output_channels, bounded finite gains."
                )
            if len(x) * matrix.shape[1] > MAX_SAMPLES:
                raise ResourceLimit("Routed audio exceeds budget.")
            x = x @ matrix
        elif op == "vst3":
            if plugin_processor is None:
                raise ValidationError("VST3 requires the isolated native host.")
            x, meta = plugin_processor(x, fs, p)
            notes.append(meta)
        x = checked(x)
    return x, fs, notes


def single_clip(region, sample_rate, channels, effects=None):
    from dataclasses import asdict

    return {
        "version": 1,
        "sample_rate": sample_rate,
        "channels": channels,
        "tracks": [
            {
                "name": "Track 1",
                "gain_db": 0,
                "clips": [{"region": asdict(region), "at": 0, "effects": effects or []}],
            }
        ],
        "effects": [],
    }


def split_clip(clip, frame):
    """Split an unprocessed clip at a source frame. Effects must be applied after splitting."""
    if clip.get("effects"):
        raise ValidationError("Split source clips before adding their effects.")
    r = clip["region"]
    a = r["start_frame"]
    b = r["end_frame"]
    if type(frame) is not int or not a < frame < b:
        raise ValidationError("Split frame outside source clip.")
    left, right = deepcopy(clip), deepcopy(clip)
    left["region"]["end_frame"] = frame
    right["region"]["start_frame"] = frame
    right["at"] = clip.get("at", 0) + frame - a
    return left, right


def cut_clip(clip, start, end, ripple=True):
    """Remove a source interval; return remaining clips for cut/copy/paste workflows."""
    if clip.get("effects"):
        raise ValidationError("Cut source clips before adding their effects.")
    r = clip["region"]
    a, b = r["start_frame"], r["end_frame"]
    if type(start) is not int or type(end) is not int or not a <= start < end <= b:
        raise ValidationError("Cut interval outside source clip.")
    out = []
    if start > a:
        left = deepcopy(clip)
        left["region"]["end_frame"] = start
        out.append(left)
    if end < b:
        right = deepcopy(clip)
        right["region"]["start_frame"] = end
        right["at"] = clip.get("at", 0) + (start - a if ripple else end - a)
        out.append(right)
    return out


def response(read, n, c, fs, p):
    from .common import output

    coeff = design(p, fs)
    if coeff.ndim == 2:
        f, h = signal.sosfreqz(coeff, worN=4096, fs=fs)
        z, poles, k = signal.sos2zpk(coeff)
    else:
        f, h = signal.freqz(coeff, worN=4096, fs=fs)
        z, poles, k = signal.tf2zpk(coeff, [1.0])
    phase = np.unwrap(np.angle(h))
    delay = -np.gradient(phase, 2 * np.pi * f / fs)
    if p.get("zero_phase", False):
        h = np.abs(h) ** 2
        phase = np.zeros(len(f))
        delay = np.zeros(len(f))
    return output(
        {
            "frequency": f,
            "magnitude_db": 20 * np.log10(np.maximum(abs(h), 1e-30)),
            "phase": phase,
            "group_delay_samples": delay,
            "zeros": z,
            "poles": poles,
        },
        {
            "stable": bool(np.all(np.abs(poles) < 1)),
            "mode": "forward-backward (noncausal), magnitude squared"
            if p.get("zero_phase", False)
            else "causal zero-initial-state prototype",
            "coefficient_gain": float(k),
            "group_delay_warning": "Numerical derivative near response nulls is ill-conditioned.",
        },
        {
            "frequency": "Hz",
            "magnitude_db": "dB ratio",
            "phase": "rad",
            "group_delay_samples": "samples",
            "zeros": "complex z-plane",
            "poles": "complex z-plane",
        },
        {"kind": "line", "x": "frequency", "y": "magnitude_db"},
    )
