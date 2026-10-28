"""Calibrated-unit models, measurement validity and controlled system experiments."""

import numpy as np
from scipy import signal, fft
from .common import dense, number, integer, output, db
from ..domain.errors import ValidationError
from ..runtime.cache import cache_key

CHAIN_FIELDS = {"device", "input_channel", "sample_rate", "gain_db", "microphone", "interface", "mode"}


def chain(value):
    if not isinstance(value, dict) or set(value) != CHAIN_FIELDS:
        raise ValidationError(
            "Chain must identify device, input_channel, sample_rate, gain_db, microphone, interface and mode."
        )
    for k in {"device", "microphone", "interface", "mode"}:
        if not isinstance(value[k], str) or not 1 <= len(value[k]) <= 256:
            raise ValidationError("Chain identifiers must be explicit.")
    integer(value, "input_channel", 0, 0, 127)
    integer(value, "sample_rate", 48000, 8000, 384000)
    number(value, "gain_db", 0, -120, 120)
    return value


def calibrate(x, fs, p):
    ch = chain(p.get("chain"))
    frequency = number(p, "reference_hz", 1000, 1, fs / 2 - 0.001)
    spl = number(p, "reference_spl_db", 94, 0, 160)
    if ch["sample_rate"] != fs or x.shape[1] != 1 or len(x) < fs // 2:
        raise ValidationError("Calibration requires ≥0.5 s mono and matching chain sample rate.")
    if np.max(np.abs(x)) >= 0.999:
        raise ValidationError("Calibration is clipped.")
    x = x[:, 0] - np.mean(x[:, 0])
    rms = np.sqrt(np.mean(x * x))
    if rms < 1e-7:
        raise ValidationError("Calibration signal is too small.")
    f, psd = signal.welch(x, fs, nperseg=min(len(x), fs))
    peak = f[np.argmax(psd)]
    if abs(peak - frequency) > max(5, frequency * 0.02):
        raise ValidationError("Calibration dominant tone differs from the stated reference.")
    power = np.sum(psd)
    around = np.sum(psd[np.abs(f - frequency) <= max(5, frequency * 0.02)])
    if around / max(power, 1e-30) < 0.95:
        raise ValidationError("Calibration tone purity below 95%.")
    profile = {
        "version": 1,
        "chain": ch,
        "pa_per_fs": 20e-6 * 10 ** (spl / 20) / rms,
        "reference_hz": frequency,
        "reference_spl_db": spl,
        "rms_fs": float(rms),
        "reference_note": str(
            p.get("reference_note", "User-supplied calibrator level; no traceability asserted")
        )[:1024],
    }
    profile["calibration_id"] = "cal-" + cache_key(profile)
    return profile


def calibrated(read, n, c, fs, p):
    profile = p.get("profile")
    ch = chain(p.get("chain"))
    if not isinstance(profile, dict) or profile.get("version") != 1:
        raise ValidationError("A saved calibration profile is required.")
    saved = dict(profile)
    ident = saved.pop("calibration_id", None)
    if ident != "cal-" + cache_key(saved):
        raise ValidationError("Calibration profile digest mismatch.")
    if ch != profile.get("chain") or ch["sample_rate"] != fs or c != 1:
        raise ValidationError("Acquisition chain changed; recalibrate before claiming physical units.")
    scale = number(profile, "pa_per_fs", None, 1e-12, 1e12)
    x = dense(read, n, c)
    rms = np.sqrt(np.mean(x * x, axis=0)) * scale
    return output(
        {"rms_pressure": rms, "spl": 20 * np.log10(np.maximum(rms, 1e-30) / 20e-6)},
        {
            "calibration_id": ident,
            "weighting": "Z/unweighted",
            "chain": ch,
            "traceability": profile.get("reference_note"),
            "clipped": bool(np.max(np.abs(x)) >= 0.999),
        },
        {"rms_pressure": "Pa RMS", "spl": "dB re 20 µPa"},
        {"kind": "table"},
    )


