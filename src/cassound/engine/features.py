"""Frame features and explicit, bounded speech/music measurement profiles."""

import numpy as np
from scipy import fft, signal, linalg
from .common import number, integer, window, frame_config, frame_batches, dense, db, output, optional
from ..domain.errors import ValidationError, ResourceLimit


def bank(read, n, c, fs, p):
    length, hop, count = frame_config(p, n, fs)
    if count * c > 1500000:
        raise ResourceLimit("Feature output budget exceeded. Increase hop or select fewer channels.")
    names = [
        "rms",
        "peak",
        "dc",
        "mean_square",
        "zcr",
        "crest_factor",
        "clipping_count",
        "centroid",
        "bandwidth",
        "rolloff",
        "flatness",
        "entropy",
        "flux",
        "contrast",
        "dominant_frequency",
        "tonal_prominence",
        "band_power",
        "harmonic_count",
        "frequency_drift",
        "click_score",
    ]
    result = {name: np.empty((count, c)) for name in names}
    win = window(p, length)
    frequency = fft.rfftfreq(length, 1 / fs)
    low = number(p, "fmin", 0, 0, fs / 2)
    high = number(p, "fmax", fs / 2, 0, fs / 2)
    if low >= high:
        raise ValidationError("Feature band bounds must be ordered.")
    selected = (frequency >= low) & (frequency <= high)
    if not selected.any():
        raise ValidationError("Band contains no frequency bins. Increase frame length.")
    clip = number(p, "clipping_threshold", 0.999, 0, 1e6)
    fundamental = p.get("fundamental")
    if fundamental is not None:
        fundamental = number(p, "fundamental", fundamental, 1, fs / 2)
    previous = None
    for first, frames in frame_batches(read, n, c, p, fs):
        size = len(frames)
        loc = slice(first, first + size)
        square = np.mean(frames**2, axis=1)
        rms = np.sqrt(square)
        peak = np.max(abs(frames), axis=1)
        result["mean_square"][loc] = square
        result["rms"][loc] = rms
        result["peak"][loc] = peak
        result["dc"][loc] = frames.mean(axis=1)
        result["zcr"][loc] = np.mean(np.diff(np.signbit(frames), axis=1), axis=1)
        result["clipping_count"][loc] = np.sum(abs(frames) >= clip, axis=1)
        result["crest_factor"][loc] = np.divide(peak, rms, out=np.full_like(peak, np.nan), where=rms > 0)
        result["click_score"][loc] = np.max(abs(np.diff(frames, axis=1)), axis=1) / np.maximum(rms, 1e-15)
        z = fft.rfft(frames * win[None, :, None], axis=1)
        power = abs(z) ** 2
        total = power.sum(axis=1)
        prob = np.divide(power, total[:, None, :], out=np.zeros_like(power), where=total[:, None, :] > 0)
        centroid = np.sum(prob * frequency[None, :, None], axis=1)
        result["centroid"][loc] = centroid
        result["bandwidth"][loc] = np.sqrt(
            np.sum(prob * (frequency[None, :, None] - centroid[:, None, :]) ** 2, axis=1)
        )
        result["rolloff"][loc] = frequency[np.argmax(np.cumsum(prob, axis=1) >= 0.85, axis=1)]
        result["flatness"][loc] = np.exp(np.mean(np.log(np.maximum(power, 1e-30)), axis=1)) / np.maximum(
            np.mean(power, axis=1), 1e-30
        )
        result["entropy"][loc] = -np.sum(prob * np.log(np.maximum(prob, 1e-30)), axis=1) / np.log(
            len(frequency)
        )
        amplitude = np.sqrt(prob)
        delta = np.diff(
            np.concatenate([previous[None] if previous is not None else amplitude[:1], amplitude], axis=0),
            axis=0,
        )
        result["flux"][loc] = np.sqrt(np.sum(np.maximum(delta, 0) ** 2, axis=1))
        previous = amplitude[-1].copy()
        result["contrast"][loc] = db(
            np.quantile(power, 0.9, axis=1) / np.maximum(np.quantile(power, 0.1, axis=1), 1e-30), power=True
        )
        indices = np.argmax(power[:, 1:, :], axis=1) + 1
        bi = np.arange(size)[:, None]
        ci = np.arange(c)[None, :]
        left = np.maximum(indices - 1, 0)
        right = np.minimum(indices + 1, len(frequency) - 1)
        logpower = np.log(np.maximum(power, 1e-30))
        a, b, d = logpower[bi, left, ci], logpower[bi, indices, ci], logpower[bi, right, ci]
        denominator = a - 2 * b + d
        shift = np.divide(0.5 * (a - d), denominator, out=np.zeros_like(b), where=abs(denominator) > 1e-20)
        shift = np.clip(shift, -0.5, 0.5)
        result["dominant_frequency"][loc] = (indices + shift) * fs / length
        offsets = np.r_[-30:-4, 5:31]
        neighbors = np.clip(indices[:, :, None] + offsets, 1, len(frequency) - 1)
        noise = np.median(power[bi[:, :, None], neighbors, ci[:, :, None]], axis=-1)
        result["tonal_prominence"][loc] = db(power[bi, indices, ci] / np.maximum(noise, 1e-30), power=True)
        psd = power / (fs * np.sum(win**2))
        psd[:, 1:] *= 2
        if length % 2 == 0:
            psd[:, -1] /= 2
        result["band_power"][loc] = psd[:, selected, :].sum(axis=1) * fs / length
        result["harmonic_count"][loc] = np.nan
        if fundamental is not None:
            harmonic_bins = np.rint(
                np.arange(1, min(32, int(fs / 2 / fundamental)) + 1) * fundamental * length / fs
            ).astype(int)
            strengths = np.stack(
                [
                    power[:, np.clip(idx + np.arange(-1, 2), 0, len(frequency) - 1), :].max(axis=1)
                    for idx in harmonic_bins
                ],
                axis=1,
            )
            result["harmonic_count"][loc] = np.sum(
                strengths > noise[:, None, :] * 10 ** (number(p, "harmonic_threshold_db", 12, 0, 100) / 10),
                axis=1,
            )
        silent = total <= 1e-25
        for name in [
            "centroid",
            "bandwidth",
            "rolloff",
            "flatness",
            "entropy",
            "contrast",
            "dominant_frequency",
            "tonal_prominence",
        ]:
            result[name][loc][silent] = np.nan
    if count > 1:
        result["frequency_drift"][:] = np.gradient(result["dominant_frequency"], hop / fs, axis=0)
    else:
        result["frequency_drift"][:] = np.nan
    result["time"] = (np.arange(count) * hop + length / 2) / fs
    units = {name: "ratio" for name in names}
    units.update({name: "Hz" for name in ["centroid", "bandwidth", "rolloff", "dominant_frequency"]})
    units.update(
        {
            "rms": "FS",
            "peak": "FS",
            "dc": "FS",
            "mean_square": "FS²",
            "band_power": "FS²",
            "tonal_prominence": "dB",
            "contrast": "dB",
            "frequency_drift": "Hz/s",
            "clipping_count": "count",
            "harmonic_count": "count",
            "time": "s relative to selection",
        }
    )
    return output(
        result,
        {
            "frame_length": length,
            "hop_length": hop,
            "window": p.get("window", "hann"),
            "padding": False,
            "covered_frames": (count - 1) * hop + length,
            "unanalysed_tail_frames": n - ((count - 1) * hop + length),
            "zcr_definition": "sign-bit transitions/(frame_length-1)",
            "prominence_definition": "peak power / median neighboring bins 5–30 away",
            "contrast_definition": "global 90th/10th spectral-power quantile; not octave-band contrast",
            "harmonic_fundamental_hz": fundamental,
            "flux_definition": "positive difference of L2-normalized magnitudes",
        },
        units,
        {"kind": "line", "x": "time", "y": "rms"},
    )


