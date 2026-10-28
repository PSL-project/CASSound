import numpy as np
from scipy import signal
from .common import dense, optional, output, number
from ..domain.errors import ValidationError


def loudness(read, n, c, fs, p):
    if c not in (1, 2):
        raise ValidationError(
            "This loudness profile supports explicitly selected mono or stereo, without implicit LFE/layout mapping."
        )
    if n / fs < 0.4:
        raise ValidationError("Integrated loudness needs at least 400 ms.")
    pyln = optional("pyloudnorm")
    x = dense(read, n, c, maximum=2000000)
    meter = pyln.Meter(fs)
    integrated = meter.integrated_loudness(x)
    weighted = x.copy()
    for filt in meter._filters.values():
        for ch in range(c):
            weighted[:, ch] = filt.apply_filter(weighted[:, ch])
    tracks = {}
    times = {}
    for label, seconds, hop in [("momentary", 0.4, 0.1), ("short_term", 3.0, 0.1)]:
        size = int(round(seconds * fs))
        step = int(round(hop * fs))
        starts = np.arange(0, max(0, n - size + 1), step)
        energy = np.asarray([np.mean(weighted[s : s + size] ** 2, axis=0).sum() for s in starts])
        value = np.full(len(energy), np.nan)
        valid = energy > 0
        value[valid] = -0.691 + 10 * np.log10(energy[valid])
        tracks[label] = value
        times[label + "_time"] = (starts + size / 2) / fs
    lra = None
    st = tracks["short_term"]
    absolute = st[np.isfinite(st) & (st >= -70)]
    if len(absolute) >= 2:
        reference = -0.691 + 10 * np.log10(np.mean(10 ** ((absolute + 0.691) / 10)))
        gated = absolute[absolute >= reference - 20]
        if len(gated) >= 2:
            lra = float(np.percentile(gated, 95) - np.percentile(gated, 10))
    reconstructed = signal.resample_poly(x, 4, 1, axis=0, window=("kaiser", 8.6))
    true_peak = abs(reconstructed).max(axis=0)
    arrays = {**tracks, **times, "sample_peak": abs(x).max(axis=0), "true_peak_estimate": true_peak}
    return output(
        arrays,
        {
            "integrated_lufs": float(integrated) if np.isfinite(integrated) else None,
            "loudness_range_lu": lra,
            "integrated_algorithm": "pyloudnorm ITU-R BS.1770-4",
            "time_track_algorithm": "ungated K-weighted means over 400 ms / 3 s",
            "true_peak_method": "4× polyphase Kaiser-sinc estimate, zero extension; not a certified Annex-2 meter",
            "undefined_values": "silent LUFS and too-short LRA are unavailable",
            "lra_method": "EBU Tech3342-style estimate: complete 3s windows every 0.1s, -70 LUFS absolute / -20 LU relative gates, P95-P10; not a certification",
        },
        {"momentary": "LUFS", "short_term": "LUFS", "sample_peak": "FS", "true_peak_estimate": "FS"},
        {"kind": "line", "x": "momentary_time", "y": "momentary"},
    )


def physics(read, n, c, fs, p):
    from .dsp import metrics

    quantity = p.get("quantity", "wavelength")
    if quantity == "wavelength":
        frequency = number(p, "frequency_hz", 0, 1e-12, 1e9)
        speed = number(p, "speed_m_s", 343, 1e-12, 1e9)
        return output(
            summary={
                "frequency_hz": frequency,
                "speed_m_s": speed,
                "wavelength_m": speed / frequency,
                "model": "lambda=c/f using supplied frequency and assumed propagation speed; not independently measured",
            }
        )
    if quantity not in {"pressure", "spl", "intensity", "digital"}:
        raise ValidationError(
            "Choose wavelength, digital, pressure, spl or plane-wave intensity. Total acoustic energy needs spatial/geometry measurements."
        )
    result = metrics(read, n, c, fs, p)
    if quantity == "digital":
        return result
    if not p.get("calibration_id"):
        raise ValidationError("Physical pressure/SPL needs an identified recording-chain calibration.")
    factor = number(p, "pa_per_fs", 0, 1e-15, 1e15)
    pressure = result["arrays"]["rms"] * factor
    arrays = {"rms_pressure": pressure, "spl": 20 * np.log10(np.maximum(pressure, 1e-30) / 20e-6)}
    units = {"rms_pressure": "Pa", "spl": "dB re 20 µPa"}
    if quantity == "intensity":
        if p.get("plane_wave_assumption") is not True:
            raise ValidationError(
                "Pressure-derived intensity requires an explicit progressive-plane-wave assumption."
            )
        density = number(p, "density_kg_m3", 1.204, 1e-12, 1e8)
        speed = number(p, "speed_m_s", 343, 1e-12, 1e9)
        arrays["intensity"] = pressure**2 / (density * speed)
        units["intensity"] = "W/m²"
    return output(
        arrays,
        {
            "calibration_id": p["calibration_id"],
            "pa_per_fs": factor,
            "assumptions": "User-supplied calibration must match microphone, gain and processing chain; no automatic authenticity of calibration.",
        },
        units,
    )