def transfer(read, n, c, fs, p):
    if c != 2:
        raise ValidationError("Select two channels: reference input first, system output second.")
    x = dense(read, n, c)
    ref = x[:, 0]
    measured = x[:, 1]
    mode = p.get("method", "h1")
    reg = number(p, "regularization", 1e-8, 1e-14, 0.1)
    if np.mean(ref * ref) < 1e-20:
        raise ValidationError("Reference has no excitation energy.")
    if mode == "h1":
        length = integer(p, "frame_length", min(4096, n), 32, min(n, 65536))
        overlap = length // 2
        f, px = signal.welch(ref, fs, nperseg=length, noverlap=overlap)
        _, py = signal.welch(measured, fs, nperseg=length, noverlap=overlap)
        _, cross = signal.csd(ref, measured, fs, nperseg=length, noverlap=overlap)
        h = cross / (px + reg * np.max(px))
        coherence = np.abs(cross) ** 2 / np.maximum(px * py, 1e-30)
        ir = np.fft.irfft(h, n=length)
        reliable = (px > np.max(px) * 1e-5) & (coherence >= 0.8)
        delay = int(np.argmax(np.abs(signal.correlate(measured, ref, mode="full", method="fft"))) - (n - 1))
        method = "H1 Welch cross PSD / input PSD; periodic IFFT IR"
    elif mode == "deconvolution":
        size = fft.next_fast_len(2 * n - 1)
        X = fft.rfft(ref, size)
        Y = fft.rfft(measured, size)
        denom = np.abs(X) ** 2
        h = Y * np.conj(X) / (denom + reg * np.max(denom))
        f = fft.rfftfreq(size, 1 / fs)
        ir = fft.irfft(h, size)[: integer(p, "impulse_frames", min(n, fs), 1, n)]
        coherence = np.full(len(f), np.nan)
        reliable = denom > np.max(denom) * 1e-5
        delay = int(np.argmax(np.abs(ir)))
        method = "Regularized single-record linear deconvolution; coherence unavailable"
    else:
        raise ValidationError("Method is h1 or deconvolution.")
    return output(
        {
            "frequency": f,
            "magnitude_db": db(np.abs(h)),
            "phase": np.unwrap(np.angle(h)),
            "coherence": coherence,
            "reliable": reliable,
            "impulse_time": np.arange(len(ir)) / fs,
            "impulse": ir,
            "step": np.cumsum(ir),
        },
        {
            "method": method,
            "delay_frames": delay,
            "regularization": reg,
            "assumptions": "Linear time-invariant system, synchronized channels and known reference; output-before-input delay may be negative.",
        },
        {
            "frequency": "Hz",
            "magnitude_db": "dB ratio",
            "phase": "rad",
            "coherence": "1",
            "reliable": "Boolean",
            "impulse_time": "s",
            "impulse": "ratio/sample",
            "step": "ratio",
        },
        {"kind": "line", "x": "frequency", "y": "magnitude_db"},
    )


def room(read, n, c, fs, p):
    x = dense(read, n, c)
    direct = integer(p, "direct_frame", int(np.argmax(np.sum(x * x, axis=1))), 0, n - 1)
    minimum = number(p, "minimum_fit_r2", 0.95, 0, 1)
    results = []
    arrays = {"time": (np.arange(n - direct) + direct) / fs}
    units = {"time": "s"}
    for ch in range(c):
        y = x[direct:, ch]
        energy = y * y
        if np.max(energy) < 1e-24:
            raise ValidationError("Impulse response has no energy.")
        total = np.sum(energy)
        tail = energy[max(0, int(len(energy) * 0.9)) :]
        noise = float(np.mean(tail))
        edc = np.cumsum(energy[::-1])[::-1]
        edb = 10 * np.log10(np.maximum(edc, 1e-300) / total)
        peak = np.max(energy)
        snr = 10 * np.log10(peak / max(noise, 1e-300))
        duration = len(y) / fs
        estimates = {}
        for label, low, high in [("edt", 0, -10), ("t20", -5, -25), ("t30", -5, -35)]:
            indices = np.flatnonzero((edb <= low) & (edb >= high))
            reason = None
            rt = None
            r2 = None
            if duration < 0.1 or snr < abs(high) + 10:
                reason = "Insufficient estimated peak-to-tail-noise margin."
            elif indices.size < 16:
                reason = "Decay range not observed."
            elif indices[-1] >= len(y) * 0.9:
                reason = "Fit enters final noise-estimation segment."
            else:
                t = indices / fs
                coeff = np.polyfit(t, edb[indices], 1)
                fit = np.polyval(coeff, t)
                r2 = 1 - np.sum((edb[indices] - fit) ** 2) / max(
                    np.sum((edb[indices] - np.mean(edb[indices])) ** 2), 1e-30
                )
                if coeff[0] >= 0 or r2 < minimum:
                    reason = "Decay fit is non-linear or non-decaying."
                else:
                    rt = float(-60 / coeff[0])
            estimates[label] = {
                "rt60_seconds": rt,
                "fit_r2": None if r2 is None else float(r2),
                "invalid_reason": reason,
            }
        clarity = {}
        for ms in [50, 80]:
            split = min(len(y), round(ms * 0.001 * fs))
            early = float(np.sum(energy[:split]))
            late = float(np.sum(energy[split:]))
            clarity["c" + str(ms) + "_db"] = (
                None if late <= 0 else float(10 * np.log10(max(early, 1e-300) / late))
            )
        clarity["d50"] = float(np.sum(energy[: round(0.05 * fs)]) / total)
        clarity["center_time_seconds"] = float(np.sum(np.arange(len(y)) / fs * energy) / total)
        results.append({"channel": ch, "peak_to_tail_noise_db": float(snr), "decays": estimates, **clarity})
        arrays[f"decay_db_{ch}"] = edb
        units[f"decay_db_{ch}"] = "dB relative integrated energy"
        arrays[f"energy_time_{ch}"] = energy
        units[f"energy_time_{ch}"] = "FS²"
    return output(
        arrays,
        {
            "channels": results,
            "direct_frame": direct,
            "band": "broadband unfiltered",
            "noise_method": "mean squared final 10%; no noise subtraction",
            "scope": "Decay estimates, not an ISO 3382 certification. Truncation/background noise can invalidate slopes.",
        },
        units,
        {"kind": "line", "x": "time", "y": "decay_db_0"},
    )