def yin_pitch(frames, fs, fmin=60, fmax=1000, threshold=0.15):
    if not (0 < fmin < fmax < fs / 2):
        raise ValidationError("Pitch bounds must be ordered inside Nyquist.")
    lo = max(2, int(fs / fmax))
    hi = min(frames.shape[1] // 2, int(fs / fmin))
    if hi <= lo:
        raise ValidationError("Pitch window is too short for the chosen pitch bounds.")
    pitch = np.full((len(frames), frames.shape[2]), np.nan)
    confidence = np.zeros_like(pitch)
    for row, frame in enumerate(frames):
        for ch in range(frame.shape[1]):
            values = frame[:, ch] - frame[:, ch].mean()
            if np.mean(values**2) < 1e-12:
                continue
            # Difference function with equal comparison support for all lags.
            support = len(values) - hi
            cross = signal.correlate(values, values[:support], mode="valid", method="fft")
            cumulative_square = np.r_[0.0, np.cumsum(values**2)]
            shifts = np.arange(hi + 1)
            diff = np.maximum(
                0,
                cumulative_square[support]
                + cumulative_square[shifts + support]
                - cumulative_square[shifts]
                - 2 * cross,
            )
            cumulative = np.cumsum(diff[1:])
            cmnd = np.ones(hi + 1)
            cmnd[1:] = np.divide(
                diff[1:] * np.arange(1, hi + 1), cumulative, out=np.ones(hi), where=cumulative > 1e-20
            )
            minima, _ = signal.find_peaks(-cmnd)
            candidates = minima[(minima >= lo) & (minima < hi) & (cmnd[minima] < threshold)]
            if not len(candidates):
                continue
            tau = int(candidates[0])
            a, b, d = cmnd[tau - 1 : tau + 2]
            den = a - 2 * b + d
            correction = 0.5 * (a - d) / den if abs(den) > 1e-20 else 0
            pitch[row, ch] = fs / (tau + np.clip(correction, -0.5, 0.5))
            confidence[row, ch] = 1 - cmnd[tau]
    return pitch, confidence


def pitch(read, n, c, fs, p):
    length, hop, count = frame_config(p, n, fs)
    if count * c > 10000:
        raise ResourceLimit("Pitch search budget exceeded. Use a shorter region or larger hop.")
    fmin = number(p, "fmin", 60, 1, fs / 2)
    fmax = number(p, "fmax", 1000, fmin, fs / 2)
    threshold = number(p, "yin_threshold", 0.15, 0.001, 0.5)
    values = np.empty((count, c))
    confidence = values.copy()
    for first, frames in frame_batches(read, n, c, p, fs):
        values[first : first + len(frames)], confidence[first : first + len(frames)] = yin_pitch(
            frames, fs, fmin, fmax, threshold
        )
    return output(
        {
            "time": (np.arange(count) * hop + length / 2) / fs,
            "pitch": values,
            "confidence": confidence,
            "voiced": np.isfinite(values),
        },
        {
            "method": "YIN difference with fixed support and parabolic lag interpolation",
            "fmin": fmin,
            "fmax": fmax,
            "confidence_kind": "1 - cumulative normalized difference; not calibrated probability",
        },
        {"pitch": "Hz", "confidence": "score"},
        {"kind": "line", "x": "time", "y": "pitch"},
    )


def acoustics(read, n, c, fs, p):
    if c != 1:
        raise ValidationError("Speech acoustics requires one selected channel.")
    x = dense(read, n, c, maximum=1000000)[:, 0]
    result = pitch(read, n, c, fs, p)
    f0 = result["arrays"]["pitch"][:, 0]
    voiced = np.isfinite(f0)
    fraction = float(voiced.mean())
    if fraction < number(p, "min_voiced_fraction", 0.8, 0.1, 1):
        raise ValidationError(
            "Speech acoustics requires a predominantly voiced selection. Select a sustained vowel."
        )
    f0median = float(np.nanmedian(f0))
    period = fs / f0median
    # Pulse peaks are waveform landmarks, not glottal closure estimates.
    peaks, _ = signal.find_peaks(
        x, distance=max(1, int(period * 0.7)), prominence=max(np.std(x) * 0.2, 1e-10)
    )
    periods = np.diff(peaks) / fs
    good = (periods >= 0.65 / f0median) & (periods <= 1.5 / f0median)
    consecutive = good[:-1] & good[1:]
    if np.sum(consecutive) < 3:
        raise ValidationError("Too few stable pulse intervals for jitter/shimmer estimates.")
    jitter = float(np.mean(abs(np.diff(periods))[consecutive]) / np.mean(periods[good]))
    amplitudes = []
    for a, b in zip(peaks[:-1], peaks[1:]):
        amplitudes.append(np.ptp(x[a : b + 1]))
    amplitudes = np.asarray(amplitudes)
    shimmer = float(np.mean(abs(np.diff(amplitudes))[consecutive]) / np.mean(amplitudes[good]))
    frame_length, hop, count = frame_config(p, n, fs)
    formants = np.full((count, 5), np.nan)
    bandwidth = np.full_like(formants, np.nan)
    hnr = np.full(count, np.nan)
    target = min(fs, 10000)
    sampled = signal.resample_poly(x, int(target), int(fs)) if fs > target else x
    order = integer(p, "lpc_order", 12, 4, 40)
    for i in np.flatnonzero(voiced):
        start = int(i * hop * target / fs)
        length = int(frame_length * target / fs)
        chunk = sampled[start : start + length]
        if len(chunk) <= order + 2:
            continue
        emphasized = signal.lfilter([1, -0.97], [1], chunk) * signal.windows.hamming(len(chunk), sym=False)
        corr = signal.correlate(emphasized, emphasized, mode="full", method="fft")[
            len(chunk) - 1 : len(chunk) + order
        ]
        if corr[0] < 1e-15:
            continue
        try:
            coeff = np.r_[
                1.0,
                linalg.solve_toeplitz(
                    corr[:order] + np.r_[corr[0] * 1e-8, np.zeros(order - 1)], -corr[1 : order + 1]
                ),
            ]
        except np.linalg.LinAlgError:
            continue
        roots = np.roots(coeff)
        roots = roots[(roots.imag > 0) & (abs(roots) < 1)]
        frequencies = np.angle(roots) * target / (2 * np.pi)
        widths = -np.log(abs(roots)) * target / np.pi
        valid = (frequencies > 90) & (frequencies < min(5000, target / 2)) & (widths < 700)
        indices = np.argsort(frequencies[valid])[:5]
        f = frequencies[valid][indices]
        w = widths[valid][indices]
        formants[i, : len(f)] = f
        bandwidth[i, : len(w)] = w
        raw = read(i * hop, i * hop + frame_length)[:, 0]
        raw = raw - raw.mean()
        lag = int(round(fs / f0[i]))
        if lag < len(raw):
            a, b = raw[:-lag], raw[lag:]
            den = np.sqrt(np.dot(a, a) * np.dot(b, b))
            r = np.dot(a, b) / den if den else 0
            if r > 0:
                hnr[i] = 10 * np.log10(min(r, 0.999999) / max(1 - r, 1e-6))
    return output(
        {
            "time": result["arrays"]["time"],
            "pitch": f0,
            "formants": formants,
            "formant_bandwidths": bandwidth,
            "hnr": hnr,
        },
        {
            "voiced_fraction": fraction,
            "jitter_local_estimate": jitter,
            "shimmer_local_estimate": shimmer,
            "voice_break_fraction": 1 - fraction,
            "pulse_method": "waveform peak intervals; not glottal closure or a clinical measure",
            "formant_method": "preemphasized autocorrelation LPC; candidates can be harmonics; inspect validity",
            "hnr_method": "normalized period-lag autocorrelation estimate",
        },
        {"time": "s", "pitch": "Hz", "formants": "Hz", "formant_bandwidths": "Hz", "hnr": "dB"},
        {"kind": "line", "x": "time", "y": "formants"},
    )


def music(read, n, c, fs, p):
    librosa = optional("librosa")
    x = dense(read, n, c, maximum=2000000)
    if c != 1:
        raise ValidationError("Select one channel; no implicit downmix for music features.")
    length = integer(p, "frame_length", min(2048, n), 32, min(65536, n))
    hop = integer(p, "hop_length", length // 4, 1, length)
    spectrum = abs(librosa.stft(x[:, 0], n_fft=length, hop_length=hop, center=False)) ** 2
    mel = librosa.feature.melspectrogram(S=spectrum, sr=fs, n_fft=length, n_mels=40)
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(mel, ref=1.0), n_mfcc=13)
    onset = librosa.onset.onset_strength(
        S=librosa.power_to_db(mel, ref=1.0), sr=fs, hop_length=hop, center=False
    )
    events = librosa.onset.onset_detect(onset_envelope=onset, sr=fs, hop_length=hop, units="time")
    tempo, beats = librosa.beat.beat_track(
        onset_envelope=onset, sr=fs, hop_length=hop, units="time", trim=False
    )
    # A transparent activity candidate, not speech identification.
    rms = np.sqrt(np.mean(np.lib.stride_tricks.sliding_window_view(x[:, 0], length)[::hop] ** 2, axis=1))
    activity = rms > max(number(p, "activity_floor", 0.01, 0, 10), float(np.quantile(rms, 0.2)) * 3)
    times = (np.arange(spectrum.shape[1]) * hop + length / 2) / fs
    return output(
        {
            "time": times,
            "mfcc": mfcc.T,
            "mel_power": mel.T,
            "onset_strength": onset,
            "onset_times": events + length / (2 * fs),
            "beat_times": beats + length / (2 * fs),
            "activity_candidate": activity,
        },
        {
            "tempo_bpm": float(np.asarray(tempo).reshape(-1)[0]),
            "activity_method": "RMS above max(floor, 3×20th-percentile RMS); not speech proof",
            "padding": False,
            "mfcc_reference": "log-mel power relative to 1 FS²",
        },
        {"mfcc": "cepstral coefficients", "mel_power": "mel-weighted raw STFT power", "time": "s"},
        {"kind": "line", "x": "time", "y": "onset_strength"},
    )


def voice_activity(read, n, c, fs, p):
    values = bank(read, n, c, fs, p)
    rms = values["arrays"]["rms"]
    zcr = values["arrays"]["zcr"]
    flatness = values["arrays"]["flatness"]
    floor = number(p, "activity_floor", 0.01, 0, 10)
    # Rule-based candidate only; evaluating actual speech identification needs labeled recordings.
    mask = (rms > floor) & (zcr > 0.005) & (zcr < 0.35) & (flatness < 0.5)
    return output(
        {"time": values["arrays"]["time"], "activity_candidate": mask, "rms": rms},
        {"method": "energy/ZCR/flatness heuristic; candidate activity, not a linguistic classifier"},
        {"rms": "FS"},
        {"kind": "line", "x": "time", "y": "activity_candidate"},
    )