def distortion(read, n, c, fs, p):
    x = dense(read, n, c)
    mode = p.get("mode", "thd")
    bands = []

    def fit(freqs):
        t = np.arange(n) / fs
        columns = [np.ones(n)]
        for f in freqs:
            columns.extend([np.sin(2 * np.pi * f * t), np.cos(2 * np.pi * f * t)])
        matrix = np.column_stack(columns)
        coef, _, _, _ = np.linalg.lstsq(matrix, x, rcond=None)
        amp = np.hypot(coef[1::2], coef[2::2])
        residual = x - matrix @ coef
        return amp, residual

    if mode == "thd":
        f = number(p, "fundamental_hz", 1000, 1, fs / 2 - 0.01)
        harmonics = integer(p, "harmonics", 5, 2, 32)
        freqs = [f * k for k in range(1, harmonics + 1) if f * k < fs / 2]
        if len(freqs) < 2 or n / fs < 10 / f:
            raise ValidationError("Need ≥10 periods and ≥2 harmonics below Nyquist.")
        amp, residual = fit(freqs)
        fundamental = amp[0]
        if np.any(fundamental < 1e-10):
            raise ValidationError("Specified fundamental absent.")
        metric = np.sqrt(np.sum(amp[1:] ** 2, axis=0)) / fundamental
        thdn = np.sqrt(np.sum(amp[1:] ** 2, axis=0) + 2 * np.mean(residual**2, axis=0)) / fundamental
        summary = {
            "thd_ratio": metric.tolist(),
            "thd_plus_residual_ratio": thdn.tolist(),
            "residual_includes": "noise and unmodeled content; no bandwidth weighting",
        }
        bands = freqs
    elif mode == "imd":
        f1 = number(p, "f1_hz", 19000, 1, fs / 2 - 0.01)
        f2 = number(p, "f2_hz", 20000, f1 + 1, fs / 2 - 0.01)
        products = sorted({f2 - f1, 2 * f1 - f2, 2 * f2 - f1} - {f1, f2})
        products = [f for f in products if 0 < f < fs / 2]
        if not products or n / fs < 10 / (f2 - f1):
            raise ValidationError("Need resolvable two-tone difference/products.")
        bands = [f1, f2] + products
        amp, residual = fit(bands)
        denom = np.sqrt(np.sum(amp[:2] ** 2, axis=0))
        if np.any(amp[:2] < 1e-10):
            raise ValidationError("Both primary tones must be present.")
        summary = {
            "selected_product_imd_ratio": (np.sqrt(np.sum(amp[2:] ** 2, axis=0)) / denom).tolist(),
            "profile": "difference and third-order products / RMS sum of primary amplitudes; not an SMPTE/CCIF standard result",
        }
    else:
        raise ValidationError("Mode is thd or imd.")
    summary.update(
        {
            "method": "joint sine/cosine least-squares at supplied frequencies",
            "clipped": bool(np.max(np.abs(x)) >= 0.999),
            "frequency_assumption": "Frequencies must be known; drifting/nonstationary sources bias estimates.",
        }
    )
    return output(
        {
            "frequency": np.array(bands),
            "amplitude": amp,
            "residual_rms": np.sqrt(np.mean(residual**2, axis=0)),
        },
        summary,
        {"frequency": "Hz", "amplitude": "FS peak", "residual_rms": "FS RMS"},
        {"kind": "line", "x": "frequency", "y": "amplitude"},
    )


def environment(p):
    if not isinstance(p, dict) or set(p) - {
        "temperature_c",
        "relative_humidity_percent",
        "pressure_pa",
        "name",
    }:
        raise ValidationError("Unknown environment profile fields.")
    t = number(p, "temperature_c", 20, -50, 60)
    humidity = number(p, "relative_humidity_percent", 50, 0, 100)
    pressure = number(p, "pressure_pa", 101325, 20000, 120000)
    kelvin = t + 273.15
    profile = {
        "version": 1,
        "name": str(p.get("name", "Environment"))[:128],
        "temperature_c": t,
        "relative_humidity_percent": humidity,
        "pressure_pa": pressure,
        "speed_m_s": float(np.sqrt(1.4 * 287.05 * kelvin)),
        "density_kg_m3": pressure / (287.05 * kelvin),
        "model": "ideal dry air: gamma=1.4, R=287.05 J/(kg K); humidity recorded but not modeled; no moist-air precision claim",
    }
    profile["environment_id"] = "env-" + cache_key(profile)
    return profile
